"""
This module is the "frontend" meant for every week use. It will: 
- analyse every stock to pick the best performing once and place them in one of budget lists.
- record top 10 performing strategies for every stock and record it to the file "strategies.json"
It will import other modules to run the analysis on the stocks -> move it to the watchlist -> dump log in Telegram.py
It will be run from Telegram or automatically as cron-job.
"""

import os, json

from .utils.context import Context
from .utils.strategy import Strategy
from .utils.settings import Settings
from .utils.log import Log


class Watchlists_Analysis:
    def __init__(self, **kwargs):
        self.ava = Context(kwargs['user'], kwargs['accounts_dict'])
        self.log_list = ['Watchlists analysis']
        self.top_strategies_per_ticket_dict = dict()

        self.run_analysis(kwargs['log_to_telegram'], kwargs['budget_list_threshold_dict'])

    def record_strategies(self, ticker, strategy_obj):
        for strategy_item_list in strategy_obj.summary["sorted_strategies_list"][:10]:
            self.top_strategies_per_ticket_dict.setdefault(ticker, list()).append(strategy_item_list[0])

    def move_ticker_to_suitable_budgetlist(self, initial_watchlist_name, ticker_dict, max_output, budget_list_threshold_dict):
        max_outputs_list = [int(i) for i in budget_list_threshold_dict if max_output > int(i)]
        target_watchlist_name = 'skip' if len(max_outputs_list) == 0 else budget_list_threshold_dict[str(max(max_outputs_list))]
        
        def _get_watchlist_id(watchlist_name):
            if watchlist_name in self.ava.watchlists_dict:
                return self.ava.watchlists_dict[watchlist_name]['watchlist_id']
            return self.ava.budget_rules_dict[watchlist_name]['watchlist_id']

        if target_watchlist_name != initial_watchlist_name:
            self.ava.ctx.add_to_watchlist(ticker_dict['order_book_id'], _get_watchlist_id(target_watchlist_name))
            self.ava.ctx.remove_from_watchlist(ticker_dict['order_book_id'], _get_watchlist_id(initial_watchlist_name))

            message = f'"{initial_watchlist_name}" -> "{target_watchlist_name}" ({ticker_dict["name"]}) [{max_output}]'
            print(f'>> {message}')
            self.log_list.append(message)

        return target_watchlist_name

    def run_analysis(self, log_to_telegram, budget_list_threshold_dict):
        watchlists_list = [
            ('budget rules', self.ava.budget_rules_dict),
            ('watchlists', self.ava.watchlists_dict)]

        for watchlist_type, watchlist_dict in watchlists_list:
            print(f'Walk through {watchlist_type}')
            for watchlist_name, watchlist_sub_dict in watchlist_dict.items():
                for ticker_dict in watchlist_sub_dict['tickers']:
                    
                    try:
                        strategy_obj = Strategy(ticker_dict['ticker_yahoo'])
                    except Exception as e:
                        print(f'(!) There was a problem with the ticker "{ticker_dict["ticker_yahoo"]}": {e}')
                        continue
                        
                    max_output = strategy_obj.summary['max_output']['result']
                    print(f'> {watchlist_name}: {ticker_dict["ticker_yahoo"]} -> {max_output}')

                    target_watchlist_name = self.move_ticker_to_suitable_budgetlist(
                        initial_watchlist_name=watchlist_name, 
                        ticker_dict=ticker_dict,
                        max_output=max_output,
                        budget_list_threshold_dict=budget_list_threshold_dict)

                    if target_watchlist_name != 'skip':
                        self.record_strategies(ticker_dict['ticker_yahoo'], strategy_obj)

        Strategy.dump(self.top_strategies_per_ticket_dict)

        # Dump log to Telegram
        if log_to_telegram:
            log_obj = Log(watchlists_analysis_log_list=self.log_list)
            log_obj.dump_to_telegram()


def run():    
    settings_obj = Settings()
    settings_json = settings_obj.load()  

    for user, settings_per_account_dict in settings_json.items():
        for settings_dict in settings_per_account_dict.values():
            if not 'budget_list_threshold_dict' in settings_dict:
                continue

            Watchlists_Analysis(
                user=user,
                accounts_dict=settings_dict["accounts"],
                log_to_telegram=settings_dict["log_to_telegram"],
                budget_list_threshold_dict=settings_dict['budget_list_threshold_dict'])