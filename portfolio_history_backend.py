"""
Multi-year portfolio history (per-account Ending Value / Cash Flow / Gain / Return).

Two modes:
  1. One-time seed (run once):
       python portfolio_history_backend.py --seed
     Imports financial_data/input/summary_investment_eoy.xlsx (the hand-maintained
     1996-2025 tracking sheet) into financial_data/input/Portfolio_History.xlsx.
     Per-account Ending Value history is preserved for every named account in that
     sheet. Per-account Cash Flow/Gain/Return only exist historically at the
     Non-Retirement/Retirement group level in that sheet (it was never tracked
     per-account), so those cells stay blank for individual accounts in the
     historical years - only the group/grand total rows are populated back to 1996.

  2. Regular run (each time you want to refresh the current year):
       python portfolio_history_backend.py
     Reads financial_data/input/Portfolio_History.xlsx (the seeded/working copy),
     computes the current year's Ending Value (from the latest
     Master_Account_Summary_*.csv), Cash Flow (from the Empower transactions CSV,
     filtered to mapped investment accounts and cash-flow categories), Gain and
     Return, and writes ONLY that year's column to
     financial_data/output/Portfolio_History.xlsx. Prior years are never touched,
     so any manual corrections already folded into the input copy are safe -
     review the output copy, then replace your working Portfolio_History.xlsx with
     it once you're happy, same workflow as Yearly_budget.xlsx.

Directory layout:
    financial_data/
        input/    <- summary_investment_eoy.xlsx (seed source, one-time)
                  <- account_mapping.csv, cashflow_categories.csv (edit directly)
                  <- Portfolio_History.xlsx (working copy, source of truth)
        output/   <- Master_Account_Summary_*.csv (from portfolio_backend.py)
                  <- Portfolio_History.xlsx (freshly computed current-year column)
    expense_data/
        input/    <- *_transactions.csv (Empower export, also used by the expense pipeline)
"""
import glob
import os
import re
import sys
from datetime import datetime

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

FIN_BASE_DIR = 'financial_data'
FIN_INPUT_DIR = os.path.join(FIN_BASE_DIR, 'input')
FIN_OUTPUT_DIR = os.path.join(FIN_BASE_DIR, 'output')

EXP_INPUT_DIR = os.path.join('expense_data', 'input')

LEGACY_WORKBOOK_PATH = os.path.join(FIN_INPUT_DIR, 'summary_investment_eoy.xlsx')
ACCOUNT_MAPPING_PATH = os.path.join(FIN_INPUT_DIR, 'account_mapping.csv')
CASHFLOW_CATEGORIES_PATH = os.path.join(FIN_INPUT_DIR, 'cashflow_categories.csv')
PORTFOLIO_HISTORY_INPUT_PATH = os.path.join(FIN_INPUT_DIR, 'Portfolio_History.xlsx')
PORTFOLIO_HISTORY_OUTPUT_PATH = os.path.join(FIN_OUTPUT_DIR, 'Portfolio_History.xlsx')

SHEET_NAMES = ['EndingValue', 'CashFlow', 'Gain', 'Return']
NR_TOTAL_LABEL = 'Non-Retirement Total'
R_TOTAL_LABEL = 'Retirement Total'
GRAND_TOTAL_LABEL = 'Grand Total'

DOLLAR_FMT = '"$"#,##0_);("$"#,##0)'
PERCENT_FMT = '0.00%'

