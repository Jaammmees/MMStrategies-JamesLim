from AlgorithmImports import *

#PROBLEM WITH THIS CODE, BTCFUTURE IS ALWAYS BELOW BTC SPOT PRICE, CANT PAIRS TRADE AND TAKE ADVANTAGE OF ARBITRAGE
#WILL NEED TO FIND OTHER PAIR

class HipsterRedCrocodile(QCAlgorithm):
    #algorithm uses a pairs market making strategy in regards to BTCFutures and BTC spot price with dynamic quote quantities and inventory rebalancing
    #to ensure that the market maker is keeping a neutral inventory so they're not
    #exposed to too much risk
    def Initialize(self):
        self.SetWarmUp(10)
        self.SetStartDate(2022, 6, 13)
        self.SetEndDate(2023, 3, 13)
        self.InitCash = 1000000
        self.SetCash(self.InitCash)
        #self.spyFuture = self.AddFuture(Futures.Indices.SP500EMini, Resolution.Minute).Symbol
        self.spyFuture = self.AddCryptoFuture("BTCUSD", market=Market.Binance).Symbol
        self.spy = self.AddCrypto("BTCUSD", Resolution.Minute).Symbol
        self.spyHistory = []
        self.symbols = [self.spyFuture,self.spy]
        self.is_trading_paused = False

        #MM parameters
        self.max_inventory_size = 10
        self.reduction_factor = 0.5
        self.bid_spread = float(self.GetParameter("bid_spread"))
        self.ask_spread = float(self.GetParameter("ask_spread"))
        self.lot_size = float(self.GetParameter("lot_size"))
        self.order_refresh_time = int(self.GetParameter("order_refresh_time"))
        self.stop_loss_percentage = int(self.GetParameter("stop_loss_percentage"))
       
        # For pairs trading
        self.lookback = 200  # number of periods to calculate mean and std
        self.z_threshold = 1.5  # Z-score threshold for trading signal
        self.bid_spread_spyFuture = self.bid_spread 
        self.ask_spread_spyFuture = self.ask_spread  
        self.bid_spread_spy = self.bid_spread 
        self.ask_spread_spy = self.ask_spread  
        self.ratio_history = []

        #stats and stuff
        self.last_filled_prices = {}  
        self.last_buy_prices = {symbol: 0 for symbol in self.symbols}
        self.last_sell_prices = {symbol: 0 for symbol in self.symbols}
        self.daily_profit_loss = {symbol: 0 for symbol in self.symbols}
        self.entry_prices = {symbol: 0 for symbol in self.symbols}

        #cancels orders n stuff
        self.Schedule.On(self.DateRules.EveryDay(), self.TimeRules.Every(TimeSpan.FromSeconds(self.order_refresh_time)), Action(self.cancel_all_orders))
        self.Schedule.On(self.DateRules.EveryDay(), self.TimeRules.At(0, 0), Action(self.reset_daily_values))

    def OnData(self, data):
        if self.is_trading_paused:
            return

        if self.Time.minute % 60 == 0:
            self.reset_spreads()

        self.bid_spread = max(min(self.bid_spread, 0.05), 0.00001)
        self.ask_spread = max(min(self.ask_spread, 0.05), 0.00001)

        # Update the price lists
        spyFuture_price = self.Securities[self.spyFuture].Price
        spy_price = self.Securities[self.spy].Price
        ratio = 0
        # Calculate the ratio of spyFuture to spy
        if self.Time.minute % 15 == 0:
            ratio = spyFuture_price / spy_price if spy_price != 0 else 0
            self.ratio_history.append(ratio)

        # Ensure the ratio history list doesn't grow indefinitely
        if len(self.ratio_history) > self.lookback:
            self.ratio_history.pop(0)

        # Calculate mean and std of ratio over lookback period
        ratio_mean = np.mean(self.ratio_history)
        ratio_std = np.std(self.ratio_history)

        # Initialise ratio_z to None
        ratio_z = None

        if self.Time.minute % 60 == 0:
            if ratio_std != 0:
                ratio_z = (ratio - ratio_mean) / ratio_std

                # Plot the Z-score, only if it's a valid and finite number
                if np.isfinite(ratio_z):
                    self.Plot("Ratio Z-Score", "spyFuture/spy", round(ratio_z, 1))
                if np.isfinite(ratio):
                    self.Plot("spyFuture/spy", "Raw Ratio", ratio)
                if np.isfinite(ratio_mean):
                    self.Plot("spyFuture/spy", "Mean Ratio", ratio_mean)
                if np.isfinite(ratio_std):
                    self.Plot("spyFuture/spy", "Std Dev", ratio_std)

                self.Plot("price differences", "btcFuture", spyFuture_price)
                self.Plot("price differences", "btcSpot", spy_price)

        # Adjust spreads based on Z-score of the spread
        if ratio_z is not None:
            if ratio_z > 0:  # BTC is being overbought or ETH is being oversold
                self.ask_spread_spyFuture *= 0.9  # Tighten ask spread for BTC
                self.bid_spread_spyFuture *= 0.9  # Tighten bid spread for BTC
                self.bid_spread_spy *= 0.9  # Tighten bid spread for ETH
                self.ask_spread_spy *= 1.1  # Widen ask spread for ETH (Capture Profit)
            elif ratio_z < 0:  # BTC is being oversold or ETH is being overbought
                self.ask_spread_spyFuture *= 1.1  # Widen ask spread for BTC (Capture Profit)
                self.bid_spread_spyFuture *= 0.9  # Tighten bid spread for BTC
                self.bid_spread_spy *= 0.9  # Tighten bid spread for ETH
                self.ask_spread_spy *= 0.9  # Tighten bid spread for ETH

        # Makes sure they dont go into astronomical numbers
        self.bid_spread_spyFuture = max(min(self.bid_spread_spyFuture, 0.05), 0.0001)
        self.ask_spread_spyFuture = max(min(self.ask_spread_spyFuture, 0.05), 0.0001)
        self.bid_spread_spy = max(min(self.bid_spread_spy, 0.05), 0.0001)
        self.ask_spread_spy = max(min(self.ask_spread_spy, 0.05), 0.0001)


        #self.Debug(f'BTC: New Bid Spread: {self.bid_spread_spyFuture}, New Ask Spread: {self.ask_spread_spyFuture}')  # Debug
        #self.Debug(f'ETH: New Bid Spread: {self.bid_spread_spy}, New Ask Spread: {self.ask_spread_spy}')  # Debug

        # Create and place orders
        proposal = self.create_proposal(data)
        self.place_orders(proposal)

        # Additional logic
        self.check_and_rebalance_inventory()
        self.check_stop_loss()

    #main logic =============================================================
    #basic pairs trading proposal
    def create_proposal(self, data) -> list:
        proposal = []
        for symbol in self.symbols:
            if symbol == self.spyFuture:
                bid_spread = self.bid_spread_spyFuture
                ask_spread = self.ask_spread_spyFuture
            else:
                bid_spread = self.bid_spread_spy
                ask_spread = self.ask_spread_spy

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
        self.bid_spread = float(self.GetParameter("bid_spread"))
        self.ask_spread = float(self.GetParameter("ask_spread"))
        self.bid_spread_spyFuture = self.bid_spread 
        self.ask_spread_spyFuture = self.ask_spread  
        self.bid_spread_spy = self.bid_spread 
        self.ask_spread_spy = self.ask_spread  

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
            bid_price = self.Securities[symbol].BidPrice
            ask_price = self.Securities[symbol].AskPrice

            if bid_price == 0 or ask_price == 0:
                continue
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
    def OnOrderEvent(self, orderEvent):
        if orderEvent.Status == OrderStatus.Filled:
            symbol = orderEvent.Symbol
            fill_price = orderEvent.FillPrice
            fill_quantity = orderEvent.FillQuantity
            quantity = orderEvent.FillQuantity
            is_buy_order = quantity > 0
            is_sell_order = quantity < 0

            # Update the entry price based on the average cost
            current_quantity = self.Portfolio[symbol].Quantity
            if current_quantity != 0:
                self.entry_prices[symbol] = (self.entry_prices[symbol] * (current_quantity - fill_quantity) + fill_price * fill_quantity) / current_quantity
            else:
                self.entry_prices[symbol] = 0

            if is_sell_order and self.last_buy_prices[symbol] != 0:
                profit_loss = (fill_price - self.last_buy_prices[symbol]) * abs(quantity)
                self.daily_profit_loss[symbol] += profit_loss

            if is_buy_order:
                self.last_buy_prices[symbol] = fill_price
            elif is_sell_order:
                self.last_sell_prices[symbol] = fill_price
    
    def OnEndOfDay(self):
        #Log the daily profit/loss for each symbol
        # for symbol, profit_loss in self.daily_profit_loss.items():
        #     if profit_loss > 0:
        #         self.Debug(f"Daily Profit for {symbol}: ${profit_loss}")
        #     elif profit_loss < 0:
        #         self.Debug(f"Daily Loss for {symbol}: -${abs(profit_loss)}")
        #     else:
        #         self.Debug(f"No Daily Profit/Loss for {symbol}")
        self.cancel_all_orders()

        # Reset daily profit/loss and last buy/sell prices for the next trading day
        self.last_buy_prices = {symbol: 0 for symbol in self.symbols}
        self.last_sell_prices = {symbol: 0 for symbol in self.symbols}
        self.daily_profit_loss = {symbol: 0 for symbol in self.symbols}

        # Plot buy and hold strategy for BTC
        spy_history = self.History(self.symbols[1], 2, Resolution.Minute)

        # Extract the close price for BTC
        spy_price = spy_history.loc[self.symbols[1]]['close'].iloc[-1]

        # Update BTC price list and calculate performance
        self.spyHistory.append(spy_price)
        spy_perf = round(self.InitCash * self.spyHistory[-1]/self.spyHistory[0],2)

        # Plot the performance
        self.Plot('Strategy Equity', self.symbols[1], spy_perf)




