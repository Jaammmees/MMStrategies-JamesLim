from AlgorithmImports import *
from datetime import time

#custom data class
class OrderBookData(PythonData):
    
    def GetSource(self, config, date, isLiveMode):
        source = "https://www.dl.dropboxusercontent.com/scl/fi/1vchqt614w2lw8wuyoss7/BTCUSDT_orderbookTransformed.csv?rlkey=daigudppuacpc7fm0ezgp3ke2&dl=1"
        return SubscriptionDataSource(source, SubscriptionTransportMedium.RemoteFile)

        
    def Reader(self, config, line, date, isLiveMode):
        if not (line.strip() and line[0].isdigit()):
            return None

        data = line.split(',')
        custom_data = OrderBookData()
        custom_data.Symbol = config.Symbol
        
        custom_data.Time = datetime.strptime(data[0], "%Y-%m-%d %H:%M:%S:%f")
        custom_data.Value = data[4]
        custom_data["BidVolume"] = float(data[1])
        custom_data["AskVolume"] = float(data[2])
        custom_data["Open"] = float(data[3])
        custom_data["Close"] = float(data[4])

        return custom_data

class HipsterRedCrocodile(QCAlgorithm):
    #order imbalance strategy that calculates the volume of bids and asks over a certain time frame
    #and so the trading hypothesis is, if there is more bid, we increase ask_price as we anticipate a rise
    #and if there is more ask, we decrease bid_price as we anticipate a fall.
    def Initialize(self):
        self.SetStartDate(2023, 2, 1)
        self.SetEndDate(2023,2,3)
        self.InitCash = 1000000
        self.SetCash(self.InitCash)
        self.symbol = self.AddData(OrderBookData, "BTCUSDT").Symbol
        self.btcusdt_real = self.AddCrypto("BTCUSDT",Resolution.Minute, Market.Binance)
        self.btcusd = []
        self.is_trading_paused = False

        #MM parameters
        self.max_inventory_size = 10
        self.reduction_factor = 0.5
        self.bid_spread = float(self.GetParameter("bid_spread"))
        self.ask_spread = float(self.GetParameter("ask_spread"))
        self.lot_size = float(self.GetParameter("lot_size"))
        self.order_refresh_time = int(self.GetParameter("order_refresh_time"))
        self.stop_loss_percentage = int(self.GetParameter("stop_loss_percentage"))
        self.order_imbalance_threshold = 0.1

        # stats and stuff
        self.last_filled_prices = {}
        self.last_buy_prices = {self.symbol.Value: 0}
        self.last_sell_prices = {self.symbol.Value: 0}
        self.daily_profit_loss = {self.symbol.Value: 0}
        self.entry_prices = {self.symbol.Value: 0}

        #cancels orders n stuff
        self.Schedule.On(self.DateRules.EveryDay(), self.TimeRules.Every(TimeSpan.FromSeconds(self.order_refresh_time)), Action(self.cancel_all_orders))
        self.Schedule.On(self.DateRules.EveryDay(), self.TimeRules.At(0, 0), Action(self.reset_daily_values))

    def OnData(self, data):

        if self.is_trading_paused:
            return

        if self.Time.minute % 15 == 0:
            self.reset_spreads()

        # Real Bid Volumes

        if data.ContainsKey(self.symbol):
            current_time = self.Time.time()
            if time(11, 0) <= current_time <= time(13, 0):
                
                # Using custom data
                if data.ContainsKey(self.symbol):
                    custom_data = data[self.symbol]
                    bid_volume = custom_data["BidVolume"]
                    ask_volume = custom_data["AskVolume"]
                    close = custom_data["Open"]
                    open = custom_data["Close"]
                    # price = (close+open) / 2  # Calculate the average price
                    # self.Plot("BTCUSD and Orders", "Price", price)
                    # self.Debug(f"Close: {close}, Open: {open}, Bid Volume: {bid_volume}, Ask Volume: {ask_volume}")

                imbalance_score = self.calculate_simple_imbalance_score(bid_volume, ask_volume)

                proposal = self.create_proposal(data, imbalance_score)
                self.place_orders(proposal)
                self.check_and_rebalance_inventory(data)   
                #self.check_stop_loss()
        
        if data.ContainsKey("BTCUSD"):
            btcusdt_data = data["BTCUSD"]
            self.Plot("BTCUSD and Orders", "Open", btcusdt_data.Open)
            self.Plot("BTCUSD and Orders", "High", btcusdt_data.High)
            self.Plot("BTCUSD and Orders", "Low", btcusdt_data.Low)
            self.Plot("BTCUSD and Orders", "Close", btcusdt_data.Close)

    #main logic =============================================================
    #orderimbalancestrategy

    def reset_spreads(self):
        self.bid_spread = float(self.GetParameter("bid_spread"))
        self.ask_spread = float(self.GetParameter("ask_spread"))

    def update_rolling_window(self, last_bid_size, last_ask_size, past_bid_sizes, past_ask_sizes):
        # Add the newest bid and ask sizes
        past_bid_sizes.append(last_bid_size)
        past_ask_sizes.append(last_ask_size)
        
        # Trim to window size
        past_bid_sizes = past_bid_sizes[-self.window_size:]
        past_ask_sizes = past_ask_sizes[-self.window_size:]

        # Apply weighting (newest items will have higher impact because they are more recent)
        weighted_past_bid_sizes = [x * (i+1) for i, x in enumerate(past_bid_sizes)]
        weighted_past_ask_sizes = [x * (i+1) for i, x in enumerate(past_ask_sizes)]
        
        # Replace the original lists with the weighted lists
        past_bid_sizes = weighted_past_bid_sizes
        past_ask_sizes = weighted_past_ask_sizes

    def calculate_simple_imbalance_score(self, bid_volume, ask_volume):

        total_volume = bid_volume + ask_volume

        if total_volume == 0:
            return 0

        imbalance_score = (bid_volume - ask_volume) / total_volume
        # if imbalance_score > 0:
        #     self.Debug(f"Bid Volume ({bid_volume}) is higher than Ask Volume ({ask_volume}). Increasing ask spread.")
        # elif imbalance_score < 0:
        #     self.Debug(f"Ask Volume ({ask_volume}) is higher than Bid Volume ({bid_volume}). Increasing bid spread.")
        self.Plot("Order Imbalance", "Order Imbalance Score", imbalance_score)
        return imbalance_score

    def create_proposal(self, data, imbalance_score) -> list:
        proposal = []
        max_spread = 0.001  # maximum allowable spread
        min_spread = 0.00005  # minimum allowable spread

        custom_data = data[self.symbol]
        ref_price = (custom_data['Close'] + custom_data['Open']) / 2

        inventory_score = self.calculate_inventory_score(self.symbol)
        
        # Dynamic spread adjustment based on order imbalance
        if imbalance_score > self.order_imbalance_threshold:
            self.ask_spread *= (1.001 + imbalance_score)  # Increase the ask spread when there are more bids
        elif imbalance_score < -self.order_imbalance_threshold:
            self.bid_spread *= (0.98 - imbalance_score)  # Increase the bid spread when there are more asks
        
        # Bound the spreads
        self.ask_spread = min(max(self.ask_spread, min_spread), max_spread)
        self.bid_spread = min(max(self.bid_spread, min_spread), max_spread)

        buy_price = ref_price * (1 - self.bid_spread)
        sell_price = ref_price * (1 + self.ask_spread)

        proposal.append((self.symbol, buy_price, sell_price, inventory_score))

        return proposal


    def place_orders(self, proposal) -> None:
        for symbol, buy_price, sell_price, inventory_score in proposal:
            quantity = self.Portfolio[symbol].Quantity
            if abs(quantity) >= self.max_inventory_size:
                buy_price = buy_price * (1 - self.reduction_factor * self.bid_spread)
                sell_price = sell_price * (1 + self.reduction_factor * self.ask_spread)

            bid_quantity = self.lot_size
            ask_quantity = self.lot_size

            buy_price = round(buy_price, 2)
            sell_price = round(sell_price, 2)
            bid_quantity = max(round(bid_quantity, 0), 1)  
            ask_quantity = max(round(ask_quantity, 0), 1)
            self.Plot("BTCUSD and Orders", "Buy Price", buy_price)
            self.Plot("BTCUSD and Orders", "Sell Price", sell_price)
            self.Debug(f"Calculated ask_quantity: {ask_quantity}, bid_quantity: {bid_quantity}")
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


    def check_and_rebalance_inventory(self, data):
        target_balance_ratio = 0.2  # 50% base, 50% quote
        tolerance = 0.05  # Allowable deviation from the target balance ratio

        # Current quantity of the base asset
        base_quantity = self.Portfolio[self.symbol].Quantity
        # Current value of the quote asset, converted to base asset units

        close_price = data[self.symbol]["Close"]

        quote_value_in_base = self.Portfolio.CashBook['USD'].Amount / close_price

        # Total value of the portfolio in base asset units
        total_value_in_base = base_quantity + quote_value_in_base

        # Current balance ratio
        current_balance_ratio = base_quantity / total_value_in_base

        # Calculate the desired quantity of the base asset
        target_base_quantity = total_value_in_base * target_balance_ratio
        delta_quantity = target_base_quantity - base_quantity

        # Place the order to rebalance
        delta_quantity = max(round(delta_quantity, 0), 1)
        self.MarketOrder(self.symbol.Value, delta_quantity)

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


    # #profit tracking stuff
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
        # self.last_buy_prices = {self.securityObj.Symbol.Value: 0}
        # self.last_sell_prices = {self.securityObj.Symbol.Value: 0}
        # self.daily_profit_loss = {self.securityObj.Symbol.Value: 0}


        # #plot buy and hold strategy of btc and eth, should also show their price movement
        # btc_history = self.History(self.securityObj.Symbol.Value, 2, Resolution.Minute)

        # # Extract the close price
        # btc_price = btc_history.loc[self.securityObj.Symbol.Value]['Close'].iloc[-1]

        # self.btcusd.append(btc_price)
        # btc_perf = self.InitCash * self.btcusd[-1]/self.btcusd[0]
        # self.Plot('Strategy Equity', self.securityObj.Symbol.Value, btc_perf)



