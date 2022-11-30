import logging
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
from avanza import OrderType

from module.day_trading_TA import Instrument, Strategy
from module.utils import Context, History, Settings

log = logging.getLogger("main.day_trading_ta_calibration")


@dataclass
class CalibrationOrder:
    instrument: Instrument

    on_balance: bool = False
    price_buy: Optional[float] = None
    price_sell: Optional[float] = None
    time_buy: Optional[datetime] = None
    time_sell: Optional[datetime] = None
    verdict: Optional[str] = None

    def buy(self, row: pd.Series, index: datetime) -> None:
        self.time_buy = index

        self.on_balance = True

        self.price_buy = ((row["Open"] + row["Close"]) / 2) * (
            1.00015 if self.instrument == Instrument.BULL else 0.99985
        )

    def sell(self, row: pd.Series, index: datetime):
        self.time_sell = index

        self.price_sell = (row["Close"] + row["Open"]) / 2

        if (
            self.price_sell <= self.price_buy and self.instrument == Instrument.BULL
        ) or (self.price_sell >= self.price_buy and self.instrument == Instrument.BEAR):
            self.verdict = "bad"

        else:
            self.verdict = "good"

    def pop_result(self) -> dict:
        profit: Optional[float] = None

        if self.price_sell is not None and self.price_buy is not None:
            profit = round(
                20
                * (self.price_sell - self.price_buy)
                * (1 if self.instrument == Instrument.BULL else -1)
                + 1000
            )

        points = 0
        points_bin = 0
        if profit is not None:
            points = 1 if (profit - 1000) > 0 else -1
            points_bin = points * (2 if abs(profit - 1000) > 100 else 1)
            points_bin = points * (3 if abs(profit - 1000) > 200 else 1)

        result = {
            "instrument": self.instrument,
            "price_buy": self.price_buy,
            "price_sell": self.price_sell,
            "time_buy": self.time_buy,
            "time_sell": self.time_sell,
            "verdict": self.verdict,
            "profit": profit,
            "points": points_bin,
        }

        self.on_balance = False
        self.price_buy = None
        self.price_sell = None
        self.time_buy = None
        self.time_sell = None
        self.verdict = None

        return result


class Helper:
    def __init__(self, strategy_name: str) -> None:
        self.strategy_name = strategy_name

        self.orders: Dict[Instrument, CalibrationOrder] = {
            i: CalibrationOrder(i) for i in Instrument
        }

        self.orders_history: List[dict] = []

    def signal_to_instrument(self, signal: OrderType) -> dict:
        return {
            OrderType.BUY: Instrument.BULL
            if signal == OrderType.BUY
            else Instrument.BEAR,
            OrderType.SELL: Instrument.BEAR
            if signal == OrderType.BUY
            else Instrument.BULL,
        }

    def buy_order(
        self,
        signal: Optional[OrderType],
        index: datetime,
        row: pd.Series,
        instrument: Instrument,
    ) -> None:
        if any([signal is None, self.orders[instrument].on_balance]):
            return

        self.orders[instrument].buy(row, index)

    def sell_order(
        self,
        index: datetime,
        row: pd.Series,
        instrument: Instrument,
    ) -> None:
        if not self.orders[instrument].on_balance:
            return

        self.orders[instrument].sell(row, index)

        self.orders_history.append(self.orders[instrument].pop_result())

    def get_signal(
        self,
        strategy_logic: dict,
        row: pd.Series,
    ) -> Optional[OrderType]:

        for signal in [OrderType.BUY, OrderType.SELL]:
            if all([i(row) for i in strategy_logic[signal]]):
                return signal

        return None

    def get_orders_history_summary(self) -> dict:
        if len(self.orders_history) == 0:
            return {"strategy": self.strategy_name, "points": 0, "profit": 0}

        df = pd.DataFrame(self.orders_history)
        df.profit = df.profit.astype(float)

        numbers = {
            "trades": len(df),
            "good": len(df[df.verdict == "good"]),
            "bad": len(df[df.verdict == "bad"]),
            "BULL": len(df[df.instrument == Instrument.BULL]),
            "BEAR": len(df[df.instrument == Instrument.BEAR]),
        }

        return {
            "strategy": self.strategy_name,
            "points": int(df.points.sum()),
            "profit": int(df.profit.sum() - len(df) * 1000),
            "efficiency": f"{round(100 * len(df[df.verdict == 'good']) / len(df))}%",
            "numbers": f"[trades: {numbers['trades']}] "
            + ("" if numbers["BULL"] == 0 else f"[BULL: {numbers['BULL']}] ")
            + ("" if numbers["BEAR"] == 0 else f"[BEAR: {numbers['BEAR']}] ")
            + f"[good: {numbers['good']}]",
        }

    def print_orders_history(self) -> None:
        if len(self.orders_history) == 0:
            return

        df = pd.DataFrame(self.orders_history)
        df.profit = df.profit.astype(int)
        df.time_buy = df.time_buy.dt.strftime("%m-%d %H:%M")
        df.time_sell = df.time_sell.dt.strftime("%m-%d %H:%M")
        df.price_sell = df.price_sell.round(2)
        df.price_buy = df.price_buy.round(2)
        df.instrument = df.instrument.apply(lambda x: x.value)

        log.info(f"\n{df}")


