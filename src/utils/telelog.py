"""
This module is used to process and dump execution logs to Telegram
"""

import logging
from typing import List, Optional

import telegram_send
from avanza import OrderType

from src.utils.context import Portfolio
from src.utils.logger import count_errors

log = logging.getLogger("main.utils.telelog")


class TeleLog:
    def __init__(self, **kwargs):
        self.message = ""

        # Long trading
        if "portfolio" in kwargs:
            self.parse_portfolio(
                kwargs["portfolio"],
                kwargs.get("account_development"),
                kwargs.get("omx_development"),
            )

        if "orders" in kwargs:
            self.parse_orders(kwargs["orders"])

        if "completed_orders" in kwargs:
            self.parse_completed_orders(kwargs["completed_orders"])

        # Day trading
        if "day_trading_stats" in kwargs:
            self.parse_day_trading_stats(
                kwargs["day_trading_stats"], kwargs.get("instruments")
            )

        # General
        if "crash_report" in kwargs:
            self.message = kwargs["crash_report"]

        if "message" in kwargs:
            self.message = kwargs["message"]

        self.append_errors()

        self.dump_to_telegram()

    def parse_day_trading_stats(
        self, day_trading_stats: dict, instruments: Optional[str]
    ) -> None:
        log.debug("Parse day_trading_stats")

        profit = round(
            day_trading_stats["balance_after"] - day_trading_stats["balance_before"]
        )

        profit_percentage = round(100 * profit / day_trading_stats["budget"], 2)

        messages = [
            "DT:\n",
            f'> Total value: {round(day_trading_stats["balance_after"])}',
            f'> Budget: {day_trading_stats["budget"]}',
            f"> Profit: {profit_percentage}% ({profit} SEK)",
            f"> Instruments: {instruments}",
        ]

        self.message += "\n".join(messages)

    def append_errors(self) -> None:
        number_errors = count_errors()

        if number_errors == 0:
            return

        self.message += f"\n\nErrors: {number_errors}"

    def parse_portfolio(
        self,
        portfolio: Portfolio,
        account_development: Optional[float],
        omx_development: Optional[float],
    ) -> None:
        log.debug("Parse portfolio")

        self.message += f"LT:\n\n> Total value: {round(portfolio.total_own_capital)}\n"
        self.message += (
            f"> Profit: {account_development}%\n" if account_development else "\n"
        )
        self.message += f"> OMX: {omx_development}%\n\n" if omx_development else "\n"
        self.message += (
            "Total free funds:\n"
            + "\n".join(
                [
                    f"> {account}: {funds}"
                    for account, funds in portfolio.buying_power.items()
                ]
            )
            + "\n\n"
        )

    def parse_orders(self, orders: dict) -> None:
        log.debug("Parse orders")

        for order_type, orders_by_type in orders.items():
            if len(orders_by_type) == 0:
                continue

            self.message += f"{order_type.name} orders:\n\n"

            for order in orders_by_type:
                order_messages = []

                if order_type == OrderType.BUY:
                    order_messages = [
                        f"> Ticker: {order['name']} ({order['ticker_yahoo']})",
                        f">> Budget: {order['budget']} SEK",
                    ]

                elif order_type == OrderType.SELL:
                    order_messages = [
                        f"> Ticker: {order['name']} ({order['ticker_yahoo']})",
                        f">> Value: {round(float(order['price']) * int(order['volume']))} SEK",
                        f">> Profit: {order['profit']} %",
                    ]

                self.message += "\n".join(order_messages + ["\n"])

    def parse_completed_orders(self, completed_orders: List[dict]) -> None:
        log.debug("Parse completed_orders")

        orders = [
            " / ".join([f"{k}: {v}" for k, v in order.items()])
            for order in completed_orders
        ]

        self.message = "\n".join(orders)

    def dump_to_telegram(self) -> None:
        log.info("Dump log to Telegram")

        telegram_send.send(messages=[self.message])
