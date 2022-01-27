from pprint import pprint
from context import Context
from strategy import Strategy
from log import Log
from plot import Plot
import pandas as pd

# TEST actions 2

class Manual:
    def __init__(self, print_transactions_bool, plot_tickers_list, check_only_tickers_in_watch_lists, show_only_tickers_to_act_on, show_total_algo_performance_vs_hold, plot_portfolio_tickers, cache):
        self.total_df = None
        self.visited_tickers = list()
        self.counter_per_strategy = {'-- MAX --': {'result': 0, 'transactions_counter': 0}}

        self.plot_tickers_list = plot_tickers_list
        self.plot_portfolio_tickers = plot_portfolio_tickers
        self.print_transactions_bool = print_transactions_bool
        self.show_only_tickers_to_act_on_bool = show_only_tickers_to_act_on

        self.run(check_only_tickers_in_watch_lists, cache)
        self.print_performance_per_strategy()
        self.plot_performance_compared_to_hold(show_total_algo_performance_vs_hold)

    def plot_ticker(self, strategy_obj):
        plot_obj = Plot(
            data_df=strategy_obj.history_df, 
            title=f'{strategy_obj.ticker_obj.info["symbol"]} ({strategy_obj.ticker_obj.info["shortName"]}) - {strategy_obj.summary["max_output"]["strategy"]}')
        plot_obj.create_extra_panels()
        plot_obj.show_single_ticker()

    def plot_performance_compared_to_hold(self, show_total_algo_performance_vs_hold):
        if not show_total_algo_performance_vs_hold:
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
        print('\n' + '\n'.join(result_message + [f'{strategy[0]}: {strategy[1]}' for strategy in sorted_strategies]))

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
            strategy_obj = Strategy(ticker, comment, cache)
        except Exception as e: 
            print(f'\n--- (!) There was a problem with the ticker "{ticker}": {e} ---')
            return

        if self.show_only_tickers_to_act_on_bool and (
            (in_portfolio_bool and strategy_obj.summary['max_output']['signal'] == 'buy') or 
            (not in_portfolio_bool and strategy_obj.summary['max_output']['signal'] == 'sell')):
            return

        # Print the result for all strategies AND count per strategy performance
        top_signal = strategy_obj.summary["max_output"].pop("signal")
        top_3_signal = strategy_obj.summary["top_3_signal"]
        signal = top_signal if top_signal == top_3_signal else f"{top_signal} ->> {top_3_signal}"
        max_output_summary = ' / '.join([f'signal: {signal}'] + [f'{k}: {v}' for k, v in strategy_obj.summary["max_output"].items()])
        print(f'\n--- {strategy_obj.summary["ticker_name"]} ({max_output_summary}) (HOLD: {strategy_obj.summary["hold_result"]}) ---')

        for parameter in ('result', 'transactions_counter'):
            self.counter_per_strategy['-- MAX --'][parameter] += strategy_obj.summary["max_output"][parameter]

        for strategy_item_list in strategy_obj.summary["sorted_strategies_list"]:
            strategy, strategy_data_dict = strategy_item_list[0], strategy_item_list[1]
            print(f'\nStrategy: {strategy} -> {strategy_data_dict["result"]} (number_transactions: {len(strategy_data_dict["transactions"])}) (signal: {strategy_data_dict["signal"]})')
            [print(f'> {t}') for t in strategy_data_dict["transactions"] if self.print_transactions_bool]
            
            self.counter_per_strategy.setdefault(strategy, {'total_sum': 0, 'win_counter': 0, 'transactions_counter': 0})
            self.counter_per_strategy[strategy]['total_sum'] += strategy_data_dict["result"]
            self.counter_per_strategy[strategy]['transactions_counter'] += len(strategy_data_dict["transactions"])
        self.counter_per_strategy[strategy_obj.summary["max_output"]['strategy']]['win_counter'] += 1

        # Create a DF with all best strategies vs HOLD
        self.record_ticker_performance(strategy_obj, ticker)

        # Plot
        if (ticker in self.plot_tickers_list) or (self.plot_portfolio_tickers and in_portfolio_bool):
            self.plot_ticker(strategy_obj)

    def run(self, check_only_new_tickers_bool, cache):
        ava_ctx = Context('bostad')
        
        in_portfolio_bool = False
        if check_only_new_tickers_bool:
            # Watch lists
            for watch_list_name, tickers_list in ava_ctx.watch_lists_dict.items():
                for ticker_dict in tickers_list:
                    self.get_strategy_on_ticker(
                        ticker_dict['ticker_yahoo'], 
                        f"{watch_list_name}: {ticker_dict['ticker_yahoo']}",
                        in_portfolio_bool,
                        cache)
        else:
            # Portfolio
            in_portfolio_bool = True
            if ava_ctx.portfolio_dict['positions']['df'] is not None:
                for _, row in ava_ctx.portfolio_dict['positions']['df'].iterrows():
                    self.get_strategy_on_ticker(
                        row["ticker_yahoo"], 
                        f"Stock: {row['name']} - {row['ticker_yahoo']}",
                        in_portfolio_bool,
                        cache)

            # Budget lists
            for budget_rule_name, tickers_list in ava_ctx.budget_rules_dict.items():
                for ticker_dict in tickers_list:
                    self.get_strategy_on_ticker(
                        ticker_dict['ticker_yahoo'], 
                        f"Budget {budget_rule_name}K: {ticker_dict['ticker_yahoo']}",
                        in_portfolio_bool,
                        cache)