# Legacy summary_investment_eoy.xlsx "Summary" sheet column headers (row 2) that hold
# per-account Ending Value, mapped to the CanonicalAccount name they become here.
# Accounts with no current-day equivalent keep a "(legacy)" name - they get
# historical Ending Value only and are never touched by the regular run.
LEGACY_ACCOUNT_COLUMN_MAP = {
    'Vanguard Guoqing': 'Guoqing_Invest',
    'Vanguard Min': 'Min_Invest',
    'JPMorgan Pension': 'JPMC Pension',
    'IRA (SSI, CA, Lucent 401k added on 2008, MathWorks 2025)': 'Guoqing_IRA',
    'Roth IRA': 'Guoqing_Roth',
    'Min Roth': 'Min_Roth',
    'Min IRA': 'Min_IRA',
    'Treasury(Sequoia)': 'Treasury(Sequoia) (legacy)',
    'Fidelity Bank': 'Fidelity Bank (legacy)',
    'Fidelity Brokerage': 'Fidelity Brokerage (legacy)',
    'Fidelity 401 (SSI, CA and Lucent)': 'Fidelity 401 (legacy)',
    'MathWorks(JPMorgan) 401K': 'MathWorks 401K (legacy)',
    'Education Fund': 'Education Fund (legacy)',
}
# Which side of the legacy sheet each legacy column lives on (matches the sheet's
# own "Non-Retirement"/"Retirement" section headers).
LEGACY_NR_COLUMNS = {
    'Vanguard Guoqing', 'Treasury(Sequoia)', 'Fidelity Bank',
    'Fidelity Brokerage', 'Vanguard Min',
}

# Legacy group-total columns -> our metric sheets, imported directly per year
# (the old sheet already computed these; no need to recompute historical totals).
LEGACY_GROUP_TOTAL_COLUMNS = {
    'NonRetirement': {'EndingValue': 'NR Total', 'CashFlow': 'NR Cash Flow',
                       'Gain': 'NR Gain', 'Return': 'NR Return'},
    'Retirement': {'EndingValue': 'R Total', 'CashFlow': 'R Cash Flow',
                    'Gain': 'R Gain', 'Return': 'R Return'},
    # Grand total: "Inv Total"/"Cash Flows"/"Net Gain"/"Return" are the sheet's
    # overall (NR+R) figures - "Net Gain" is Gain net of cash flow (matches our
    # Gain = Ending - Beginning - CashFlow definition), not the raw "Gain" column.
    'Grand': {'EndingValue': 'Inv Total', 'CashFlow': 'Cash Flows',
              'Gain': 'Net Gain', 'Return': 'Return'},
}

# Empower categories we exclude from investment-account "possibly this is a real
# account, why isn't it mapped" warnings even though they're not in
# account_mapping.csv (obviously non-investment accounts).
NON_INVESTMENT_KEYWORDS = ('visa', 'card', 'checking', 'citigold')

# Transaction descriptions that mean "not a real external cash flow event", even
# though Empower sometimes tags them Category=Transfers - excluded regardless of
# Category:
#   "core account"  - Fidelity's automatic cash sweep (settled cash <-> money
#                      market fund within the SAME account); purely internal.
#   "toa acat"       - Transfer-of-Assets/ACAT in-kind custodian moves (e.g. the
#                      Vanguard -> Schwab consolidation); the position just moved
#                      broker, no new money entered or left the portfolio.
CASHFLOW_EXCLUDE_DESCRIPTION_KEYWORDS = ('core account', 'toa acat')


# ---------------------------------------------------------------------------
# Config loaders
# ---------------------------------------------------------------------------

def load_account_mapping():
    if not os.path.exists(ACCOUNT_MAPPING_PATH):
        print(f"Error: {ACCOUNT_MAPPING_PATH} not found. Create it first (see README).")
        sys.exit(1)
    df = pd.read_csv(ACCOUNT_MAPPING_PATH)
    df.columns = df.columns.str.strip()
    for col in ['CanonicalAccount', 'Group', 'PortfolioAccountName', 'EmpowerAccount', 'CashFlowSource']:
        df[col] = df[col].fillna('').astype(str).str.strip()
    return df


def load_cashflow_categories():
    if not os.path.exists(CASHFLOW_CATEGORIES_PATH):
        print(f"Error: {CASHFLOW_CATEGORIES_PATH} not found. Create it first (see README).")
        sys.exit(1)
    df = pd.read_csv(CASHFLOW_CATEGORIES_PATH)
    return set(df['Category'].astype(str).str.strip())


