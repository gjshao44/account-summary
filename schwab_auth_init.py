"""
One-time (and occasional re-run) interactive Schwab OAuth login.

Run this manually whenever:
  - Setting up Schwab API access for the first time, or
  - The refresh token has hard-expired (Schwab caps it at 7 days with no
    workaround -- schwab_alerts.py emails a warning a couple days before
    this happens so you can re-run this before anything silently breaks).

This is NEVER invoked by cron -- Schwab's login requires your own MFA, which
cannot be scripted. Usage:

    .venv/bin/python schwab_auth_init.py

It will print a URL. Open it, log in to Schwab, approve the app, and your
browser will be redirected to the callback URL (https://127.0.0.1/...) --
the page will likely show a "can't reach this site" error, which is
expected (nothing is listening on that port). Copy the FULL resulting URL
from your browser's address bar and paste it back into this script when
prompted.
"""
import schwab.auth

from financial_paths import SCHWAB_TOKEN_PATH, ensure_dirs
from schwab_client import load_credentials


def main():
    ensure_dirs()
    creds = load_credentials()

    print("Opening the Schwab login flow. If a browser does not open automatically,")
    print("copy the printed URL manually.\n")

    client = schwab.auth.client_from_manual_flow(
        api_key=creds['app_key'],
        app_secret=creds['app_secret'],
        callback_url=creds['callback_url'],
        token_path=SCHWAB_TOKEN_PATH,
    )

    print(f"\nSuccess. Token written to {SCHWAB_TOKEN_PATH}.")
    print("This refresh token is valid for 7 days. You'll get an email alert")
    print("before it expires once schwab_cron_driver.py is running.")

    # Sanity check: confirm the token actually works before declaring success.
    resp = client.get_account_numbers()
    resp.raise_for_status()
    accounts = resp.json()
    print(f"\nVerified: found {len(accounts)} linked account(s).")
    print("Next step: run schwab_account_discovery.py to map these to your own labels.")


if __name__ == "__main__":
    main()
