"""
Shared filesystem layout for the financial_data/ directory tree.

portfolio_backend.py, expense_budget_backend.py, portfolio_history_backend.py,
and dashboard.py all import paths from here instead of redefining them, so
there's a single place to change the layout or add a new config file.

Directory layout:
    financial_data/
        input/    <- raw exports (Empower transactions CSV, Schwab/Fidelity
                     positions, Portfolio_History.xlsx, Yearly_budget.xlsx, ...)
        config/   <- hand-maintained mapping/alias CSVs (edit these directly)
        output/   <- generated reports/workbooks
"""
import os

BASE_DIR = 'financial_data'
INPUT_DIR = os.path.join(BASE_DIR, 'input')
CONFIG_DIR = os.path.join(BASE_DIR, 'config')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')

# Config CSVs (all hand-edited by the user)
CATEGORY_TYPE_MAPPING_PATH = os.path.join(CONFIG_DIR, 'category_type_mapping.csv')
BUDGET_CATEGORY_ALIASES_PATH = os.path.join(CONFIG_DIR, 'budget_category_aliases.csv')
ASSET_ALLOCATION_MAPPING_PATH = os.path.join(CONFIG_DIR, 'asset_allocation_mapping.csv')
ACCOUNT_MAPPING_PATH = os.path.join(CONFIG_DIR, 'account_mapping.csv')
CASHFLOW_CATEGORIES_PATH = os.path.join(CONFIG_DIR, 'cashflow_categories.csv')

# Schwab API integration (credentials/token files are gitignored via the
# blanket /financial_data ignore; never hand-edit schwab_token.json)
SCHWAB_CREDENTIALS_PATH = os.path.join(CONFIG_DIR, 'schwab_credentials.json')
SCHWAB_TOKEN_PATH = os.path.join(CONFIG_DIR, 'schwab_token.json')
SCHWAB_ACCOUNTS_PATH = os.path.join(CONFIG_DIR, 'schwab_accounts.csv')
SCHWAB_AUTOMATION_CONFIG_PATH = os.path.join(CONFIG_DIR, 'schwab_automation_config.json')
SCHWAB_NOTIFY_CONFIG_PATH = os.path.join(CONFIG_DIR, 'schwab_notify.json')
SCHWAB_AUTOMATION_STATE_PATH = os.path.join(CONFIG_DIR, 'schwab_automation_state.json')


def ensure_dirs():
    for d in (INPUT_DIR, CONFIG_DIR, OUTPUT_DIR):
        os.makedirs(d, exist_ok=True)