def account_roster(mapping_df):
    """Returns (nonretirement_accounts, retirement_accounts) as ordered lists of
    CanonicalAccount names, mapped accounts first (in mapping.csv order) followed
    by legacy-only accounts (in their original sheet order), per group."""
    mapped_nr = [r.CanonicalAccount for r in mapping_df.itertuples() if r.Group == 'NonRetirement']
    mapped_r = [r.CanonicalAccount for r in mapping_df.itertuples() if r.Group == 'Retirement']

    legacy_nr = [v for k, v in LEGACY_ACCOUNT_COLUMN_MAP.items() if k in LEGACY_NR_COLUMNS]
    legacy_r = [v for k, v in LEGACY_ACCOUNT_COLUMN_MAP.items() if k not in LEGACY_NR_COLUMNS]
    # Drop legacy entries that turned out to just be the historical name for a
    # mapped account (already covered by mapped_nr/mapped_r).
    mapped_set = set(mapped_nr) | set(mapped_r)
    legacy_nr = [a for a in legacy_nr if a not in mapped_set]
    legacy_r = [a for a in legacy_r if a not in mapped_set]

    return mapped_nr + legacy_nr, mapped_r + legacy_r


# ---------------------------------------------------------------------------
# Sheet scaffolding shared by seed + regular run
# ---------------------------------------------------------------------------

def new_history_workbook(nr_accounts, r_accounts):
    wb = Workbook()
    wb.remove(wb.active)
    for sheet_name in SHEET_NAMES:
        ws = wb.create_sheet(sheet_name)
        ws['A1'] = 'Account'
        ws['A1'].font = Font(bold=True)
        row = 2
        for acct in nr_accounts:
            ws.cell(row=row, column=1, value=acct)
            row += 1
        nr_total_row = row
        ws.cell(row=row, column=1, value=NR_TOTAL_LABEL).font = Font(bold=True)
        row += 1
        for acct in r_accounts:
            ws.cell(row=row, column=1, value=acct)
            row += 1
        r_total_row = row
        ws.cell(row=row, column=1, value=R_TOTAL_LABEL).font = Font(bold=True)
        row += 1
        grand_row = row
        ws.cell(row=row, column=1, value=GRAND_TOTAL_LABEL).font = Font(bold=True)
        ws.column_dimensions['A'].width = 32
    return wb


def find_row(ws, label):
    for r in range(2, ws.max_row + 1):
        val = ws.cell(row=r, column=1).value
        if val is not None and str(val).strip().lower() == label.strip().lower():
            return r
    return None


def get_or_create_year_column(ws, year):
    """Finds the column headered with `year`; appends a new one (copying the
    previous column's number format) if it doesn't exist yet."""
    last_col = 1
    for c in range(2, ws.max_column + 1):
        header = ws.cell(row=1, column=c).value
        if header not in (None, ''):
            last_col = c
        if str(header).strip() == str(year):
            return c
    new_col = last_col + 1 if last_col > 1 else 2
    ws.cell(row=1, column=new_col, value=str(year)).font = Font(bold=True)
    return new_col


def set_cell(ws, row, col, value, number_format):
    cell = ws.cell(row=row, column=col)
    cell.value = round(value, 2) if isinstance(value, (int, float)) else value
    cell.number_format = number_format
    return cell


def cell_number(ws, row, col):
    if row is None:
        return None
    val = ws.cell(row=row, column=col).value
    if val is None or val == '':
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Mode 1: one-time seed from summary_investment_eoy.xlsx
# ---------------------------------------------------------------------------

