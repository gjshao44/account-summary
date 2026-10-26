"""
One-time (read-only) helper: lists every Schwab account linked to this API
app, showing the masked account number next to the opaque hashValue the API
requires for all balance/position/order calls.

This script NEVER writes financial_data/config/schwab_accounts.csv itself --
you must manually transcribe the hashValue for ...568 and ...985 (and any
other accounts you want synced/traded) into that file yourself. This is a
deliberate checkpoint: nothing should be marked TradingEnabled=true without
you personally confirming which hash maps to which real account.

Usage:
    .venv/bin/python schwab_account_discovery.py
"""
from financial_paths import SCHWAB_ACCOUNTS_PATH
from schwab_client import get_client


def main():
    client = get_client()
    resp = client.get_account_numbers()
    resp.raise_for_status()
    pairs = resp.json()  # [{"accountNumber": "...", "hashValue": "..."}]

    if not pairs:
        print("No accounts returned -- check that the app has Accounts & Trading access.")
        return

    print(f"Found {len(pairs)} linked account(s):\n")
    for p in pairs:
        acct_num = p['accountNumber']
        masked = f"...{acct_num[-3:]}"
        print(f"  masked number: {masked:>8}   hashValue: {p['hashValue']}")

    print(f"\nNow manually edit {SCHWAB_ACCOUNTS_PATH} with rows like:\n")
    print("CanonicalName,MaskedNumber,HashValue,Role,TradingEnabled")
    print("Guoqing_Invest,...568,<hashValue from above>,sweep_target,true")
    print("Min_Invest,...985,<hashValue from above>,sweep_target,true")
    print("\nOnly set Role=sweep_target and TradingEnabled=true for accounts")
    print("you've personally verified against the masked number above.")


if __name__ == "__main__":
    main()