class Auto:
    def __init__(self, account, signals_dict=dict()):
        self.signals_dict = signals_dict

        self.run(account)

    def get_signal_on_ticker(self, ticker):
        if ticker not in self.signals_dict:
            try:
                strategy_obj = Strategy(ticker)
            except Exception as e:
                print(f'(!) There was a problem with the ticker "{ticker}": {e}')
                return None 

            self.signals_dict[ticker] = {
                'signal': strategy_obj.summary["top_3_signal"],
                'return': strategy_obj.summary['max_output']['result']}
        return self.signals_dict[ticker]

    def run(self, account):
        ava_ctx = Context(account)
        removed_orders_dict = ava_ctx.remove_active_orders()

        orders_dict = {
            'buy': list(),
            'sell': list()}
        
        # Deleted orders
        for order_type, orders_list in removed_orders_dict.items():
            for order in orders_list:
                signal_dict = self.get_signal_on_ticker(order["ticker_yahoo"])
                if signal_dict is None or signal_dict['signal'] != order_type:
                    continue

                stock_price_dict = ava_ctx.get_stock_price(order['order_book_id'])
                orders_dict[order_type].append({
                    'account_id': order['account_id'],
                    'order_book_id': order['order_book_id'],
                    'name': order['name'],
                    'price': stock_price_dict[order_type],
                    'volume': order['volume'],
                    'budget': stock_price_dict[order_type] * order['volume'],
                    'ticker_yahoo': order["ticker_yahoo"],
                    'max_return': signal_dict['return']})

        # Portfolio
        portfolio_tickers_list = list()
        if ava_ctx.portfolio_dict['positions']['df'] is not None:
            for _, row in ava_ctx.portfolio_dict['positions']['df'].iterrows():
                portfolio_tickers_list.append(row["ticker_yahoo"])

                signal_dict = self.get_signal_on_ticker(row["ticker_yahoo"])
                if signal_dict is None or signal_dict['signal'] == 'buy':
                    continue

                orders_dict['sell'].append({
                    'account_id': row['accountId'], 
                    'order_book_id': row['orderbookId'], 
                    'volume': row['volume'], 
                    'price': row['lastPrice'],
                    'profit': row['profitPercent'],
                    'name': row['name'],
                    'ticker_yahoo': row["ticker_yahoo"],
                    'max_return': signal_dict['return']})

        # Budget lists
        for budget_rule_name, tickers_list in ava_ctx.budget_rules_dict.items():
            for ticker_dict in tickers_list:
                if ticker_dict['ticker_yahoo'] in portfolio_tickers_list: 
                    continue
                
                signal_dict = self.get_signal_on_ticker(ticker_dict['ticker_yahoo'])
                if signal_dict is None or signal_dict['signal'] == 'sell':
                    continue

                stock_price_dict = ava_ctx.get_stock_price(ticker_dict['order_book_id'])
                orders_dict['buy'].append({
                    'ticker_yahoo': ticker_dict['ticker_yahoo'],
                    'order_book_id': ticker_dict['order_book_id'], 
                    'budget': int(budget_rule_name) * 1000,
                    'price': stock_price_dict['buy'],
                    'volume': round(int(budget_rule_name) * 1000 / stock_price_dict['buy']),
                    'name': ticker_dict['name'],
                    'max_return': signal_dict['return']})
        
        # Create orders
        ava_ctx.create_orders(orders_dict)

        # Dump log to Telegram
        if account == 'bostad':
            log_obj = Log(orders_dict, ava_ctx.portfolio_dict)
            log_obj.dump_to_telegram()

if __name__ == '__main__':

    Manual(
        plot_tickers_list=[], 
        check_only_tickers_in_watch_lists=False,
        print_transactions_bool=False, 
        show_only_tickers_to_act_on=False,
        plot_portfolio_tickers=False,
        show_total_algo_performance_vs_hold=True,
        cache=True)

    '''
    walkthrough_obj = Auto('bostad')
    Auto('semester', signals_dict=walkthrough_obj.signals_dict)
    '''
