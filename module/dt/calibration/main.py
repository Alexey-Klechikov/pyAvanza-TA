import logging
import time
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
from avanza import InstrumentType, OrderType

from module.dt import DayTime, Strategy, TradingTime
from module.dt.calibration.order import CalibrationOrder
from module.dt.common_types import Instrument
from module.utils import Cache, Context, History, Settings, TeleLog, displace_message

log = logging.getLogger("main.dt.calibration.main")

DISPLACEMENTS = (9, 60, 3, 4, 4, 15, 15, 0)


class Helper:
    def __init__(self, strategy_name: str, settings: dict) -> None:
        self.strategy_name = strategy_name
        self.settings = settings

        self.orders: Dict[Instrument, CalibrationOrder] = {
            i: CalibrationOrder(i) for i in Instrument
        }

        self.orders_history: List[dict] = []

    def buy_order(
        self,
        row: pd.Series,
        instrument: Instrument,
    ) -> None:
        if not self.orders[instrument].on_balance:
            self.orders[instrument].buy(row)

        self.orders[instrument].set_limits(row, self.settings["trading"])

    def sell_order(
        self,
        row: pd.Series,
        instrument: Instrument,
    ) -> None:
        if not self.orders[instrument].on_balance:
            return

        self.orders[instrument].sell(row)

        self.orders_history.append(self.orders[instrument].pop_result())

    def check_orders_for_limits(self, row: pd.Series) -> None:
        for instrument, calibration_order in self.orders.items():
            if calibration_order.check_limits(row):
                self.sell_order(row, instrument)

    @staticmethod
    def get_signal(strategy_logic: dict, row: pd.Series) -> Optional[OrderType]:
        for signal in [OrderType.BUY, OrderType.SELL]:
            if all([i(row) for i in strategy_logic[signal]]):
                return signal

        return None

    def get_exit_instrument(
        self, row: pd.Series, history: pd.DataFrame
    ) -> Optional[Instrument]:
        for instrument, calibration_order in self.orders.items():
            if (
                not calibration_order.on_balance
                or not calibration_order.price_buy
                or not calibration_order.price_sell
                or not calibration_order.time_buy
            ):
                continue

            history_slice = history.loc[calibration_order.time_buy : row.name]["Close"]  # type: ignore
            price_change = calibration_order.price_sell / calibration_order.price_buy
            percent_exit = self.settings["trading"]["exit"] - 1
            percent_pullback = 1 - self.settings["trading"]["pullback"]

            if any(
                [
                    all(
                        [
                            instrument == Instrument.BULL,
                            row["RSI"] < 60,
                            (price_change - 1) * 20 > percent_exit,
                            ((1 - row["Close"] / history_slice.max()) * 20)
                            > percent_pullback,
                        ]
                    ),
                    all(
                        [
                            instrument == Instrument.BEAR,
                            row["RSI"] > 40,
                            (1 - price_change) * 20 > percent_exit,
                            ((row["Close"] / history_slice.min() - 1) * 20)
                            > percent_pullback,
                        ]
                    ),
                ]
            ):
                return instrument

        return None

    def get_orders_history_summary(self) -> dict:
        if len(self.orders_history) == 0:
            return {
                "strategy": self.strategy_name,
                "points": 0,
                "profit": 0,
                "efficiency": "0%",
            }

        df = pd.DataFrame(self.orders_history)
        df.profit = df.profit.astype(float)

        numbers = {}
        for instrument in Instrument:
            number_trades = len(df[df.instrument == instrument])
            number_good_trades = len(
                df[(df.instrument == instrument) & (df.verdict == "good")]
            )
            numbers[instrument] = (
                ""
                if number_trades == 0
                else f"{round(number_good_trades / number_trades * 100)}% - {number_good_trades} / {number_trades}"
            )

        return {
            "strategy": self.strategy_name,
            "points": int(df.points.sum()),
            "profit": int(df.profit.sum() - len(df) * 1000),
            "efficiency": f"{round(100 * len(df[df.verdict == 'good']) / len(df))}%",
            "numbers_bull": numbers[Instrument.BULL],
            "numbers_bear": numbers[Instrument.BEAR],
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
    def __init__(self, print_orders_history: bool):
        self.settings = Settings().load("DT")
        self.print_orders_history = print_orders_history

        self.ava = Context(
            self.settings["user"], self.settings["accounts"], process_lists=False
        )

        self.conditions: dict = {}

    def _walk_through_strategies(
        self,
        period: str,
        interval: str,
        cache: Cache,
        filter_strategies: bool,
        loaded_strategies: List[dict],
        limit_history_hours: int = 9999,
    ) -> List[dict]:
        strategy = Strategy(
            History(
                self.settings["instruments"]["MONITORING"]["YAHOO"],
                period,
                interval,
                cache,
                extra_data=self.ava.get_today_history(
                    self.settings["instruments"]["MONITORING"]["AVA"]
                ),
            ).data[
                datetime.now() - timedelta(hours=limit_history_hours) :  # type: ignore
            ],
            strategies=loaded_strategies,
        )

        strategies = []

        self.conditions = strategy.components.conditions

        daily_volumes = strategy.data.groupby([strategy.data.index.date])["Volume"].sum().values.tolist()  # type: ignore

        log.info(
            " ".join(
                [
                    f"Dates range: {strategy.data.index[0].strftime('%Y.%m.%d')} - {strategy.data.index[-1].strftime('%Y.%m.%d')}",  # type: ignore
                    f"(Rows: {strategy.data.shape[0]})",
                    f"(Days with volume: {len([i for i in daily_volumes if i > 0])} / {len(daily_volumes)})",
                ]
            )
        )

        log.info(
            displace_message(
                DISPLACEMENTS,
                (
                    "Counter",
                    "Strategy",
                    "Pts",
                    "Prft",
                    "Effi",
                    " | ".join(
                        ["Numbers BULL", "Numbers BEAR", "Signal (at)"][
                            : 2 if filter_strategies else 3
                        ]
                    ),
                ),
            )
        )

        for i, (strategy_name, strategy_logic) in enumerate(
            strategy.strategies.items()
        ):
            helper = Helper(strategy_name, self.settings)
            last_signal = {"signal": None, "time": ""}

            last_signal = self._walk_through_day(
                strategy, helper, strategy_logic, last_signal
            )

            strategy_summary = helper.get_orders_history_summary()

            if filter_strategies and any(
                [
                    strategy_summary["points"] < -10,
                    strategy_summary["profit"] <= 100,
                    int(strategy_summary["efficiency"][:-1]) < 50,
                ]
            ):
                continue

            strategies.append(strategy_summary)

            log.info(
                displace_message(
                    DISPLACEMENTS,
                    list(
                        [f"[{i+1}/{len(strategy.strategies)}]"]
                        + list(strategy_summary.values())
                        + [
                            ""
                            if not last_signal["signal"]
                            else f"{last_signal['signal']} ({last_signal['time']})"
                        ]
                    )[: 7 if filter_strategies else 8],
                )
            )

            if self.print_orders_history:
                helper.print_orders_history()

        return strategies

    def _walk_through_day(
        self,
        strategy: Strategy,
        helper: Helper,
        strategy_logic: dict,
        last_signal: dict,
    ) -> dict:
        exit_instrument = None
        signal = None

        for index, row in strategy.data.iterrows():
            time_index: datetime = index  # type: ignore

            if time_index.hour < 10:
                continue

            if (time_index.hour == 17 and time_index.minute >= 15) or (
                strategy.data.iloc[-1].name == time_index
            ):
                if any(
                    [
                        (
                            last_signal["signal"] == OrderType.BUY
                            and exit_instrument == Instrument.BULL
                        ),
                        (
                            last_signal["signal"] == OrderType.SELL
                            and exit_instrument == Instrument.BEAR
                        ),
                        not any([o.on_balance for o in helper.orders.values()]),
                    ]
                ):
                    last_signal["signal"] = None

                for instrument in helper.orders:
                    helper.sell_order(row, instrument)

                continue

            if not signal and exit_instrument:
                helper.sell_order(
                    row,
                    exit_instrument,
                )

            elif signal:
                helper.sell_order(
                    row,
                    Instrument.from_signal(signal)[OrderType.SELL],
                )
                helper.buy_order(
                    row,
                    Instrument.from_signal(signal)[OrderType.BUY],
                )

            helper.check_orders_for_limits(row)

            signal = Helper.get_signal(strategy_logic, row)
            if signal:
                last_signal = {
                    "signal": signal.value,
                    "time": time_index.strftime("%H:%M"),
                }

            exit_instrument = helper.get_exit_instrument(row, strategy.data)

        return last_signal

    def _traverse_instruments(
        self, market_direction: Instrument, settings: dict
    ) -> list:
        instruments = []

        for instrument_type, instrument_id in settings["instruments"]["TRADING_POOL"][
            market_direction
        ]:
            instrument_info = self.ava.get_instrument_info(
                InstrumentType[instrument_type],
                str(instrument_id),
            )

            log_prefix = (
                f"Instrument {market_direction} ({instrument_type} - {instrument_id})"
            )

            if instrument_info["position"] or instrument_info["order"]:
                log.debug(f"{log_prefix} is in use")

                return [
                    {
                        "identifier": [instrument_type, instrument_id],
                        "numbers": {
                            "score": 0,
                        },
                    }
                ]

            elif market_direction != {
                "Lång": Instrument.BULL,
                "Kort": Instrument.BEAR,
            }.get(instrument_info["key_indicators"]["direction"]):
                log.debug(
                    f"{log_prefix} is in wrong category: {instrument_info['key_indicators']['direction']}"
                )

            elif (
                not instrument_info[OrderType.BUY]
                or instrument_info[OrderType.BUY] > 280
            ):
                log.debug(
                    f"{log_prefix} has bad price: {instrument_info[OrderType.BUY]}"
                )

            elif not instrument_info["spread"] or not (
                0.1 < instrument_info["spread"] < 0.9
            ):
                log.debug(f"{log_prefix} has bad spread: {instrument_info['spread']}")

            elif (
                not instrument_info["key_indicators"].get("leverage")
                or instrument_info["key_indicators"]["leverage"] < 18
            ):
                log.debug(
                    f"{log_prefix} has bad leverage: {instrument_info['key_indicators'].get('leverage')}"
                )

            else:
                instruments.append(
                    {
                        "identifier": [instrument_type, instrument_id],
                        "numbers": {
                            "spread": instrument_info["spread"],
                            "leverage": instrument_info["key_indicators"]["leverage"],
                            "score": round(
                                instrument_info["key_indicators"]["leverage"]
                                / instrument_info["spread"]
                            )
                            // 3,
                        },
                    }
                )

        return instruments

    def _update_trading_settings(self) -> None:
        settings = Settings().load("DT")

        instruments_info: dict = {}

        for market_direction in Instrument:
            instruments_info[market_direction] = []

            instruments_info[market_direction] = self._traverse_instruments(
                market_direction, settings
            )

            top_instruments = sorted(
                filter(
                    lambda x: x["numbers"]["score"]
                    == max(
                        [
                            i["numbers"]["score"]
                            for i in instruments_info[market_direction]
                        ]
                    ),
                    instruments_info[market_direction],
                ),
                key=lambda x: x["identifier"],
            )

            if top_instruments and (
                settings["instruments"]["TRADING"].get(market_direction)
                not in [i["identifier"] for i in top_instruments]
            ):
                log.info(
                    f'Change instrument {market_direction} -> {top_instruments[0]["identifier"]} ({top_instruments[0]["numbers"]})'
                )

                settings["instruments"]["TRADING"][market_direction] = top_instruments[
                    0
                ]["identifier"]

        Settings().dump(settings, "DT")

    def _count_indicators_usage(self, strategies: List[dict]) -> list:
        used_indicators: str = " + ".join([i["strategy"] for i in strategies])

        conditions_counter = {}
        for category, indicators in self.conditions.items():
            conditions_counter.update(
                {
                    f"({category}) {i}": used_indicators.count(f"({category}) {i}")
                    for i in indicators
                }
            )

        return [
            f"{i[0]} - {i[1]}"
            for i in sorted(
                conditions_counter.items(), key=lambda x: x[1], reverse=True
            )
        ]

    def _extract_top_strategies(self, strategies: list) -> list:
        top_strategies = []
        points = sorted({i["points"] for i in strategies}, reverse=True)

        for point in points:
            top_strategies += [
                i["strategy"]
                for i in filter(lambda s: s["points"] == point, strategies)
            ]

            if len(top_strategies) > 3:
                break

        return top_strategies

    def update(self) -> None:
        log.info("Updating strategies")

        profitable_strategies = sorted(
            self._walk_through_strategies(
                "30d", "1m", Cache.APPEND, filter_strategies=True, loaded_strategies=[]
            ),
            key=lambda s: (s["points"], s["profit"]),
            reverse=True,
        )

        indicators_counter = self._count_indicators_usage(profitable_strategies)

        Strategy.dump(
            "DT",
            {"30d": profitable_strategies, "indicators_stats": indicators_counter},
        )

    def test(self) -> list:
        log.info("Testing strategies")

        stored_strategies = Strategy.load("DT")

        profitable_strategies = sorted(
            self._walk_through_strategies(
                "15d",
                "1m",
                Cache.APPEND,
                filter_strategies=True,
                loaded_strategies=[
                    i["strategy"] for i in stored_strategies.get("30d", [])
                ],
            ),
            key=lambda s: (s["points"], s["profit"]),
            reverse=True,
        )

        top_strategies = self._extract_top_strategies(profitable_strategies)

        Strategy.dump(
            "DT",
            {
                **stored_strategies,
                **{"15d": profitable_strategies},
                **{"use": top_strategies},
            },
        )

        return top_strategies

    def adjust(self) -> None:
        log.info("Adjusting strategies")

        self._update_trading_settings()

        stored_strategies = Strategy.load("DT")

        profitable_strategies = sorted(
            self._walk_through_strategies(
                "1d",
                "1m",
                Cache.SKIP,
                filter_strategies=False,
                loaded_strategies=stored_strategies.get("use", []),
                limit_history_hours=4,
            ),
            key=lambda s: s["profit"],
            reverse=True,
        )

        Strategy.dump(
            "DT",
            {
                **stored_strategies,
                **{"use": [s["strategy"] for s in profitable_strategies]},
            },
        )


def run(
    update: bool = True, adjust: bool = True, print_orders_history: bool = False
) -> None:
    trading_time = TradingTime()
    calibration = Calibration(print_orders_history)

    # day run
    while True:
        if not adjust:
            break

        try:
            trading_time.update_day_time()

            if trading_time.day_time == DayTime.MORNING:
                pass

            elif trading_time.day_time == DayTime.DAY:
                calibration.adjust()

            elif trading_time.day_time == DayTime.EVENING:
                break

            time.sleep(60 * 5)

        except Exception as e:
            log.error(f">>> {e}: {traceback.format_exc()}")

    # full calibration
    try:
        if update:
            calibration.update()

        strategy_use = calibration.test()

        TeleLog(
            message="DT calibration:\n"
            + "\n".join(["\n> " + "\n> ".join(s.split(" + ")) for s in strategy_use])
        )

    except Exception as e:
        log.error(f">>> {e}: {traceback.format_exc()}")

        TeleLog(crash_report=f"DT_Calibration: script has crashed: {e}")

    return
