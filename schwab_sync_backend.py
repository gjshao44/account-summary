"""
Phase 1: pulls positions from the Schwab Trader API and writes them into
financial_data/input/ using the EXACT filename and column conventions portfolio_backend.py already globs for and
parses (see parse_schwab_multi_account_csv() and run_portfolio_workflow()).
This means nothing downstream of input/ needs to change -- the API sync's
only job is "fetch, then drop a correctly-shaped file before the existing
pipeline runs."

Income is NOT pulled from the API -- keep downloading the Schwab
"Income Summary -- next 12 months" CSV (XXXX*_InvestmentIncome_*.CSV) by
hand. The Trader API has no forward income projection, and its
DIVIDEND_OR_INTEREST transactions carry only a CURRENCY_USD instrument (the
security appears just as a name in `description`), so they can't be tied
back to a ticker reliably.
"""
import os
from datetime import datetime

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
            # API uses BRK.B; the CSV export (and asset_allocation_mapping) use BRK/B.
            symbol = instrument.get('symbol', '').replace('.', '/')
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


def _write_positions_csv(path, blocks, as_of, account_map):
    ensure_dirs()
    with open(path, 'w', newline='') as f:
        f.write(_quote(f"Positions for All-Accounts as of {as_of}") + "\n\n")
        for canonical_name, rows in blocks.items():
            # parse_schwab_multi_account_csv() detects each account block by
            # the "Name ...123" header, so the masked number is required.
            f.write(_quote(f"{canonical_name} {account_map[canonical_name]['MaskedNumber']}") + "\n")
            f.write(",".join(POSITION_COLUMNS) + ",\n")
            for r in rows:
                f.write(",".join(_quote(r[c]) for c in
                                  ['Symbol', 'Description', 'Qty (Quantity)', 'Mkt Val (Market Value)', 'Cost Basis']) + ",\n")
            f.write("\n")


def run_schwab_api_sync():
    client = get_client()
    account_map = get_account_hash_map()

    now = datetime.now()
    as_of_str = now.strftime('%I:%M %p, %m/%d/%Y')
    filename_ts = now.strftime('%Y-%m-%d-%H%M%S')

    positions_blocks = fetch_all_positions(client, account_map)
    positions_path = os.path.join(INPUT_DIR, f"All-Accounts-Positions-{filename_ts}.csv")
    _write_positions_csv(positions_path, positions_blocks, as_of_str, account_map)
    print(f"Wrote {positions_path}")


if __name__ == "__main__":
    run_schwab_api_sync()