def seed_from_legacy_workbook():
    if not os.path.exists(LEGACY_WORKBOOK_PATH):
        print(f"Error: {LEGACY_WORKBOOK_PATH} not found - nothing to seed from.")
        sys.exit(1)

    mapping_df = load_account_mapping()
    nr_accounts, r_accounts = account_roster(mapping_df)

    legacy_wb = load_workbook(LEGACY_WORKBOOK_PATH, data_only=True)
    legacy_ws = legacy_wb['Summary']

    header_row = 2
    col_by_header = {}
    for c in range(1, legacy_ws.max_column + 1):
        header = legacy_ws.cell(row=header_row, column=c).value
        if header is not None:
            col_by_header[str(header)] = c
    year_col = col_by_header['Year']
    networth_col = col_by_header['Networth']

    year_rows = {}
    for r in range(header_row + 1, legacy_ws.max_row + 1):
        y = legacy_ws.cell(row=r, column=year_col).value
        if y is None:
            continue
        # Skip blank placeholder rows (e.g. future years pre-filled with a year
        # number but no data yet).
        if legacy_ws.cell(row=r, column=networth_col).value is None:
            continue
        try:
            year_rows[int(y)] = r
        except (TypeError, ValueError):
            continue
    years = sorted(year_rows)
    if not years:
        print("Error: could not find any populated year rows in summary_investment_eoy.xlsx.")
        sys.exit(1)

    wb = new_history_workbook(nr_accounts, r_accounts)
    sheets = {name: wb[name] for name in SHEET_NAMES}
    for ws in sheets.values():
        for i, year in enumerate(years):
            ws.cell(row=1, column=2 + i, value=str(year)).font = Font(bold=True)

    fmt = {'EndingValue': DOLLAR_FMT, 'CashFlow': DOLLAR_FMT, 'Gain': DOLLAR_FMT, 'Return': PERCENT_FMT}

    # Per-account Ending Value history (only sheet with per-account historical detail).
    ws_end = sheets['EndingValue']
    for legacy_col_name, canonical in LEGACY_ACCOUNT_COLUMN_MAP.items():
        col = col_by_header.get(legacy_col_name)
        row = find_row(ws_end, canonical)
        if col is None or row is None:
            continue
        for i, year in enumerate(years):
            val = legacy_ws.cell(row=year_rows[year], column=col).value
            if val is not None:
                set_cell(ws_end, row, 2 + i, float(val), DOLLAR_FMT)

    # Group/grand total rows, all 4 metrics, imported directly for every year.
    total_rows = {
        'NonRetirement': {name: find_row(ws, NR_TOTAL_LABEL) for name, ws in sheets.items()},
        'Retirement': {name: find_row(ws, R_TOTAL_LABEL) for name, ws in sheets.items()},
        'Grand': {name: find_row(ws, GRAND_TOTAL_LABEL) for name, ws in sheets.items()},
    }
    for group, metric_cols in LEGACY_GROUP_TOTAL_COLUMNS.items():
        for metric, legacy_col_name in metric_cols.items():
            col = col_by_header.get(legacy_col_name)
            row = total_rows[group][metric]
            if col is None or row is None:
                continue
            ws = sheets[metric]
            for i, year in enumerate(years):
                val = legacy_ws.cell(row=year_rows[year], column=col).value
                if val is not None:
                    set_cell(ws, row, 2 + i, float(val), fmt[metric])

    for ws in sheets.values():
        for col_cells in ws.columns:
            letter = get_column_letter(col_cells[0].column)
            if letter != 'A':
                ws.column_dimensions[letter].width = 13

    wb.save(PORTFOLIO_HISTORY_INPUT_PATH)
    print(f"Seeded {PORTFOLIO_HISTORY_INPUT_PATH} with {len(years)} years "
          f"({years[0]}-{years[-1]}) from {LEGACY_WORKBOOK_PATH}.")
    print(f"Accounts tracked: {len(nr_accounts)} non-retirement, {len(r_accounts)} retirement.")


# ---------------------------------------------------------------------------
# Mode 2: regular run - compute and write the current year's column
# ---------------------------------------------------------------------------

def find_input_file(directory, patterns):
    matches = []
    for pat in patterns:
        matches.extend(glob.glob(os.path.join(directory, pat)))
    if not matches:
        return None
    return max(matches, key=os.path.getmtime)


