from AlgorithmImports import *

class HipsterRedCrocodile(QCAlgorithm):
    #algorithm uses a market making strategy with dynamic quote quantities and inventory rebalancing
    #to ensure that the market maker is keeping a neutral inventory so they're not
    #exposed to too much risk
    def Initialize(self):
        self.SetWarmUp(10)
        self.SetStartDate(2020, 11, 10)
        self.SetEndDate(2021, 8, 10)
        self.InitCash = 1000000
        self.SetCash(self.InitCash)
        self.symbols = [self.AddCrypto(ticker, Resolution.Minute).Symbol for ticker in ["BTCUSD"]]
        self.btcusd = []
        self.ethusd = []
        self.is_trading_paused = False

        #MM parameters
        self.max_inventory_size = 10
        self.reduction_factor = 0.5
        self.bid_spread = float(self.GetParameter("bid_spread"))
        self.ask_spread = float(self.GetParameter("ask_spread"))
        self.lot_size = float(self.GetParameter("lot_size"))
        self.order_refresh_time = int(self.GetParameter("order_refresh_time"))
        self.stop_loss_percentage = int(self.GetParameter("stop_loss_percentage"))
       
        #Boillinger Bands
        self.bollingerWindow = 20  # Choose your own window
        self.bollingerBand = {}
        for symbol in self.symbols:
            self.bollingerBand[symbol] = self.BB(symbol, self.bollingerWindow, 2, Resolution.Minute)

        #charting
        self.AddChart(Chart("Asset Price and Bollinger Bands"))
        
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

        # for symbol in self.symbols:
        #     if self.Time.minute == 0:
        #         self.Plot("Asset Price and Bollinger Bands", "Price", data[symbol].Close)
        #         self.Plot("Asset Price and Bollinger Bands", "Upper Band", self.bollingerBand[symbol].UpperBand.Current.Value)
        #         self.Plot("Asset Price and Bollinger Bands", "Lower Band", self.bollingerBand[symbol].LowerBand.Current.Value)

        proposal = self.create_proposal(data)
        self.place_orders(proposal)
        self.check_and_rebalance_inventory()
        self.check_stop_loss()

    #main logic =============================================================
    #dynamic bid-ask proposal using boillinger bands as the upper and lower bound
    def create_proposal(self, data) -> list:
        proposal = []
        for symbol in self.symbols:
            ref_price = (self.Securities[symbol].BidPrice + self.Securities[symbol].AskPrice) / 2
            upper_band = self.bollingerBand[symbol].UpperBand.Current.Value
            lower_band = self.bollingerBand[symbol].LowerBand.Current.Value

            upper_distance = (upper_band - ref_price) / ref_price
            lower_distance = (ref_price - lower_band) / ref_price

            # If closer to the lower band, decrease bid_spread and increase ask_spread
            if lower_distance < upper_distance:
                dynamic_bid_spread = self.bid_spread * (1 - lower_distance)
                dynamic_ask_spread = self.ask_spread * (1 + lower_distance)

            # If closer to the upper band, increase bid_spread and decrease ask_spread
            else:
                dynamic_bid_spread = self.bid_spread * (1 + upper_distance)
                dynamic_ask_spread = self.ask_spread * (1 - upper_distance)

            inventory_score = self.calculate_inventory_score(symbol)

            buy_price = ref_price * (1 - dynamic_bid_spread)
            sell_price = ref_price * (1 + dynamic_ask_spread)
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


