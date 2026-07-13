# Summary
The python scripts handle both portfolio and expense from outside sources, generate a dashboard for summary review. Users only need to download the necessary files in input directories, with some minor config file for mapping in the config. 
## Portfolio data
Under finacial_data/input, take downloaded position and income files from schwab, supplemented by position file from fidelity, generate a account summary csv file, with account, sumbol, market value, cost basis and estimated annual income. 
## Budget data
Under expense_data/input, take downloaded transaction file from Empower Personal Dashboard (usually YTD), combined with a multi-year Yearly_budget.xlsx spreadsheet, the run will produce a pivot style output with detailed expense, income and transfer organized monthly, update the Yearly_budget.xlsx
## Dashboard
Both outputs are reflected in the common dashboard, with Portfolio and Expense tabs

# Steps
## Download portoflio data
- Go to Schwab positions, and download all brokerage positions
- Go to schwab income, select next 12 month, and download estimated incomes
- Go to fidelity positions, and download all positions
- Put all csv files under subdirectory financial_data/input
## Download budget data
- Go to Empower personal dashboard transactions, and download all transactions for the year
- Setup a Yearly_budget.xlsx as a multi-year tracking with income and expense tabs
- Setup category_type_mapping.csv for any category you want to map to expense/income/transfer
- Setup budget_category_aiases.csv for any customized category you want to use
- Put all speadsheet files under subdirectory expense_data/input

## Run
```bash
streamlit run dashboard.py
```
go to http://localhost:8501 on your browser, the data is loaded automatically if present in the output directory already, otherwise, manuallyclick the run button for synching brokerage data and/or budget data as needed.

## Further Analysis
One can also look at the account_summary csv file and *transaction_processed.csv/Yearly_budget.xlsx in the output directory and conduct other excel related activities as needed, such as reading detailed positions, generate pivot table or graphics