def load_latest_ending_values(mapping_df):
    """Latest Master_Account_Summary_*.csv -> {CanonicalAccount: MarketValue}, plus
    the target year inferred from the file's date suffix."""
    summary_files = glob.glob(os.path.join(FIN_OUTPUT_DIR, 'Master_Account_Summary_*.csv'))
    if not summary_files:
        print(f"Error: no Master_Account_Summary_*.csv found in {FIN_OUTPUT_DIR}. "
              f"Run portfolio_backend.py first.")
        sys.exit(1)
    latest = max(summary_files, key=os.path.getmtime)

    m = re.search(r'Master_Account_Summary_(\d{4})(\d{2})(\d{2})', os.path.basename(latest))
    target_year = int(m.group(1)) if m else datetime.now().year

    df = pd.read_csv(latest)
    df['MarketValue'] = pd.to_numeric(df['MarketValue'], errors='coerce').fillna(0)
    by_portfolio_account = df.groupby('AccountName')['MarketValue'].sum().to_dict()

    portfolio_to_canonical = dict(zip(mapping_df['PortfolioAccountName'], mapping_df['CanonicalAccount']))
    portfolio_to_canonical = {k: v for k, v in portfolio_to_canonical.items() if k}

    ending_by_canonical = {}
    unmapped = []
    for portfolio_account, market_value in by_portfolio_account.items():
        canonical = portfolio_to_canonical.get(portfolio_account)
        if canonical:
            ending_by_canonical[canonical] = ending_by_canonical.get(canonical, 0.0) + market_value
        else:
            unmapped.append((portfolio_account, market_value))

    if unmapped:
        print(f"NOTE: {len(unmapped)} account(s) in {os.path.basename(latest)} are not in "
              f"{ACCOUNT_MAPPING_PATH} and were skipped:")
        for name, val in unmapped:
            print(f"    - {name}  (${val:,.2f})")

    print(f"Using portfolio snapshot: {latest} (target year {target_year})")
    return ending_by_canonical, target_year, unmapped


def load_cashflow_by_account(mapping_df, cashflow_categories, target_year):
    """Latest Empower transactions CSV -> {CanonicalAccount: net cash flow} for
    Auto-source accounts, restricted to the configured cash-flow categories."""
    txn_path = find_input_file(EXP_INPUT_DIR, ['*_thru_*_transactions.csv', '*transactions*.csv'])
    if not txn_path:
        msg = f"No transactions CSV found in {EXP_INPUT_DIR} - Cash Flow left blank for Auto accounts this run."
        print(f"NOTE: {msg}")
        return {}, [], msg

    df = pd.read_csv(txn_path, encoding='utf-8-sig')
    df['Date'] = pd.to_datetime(df['Date'])
    df = df[df['Date'].dt.year == target_year]
    df['Category'] = df['Category'].astype(str).str.strip()
    df['Account'] = df['Account'].astype(str).str.strip()
    df['Description'] = df['Description'].astype(str)

    auto_rows = mapping_df[(mapping_df['CashFlowSource'] == 'Auto') & (mapping_df['EmpowerAccount'] != '')]
    empower_to_canonical = dict(zip(auto_rows['EmpowerAccount'], auto_rows['CanonicalAccount']))

    investment_accounts = df[df['Account'].isin(empower_to_canonical.keys())]

    # Exclude internal-mechanic rows (cash sweeps, in-kind custodian transfers)
    # regardless of Category - Empower tags these inconsistently, sometimes as
    # Transfers, and they aren't real external cash flow.
    desc_lower = investment_accounts['Description'].str.lower()
    is_internal_mechanic = desc_lower.apply(
        lambda d: any(kw in d for kw in CASHFLOW_EXCLUDE_DESCRIPTION_KEYWORDS)
    )
    excluded_rows = investment_accounts[is_internal_mechanic]
    cashflow_rows = investment_accounts[
        investment_accounts['Category'].isin(cashflow_categories) & ~is_internal_mechanic
    ]

    if not excluded_rows.empty:
        print(f"NOTE: excluded {len(excluded_rows)} internal-mechanic row(s) "
              f"(core-account sweeps / in-kind ACAT transfers) totaling "
              f"${excluded_rows['Amount'].sum():,.2f} from cash flow.")

    by_empower_account = cashflow_rows.groupby('Account')['Amount'].sum()
    cashflow_by_canonical = {}
    for empower_account, amount in by_empower_account.items():
        canonical = empower_to_canonical[empower_account]
        cashflow_by_canonical[canonical] = cashflow_by_canonical.get(canonical, 0.0) + amount

    print(f"Using transactions file: {txn_path} ({len(investment_accounts)} investment-account rows, "
          f"{len(cashflow_rows)} counted as cash flow)")

    # Nudge: flag investment-sounding Empower accounts that showed up but aren't
    # in account_mapping.csv at all, so the user can decide whether to add them.
    all_mapped_empower = set(mapping_df.loc[mapping_df['EmpowerAccount'] != '', 'EmpowerAccount'])
    seen_accounts = set(df['Account'].unique())
    unmapped_investment_like = sorted(
        a for a in seen_accounts
        if a not in all_mapped_empower
        and not any(kw in a.lower() for kw in NON_INVESTMENT_KEYWORDS)
        and ('guoqing' in a.lower() or 'min' in a.lower())
    )
    if unmapped_investment_like:
        print(f"NOTE: these Empower accounts look like they might be investment-related but "
              f"aren't in {ACCOUNT_MAPPING_PATH} - check if any should be added:")
        for a in unmapped_investment_like:
            print(f"    - {a}")

    return cashflow_by_canonical, unmapped_investment_like, None


