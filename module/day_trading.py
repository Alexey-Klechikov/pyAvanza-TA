import time
import logging
import traceback
import yfinance as yf
from datetime import datetime

from requests import ReadTimeout

from .utils import History
from .utils import Context
from .utils import TeleLog
from .utils import Settings
from .utils import Instrument
from .utils import Strategy_DT
from .utils import Status_DT as Status


log = logging.getLogger("main.day_trading")


class Helper:
    def __init__(self, user, account_ids_dict, settings_dict):
        self.settings_trade_dict = settings_dict["trade_dict"]

        self.end_of_day_bool = False
        self.account_ids_dict = account_ids_dict

        self.ava = Context(user, account_ids_dict, skip_lists=True)
        self.strategies_dict = Strategy_DT.load("DT")
        self.instruments_obj = Instrument(self.settings_trade_dict["multiplier"])

        self.overwrite_last_line = {"bool": True, "message_list": []}

    def _check_last_candle_buy(
        self, strategy_obj, row, strategies_dict, instrument_type
    ):
        def _get_ta_signal(row, ta_indicator):
            ta_signal = None

            if strategy_obj.ta_indicators_dict[ta_indicator]["buy"](row):
                ta_signal = "BULL"

            elif strategy_obj.ta_indicators_dict[ta_indicator]["sell"](row):
                ta_signal = "BEAR"

            return ta_signal

        def _get_cs_signal(row, patterns_list):
            cs_signal, cs_pattern = None, None

            for pattern in patterns_list:
                if row[pattern] > 0:
                    cs_signal = "BULL"
                elif row[pattern] < 0:
                    cs_signal = "BEAR"

                if cs_signal is not None:
                    cs_pattern = pattern
                    break

            return cs_signal, cs_pattern

        ta_indicator, cs_pattern = None, None
        for ta_indicator in strategies_dict:
            ta_signal = _get_ta_signal(row, ta_indicator)
            if ta_signal is None:
                continue

            cs_signal, cs_pattern = _get_cs_signal(
                row,
                strategies_dict.get(ta_indicator, list()),
            )
            if cs_signal is None:
                continue

            if cs_signal == ta_signal == instrument_type:
                log.warning(
                    f">>> signal - BUY: {instrument_type}-{ta_indicator}-{cs_pattern} at {row.name}"
                )
                return True

        return False

    def get_signal(self, strategies_dict, instrument_type):
        history_obj = History(
            self.instruments_obj.ids_dict["MONITORING"]["YAHOO"],
            "2d",
            "1m",
            cache="skip",
        )

        strategy_obj = Strategy_DT(
            history_obj.history_df,
            order_price_limits_dict=self.settings_trade_dict["limits_dict"],
        )

        strategies_dict = (
            strategies_dict if strategies_dict else strategy_obj.load("DT")
        )

        last_full_candle_index = -2

        if (datetime.now() - strategy_obj.history_df.iloc[last_full_candle_index].name.replace(tzinfo=None)).seconds > 130:  # type: ignore
            return

        last_candle_signal_buy_bool = self._check_last_candle_buy(
            strategy_obj,
            strategy_obj.history_df.iloc[last_full_candle_index],
            strategies_dict[instrument_type],
            instrument_type,
        )

        return last_candle_signal_buy_bool

    def check_instrument_status(self, instrument_type):
        instrument_id = str(self.instruments_obj.ids_dict["TRADING"][instrument_type])

        instrument_status_dict = {
            "has_position_bool": False,
            "active_order_dict": dict(),
        }

        # Check if instrument has a position
        certificate_info_dict = self.ava.get_certificate_info(
            self.instruments_obj.ids_dict["TRADING"][instrument_type]
        )

        for position_dict in certificate_info_dict["positions"]:
            instrument_status_dict.update(
                {
                    "has_position_bool": True,
                    "current_price": certificate_info_dict["sell"],
                    "stop_loss_price": round(
                        position_dict["averageAcquiredPrice"]
                        * self.settings_trade_dict["limits_dict"]["SL"],
                        2,
                    ),
                    "take_profit_price": round(
                        position_dict["averageAcquiredPrice"]
                        * self.settings_trade_dict["limits_dict"]["TP"],
                        2,
                    ),
                    "trailing_stop_loss_price": round(
                        certificate_info_dict["sell"]
                        * self.settings_trade_dict["limits_dict"]["SL_trailing"],
                        2,
                    ),
                }
            )

        # Check if active order exists
        deals_and_orders_dict = self.ava.ctx.get_deals_and_orders()
        active_orders_list = (
            list() if not deals_and_orders_dict else deals_and_orders_dict["orders"]
        )

        for order_dict in active_orders_list:
            if (order_dict["orderbook"]["id"] != instrument_id) or (
                order_dict["rawStatus"] != "ACTIVE"
            ):
                continue

            instrument_status_dict["active_order_dict"] = order_dict

            if order_dict["type"] == "BUY":
                instrument_status_dict.update(
                    {
                        "stop_loss_price": round(
                            order_dict["price"]
                            * self.settings_trade_dict["limits_dict"]["SL"],
                            2,
                        ),
                        "take_profit_price": round(
                            order_dict["price"]
                            * self.settings_trade_dict["limits_dict"]["TP"],
                            2,
                        ),
                    }
                )

        return instrument_status_dict

    def place_order(self, signal, instrument_type, instrument_status_dict):
        if (signal == "buy" and instrument_status_dict["has_position_bool"]) or (
            signal == "sell" and not instrument_status_dict["has_position_bool"]
        ):
            return

        self.overwrite_last_line["bool"] = False

        certificate_info_dict = self.ava.get_certificate_info(
            self.instruments_obj.ids_dict["TRADING"][instrument_type]
        )

        order_data_dict = {
            "name": instrument_type,
            "signal": signal,
            "account_id": list(self.account_ids_dict.values())[0],
            "order_book_id": self.instruments_obj.ids_dict["TRADING"][instrument_type],
            "max_return": 0,
        }

        if certificate_info_dict[signal] is None:
            log.error(f"Certificate info could not be fetched")
            return

        if signal == "buy":
            order_data_dict.update(
                {
                    "price": certificate_info_dict[signal],
                    "volume": int(
                        self.settings_trade_dict["budget"]
                        // certificate_info_dict[signal]
                    ),
                    "budget": self.settings_trade_dict["budget"],
                }
            )

        elif signal == "sell":
            price = (
                certificate_info_dict[signal]
                if certificate_info_dict[signal]
                < instrument_status_dict["stop_loss_price"]
                else instrument_status_dict["take_profit_price"]
            )

            order_data_dict.update(
                {
                    "price": price,
                    "volume": certificate_info_dict["positions"][0]["volume"],
                    "profit": certificate_info_dict["positions"][0]["profitPercent"],
                }
            )

        self.ava.create_orders(
            [order_data_dict],
            signal,
        )

        log.info(
            f'{order_data_dict["signal"].upper()}: {order_data_dict["name"]} - {order_data_dict["price"]}'
        )

    def update_order(self, signal, instrument_type, instrument_status_dict, price):
        if price is None:
            return

        self.overwrite_last_line["bool"] = False

        instrument_type = instrument_status_dict["active_order_dict"]["orderbook"][
            "name"
        ].split(" ")[0]

        log.info(
            f'{signal.upper()} (UPD): {instrument_type} - {instrument_status_dict["active_order_dict"]["price"]} -> {price}'
        )

        self.ava.update_order(instrument_status_dict["active_order_dict"], price)

    def combine_stdout_line(self, instrument_type, status_obj):
        instrument_status_dict = status_obj.get_instrument(instrument_type)

        if instrument_status_dict["has_position_bool"]:
            self.overwrite_last_line["message_list"].append(
                f'{instrument_type} - {instrument_status_dict["stop_loss_price"]} < {instrument_status_dict["current_price"]} < {instrument_status_dict["take_profit_price"]}'
            )

    def update_last_stdout_line(self):
        if self.overwrite_last_line["bool"]:
            LINE_UP = "\033[1A"
            LINE_CLEAR = "\x1b[2K"

            print(LINE_UP, end=LINE_CLEAR)

        print(
            f'[{datetime.now().strftime("%H:%M")}] {" ||| ".join(self.overwrite_last_line["message_list"])}'
        )

        self.overwrite_last_line["bool"] = True


