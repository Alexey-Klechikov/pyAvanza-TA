"""
This module is the "frontend" meant for every second month use. It will analyse every stock to pick the best performing once and place them in one of budget lists.
It will import other modules to run the analysis on the stocks -> move it to the watchlist -> dump log in Telegram.py
It will be run from Telegram or automatically as cron-job.
"""


from .utils.context import Context
from .utils.strategy import Strategy
from .utils.settings import Settings
from .utils.log import Log


class Watchlists_Analysis:
    def __init__(self, **kwargs):
        self.ava = Context(kwargs['user'], kwargs['accounts_dict'])
        self.log_list = ['Watchlists analysis']
        self.run(kwargs['log_to_telegram'], kwargs['budget_list_threshold_dict'])

    def get_max_output_on_ticker(self, ticker):
        try:
            strategy_obj = Strategy(ticker)
            return strategy_obj.summary['max_output']['result']
        except Exception as e:
            print(f'(!) There was a problem with the ticker "{ticker}": {e}')
            return None 

    def move_ticker_to_suitable_budgetlist(self, initial_watchlist_name, ticker_dict, max_output, budget_list_threshold_dict):
        max_outputs_list = [int(i) for i in budget_list_threshold_dict if max_output > int(i)]
        target_watchlist_name = 'skip' if len(max_outputs_list) == 0 else budget_list_threshold_dict[str(max(max_outputs_list))]     
        
        if target_watchlist_name == initial_watchlist_name:
            return 

        def _get_watchlist_id(watchlist_name):
            if watchlist_name in self.ava.watchlists_dict:
                return self.ava.watchlists_dict[watchlist_name]['watchlist_id']
            return self.ava.budget_rules_dict[watchlist_name]['watchlist_id']
        
        self.ava.ctx.add_to_watchlist(ticker_dict['order_book_id'], _get_watchlist_id(target_watchlist_name))
        self.ava.ctx.remove_from_watchlist(ticker_dict['order_book_id'], _get_watchlist_id(initial_watchlist_name))

        message = f'"{initial_watchlist_name}" -> "{target_watchlist_name}" ({ticker_dict["name"]}) [{max_output}]'
        print(f'>> {message}')
        self.log_list.append(message)

    def run(self, log_to_telegram, budget_list_threshold_dict):
        watchlists_list = [
            ('budget rules', self.ava.budget_rules_dict),
            ('watchlists', self.ava.watchlists_dict)]

        for watchlist_type, watchlist_dict in watchlists_list:
            print(f'Walk through {watchlist_type}')
            for watchlist_name, watchlist_sub_dict in watchlist_dict.items():
                for ticker_dict in watchlist_sub_dict['tickers']:
                    max_output = self.get_max_output_on_ticker(ticker_dict['ticker_yahoo'])
                    if max_output is None:
                        continue
                    print(f'> {watchlist_name}: {ticker_dict["ticker_yahoo"]} -> {max_output}')

                    self.move_ticker_to_suitable_budgetlist(
                        initial_watchlist_name=watchlist_name, 
                        ticker_dict=ticker_dict,
                        max_output=max_output,
                        budget_list_threshold_dict=budget_list_threshold_dict)
        
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