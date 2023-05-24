import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

import pandas as pd
from avanza import OrderType

from src.dt import Plot, Strategy
from src.dt.calibration.walker import Helper, Walker
from src.dt.common_types import Instrument
from src.dt.trading.signal import Signal
from src.utils import Cache, History, Settings

log = logging.getLogger("main.dt.calibration._testing")


TARGET_FOLDER = ""


class SignalMod(Signal):
    def get(  # type: ignore
        self, strategy_names: list, strategy: Strategy
    ) -> Tuple[Optional[OrderType], list]:
        if len(strategy_names) == 0:
            return None, ["No strategies"]

        self.candle = strategy.data.iloc[-1]

        signals: list = []
        for strategy_name in strategy_names:
            (
                strategy_last_signal,
                strategy_last_signal_time,
            ) = self._get_last_signal_on_strategy(strategy, strategy_name)

            if strategy_last_signal:
                signals.append(
                    {
                        "signal": strategy_last_signal,
                        "time": strategy_last_signal_time,
                        "strategy_name": strategy_name,
                    }
                )

        if len(signals) == 0:
            return None, ["No signals"]

        (
            current_signal,
            latest_signal_time,
            signals_summary,
        ) = self._extract_signal_from_list(signals)

        if (
            self.last_signal["signal"] == current_signal
            and self.last_signal["time"] >= latest_signal_time
        ):
            log.debug(
                f"Outdated signal: {current_signal.name} at {latest_signal_time.strftime('%H:%M')}"
            )

            return None, ["Outdated signal"]

        self.last_signal = {"signal": current_signal, "time": latest_signal_time}  # type: ignore

        return current_signal, [
            f"Signal: {current_signal.name}",
            f"Candle: {latest_signal_time.strftime('%H:%M')}",
            f"OMX: {round(self.candle['Close'], 2)}",
            f"ATR: {round(self.candle['ATR'], 2)}",
            f"Counts: {signals_summary}",
        ]


class Testing:
    def __init__(self, target_date):
        self.target_date = target_date

        self.stored_strategies = self._get_stored_strategies()
        self.full_history = self._get_full_history()

        self.walker = Walker(Settings().load("DT"))

    def _get_stored_strategies(self) -> Dict[str, list]:
        stored_strategies = {}
        for direction in ["BULL", "BEAR"]:
            stored_strategies[direction] = [
                i["strategy"]
                for i in Strategy.load("DT").get(f"{direction}_10d", [])
                if int(i["efficiency"][:-1]) >= 65
            ]

        return stored_strategies

    def _get_full_history(self) -> pd.DataFrame:
        target_days_limits = (
            datetime.strptime(self.target_date, "%Y-%m-%d").replace(
                hour=7, tzinfo=None
            ),
            datetime.strptime(self.target_date, "%Y-%m-%d").replace(
                hour=21, tzinfo=None
            ),
        )

        history_data = History(
            Settings().load("DT")["instruments"]["MONITORING"]["YAHOO"],
            "1d",
            "1m",
            cache=Cache.REUSE,
        ).data

        history_data.index = pd.to_datetime(history_data.index).tz_convert(None)
        history_data = history_data.loc[target_days_limits[0] : target_days_limits[1]]  # type: ignore

        for _ in range(3):
            if history_data.index[0].hour == 9:  # type: ignore
                break
            history_data.index = history_data.index + timedelta(hours=1)  # type: ignore

        return history_data

    def backtest_strategies(self, sliced_history: pd.DataFrame, direction: str) -> list:
        log.info(
            "Back-testing strategies "
            + f"({sliced_history.index[0].strftime('%H:%M')} : {sliced_history.index[-1].strftime('%H:%M')})"  # type: ignore
        )

        profitable_strategies = sorted(
            self.walker.traverse_strategies(
                custom_history=sliced_history,
                loaded_strategies=self.stored_strategies.get(direction, []),
                filter_strategies=False,
                history_cutoff={"hours": 2, "minutes": 30},
            ),
            key=lambda s: s["profit"],
            reverse=True,
        )

        max_profit = max([s["profit"] for s in profitable_strategies])
        profitable_strategies = [
            s["strategy"]
            for s in profitable_strategies
            if s["profit"] >= max_profit * 0.8
        ]

        return profitable_strategies


def run(target_dates) -> None:
    for target_date in target_dates:
        log.warn(f"Target date: {target_date}")

        testing = Testing(target_date)
        signal_obj = SignalMod(testing.walker.ava, Settings().load("DT"))
        helper = Helper("TESTING")

        signal = None
        message: list = []
        exit_instrument = None
        last_direction = direction = Instrument.BULL

        strategies = []
        signals: dict = {"BUY": [], "SELL": [], "EXIT": []}

        for time_index in testing.full_history.index:
            # Before the day
            if time_index < time_index.replace(hour=12):
                continue

            # Calibration
            if time_index.minute % 6 == 0 or last_direction != direction:
                strategies = testing.backtest_strategies(
                    testing.full_history.loc[:time_index], direction
                )

            last_direction = direction

            # Only act on even minutes
            if time_index.minute % 2 != 0:
                continue

            # Act on the last minute signal
            strategy = Strategy(
                testing.full_history.loc[:time_index],  # type: ignore
                strategies=strategies,
            )

            row = strategy.data.loc[time_index]
            if not signal and exit_instrument:
                helper.sell_order(
                    row,
                    exit_instrument,
                )

                signals["EXIT"].append(time_index)

            elif signal:
                log.warn(" | ".join(message))

                signals[signal.name].append(time_index)

                helper.sell_order(
                    row,
                    Instrument.from_signal(signal)[OrderType.SELL],
                )
                helper.buy_order(
                    row,
                    Instrument.from_signal(signal)[OrderType.BUY],
                )

            helper.check_orders_for_limits(row)

            # Get next action
            direction = (
                Instrument.BULL
                if (row.Close + row.Open) / 2 > row.MA
                else Instrument.BEAR
            )

            signal, message = signal_obj.get(strategies, strategy)

            exit_instrument = helper.get_exit_instrument(row, strategy.data)

            # "End of day" or "No strategies"
            end_of_day = time_index.hour == 17 and time_index.minute >= 15
            if end_of_day or not strategies:
                for instrument in helper.orders:
                    helper.sell_order(row, instrument)
                    signals["EXIT"].append(time_index)

            if end_of_day:
                break

        # Plot
        plot = Plot(strategy.data[["Close", "Open", "High", "Low", "MA"]])  # type: ignore

        path = os.path.dirname(os.path.abspath(__file__))
        for _ in range(2):
            path = os.path.dirname(path)

        plot.add_signals_to_figure(signals=signals)
        plot.add_balance_to_figure(helper.orders_history)
        plot.add_moving_average_to_figure()
        plot.save_figure(
            f"{path}/logs/"
            + (f"{TARGET_FOLDER}/" if TARGET_FOLDER else "")
            + f"manual_day_trading_{target_date}.png"
        )