def write_alerts_log(target_year, unmapped_accounts, unmapped_investment_like, no_txn_file_msg, row_warnings):
    """Writes financial_data/output/{year}_portfolio_alerts.txt, same plaintext-log
    convention as the expense pipeline's {year}_alerts.txt, so the dashboard can
    parse and surface these as a UI alert card instead of console-only output."""
    lines = [f"Portfolio History Alerts - generated {datetime.now().strftime('%Y-%m-%d %H:%M')}", '=' * 60, '']

    if unmapped_accounts:
        lines.append(f"ACCOUNTS SKIPPED (in Master_Account_Summary but not in {ACCOUNT_MAPPING_PATH}):")
        for name, val in unmapped_accounts:
            lines.append(f"  - {name}  (${val:,.2f})")
        lines.append('')

    if unmapped_investment_like:
        lines.append(f"POSSIBLE UNMAPPED INVESTMENT ACCOUNTS (seen in Empower transactions, "
                      f"not in {ACCOUNT_MAPPING_PATH} - check if any should be added):")
        for a in unmapped_investment_like:
            lines.append(f"  - {a}")
        lines.append('')

    if no_txn_file_msg:
        lines.append("NO TRANSACTIONS FILE FOUND:")
        lines.append(f"  - {no_txn_file_msg}")
        lines.append('')

    if row_warnings:
        lines.append(f"ROW LOOKUP WARNINGS (account in {ACCOUNT_MAPPING_PATH} but no matching row "
                      f"in Portfolio_History.xlsx):"),
        for w in row_warnings:
            lines.append(f"  - {w}")
        lines.append('')

    if len(lines) == 3:
        lines.append('No alerts - nothing unmapped or missing this run.')

    log_path = os.path.join(FIN_OUTPUT_DIR, f'{target_year}_portfolio_alerts.txt')
    with open(log_path, 'w') as f:
        f.write('\n'.join(lines))
    print(f"Wrote alerts log: {log_path}")


