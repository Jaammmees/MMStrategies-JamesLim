# region imports
from AlgorithmImports import *
# endregion

#HOLDS BTCUSDT FROM SET START AND END DATE, USED TO CALCULATE PERFORMANCE OF BUY AND HOLD STRATEGY.

class CalmYellowGreenGorilla(QCAlgorithm):

    def Initialize(self):
        self.SetStartDate(2021, 11, 10)
        self.SetEndDate(2022,11,10)
        self.SetCash(1000000)
        self.btc = self.AddCrypto("BTCUSD",Resolution.Minute).Symbol

    def OnData(self, data: Slice):
        if not self.Portfolio.Invested:
            self.SetHoldings("BTCUSD",1)

        