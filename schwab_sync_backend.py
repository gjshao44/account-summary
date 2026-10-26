"""
Phase 1: pulls positions and trailing dividend/interest income from the
Schwab Trader API and writes them into financial_data/input/ using the EXACT
filename and column conventions portfolio_backend.py already globs for and
parses (see parse_schwab_multi_account_csv() and run_portfolio_workflow()).
This means nothing downstream of input/ needs to change -- the API sync's
only job is "fetch, then drop a correctly-shaped file before the existing
pipeline runs."

KNOWN GAP (flagged in the plan, not yet resolved): Schwab's Trader API has no
confirmed endpoint matching the retail UI's "Income Summary -- next 12
months" forward projection. fetch_projected_income() below instead sums each
account's ACTUAL dividend/interest transactions over the trailing 12 months
as the closest available proxy. This is logged explicitly (method="trailing_12mo_actual")
so a future maintainer knows the income figures are a trailing-realized
proxy, not Schwab's own forward projection, until/unless a real projection
endpoint is confirmed to exist.
"""
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from financial_paths import INPUT_DIR, ensure_dirs
from schwab_client import get_client, get_account_hash_map, get_account_data

POSITION_COLUMNS = [
    '"Symbol"', '"Description"', '"Qty (Quantity)"', '"Mkt Val (Market Value)"', '"Cost Basis"'
]


def _quote(value):
    return '"' + str(value).replace('"', '""') + '"'


def fetch_all_positions(client, account_map):
    """Returns {CanonicalName: list_of_position_dict} for every account in
    schwab_accounts.csv (not just sweep targets -- Role is irrelevant here,
    any linked account gets synced for the read-only portfolio summary)."""
    blocks = {}
    for canonical_name, row in account_map.items():
        account = get_account_data(client, row['HashValue'])
        balances = account.get('currentBalances', {})
        rows = []
        for pos in account.get('positions', []):
            instrument = pos.get('instrument', {})
            symbol = instrument.get('symbol', '')
            qty = pos.get('longQuantity', 0) or 0
            market_value = pos.get('marketValue', 0) or 0
            # NOTE: averageCostBasis here is assumed to be a per-share figure
            # (consistent with schwab-py's documented position shape at the
            # time this was written) -- verify against a real response during
            # Phase 1 validation; if Schwab instead reports an aggregate cost
            # basis directly, drop the qty multiplication below.
            avg_cost = pos.get('averageCostBasis') or pos.get('averagePrice') or 0
            cost_basis = qty * avg_cost
            rows.append({
                'Symbol': symbol,
                'Description': instrument.get('description', symbol),
                'Qty (Quantity)': qty,
                'Mkt Val (Market Value)': market_value,
                'Cost Basis': cost_basis,
            })

        cash = balances.get('cashAvailableForTrading') or balances.get('cashBalance') or 0
        if cash:
            rows.append({
                'Symbol': 'Cash & Cash Investments',
                'Description': 'Cash & Cash Investments',
                'Qty (Quantity)': 1,
                'Mkt Val (Market Value)': cash,
                'Cost Basis': cash,
            })
        blocks[canonical_name] = rows
    return blocks


def fetch_projected_income(client, account_map, lookback_days=365):
    """Trailing-12-month DIVIDEND_OR_INTEREST transactions per symbol/account,
    used as a proxy for the "next 12 months" projection -- see module
    docstring. Returns a DataFrame matching the income CSV's post-parse shape:
    columns Symbol, AccountName, AnnualIncome."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    records = []
    for canonical_name, row in account_map.items():
        resp = client.get_transactions(
            row['HashValue'],
            start_date=start,
            end_date=end,
            transaction_types=client.Transactions.TransactionType.DIVIDEND_OR_INTEREST,
        )
        resp.raise_for_status()
        for txn in resp.json():
            amount = txn.get('netAmount')
            symbol = None
            for item in txn.get('transferItems', []):
                instrument = item.get('instrument', {})
                if instrument.get('symbol'):
                    symbol = instrument['symbol']
                    if amount is None:
                        amount = item.get('amount') or item.get('cost')
                    break
            if symbol is None or amount is None:
                print(f"WARNING: unrecognized transaction shape, skipping: {txn}")
                continue
            records.append({'Symbol': symbol, 'AccountName': canonical_name, 'Amount': float(amount)})

    if not records:
        return pd.DataFrame(columns=['Transaction Date', 'Account Number', 'Account Name',
                                      'Account Type', 'Security Description', 'Symbol',
                                      'Security Type', 'Transaction Type', 'Transaction Amount',
                                      'Income Type'])

    df = pd.DataFrame(records)
    # Emit one row per symbol/account (already trailing-12mo summed amounts
    # are fine as individual rows too, since run_portfolio_workflow() groups
    # and sums again -- but summing here keeps the output file small).
    grouped = df.groupby(['Symbol', 'AccountName'])['Amount'].sum().reset_index()

    out_rows = []
    for _, r in grouped.iterrows():
        out_rows.append({
            'Transaction Date': end.strftime('%m/%d/%Y'),
            'Account Number': '',
            'Account Name': r['AccountName'],
            'Account Type': '',
            'Security Description': '',
            'Symbol': r['Symbol'],
            'Security Type': '',
            'Transaction Type': 'Dividend',
            'Transaction Amount': r['Amount'],
            'Income Type': 'Dividend',
        })
    return pd.DataFrame(out_rows)


def _write_positions_csv(path, blocks, as_of):
    ensure_dirs()
    with open(path, 'w', newline='') as f:
        f.write(_quote(f"Positions for All-Accounts as of {as_of}") + "\n\n")
        for canonical_name, rows in blocks.items():
            f.write(_quote(canonical_name) + "\n")
            f.write(",".join(POSITION_COLUMNS) + ",\n")
            for r in rows:
                f.write(",".join(_quote(r[c]) for c in
                                  ['Symbol', 'Description', 'Qty (Quantity)', 'Mkt Val (Market Value)', 'Cost Basis']) + ",\n")
            f.write("\n")


def _write_income_csv(path, income_df, as_of):
    ensure_dirs()
    with open(path, 'w', newline='') as f:
        f.write(f"Investment Income Transactions as of {as_of}\n")
        income_df.to_csv(f, index=False)


def run_schwab_api_sync():
    client = get_client()
    account_map = get_account_hash_map()

    now = datetime.now()
    as_of_str = now.strftime('%I:%M %p, %m/%d/%Y')
    filename_ts = now.strftime('%Y-%m-%d-%H%M%S')

    positions_blocks = fetch_all_positions(client, account_map)
    positions_path = os.path.join(INPUT_DIR, f"All-Accounts-Positions-{filename_ts}.csv")
    _write_positions_csv(positions_path, positions_blocks, as_of_str)
    print(f"Wrote {positions_path}")

    income_df = fetch_projected_income(client, account_map)
    income_path = os.path.join(INPUT_DIR, f"XXXX000_InvestmentIncome_{now.strftime('%Y%m%d-%H%M%S')}.CSV")
    _write_income_csv(income_path, income_df, as_of_str)
    print(f"Wrote {income_path} (method=trailing_12mo_actual -- see module docstring)")


if __name__ == "__main__":
    run_schwab_api_sync()