def run_portfolio_history_workflow():
    if not os.path.exists(PORTFOLIO_HISTORY_INPUT_PATH):
        print(f"Error: {PORTFOLIO_HISTORY_INPUT_PATH} not found. Run with --seed first:\n"
              f"    python portfolio_history_backend.py --seed")
        sys.exit(1)
    os.makedirs(FIN_OUTPUT_DIR, exist_ok=True)

    mapping_df = load_account_mapping()
    cashflow_categories = load_cashflow_categories()
    nr_accounts, r_accounts = account_roster(mapping_df)

    ending_by_canonical, target_year, unmapped_accounts = load_latest_ending_values(mapping_df)
    cashflow_by_canonical, unmapped_investment_like, no_txn_file_msg = load_cashflow_by_account(
        mapping_df, cashflow_categories, target_year)
    row_warnings = []

    wb = load_workbook(PORTFOLIO_HISTORY_INPUT_PATH)
    sheets = {name: wb[name] for name in SHEET_NAMES}
    ws_end, ws_cf, ws_gain, ws_ret = (sheets[n] for n in SHEET_NAMES)

    year_cols = {name: get_or_create_year_column(ws, target_year) for name, ws in sheets.items()}
    year_col = year_cols['EndingValue']  # same column index in every sheet, they're built in lockstep
    prior_col = year_col - 1 if year_col > 2 else None

    cashflow_source = dict(zip(mapping_df['CanonicalAccount'], mapping_df['CashFlowSource']))

    def apply_account(canonical):
        ending = ending_by_canonical.get(canonical)
        if ending is None:
            return  # account not present in this snapshot (e.g. closed) - leave untouched
        row = find_row(ws_end, canonical)
        if row is None:
            msg = f"'{canonical}' is in {ACCOUNT_MAPPING_PATH} but has no row in " \
                  f"{PORTFOLIO_HISTORY_INPUT_PATH} - re-run --seed or add it manually."
            print(f"WARNING: {msg}")
            row_warnings.append(msg)
            return

        beginning = cell_number(ws_end, row, prior_col)
        set_cell(ws_end, row, year_col, ending, DOLLAR_FMT)

        if cashflow_source.get(canonical) == 'Auto':
            cashflow = cashflow_by_canonical.get(canonical, 0.0)
            set_cell(ws_cf, row, year_col, cashflow, DOLLAR_FMT)
        else:
            # Manual account: never overwrite - use whatever's already there (0 if blank).
            existing = cell_number(ws_cf, row, year_col)
            cashflow = existing if existing is not None else 0.0

        if beginning is not None:
            gain = ending - beginning - cashflow
            denom = beginning + cashflow / 2.0
            set_cell(ws_gain, row, year_col, gain, DOLLAR_FMT)
            if abs(denom) > 1e-9:
                set_cell(ws_ret, row, year_col, gain / denom, PERCENT_FMT)

        return ending, cashflow

    def group_totals(accounts):
        end_total = cf_total = 0.0
        any_value = False
        for acct in accounts:
            result = apply_account(acct)
            if result is not None:
                ending, cashflow = result
                end_total += ending
                cf_total += cashflow
                any_value = True
        return (end_total, cf_total) if any_value else (None, None)

    nr_end, nr_cf = group_totals(nr_accounts)
    r_end, r_cf = group_totals(r_accounts)

    def write_group_total(label, ws_map, end_total, cf_total):
        if end_total is None:
            return None, None
        row = find_row(ws_end, label)
        beginning = cell_number(ws_end, row, prior_col)
        set_cell(ws_end, row, year_col, end_total, DOLLAR_FMT)
        set_cell(ws_cf, row, year_col, cf_total, DOLLAR_FMT)
        if beginning is not None:
            gain = end_total - beginning - cf_total
            denom = beginning + cf_total / 2.0
            set_cell(ws_gain, row, year_col, gain, DOLLAR_FMT)
            if abs(denom) > 1e-9:
                set_cell(ws_ret, row, year_col, gain / denom, PERCENT_FMT)
        return end_total, cf_total

    write_group_total(NR_TOTAL_LABEL, sheets, nr_end, nr_cf)
    write_group_total(R_TOTAL_LABEL, sheets, r_end, r_cf)

    grand_end = (nr_end or 0.0) + (r_end or 0.0) if (nr_end is not None or r_end is not None) else None
    grand_cf = (nr_cf or 0.0) + (r_cf or 0.0) if (nr_cf is not None or r_cf is not None) else None
    write_group_total(GRAND_TOTAL_LABEL, sheets, grand_end, grand_cf)

    wb.save(PORTFOLIO_HISTORY_OUTPUT_PATH)
    print(f"Wrote {target_year} column to {PORTFOLIO_HISTORY_OUTPUT_PATH}.")
    print("Review the output copy, then replace your working Portfolio_History.xlsx with it "
          "once you're happy with the numbers.")

    write_alerts_log(target_year, unmapped_accounts, unmapped_investment_like, no_txn_file_msg, row_warnings)


if __name__ == '__main__':
    if '--seed' in sys.argv:
        seed_from_legacy_workbook()
    else:
        run_portfolio_history_workflow()
