#THIS IS ONLY AN ATTEMPT, UNFINISHED

from AlgorithmImports import *
from datetime import timedelta
from itertools import combinations
import pandas as pd

class PairAnalysis:
    def __init__(self, asset_a_history, asset_b_history):
        self.asset_a_history = asset_a_history
        self.asset_b_history = asset_b_history

    def calculate_correlation(self):
        if len(self.asset_a_history) == 0 or len(self.asset_b_history) == 0:
            return 0

        corr_matrix = np.corrcoef(self.asset_a_history, self.asset_b_history)
        return corr_matrix[0, 1]

    def cointegration_test(self):
        _, p_value, _ = coint(self.asset_a_history, self.asset_b_history)
        self.Debug(p_value)
        return p_value < 0.05  # Using 5% significance level

class HipsterRedCrocodile(QCAlgorithm):
    #algorithm uses a pairs market making strategy with dynamic quote quantities and inventory rebalancing
    #to ensure that the market maker is keeping a neutral inventory so they're not
    #exposed to too much risk
    def Initialize(self):
        self.SetWarmUp(100)
        
        self.SetStartDate(2022, 6, 15)
        self.SetEndDate(2022, 7, 30)
        self.InitCash = 1000000
        self.SetCash(self.InitCash)
        # self.symbols = [self.AddCrypto(ticker, Resolution.Minute).Symbol for ticker in ["BTCUSD","ETHUSD"]]
        # self.btcusd = []
        # self.ethusd = []
        self.viable_pairs = self.identify_viable_pairs()
        self.is_trading_paused = False

        #MM parameters
        self.max_inventory_size = 10
        self.reduction_factor = 0.5
        self.lot_size = float(self.GetParameter("lot_size"))
        self.order_refresh_time = int(self.GetParameter("order_refresh_time"))
        self.stop_loss_percentage = int(self.GetParameter("stop_loss_percentage"))
       
        # For pairs trading
        self.lookback = 200  # number of periods to calculate mean and std
        self.z_threshold = 1.5  # Z-score threshold for trading signal
        self.pair_one_history = []
        self.pair_two_history = []
        self.bid_spread = {pair: float(self.GetParameter("bid_spread")) for pair in self.viable_pairs}
        self.ask_spread = {pair: float(self.GetParameter("ask_spread")) for pair in self.viable_pairs}
        self.ratio_history = {pair: deque(maxlen=self.lookback) for pair in self.viable_pairs}
        self.ratio_mean = {pair: 0 for pair in self.viable_pairs}
        self.ratio_std = {pair: 0 for pair in self.viable_pairs}

        #stats and stuff
        # self.last_filled_prices = {}  
        # self.last_buy_prices = {symbol: 0 for symbol in self.symbols}
        # self.last_sell_prices = {symbol: 0 for symbol in self.symbols}
        # self.daily_profit_loss = {symbol: 0 for symbol in self.symbols}
        # self.entry_prices = {symbol: 0 for symbol in self.symbols}

        #cancels orders n stuff
        self.Schedule.On(self.DateRules.EveryDay(), self.TimeRules.Every(TimeSpan.FromSeconds(self.order_refresh_time)), Action(self.cancel_all_orders))
        self.Schedule.On(self.DateRules.EveryDay(), self.TimeRules.At(0, 0), Action(self.reset_daily_values))

    def OnData(self, data):
        if self.is_trading_paused:
            return

        if self.Time.minute % 60 == 0:
            self.reset_spreads()

        self.bid_spread = max(min(self.bid_spread, 0.1), 0.00001)
        self.ask_spread = max(min(self.ask_spread, 0.1), 0.00001)

        for assetA, assetB in self.viable_pairs:
            # Update the price lists
            assetA_price = self.Securities[assetA].Price
            assetB_price = self.Securities[assetB].Price
            ratio = 0
            # Calculate the ratio of assetA to assetB
            if self.Time.minute % 15 == 0:
                ratio = assetA_price / assetB_price if assetB_price != 0 else 0
                self.ratio_history[(assetA, assetB)].append(ratio)

            ratio_mean = np.mean(self.ratio_history[(assetA, assetB)])
            ratio_std = np.std(self.ratio_history[(assetA, assetB)])
            self.ratio_mean[(assetA, assetB)] = ratio_mean
            self.ratio_std[(assetA, assetB)] = ratio_std

            # Initialize ratio_z to None
            ratio_z = None

            if self.Time.minute % 60 == 0:
                if ratio_std != 0:
                    ratio_z = (ratio - ratio_mean) / ratio_std

                    # Plot the Z-score, only if it's a valid and finite number
                    if np.isfinite(ratio_z):
                        self.Plot("Ratio Z-Score", "assetA/assetB", round(ratio_z, 1))
                    if np.isfinite(ratio):
                        self.Plot("assetA/assetB Analysis", "Raw Ratio", ratio)
                    if np.isfinite(ratio_mean):
                        self.Plot("assetA/assetB Analysis", "Mean Ratio", ratio_mean)
                    if np.isfinite(ratio_std):
                        self.Plot("assetA/assetB Analysis", "Std Dev", ratio_std)

            # Adjust spreads based on Z-score of the spread
            if ratio_z is not None:
                    if ratio_z > 0:  # assetA is being overbought or assetB is being oversold
                        self.ask_spread[(assetA, assetB)] *= 0.9  # Tighten ask spread for assetA
                        self.bid_spread[(assetA, assetB)] *= 0.9  # Tighten bid spread for assetA
                        self.bid_spread[(assetB, assetA)] *= 0.9  # Tighten bid spread for assetB
                        self.ask_spread[(assetB, assetA)] *= 1.1  # Widen ask spread for assetB (Capture Profit)
                    elif ratio_z < 0:  # assetA is being oversold or assetB is being overbought
                        self.ask_spread[(assetA, assetB)] *= 1.1  # Widen ask spread for assetA (Capture Profit)
                        self.bid_spread[(assetA, assetB)] *= 0.9  # Tighten bid spread for assetA
                        self.bid_spread[(assetB, assetA)] *= 0.9  # Tighten bid spread for assetB
                        self.ask_spread[(assetB, assetA)] *= 0.9  # Tighten ask spread for assetB

            #self.Debug(f'BTC: New Bid Spread: {self.pair_one_bidSpread}, New Ask Spread: {self.pair_one_askSpread}')  # Debug
            #self.Debug(f'ETH: New Bid Spread: {self.pair_two_bidSpread}, New Ask Spread: {self.pair_two_askSpread}')  # Debug

            # Create and place orders
            proposal = self.create_proposal(assetA,assetB,data)
            self.place_orders(proposal)

            # Additional logic
            self.check_and_rebalance_inventory()
            self.check_stop_loss()

    #main logic =============================================================
    
    def identify_viable_pairs(self):
        asset_list = ["BTCUSD", "ETHUSD"]  # Add more assets as needed
        pair_combinations = list(combinations(asset_list, 2))
        self.Debug(f"Pair combinations: {pair_combinations}")  # Debug statement
        pair = None
        correlation = None
        cointegration_test_result = None
        viable_pairs = []
        for a, b in pair_combinations:
            # Fetch 2 months of daily historical data for assets a and b
            history_a = pd.DataFrame([x for x in self.History(a, timedelta(days=60), Resolution.Daily)])
            history_b = pd.DataFrame([x for x in self.History(b, timedelta(days=60), Resolution.Daily)])
            
            self.Debug(f"Fetched history for {a} and {b}")  # Debug statement
            self.Debug(f"Length of history for {a}: {len(history_a)}")  # Debug statement
            self.Debug(f"Length of history for {b}: {len(history_b)}")  # Debug statement

            if not history_a.empty:
                history_a.set_index('time', inplace=True)
                history_a = history_a['close']
                
            if not history_b.empty:
                history_b.set_index('time', inplace=True)
                history_b = history_b['close']

            # Ensure that the data is aligned and there are no missing values
            aligned_a, aligned_b = history_a.align(history_b, join='inner')
            self.Debug(f"Size of aligned data: {len(aligned_a)}")  # Debug statement

            if len(aligned_a) > 10:  # Assuming 10 as the minimum size for meaningful calculations
                pair = PairAnalysis(aligned_a.values, aligned_b.values)
                cointegration_test_result = pair.cointegration_test()
                self.Debug(f"Cointegration test result for {a} and {b}: {cointegration_test_result}")  # Debug statement
            else:
                self.Debug(f"Skipping pair ({a}, {b}) due to insufficient aligned data.")
                continue

            if pair:
                correlation = pair.calculate_correlation()
                cointegration_test_result = pair.cointegration_test()

            self.Debug(f"Correlation between {a} and {b}: {correlation}")  # Debug statement
            self.Debug(f"Cointegration test result for {a} and {b}: {cointegration_test_result}")  # Debug statement

            if correlation > 0.9 and cointegration_test_result:
                viable_pairs.append((a, b))
                self.Debug(f"Added pair: ({a}, {b}) to viable_pairs")  # Debug statement

        self.Debug(f"Final list of viable pairs: {viable_pairs}")  # Debug statement
        return viable_pairs


    def create_proposal(self, a,b, data) -> list:
        proposal = []
        for symbol in [a,b]:
            bid_spread = self.bid_spread[(assetA, assetB)]
            ask_spread = self.ask_spread[(assetA, assetB)]

            ref_price = (self.Securities[symbol].BidPrice + self.Securities[symbol].AskPrice) / 2
            inventory_score = self.calculate_inventory_score(symbol) 

            buy_price = ref_price * (1 - bid_spread)
            sell_price = ref_price * (1 + ask_spread)
            proposal.append((symbol, buy_price, sell_price, inventory_score))
        return proposal


    def place_orders(self, proposal) -> None:
        for symbol, buy_price, sell_price, inventory_score in proposal:
            quantity = self.Portfolio[symbol].Quantity
            if abs(quantity) >= self.max_inventory_size:
                buy_price = buy_price * (1 - self.reduction_factor * self.bid_spread)
                sell_price = sell_price * (1 + self.reduction_factor * self.ask_spread)

            if inventory_score == 0:
                bid_quantity = self.lot_size
                ask_quantity = self.lot_size
            else:
                bid_quantity = self.lot_size * inventory_score  
                ask_quantity = self.lot_size / inventory_score 

            buy_price = round(buy_price, 2)
            sell_price = round(sell_price, 2)
            bid_quantity = round(bid_quantity, 5)
            ask_quantity = round(sell_price, 5)

            self.LimitOrder(symbol, bid_quantity, buy_price)  
            self.LimitOrder(symbol, -ask_quantity, sell_price) 

    #end main logic =============================================================
    #Start of Inventory & Risk Management -------------------------------

    def reset_spreads(self):
        self.bid_spread = {pair: float(self.GetParameter("bid_spread")) for pair in self.viable_pairs}
        self.ask_spread = {pair: float(self.GetParameter("ask_spread")) for pair in self.viable_pairs}

    def calculate_inventory_score(self, symbol) -> float:
        current_quantity = self.Portfolio[symbol].Quantity
        inventory_ratio = abs(current_quantity) / self.max_inventory_size

        # Ensuring the ratio stays within [0, 1]
        inventory_ratio = max(0, min(inventory_ratio, 1))

        # Inverse logic - if you have too much inventory, the score will be lower
        inventory_score = 1 - inventory_ratio
        #self.Debug(f'{symbol}: Current Quantity: {current_quantity}, Inventory Ratio: {inventory_ratio}, Inventory Score: {inventory_score}')  # Debug
        return inventory_score


    def check_and_rebalance_inventory(self):
        target_balance_ratio = 0.5  # 50% base, 50% quote
        tolerance = 0.05  # Allowable deviation from the target balance ratio

        for symbol in self.symbols:
            # Current quantity of the base asset
            base_quantity = self.Portfolio[symbol].Quantity
            # Current value of the quote asset, converted to base asset units
            quote_value_in_base = self.Portfolio.CashBook['USD'].Amount / (self.Securities[symbol].BidPrice + self.Securities[symbol].AskPrice) / 2

            # Total value of the portfolio in base asset units
            total_value_in_base = base_quantity + quote_value_in_base

            # Current balance ratio
            current_balance_ratio = base_quantity / total_value_in_base

            # Check if rebalancing is needed
            if abs(current_balance_ratio - target_balance_ratio) < tolerance:
                continue

            # Calculate the desired quantity of the base asset
            target_base_quantity = total_value_in_base * target_balance_ratio
            delta_quantity = target_base_quantity - base_quantity

            # Place the order to rebalance
            self.MarketOrder(symbol, delta_quantity)

    def cancel_all_orders(self):
        openOrders = self.Transactions.GetOpenOrders()
        if len(openOrders) > 0:
            for x in openOrders:
                self.Transactions.CancelOrder(x.Id)

    def check_stop_loss(self):
        # Get the starting cash for the day
        starting_cash_for_day = self.InitCash

        # Calculate the current portfolio value
        current_portfolio_value = self.Portfolio.TotalPortfolioValue

        # Calculate the daily loss percentage
        daily_loss_percentage = (starting_cash_for_day - current_portfolio_value) / starting_cash_for_day * 100

        # Check if the daily loss exceeds the threshold 
        if daily_loss_percentage > 2:
            # Cancel all existing orders
            self.cancel_all_orders()

            # Set the flag to pause trading
            self.is_trading_paused = True
    
    def reset_daily_values(self):
        # Reset the trading paused flag
        self.is_trading_paused = False

        # Reset the starting cash for the day to the current portfolio value
        self.InitCash = self.Portfolio.TotalPortfolioValue
    #end of Inventory & Risk Management -----------------------------------

    #end main logic =============================================================


    #profit tracking stuff
    # def OnOrderEvent(self, orderEvent):
    #     if orderEvent.Status == OrderStatus.Filled:
    #         symbol = orderEvent.Symbol
    #         fill_price = orderEvent.FillPrice
    #         fill_quantity = orderEvent.FillQuantity
    #         quantity = orderEvent.FillQuantity
    #         is_buy_order = quantity > 0
    #         is_sell_order = quantity < 0

    #         # Update the entry price based on the average cost
    #         current_quantity = self.Portfolio[symbol].Quantity
    #         if current_quantity != 0:
    #             self.entry_prices[symbol] = (self.entry_prices[symbol] * (current_quantity - fill_quantity) + fill_price * fill_quantity) / current_quantity
    #         else:
    #             self.entry_prices[symbol] = 0

    #         if is_sell_order and self.last_buy_prices[symbol] != 0:
    #             profit_loss = (fill_price - self.last_buy_prices[symbol]) * abs(quantity)
    #             self.daily_profit_loss[symbol] += profit_loss

    #         if is_buy_order:
    #             self.last_buy_prices[symbol] = fill_price
    #         elif is_sell_order:
    #             self.last_sell_prices[symbol] = fill_price
    
    # def OnEndOfDay(self):
    #     #Log the daily profit/loss for each symbol
    #     # for symbol, profit_loss in self.daily_profit_loss.items():
    #     #     if profit_loss > 0:
    #     #         self.Debug(f"Daily Profit for {symbol}: ${profit_loss}")
    #     #     elif profit_loss < 0:
    #     #         self.Debug(f"Daily Loss for {symbol}: -${abs(profit_loss)}")
    #     #     else:
    #     #         self.Debug(f"No Daily Profit/Loss for {symbol}")
    #     self.cancel_all_orders()

    #     # Reset daily profit/loss and last buy/sell prices for the next trading day
    #     self.last_buy_prices = {symbol: 0 for symbol in self.symbols}
    #     self.last_sell_prices = {symbol: 0 for symbol in self.symbols}
    #     self.daily_profit_loss = {symbol: 0 for symbol in self.symbols}

    #     # Plot buy and hold strategy for BTC
    #     btc_history = self.History(self.symbols[0], 2, Resolution.Minute)

    #     # Extract the close price for BTC
    #     btc_price = btc_history.loc[self.symbols[0]]['close'].iloc[-1]

    #     # Update BTC price list and calculate performance
    #     self.btcusd.append(btc_price)
    #     btc_perf = round(self.InitCash * self.btcusd[-1]/self.btcusd[0],2)

    #     # Plot the performance
    #     self.Plot('Strategy Equity', self.symbols[0], btc_perf)

    #     # Plot buy and hold strategy for ETH
    #     eth_history = self.History(self.symbols[1], 2, Resolution.Minute)

    #     # Extract the close price for ETH
    #     eth_price = eth_history.loc[self.symbols[1]]['close'].iloc[-1]

    #     # Update ETH price list and calculate performance
    #     self.ethusd.append(eth_price)
    #     eth_perf = round(self.InitCash * self.ethusd[-1]/self.ethusd[0],2)

    #     # Plot the performance
    #     self.Plot('Strategy Equity', self.symbols[1], eth_perf)



