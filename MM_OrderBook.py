from AlgorithmImports import *
from collections import deque

class HipsterRedCrocodile(QCAlgorithm):
    #order imbalance strategy that calculates the volume of bids and asks over a certain time frame
    #and so the trading hypothesis is, if there is more bid, we increase ask_price as we anticipate a rise
    #and if there is more ask, we decrease bid_price as we anticipate a fall.
    def Initialize(self):
        self.SetWarmUp(10)
        self.SetStartDate(2020, 11, 10)
        self.SetEndDate(2021, 8, 10)
        self.InitCash = 1000000
        self.SetCash(self.InitCash)
        self.symbols = [self.AddCrypto(ticker, Resolution.Minute).Symbol for ticker in ["BTCUSD"]]
        self.current_time = self.Time
        self.btcusd = []
        self.ethusd = []
        self.is_trading_paused = False
        self.imbalance_scores = deque(maxlen=100)
        self.bid_volumes = deque(maxlen=100)
        self.ask_volumes = deque(maxlen=100)


        #MM parameters
        self.max_inventory_size = 10
        self.reduction_factor = 0.5
        self.bid_spread = float(self.GetParameter("bid_spread"))
        self.ask_spread = float(self.GetParameter("ask_spread"))
        self.lot_size = float(self.GetParameter("lot_size"))
        self.order_refresh_time = int(self.GetParameter("order_refresh_time"))
        self.stop_loss_percentage = int(self.GetParameter("stop_loss_percentage"))
        self.order_imbalance_threshold = 0.1

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
        for symbol in self.symbols:
            if data.ContainsKey(symbol):
                quote_bar = data[symbol]
                #self.Plot("Bid and Ask Sizes", f"{symbol} Bid Size", quote_bar.LastBidSize)
                #self.Plot("Bid and Ask Sizes", f"{symbol} Ask Size", quote_bar.LastAskSize)
                self.bid_volumes.append(quote_bar.LastBidSize)
                self.ask_volumes.append(quote_bar.LastAskSize)

                # Calculate imbalance score based on current bid and ask sizes
                imbalance_score = self.calculate_simple_imbalance_score(quote_bar.LastBidSize, quote_bar.LastAskSize)
                #self.Plot("Imbalance Score", "Imbalance Score", imbalance_score)
                proposal = self.create_proposal(data, imbalance_score)
                self.place_orders(proposal)

                self.check_and_rebalance_inventory()
                self.check_stop_loss()
                if self.current_time.minute == 0:
                    self.reset_spreads()

    #main logic =============================================================
    #orderimbalancestrategy

    def reset_spreads(self):
        self.bid_spread = float(self.GetParameter("bid_spread"))
        self.ask_spread = float(self.GetParameter("ask_spread"))

    def calculate_simple_imbalance_score(self, current_bid_size, current_ask_size):
        min_spread = 0.0001  # Minimum allowed spread
        max_spread = 0.1     # Maximum allowed spread

        total_volume = current_bid_size + current_ask_size

        if total_volume == 0:
            return 0

        imbalance_score = (current_bid_size - current_ask_size) / total_volume

        # Add the latest imbalance score to the deque
        self.imbalance_scores.append(imbalance_score)

        # Calculate the moving average of the imbalance scores
        avg_imbalance_score = sum(self.imbalance_scores) / len(self.imbalance_scores)

        # # Adjust spreads based on average imbalance score
        # if avg_imbalance_score > self.order_imbalance_threshold:
        #     self.ask_spread *= 1.05  # Increase by 5%
        # elif avg_imbalance_score < -self.order_imbalance_threshold:
        #     self.bid_spread *= 0.95  # Decrease by 5%

            # Use the moving average to adjust spreads in a ratio-like manner
        self.bid_spread = max(0.0001, 0.1 * (1 - avg_imbalance_score))  # Example formula, adjust as needed
        self.ask_spread = max(0.0001, 0.1 * (1 + avg_imbalance_score))  # Example formula, adjust as needed

        # Ensure spreads are within min-max range
        self.bid_spread = max(min(self.bid_spread, max_spread), min_spread)
        self.ask_spread = max(min(self.ask_spread, max_spread), min_spread)

        #self.Plot("Bid and Ask Spreads", "Bid Spread", self.bid_spread)
        #self.Plot("Bid and Ask Spreads", "Ask Spread", self.ask_spread)

        return avg_imbalance_score


    def create_proposal(self, data, imbalance_score) -> list:
        proposal = []
        for symbol in self.symbols:
            ref_price = (self.Securities[symbol].BidPrice + self.Securities[symbol].AskPrice) / 2
            inventory_score = self.calculate_inventory_score(symbol)

            buy_price = ref_price * (1 - self.bid_spread)
            sell_price = ref_price * (1 + self.ask_spread)
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

        # # Reset daily profit/loss and last buy/sell prices for the next trading day
        # self.last_buy_prices = {symbol: 0 for symbol in self.symbols}
        # self.last_sell_prices = {symbol: 0 for symbol in self.symbols}
        # self.daily_profit_loss = {symbol: 0 for symbol in self.symbols}

        # #plot buy and hold strategy of btc and eth, should also show their price movement
        # btc_history = self.History(self.symbols[0], 2, Resolution.Minute)

        # # Extract the close price
        # btc_price = btc_history.loc[self.symbols[0]]['close'].iloc[-1]

        # self.btcusd.append(btc_price)
        # btc_perf = self.InitCash * self.btcusd[-1]/self.btcusd[0]
        # self.Plot('Strategy Equity', self.symbols[0], btc_perf)


