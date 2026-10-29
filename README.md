# Summary
The python scripts handle both portfolio and expense from outside sources, generate a dashboard for summary review. Users only need to download the necessary files in input directories, with some minor config file for mapping in the input. 
## Portfolio data
Under finacial_data/input, take position and income files from schwab (positions are pulled automatically by the Schwab API, see below), supplemented by position file from fidelity, generate a account summary csv file, with account, sumbol, market value, cost basis and estimated annual income. 
## Budget data
Under financial_data/input, take downloaded transaction file from Empower Personal Dashboard (usually YTD), combined with a multi-year Yearly_budget.xlsx spreadsheet, the run will produce a pivot style output with detailed expense, income and transfer organized monthly, update the Yearly_budget.xlsx
## Dashboard
Both outputs are reflected in the common dashboard, with Portfolio and Expense tabs, plus a Schwab Automation tab

# Steps
## Download portoflio data
- Schwab positions: pulled automatically by cron every Tue/Fri (see Schwab automation). Only download by hand, or click "Run Schwab Sync Now" on the dashboard, if you need a fresher file
- Go to schwab income, select next 12 month, and download estimated incomes (the API can't provide this)
- Go to fidelity positions, and download all positions
- Put all csv files under subdirectory financial_data/input
## Download budget data
- Go to Empower personal dashboard transactions, and download all transactions for the year
- Setup a Yearly_budget.xlsx as a multi-year tracking with income and expense tabs
- Setup category_type_mapping.csv for any category you want to map to expense/income/transfer
- Setup budget_category_aiases.csv for any customized category you want to use
- Put all speadsheet files under subdirectory financial_data/input

## Run
```bash
streamlit run dashboard.py
```
go to http://localhost:8501 on your browser, the data is loaded automatically if present in the output directory already, otherwise, manually click the run button for synching brokerage data and/or budget data as needed.

"Sync Brokerage Data" / "Sync All" do NOT call Schwab; they rebuild the summary from the newest files already in financial_data/input.

## Further Analysis
One can also look at the account_summary csv file and *transaction_processed.csv/Yearly_budget.xlsx in the output directory and conduct other excel related activities as needed, such as reading detailed positions, generate pivot table or graphics

# Schwab automation
Cron runs `schwab_cron_driver.py` every weekday at :35 past each hour from 6:35 to 12:35 Pacific (`financial_data/config/schwab.cron`). Hourly runs mean a laptop that is asleep for part of the morning still gets a run; every job below is guarded so it only acts once per day. Cron only runs while the laptop and WSL are awake; missed runs are not caught up.

## What each run does
| Job | When | What |
|---|---|---|
| Token check | every run | Emails a warning when the Schwab refresh token is within `warn_token_days` (2) of expiring |
| Positions pull | Tue, Fri (`positions_sync_days`) | Writes `All-Accounts-Positions-<timestamp>.csv` into financial_data/input for all 6 accounts, then deletes older ones, keeping the newest `positions_keep_files` (2: the new file plus the last known good one). If any account comes back empty, no file is written and nothing is deleted. Once per day; retried next hour if it fails. Runs even if the market is closed |
| Cash sweep | Tue, Fri (`sweep_days`), market open | Buys `floor(cash / price)` shares of SGOV in Guoqing_Invest and Min_Invest. Skipped from the last day of Dec/Mar/Jun/Sep through day 7 (`sweep_blackout_end_day`) of Jan/Apr/Jul/Oct |
| Quarterly sell | Jan/Apr/Jul/Oct, trading days 1st-4th (`liquidate_last_day`), market open | Sells all SGOV in the two sweep accounts so cash is ready for the Fidelity transfer on the 5th. Retried every run until FILLED |

Trading is skipped on NYSE holidays and outside market hours. Any crash, failed connection or order that isn't FILLED sends one "Schwab automation: action needed" email per day to `alert_email_to`.

## Where things live
- Settings: `financial_data/config/schwab_automation_config.json` (missing keys use the defaults in `schwab_client.py`). `dry_run: true` logs orders without placing them; `automation_enabled: false` turns trading off
- Accounts: `financial_data/config/schwab_accounts.csv` (`sweep_target` accounts trade, `sync_only` accounts are only read)
- What happened last: `financial_data/config/schwab_automation_state.json`, the cron log `financial_data/output/schwab_cron.log`, and the dashboard Schwab Automation tab
- Secrets (git-ignored): `schwab_credentials.json`, `schwab_token.json`, `schwab_notify.json`

## Weekly chore: re-authorize (every 7 days)
Schwab refresh tokens expire after 7 days. When the warning email arrives:
```bash
.venv/bin/python schwab_auth_init.py
```
Log in, tick all 6 accounts, then paste the full `https://127.0.0.1/...` URL quickly (the code expires in about 30 seconds).

## Install / change the cron schedule
```bash
crontab financial_data/config/schwab.cron   # replaces your crontab with this file
crontab -l                                  # check
```
