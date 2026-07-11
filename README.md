# Summary
The python scripts take dowwloaded position and income files from schwab, supplemented by position file from fidelity, generate a account summary csv
file, with account, sumbol, market value, cost basis and
estimated annual income. User can invoke a ui with a button click to generate
a presentable summary table, pie chart and detailed table

# Steps
## Download
### Go to Schwab positions, and download all brokerage positions
### Go to schwab income, select next 12 month, and download estimated incomes
### Go to fidelity positions, and download all positions
### Put all csv files under subdirectory financial_data/input
### Create subdirectory financial_data/output if you haven't done so
## Run
```bash
streamlit run portfolio_presentation.py
```
go to localhost:8501 on your browser 
and click the run button