class Calibration:
    def __init__(self, settings: dict, user: str):
        self.settings = settings

        self.ava = Context(user, settings["accounts"], skip_lists=True)

        self.strategies: List[dict] = []

    def _walk_through_strategies(
        self,
        history: History,
        strategy: Strategy,
        print_orders_history: bool,
    ) -> None:
        self.strategies = []

        daily_volumes = history.data.groupby([history.data.index.date]).sum()["Volume"].values.tolist()  # type: ignore

        log.info(
            f"Dates range: {history.data.index[0].strftime('%Y.%m.%d')} - {history.data.index[-1].strftime('%Y.%m.%d')} "  # type: ignore
            + f"({history.data.shape[0]} rows) "
            + f"({len([i for i in daily_volumes if i > 0])} / {len(daily_volumes)} days with Volume)"
        )

        for i, (strategy_name, strategy_logic) in enumerate(
            strategy.strategies.items()
        ):
            helper = Helper(strategy_name)
            signal = None

            for index, row in history.data.iterrows():
                time_index: datetime = index  # type: ignore

                if time_index.hour < 10:
                    continue

                if time_index.hour == 17 and time_index.minute >= 15:
                    for instrument in helper.orders:
                        helper.sell_order(time_index, row, instrument)

                    continue

                if signal is not None:
                    helper.sell_order(
                        time_index,
                        row,
                        helper.signal_to_instrument(signal)[OrderType.SELL],
                    )
                    helper.buy_order(
                        signal,
                        time_index,
                        row,
                        helper.signal_to_instrument(signal)[OrderType.BUY],
                    )

                signal = helper.get_signal(strategy_logic, row)

            strategy_summary = helper.get_orders_history_summary()

            self.strategies.append(strategy_summary)

            if strategy_summary["profit"] <= 0:
                continue

            log.info(
                f"[{i+1}/{len(strategy.strategies)}] > "
                + " | ".join([f"{k}: {v}" for k, v in strategy_summary.items()])
            )

            if print_orders_history:
                helper.print_orders_history()

    def update(self, print_orders_history: bool) -> None:
        log.info("Updating strategies")

        extra_data = self.ava.get_today_history(
            self.settings["instruments"]["MONITORING"]["AVA"]
        )

        history = History(
            self.settings["instruments"]["MONITORING"]["YAHOO"],
            "80d",
            "1m",
            cache="append",
            extra_data=extra_data,
        )

        strategy = Strategy(history.data)

        self._walk_through_strategies(history, strategy, print_orders_history)

        self.strategies = [
            s
            for s in sorted(self.strategies, key=lambda s: s["points"], reverse=True)
            if s["profit"] > 0 and s["points"] > 0
        ]

        Strategy.dump(
            "DT_TA",
            {
                "80d": self.strategies,
            },
        )

    def test(self, print_orders_history: bool) -> None:
        log.info("Testing strategies")

        extra_data = self.ava.get_today_history(
            self.settings["instruments"]["MONITORING"]["AVA"]
        )

        history = History(
            self.settings["instruments"]["MONITORING"]["YAHOO"],
            "14d",
            "1m",
            cache="append",
            extra_data=extra_data,
        )

        strategies = Strategy.load("DT_TA")
        strategies_dict = {
            i["strategy"]: i["points"] for i in strategies.get("80d", [])
        }

        strategy = Strategy(history.data, strategies=list(strategies_dict.keys()))

        self._walk_through_strategies(history, strategy, print_orders_history)

        strategies["14d"] = [
            s
            for s in sorted(self.strategies, key=lambda s: s["points"], reverse=True)
            if s["profit"] > 0
        ]

        for s in strategies["14d"]:
            strategies_dict[s["strategy"]] += s["points"]

        strategies["use"] = max(strategies_dict, key=strategies_dict.get)  # type: ignore

        Strategy.dump("DT_TA", strategies)


def run(
    update: bool = True, test: bool = True, print_orders_history: bool = False
) -> None:
    settings = Settings().load()

    for user, settings_per_user in settings.items():
        for setting_per_setup in settings_per_user.values():
            if not setting_per_setup.get("run_day_trading", False):
                continue

            try:
                calibration = Calibration(setting_per_setup, user)

                if update:
                    calibration.update(print_orders_history)

                if test:
                    calibration.test(print_orders_history)

            except Exception as exc:
                log.error(f">>> {exc}: {traceback.format_exc()}")

            return