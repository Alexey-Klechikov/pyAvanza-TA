import logging
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Tuple, TypedDict, Union

import numpy as np
import pandas as pd
import yfinance as yf
from requests import ReadTimeout
from requests.exceptions import ConnectionError

from .utils import Context, History, InstrumentStatus, Settings
from .utils import Status_DT as Status
from .utils import Strategy_DT, TeleLog

log = logging.getLogger("main.day_trading")


@dataclass
class LastLine:
    overwrite: bool = True
    messages: list = field(default_factory=list)


class Helper:
    def __init__(self, user, accounts: dict, settings: dict):
        self.settings = settings

        self.trading_done = False
        self.accounts = accounts

        self.strategies = Strategy_DT.load("DT")
        self.status = Status(self.settings["trading"])

        self.last_line: LastLine = LastLine()

        self.ava = Context(user, accounts, skip_lists=True)

        self._update_budget()

        self.log_data = {
            "balance_before": 0,
            "balance_after": 0,
            "number_errors": 0,
            "number_trades": 0,
            "budget": self.settings["trading"]["budget"],
        }

    def _check_last_candle_buy(
        self,
        strategy: Strategy_DT,
        row: pd.Series,
        strategies: dict,
        instrument_type: str,
    ) -> bool:
        def _get_ta_signal(row: pd.Series, ta_indicator: str) -> Optional[str]:
            ta_signal = None

            if strategy.ta_indicators[ta_indicator]["buy"](row):
                ta_signal = "BULL"

            elif strategy.ta_indicators[ta_indicator]["sell"](row):
                ta_signal = "BEAR"

            return ta_signal

        def _get_cs_signal(
            row: pd.Series, patterns: list
        ) -> Tuple[Optional[str], Optional[str]]:
            cs_signal, cs_pattern = None, None

            for pattern in patterns:
                if row[pattern] > 0:
                    cs_signal = "BULL"
                elif row[pattern] < 0:
                    cs_signal = "BEAR"

                if cs_signal is not None:
                    cs_pattern = pattern
                    break

            return cs_signal, cs_pattern

        ta_indicator, cs_pattern = None, None
        for ta_indicator in strategies:
            ta_signal = _get_ta_signal(row, ta_indicator)
            if ta_signal is None:
                continue

            cs_signal, cs_pattern = _get_cs_signal(
                row,
                strategies.get(ta_indicator, list()),
            )
            if cs_signal is None:
                continue

            if cs_signal == ta_signal == instrument_type:
                log.warning(
                    f">>> {instrument_type} - {ta_indicator}-{cs_pattern} at {str(row.name)[:-9]}"
                )
                return True

        return False

    def _update_budget(self) -> None:
        own_capital = self.ava.get_portfolio().total_own_capital
        floating_budget = (own_capital // 1000 - 1) * 1000

        self.settings["trading"]["budget"] = max(
            floating_budget, self.settings["trading"]["budget"]
        )

    def get_signal(self, strategies: dict, instrument_type: str) -> bool:
        history = self.ava.get_today_history(
            self.settings["instruments"]["MONITORING"]["AVA"]
        )

        if self.status.day_time == "morning_transition":
            history = History(
                self.settings["instruments"]["MONITORING"]["YAHOO"],
                "2d",
                "1m",
                cache="skip",
            ).data[["Open", "High", "Low", "Close"]]

        strategy = Strategy_DT(
            history,
            order_price_limits=self.settings["trading"]["limits_percent"],
        )

        strategies = strategies if strategies else strategy.load("DT")

        last_full_candle_index = -2

        if (datetime.now() - strategy.data.iloc[last_full_candle_index].name.replace(tzinfo=None)).seconds > 122:  # type: ignore
            return False

        last_candle_signal_buy = self._check_last_candle_buy(
            strategy,
            strategy.data.iloc[last_full_candle_index],
            strategies[instrument_type],
            instrument_type,
        )

        return last_candle_signal_buy

    def update_instrument_status(self, instrument_type: str) -> InstrumentStatus:
        instrument_id = str(self.settings["instruments"]["TRADING"][instrument_type])

        certificate_info = self.ava.get_certificate_info(instrument_id)

        active_order = self.ava.get_active_order(instrument_id)

        instrument_status = self.status.update_instrument(
            instrument_type, certificate_info, active_order
        )

        return instrument_status

    def place_order(
        self, signal: str, instrument_type: str, instrument_status: InstrumentStatus
    ) -> None:
        if (signal == "buy" and instrument_status.has_position) or (
            signal == "sell" and not instrument_status.has_position
        ):
            return

        if (
            instrument_status.spread is None or instrument_status.spread > 0.65
        ) and self.status != "evening":
            log.error(
                f"{instrument_type} - (place_order) HIGH SPREAD: {instrument_status.spread}"
            )

            self.log_data["number_errors"] += 1

            return

        self.last_line.overwrite = False

        certificate_info = self.ava.get_certificate_info(
            self.settings["instruments"]["TRADING"][instrument_type],
        )

        order_data = {
            "name": instrument_type,
            "signal": signal,
            "account_id": list(self.accounts.values())[0],
            "order_book_id": self.settings["instruments"]["TRADING"][instrument_type],
            "max_return": 0,
        }

        if certificate_info[signal] is None:
            return

        if signal == "buy":
            self.log_data["number_trades"] += 1

            order_data.update(
                {
                    "price": certificate_info[signal],
                    "volume": int(
                        self.settings["trading"]["budget"] // certificate_info[signal]
                    ),
                    "budget": self.settings["trading"]["budget"],
                }
            )

        elif signal == "sell":
            price = (
                certificate_info[signal]
                if certificate_info[signal] < instrument_status.price_stop_loss
                else instrument_status.price_take_profit_super
            )

            if len(certificate_info["positions"]) == 0:
                return

            order_data.update(
                {
                    "price": price,
                    "volume": certificate_info["positions"][0]["volume"],
                    "profit": certificate_info["positions"][0]["profitPercent"],
                }
            )

        self.ava.create_orders(
            [order_data],
            signal,
        )

        log.info(
            f'{instrument_type} - (SET {signal.upper()} order): {order_data["price"]}'
        )

    def update_order(
        self,
        signal: str,
        instrument_type: str,
        instrument_status: InstrumentStatus,
        price: Optional[float],
    ) -> None:

        if instrument_status.spread is None or instrument_status.spread > 0.6:
            log.error(
                f"{instrument_type} - (update_order) HIGH SPREAD: {instrument_status.spread}"
            )

            self.log_data["number_errors"] += 1

            return

        elif price is None:
            return

        self.last_line.overwrite = False

        instrument_type = instrument_status.active_order["orderbook"]["name"].split(
            " "
        )[0]

        log.info(
            f'{instrument_type} - (UPD {signal.upper()} order): {instrument_status.active_order["price"]} -> {price}'
        )

        self.ava.update_order(instrument_status.active_order, price)

    def combine_stdout_line(self, instrument_type: str) -> None:
        instrument_status = getattr(self.status, instrument_type)

        if instrument_status.has_position:
            self.last_line.messages.append(
                f"{instrument_type} - {instrument_status.price_stop_loss} < {instrument_status.price_current} < {instrument_status.price_take_profit}"
            )

    def update_last_stdout_line(self) -> None:
        if self.last_line.overwrite:
            LINE_UP = "\033[1A"
            LINE_CLEAR = "\x1b[2K"

            print(LINE_UP, end=LINE_CLEAR)

        print(
            f'[{datetime.now().strftime("%H:%M")}] {" ||| ".join(self.last_line.messages)}'
        )

        self.last_line.overwrite = True


class Day_Trading:
    def __init__(self, user: str, accounts: dict, settings: dict):
        self.settings = settings

        self.helper = Helper(user, accounts, settings)

        while True:
            try:
                self.run_analysis(settings["log_to_telegram"])

                break

            except (ReadTimeout, ConnectionError):
                log.error("AVA Connection error, retrying in 5 seconds")
                self.helper.log_data["number_errors"] += 1

                self.helper.ava.ctx = self.helper.ava.get_ctx(user)

    def check_instrument_for_buy_action(
        self, strategies: dict, instrument_type: str
    ) -> None:
        main_instrument_type = instrument_type
        main_instrument_status = self.helper.update_instrument_status(
            main_instrument_type
        )
        main_instrument_price_buy = self.helper.ava.get_certificate_info(
            self.settings["instruments"]["TRADING"][main_instrument_type]
        ).get("buy")
        main_instrument_signal_buy = self.helper.get_signal(
            strategies, main_instrument_type
        )

        if not main_instrument_signal_buy:
            return

        other_instrument_type = "BEAR" if main_instrument_type == "BULL" else "BULL"
        other_instrument_status = self.helper.update_instrument_status(
            other_instrument_type
        )

        # action for other instrument
        if other_instrument_status.has_position:
            other_instrument_price_sell = self.helper.ava.get_certificate_info(
                self.settings["instruments"]["TRADING"][other_instrument_type]
            ).get("sell")

            self.helper.update_order(
                "sell",
                other_instrument_type,
                other_instrument_status,
                other_instrument_price_sell,
            )
            time.sleep(1)

            other_instrument_status = self.helper.update_instrument_status(
                other_instrument_type
            )

            if other_instrument_status.has_position:
                return

        # action for main instrument
        if main_instrument_status.has_position:
            pass

            """ TODO: I currently don't test for this scenario, so let's pass on it for now
            self.helper.status.update_instrument_trading_limits(
                main_instrument_type, main_instrument_price_buy
            )
            """

        elif main_instrument_status.active_order:
            self.helper.update_order(
                "buy",
                main_instrument_type,
                main_instrument_status,
                main_instrument_price_buy,
            )

        else:
            self.helper.place_order("buy", main_instrument_type, main_instrument_status)

    def check_instrument_for_sell_action(
        self, instrument_type: str, enforce_sell_bool: bool = False
    ) -> None:
        instrument_status = self.helper.update_instrument_status(instrument_type)

        if not instrument_status.has_position:
            return

        # Create sell orders (take_profit)
        if not instrument_status.active_order:
            self.helper.place_order("sell", instrument_type, instrument_status)

        # Update sell order (if hit stop_loss / enforced / trailing_stop_loss initiated, so price_take_profit has changed)
        else:
            price_sell = None
            current_price_sell = self.helper.ava.get_certificate_info(
                self.settings["instruments"]["TRADING"][instrument_type]
            )["sell"]

            if any(
                [
                    current_price_sell <= instrument_status.price_stop_loss,
                    current_price_sell >= instrument_status.price_take_profit,
                    enforce_sell_bool,
                ]
            ):
                price_sell = current_price_sell

            self.helper.update_order(
                "sell",
                instrument_type,
                instrument_status,
                price_sell,
            )

    # MAIN method
    def run_analysis(self, log_to_telegram: bool) -> None:
        self.helper.log_data["balance_before"] = sum(
            self.helper.ava.get_portfolio().buying_power.values()
        )

        log.info(
            f'> Running trading for account(s): {" & ".join(self.helper.accounts)} [{self.helper.log_data["balance_before"]}]'
        )

        strategies: dict = dict()

        while True:
            self.helper.status.update_day_time()
            self.helper.last_line.messages = list()

            if self.helper.status.day_time == "morning":
                continue

            elif self.helper.status.day_time == "evening_transition":
                time.sleep(60)

                continue

            elif self.helper.status.day_time == "night":
                break

            # Walk through instruments
            for instrument_type in ["BULL", "BEAR"]:

                if self.helper.status.day_time != "evening":
                    self.check_instrument_for_buy_action(strategies, instrument_type)

                self.check_instrument_for_sell_action(instrument_type)

                self.helper.combine_stdout_line(instrument_type)

            self.helper.update_last_stdout_line()

            time.sleep(60)

        self.helper.log_data["balance_after"] = sum(
            self.helper.ava.get_portfolio().buying_power.values()
        )

        log.info(f'> End of the day. [{self.helper.log_data["balance_after"]}]')

        if log_to_telegram:
            TeleLog(day_trading_stats=self.helper.log_data)


def run() -> None:
    settings = Settings().load()

    for user, settings_per_user in settings.items():
        for setting_per_setup in settings_per_user.values():
            if not setting_per_setup.get("run_day_trading", False):
                continue

            try:
                Day_Trading(user, setting_per_setup["accounts"], setting_per_setup)

            except Exception as e:
                log.error(f">>> {e}: {traceback.format_exc()}")

                TeleLog(crash_report=f"DT: script has crashed: {e}")

            return
