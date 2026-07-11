import datetime
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
#from pandas_datareader import data as web
import yfinance as yf
# yf.pdr_override()

pd.options.display.max_rows = 1000
pd.options.display.max_columns = 100

tickers = ["AGNC","AMD","AMZN","ARI","BND","BRK-B","BXMT","CNQ","DNKEY","FLIN","FLJP","FREL","GPIQ","GPIX","GOOGL","IBIT","IWM","JEPI","JEPQ","JPM","MAA","NVDA","NVS","ORCL","PFE","SCHB","SCHD","SCHY","SPY","SPYI","STWD","TSLA","VB","VEA","VGSNX","VIIIX","VMATX","VMCIX","VOD","VOO","VPU","VSCIX","VTI","VTSNX","VV","VXUS","VYM","VYMI","VZ","WF","XOM"]
#tickers = ["AMD","AMZN"]

today = datetime.date.today()
year = today.year
print(today.day)

# multpl_stocks = pd.DataFrame()
# multpl_stocks = web.get_data_yahoo(tickers,
multpl_stocks = yf.download(tickers,
    start = "2002-01-01",
    end = today.isoformat())

print (multpl_stocks)

multpl_stock_daily_returns = multpl_stocks['Close'].pct_change()
multpl_stock_monthly_returns = multpl_stocks['Close'].resample('M').ffill().pct_change()
multpl_stock_monthly_cum_returns = (multpl_stock_monthly_returns + 1).cumprod()

print("monthly cum return")
print(multpl_stock_monthly_cum_returns.head(2))
print(multpl_stock_monthly_cum_returns.tail(2))
(multpl_stock_monthly_returns.mean()).to_csv("mean.csv")
(multpl_stock_monthly_returns.std()).to_csv("std.csv")
(multpl_stock_monthly_returns.corr()).to_csv("corr.csv")
(multpl_stock_monthly_returns.cov()).to_csv("cov.csv")

data = yf.Tickers(tickers)
div_last =pd.Series()
for ticker in tickers:
    div=data.tickers[ticker].dividends 
    div_last[ticker]=sum(div[pd.to_datetime(div.index).year == year-1])
    
div_last.to_csv("income.csv")

# alternatives, more foreward looking
# import yfinance as yf
# import pandas as pd

# # Your ticker list
# tickers = ['VOO', 'VTI', 'SCHD', 'SPY', 'VEA', 'VPU', 'JPM', 'PFE', 'ORCL', 'GOOGL', 'XOM']

# report = []
# for t in tickers:
#     stock = yf.Ticker(t)
#     # Get dividends for the last 6 months
#     divs = stock.dividends.tail(6)
#     total_div = divs.sum()
#     avg_div = total_div / 2 # Converting 6-month total to a quarterly average
#     report.append({'Ticker': t, 'Last 6mo Total': total_div, 'Quarterly Avg': avg_div})

# df = pd.DataFrame(report)
# print(df)