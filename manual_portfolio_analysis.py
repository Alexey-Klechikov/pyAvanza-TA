"""
This module is used for manual runs (checkups, improvements, tests)
"""


from pprint import pprint
from module.utils import Context, Strategy, Plot, Settings
import pandas as pd


class Portfolio_Analysis:
    def __init__(self, **kwargs):
        self.total_df = None
        self.visited_tickers = list()
        self.counter_per_strategy = {'-- MAX --': {'result': 0, 'transactions_counter': 0}}

        self.plot_extra_tickers_list = kwargs['plot_extra_tickers_list']
        self.plot_portfolio_tickers_bool = kwargs['plot_portfolio_tickers_bool']
        self.print_transactions_bool = kwargs['print_transactions_bool']

        self.show_only_tickers_to_act_on_bool = kwargs['show_only_tickers_to_act_on_bool']
        self.plot_tickers_to_act_on_bool = kwargs['plot_tickers_to_act_on_bool']

        self.run_analysis(kwargs['check_only_watchlist_bool'], kwargs['cache'])
        self.print_performance_per_strategy()
        self.plot_performance_compared_to_hold(kwargs['plot_total_algo_performance_vs_hold_bool'])

    def plot_ticker(self, strategy_obj):
        plot_obj = Plot(
            data_df=strategy_obj.history_df, 
            title=f'{strategy_obj.ticker_obj.info["symbol"]} ({strategy_obj.ticker_obj.info["shortName"]}) - {strategy_obj.summary["max_output"]["strategy"]}')
        plot_obj.create_extra_panels()
        plot_obj.show_single_ticker()

    def plot_performance_compared_to_hold(self, plot_total_algo_performance_vs_hold_bool):
        if not plot_total_algo_performance_vs_hold_bool:
            return
        
        columns_dict = {
            'Close': list(),
            'total': list()}
        for col in self.total_df.columns:
            for column_to_merge in columns_dict:
                if col.startswith(column_to_merge):
                    columns_dict[column_to_merge].append(col)
        
        for result_column, columns_to_merge_list in columns_dict.items():
            self.total_df[result_column] = self.total_df[columns_to_merge_list].sum(axis=1)
        
        plot_obj = Plot(
            data_df=self.total_df, 
            title=f'Total HOLD (red) vs Total algo (black)')
        plot_obj.show_entire_portfolio()
    
    def print_performance_per_strategy(self):
        result_dict = self.counter_per_strategy.pop('-- MAX --')
        result_message = [f'-- MAX -- : {str(result_dict)}']
        sorted_strategies = sorted(self.counter_per_strategy.items(), key=lambda x: int(x[1]["total_sum"]), reverse=True)
        print('\n' + '\n'.join(result_message + [f'{i+1}. {strategy[0]}: {strategy[1]}' for i, strategy in enumerate(sorted_strategies)]))

    def record_ticker_performance(self, strategy_obj, ticker):
        self.total_df = strategy_obj.history_df if self.total_df is None else pd.merge(
            self.total_df, strategy_obj.history_df,
            how='outer',
            left_index=True, 
            right_index=True)
        self.total_df['Close'] = self.total_df['Close'] / (self.total_df['Close'].values[0] / 1000)
        self.total_df.rename(
            columns={
                'Close': f'Close / {ticker}',
                'total': f'total / {ticker} / {strategy_obj.summary["max_output"]["strategy"]}'}, 
            inplace=True)
        self.total_df = self.total_df[[i for i in self.total_df.columns if (i.startswith('Close') or i.startswith('total'))]]

    def get_strategy_on_ticker(self, ticker, comment, in_portfolio_bool, cache):
        if ticker in self.visited_tickers:
            return 
        self.visited_tickers.append(ticker)

        try:
            strategy_obj = Strategy(ticker, ticker_name=comment, cache=cache)
        except Exception as e: 
            print(f'\n--- (!) There was a problem with the ticker "{ticker}": {e} ---')
            return

        if self.show_only_tickers_to_act_on_bool and (
            (in_portfolio_bool and strategy_obj.summary['signal'] == 'buy') or 
            (not in_portfolio_bool and strategy_obj.summary['signal'] == 'sell')):
            return

        # Print the result for all strategies AND count per strategy performance
        top_signal = strategy_obj.summary["max_output"].pop("signal")
        signal = strategy_obj.summary["signal"]
        signal = top_signal if top_signal == signal else f"{top_signal} ->> {signal}"
        max_output_summary = f'signal: {signal} / ' + ' / '.join([f'{k}: {v}' for k, v in strategy_obj.summary["max_output"].items() if k in ("result", "transactions_counter")])
        print(f'\n--- {strategy_obj.summary["ticker_name"]} ({max_output_summary}) (HOLD: {strategy_obj.summary["hold_result"]}) ---\n')

        for parameter in ('result', 'transactions_counter'):
            self.counter_per_strategy['-- MAX --'][parameter] += strategy_obj.summary["max_output"][parameter]

        for i, strategy_item_list in enumerate(strategy_obj.summary["sorted_strategies_list"]):
            strategy, strategy_data_dict = strategy_item_list[0], strategy_item_list[1]
            
            self.counter_per_strategy.setdefault(strategy, {'total_sum': 0, 'win_counter': dict(), 'transactions_counter': 0})
            self.counter_per_strategy[strategy]['total_sum'] += strategy_data_dict["result"]
            self.counter_per_strategy[strategy]['transactions_counter'] += len(strategy_data_dict["transactions"])
            
            if i < 3: 
                print(f'Strategy: {strategy} -> {strategy_data_dict["result"]} (number_transactions: {len(strategy_data_dict["transactions"])}) (signal: {strategy_data_dict["signal"]})')
                [print(f'> {t}') for t in strategy_data_dict["transactions"] if self.print_transactions_bool]
                
                self.counter_per_strategy[strategy]['win_counter'].setdefault(f'{i+1}', 0)
                self.counter_per_strategy[strategy]['win_counter'][f'{i+1}'] += 1

        # Plot
        plot_conditions_list = [
            ticker in self.plot_extra_tickers_list,
            in_portfolio_bool and self.plot_portfolio_tickers_bool,
            (in_portfolio_bool and signal == 'sell') and self.plot_tickers_to_act_on_bool,
            (not in_portfolio_bool and signal == 'buy') and self.plot_tickers_to_act_on_bool]
        if any(plot_conditions_list):
            self.plot_ticker(strategy_obj)

        # Create a DF with all best strategies vs HOLD
        self.record_ticker_performance(strategy_obj, ticker)

    def run_analysis(self, check_only_watchlist_bool, cache):
        settings_obj = Settings()
        settings_json = settings_obj.load()  

        ava = Context(
            user=list(settings_json.keys())[0],
            accounts_dict=list(settings_json.values())[0]["1"]['accounts'])
        
        if check_only_watchlist_bool:
            # Watchlists
            for watchlist_name, tickers_list in ava.watchlists_dict.items():
                for ticker_dict in tickers_list:
                    self.get_strategy_on_ticker(
                        ticker_dict['ticker_yahoo'], 
                        f"Watchlist ({watchlist_name}): {ticker_dict['ticker_yahoo']}",
                        in_portfolio_bool=False,
                        cache=cache)
        else:
            # Portfolio
            if ava.portfolio_dict['positions']['df'] is not None:
                for _, row in ava.portfolio_dict['positions']['df'].iterrows():
                    self.get_strategy_on_ticker(
                        row["ticker_yahoo"], 
                        f"Stock: {row['name']} - {row['ticker_yahoo']}",
                        in_portfolio_bool=True,
                        cache=cache)

            # Budget lists
            for budget_rule_name, watchlist_dict in ava.budget_rules_dict.items():
                for ticker_dict in watchlist_dict['tickers']:
                    self.get_strategy_on_ticker(
                        ticker_dict['ticker_yahoo'], 
                        f"Budget ({budget_rule_name}K): {ticker_dict['ticker_yahoo']}",
                        in_portfolio_bool=False,
                        cache=cache)


if __name__ == '__main__':
    Portfolio_Analysis(
        check_only_watchlist_bool=False,
        show_only_tickers_to_act_on_bool=False,
        
        print_transactions_bool=False, 
        
        plot_extra_tickers_list=['ENQ.ST'], 
        plot_portfolio_tickers_bool=False,
        plot_total_algo_performance_vs_hold_bool=True,
        plot_tickers_to_act_on_bool=False,
                
        cache=True)