class Day_Trading:
    def __init__(self, user, account_ids_dict, settings_dict):
        self.settings_trade_dict = settings_dict["trade_dict"]

        self.helper_obj = Helper(user, account_ids_dict, settings_dict)
        self.balance_dict = {"before": 0, "after": 0}
        self.status_obj = Status(self.settings_trade_dict)

        while True:
            try:
                self.run_analysis(settings_dict["log_to_telegram"])

                break

            except ReadTimeout:
                self.helper_obj.ava.ctx = self.helper_obj.ava.get_ctx(user)

    def check_instrument_for_buy_action(self, strategies_dict, instrument_type):
        self.status_obj.update_instrument(
            instrument_type, self.helper_obj.check_instrument_status(instrument_type)
        )

        if self.status_obj.get_instrument(instrument_type)["has_position_bool"]:
            return

        # Create buy order (if there is no position)
        if not self.status_obj.get_instrument(instrument_type)["active_order_dict"]:
            buy_instrument_bool = self.helper_obj.get_signal(
                strategies_dict, instrument_type
            )
            if not buy_instrument_bool:
                return

            # Sell the other instrument if exists
            self.check_instrument_for_sell_action(
                "BEAR" if instrument_type == "BULL" else "BULL",
                enforce_sell_bool=True,
            )
            time.sleep(1)

            self.helper_obj.place_order(
                "buy", instrument_type, self.status_obj.get_instrument(instrument_type)
            )
            time.sleep(2)

        # Update buy order (if there is no position, but open order exists)
        else:
            current_buy_price = self.helper_obj.ava.get_certificate_info(
                self.helper_obj.instruments_obj.ids_dict["TRADING"][instrument_type]
            )["buy"]

            self.helper_obj.update_order(
                "buy",
                instrument_type,
                self.status_obj.get_instrument(instrument_type),
                current_buy_price,
            )
            time.sleep(2)

    def check_instrument_for_sell_action(
        self, instrument_type, enforce_sell_bool=False
    ):
        self.status_obj.update_instrument(
            instrument_type,
            self.helper_obj.check_instrument_status(instrument_type),
            self.settings_trade_dict["limits_dict"]["TP"],
        )

        if not self.status_obj.get_instrument(instrument_type)["has_position_bool"]:
            return

        # Create sell orders (take_profit)
        if not self.status_obj.get_instrument(instrument_type)["active_order_dict"]:
            self.helper_obj.place_order(
                "sell", instrument_type, self.status_obj.get_instrument(instrument_type)
            )

        # Update sell order (if hit stop_loss / enforced / trailing_stop_loss initiated, so take_profit_price has changed)
        else:
            sell_price = None

            current_sell_price = self.helper_obj.ava.get_certificate_info(
                self.helper_obj.instruments_obj.ids_dict["TRADING"][instrument_type]
            )["sell"]

            if (
                current_sell_price
                < self.status_obj.get_instrument(instrument_type)["stop_loss_price"]
            ) or enforce_sell_bool:
                sell_price = current_sell_price

            elif (
                self.status_obj.get_instrument(instrument_type)["active_order_dict"][
                    "price"
                ]
                != self.status_obj.get_instrument(instrument_type)["take_profit_price"]
            ):
                sell_price = self.status_obj.get_instrument(instrument_type)[
                    "take_profit_price"
                ]

            self.helper_obj.update_order(
                "sell",
                instrument_type,
                self.status_obj.get_instrument(instrument_type),
                sell_price,
            )

    # MAIN method
    def run_analysis(self, log_to_telegram):
        self.balance_dict["before"] = sum(
            self.helper_obj.ava.get_portfolio()["buying_power"].values()
        )

        log.info(
            f'> Running trading for account(s): {" & ".join(self.helper_obj.account_ids_dict)} [{self.balance_dict["before"]}]'
        )

        strategies_dict = dict()
        while True:
            self.status_obj.update_day_time()
            self.helper_obj.overwrite_last_line["message_list"] = []

            if self.status_obj.day_time == "morning":
                continue

            elif self.status_obj.day_time == "night":
                break

            # Walk through instruments
            for instrument_type in ["BULL", "BEAR"]:

                if self.status_obj.day_time != "evening":
                    self.check_instrument_for_buy_action(
                        strategies_dict, instrument_type
                    )

                self.check_instrument_for_sell_action(instrument_type)

                self.helper_obj.combine_stdout_line(instrument_type, self.status_obj)

            self.helper_obj.update_last_stdout_line()

            time.sleep(30)

        self.balance_dict["after"] = sum(
            self.helper_obj.ava.get_portfolio()["buying_power"].values()
        )

        log.info(f'> End of the day. [{self.balance_dict["after"]}]')

        if log_to_telegram:
            TeleLog(
                day_trading_stats_dict={
                    "balance_before": self.balance_dict["before"],
                    "balance_after": self.balance_dict["after"],
                    "budget": self.settings_trade_dict["budget"],
                }
            )


def run():
    settings_json = Settings().load()

    for user, settings_per_account_dict in settings_json.items():
        for settings_dict in settings_per_account_dict.values():
            if not settings_dict.get("run_day_trading", False):
                continue

            try:
                Day_Trading(user, settings_dict["accounts"], settings_dict)

            except Exception as e:
                log.error(f">>> {e}: {traceback.format_exc()}")

                TeleLog(crash_report=f"DT: script has crashed: {e}")

            return
