"""
Thin wrapper around the `schwab-py` library used by all Schwab automation
scripts (schwab_sync_backend.py, schwab_cash_sweep.py, schwab_pmmf_liquidate.py,
schwab_cron_driver.py). Centralizes client construction, account-hash lookup,
and the dry-run/live branch for order placement so callers never touch
schwab-py or raw HTTP directly.

Token lifecycle note: Schwab hard-expires the OAuth refresh token 7 days after
creation, with no way to extend it. There is no silent workaround -- once it
lapses, re-running schwab_auth_init.py (a fresh interactive browser login) is
the only remedy. token_expiry_info() below reads the token file's own
creation_timestamp so callers can proactively alert before that happens.
"""
import csv
import json
import os
import time
from datetime import datetime, timezone

import schwab.auth
from schwab.orders.equities import equity_buy_market, equity_sell_market
from schwab.utils import Utils

from financial_paths import (
    CONFIG_DIR,
    SCHWAB_CREDENTIALS_PATH,
    SCHWAB_TOKEN_PATH,
    SCHWAB_ACCOUNTS_PATH,
    SCHWAB_AUTOMATION_CONFIG_PATH,
    SCHWAB_AUTOMATION_STATE_PATH,
    ensure_dirs,
)

REFRESH_TOKEN_LIFETIME_SECONDS = 7 * 24 * 60 * 60

DEFAULT_AUTOMATION_CONFIG = {
    "dry_run": True,
    "automation_enabled": False,
    "sweep_days": ["Tuesday", "Friday"],
    # Cron pulls a fresh positions file into input/ on these days (once,
    # on the first run that succeeds), then deletes all but the newest
    # positions_keep_files of them.
    "positions_sync_days": ["Tuesday", "Thursday"],
    "positions_keep_files": 1,
    "liquidate_months": [1, 4, 7, 10],
    # Quarterly sell retries each run through this day of the month (the
    # Fidelity transfer pulls the cash on the 5th).
    "liquidate_last_day": 4,
    # No sweep buys from the last day of the month before a liquidation month
    # through this day of the liquidation month, so cash raised for the
    # quarterly transfer isn't immediately re-swept into the sweep ETF.
    "sweep_blackout_end_day": 7,
    "sweep_symbol": "SGOV",
    "alert_email_to": "",
    "data_source_mode": "csv",
    "warn_token_days": 2,
}


def load_credentials():
    with open(SCHWAB_CREDENTIALS_PATH, 'r') as f:
        creds = json.load(f)
    missing = [k for k in ('app_key', 'app_secret', 'callback_url') if not creds.get(k)]
    if missing:
        raise RuntimeError(
            f"schwab_credentials.json is missing: {', '.join(missing)}. "
            f"Fill in {SCHWAB_CREDENTIALS_PATH} after Schwab approves the developer app."
        )
    return creds


def load_automation_config():
    if not os.path.exists(SCHWAB_AUTOMATION_CONFIG_PATH):
        return dict(DEFAULT_AUTOMATION_CONFIG)
    with open(SCHWAB_AUTOMATION_CONFIG_PATH, 'r') as f:
        cfg = json.load(f)
    merged = dict(DEFAULT_AUTOMATION_CONFIG)
    merged.update(cfg)
    return merged


def save_automation_config(cfg):
    ensure_dirs()
    with open(SCHWAB_AUTOMATION_CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)


def load_state():
    if not os.path.exists(SCHWAB_AUTOMATION_STATE_PATH):
        return {
            "last_checked_utc": None,
            "token_expires_utc": None,
            "last_sweep": None,
            "last_liquidation": None,
            "last_alert_sent_utc": None,
            "last_error": None,
        }
    with open(SCHWAB_AUTOMATION_STATE_PATH, 'r') as f:
        return json.load(f)


def save_state(state):
    ensure_dirs()
    with open(SCHWAB_AUTOMATION_STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2)


def get_client():
    """Returns an authenticated schwab-py Client, refreshing the access token
    as needed. Raises if schwab_token.json doesn't exist yet (run
    schwab_auth_init.py first) or if the refresh token has hard-expired
    (same remedy: re-run schwab_auth_init.py)."""
    creds = load_credentials()
    if not os.path.exists(SCHWAB_TOKEN_PATH):
        raise RuntimeError(
            f"No token file at {SCHWAB_TOKEN_PATH} yet. Run schwab_auth_init.py "
            f"once to complete the interactive browser login."
        )
    return schwab.auth.client_from_token_file(
        SCHWAB_TOKEN_PATH, creds['app_key'], creds['app_secret']
    )


def token_expiry_info():
    """Reads schwab_token.json's own creation_timestamp (written by schwab-py)
    and returns how close the refresh token is to its hard 7-day expiry.
    Does not make a network call."""
    if not os.path.exists(SCHWAB_TOKEN_PATH):
        return {"exists": False, "days_left": None, "expires_at_utc": None}
    with open(SCHWAB_TOKEN_PATH, 'r') as f:
        token_data = json.load(f)
    created = token_data.get('creation_timestamp')
    if created is None:
        return {"exists": True, "days_left": None, "expires_at_utc": None}
    expires_at = created + REFRESH_TOKEN_LIFETIME_SECONDS
    days_left = (expires_at - time.time()) / 86400.0
    return {
        "exists": True,
        "days_left": days_left,
        "expires_at_utc": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
    }


def get_account_hash_map():
    """CanonicalName -> hashValue, read from the hand-confirmed
    schwab_accounts.csv (populated once via schwab_account_discovery.py)."""
    if not os.path.exists(SCHWAB_ACCOUNTS_PATH):
        raise RuntimeError(
            f"{SCHWAB_ACCOUNTS_PATH} does not exist yet. Run schwab_account_discovery.py "
            f"and fill in the CanonicalName/Role/TradingEnabled columns first."
        )
    mapping = {}
    with open(SCHWAB_ACCOUNTS_PATH, 'r', newline='') as f:
        for row in csv.DictReader(f):
            mapping[row['CanonicalName']] = row
    return mapping


def get_sweep_target_accounts():
    """Rows from schwab_accounts.csv where Role == 'sweep_target' and
    TradingEnabled is truthy -- the accounts eligible for the cash sweep /
    quarterly liquidation."""
    mapping = get_account_hash_map()
    return {
        name: row for name, row in mapping.items()
        if row.get('Role') == 'sweep_target' and row.get('TradingEnabled', '').strip().lower() == 'true'
    }


def get_account_data(client, account_hash):
    """Full account response (balances + positions) for one account hash."""
    resp = client.get_account(account_hash, fields=client.Account.Fields.POSITIONS)
    resp.raise_for_status()
    return resp.json()['securitiesAccount']


def get_cash_balance(client, account_hash):
    account = get_account_data(client, account_hash)
    balances = account.get('currentBalances', {})
    # Prefer cashAvailableForTrading (settled funds actually usable) over the
    # broader cashBalance, falling back if the account type doesn't report it.
    for field in ('cashAvailableForTrading', 'cashBalance'):
        if field in balances:
            return float(balances[field])
    raise RuntimeError(f"No cash balance field found in currentBalances: {balances}")


def get_position_quantity(client, account_hash, symbol):
    account = get_account_data(client, account_hash)
    for pos in account.get('positions', []):
        if pos.get('instrument', {}).get('symbol') == symbol:
            return float(pos.get('longQuantity', 0))
    return 0.0


def get_quote(client, symbol):
    resp = client.get_quote(symbol)
    resp.raise_for_status()
    data = resp.json()[symbol]
    quote = data.get('quote', {})
    for field in ('lastPrice', 'mark', 'closePrice'):
        if quote.get(field):
            return float(quote[field])
    raise RuntimeError(f"No usable price field in quote for {symbol}: {quote}")


def place_equity_order(client, account_hash, symbol, quantity, instruction, dry_run):
    """instruction is 'BUY' or 'SELL'. quantity must be a positive whole-share
    int -- Schwab does not support fractional ETF shares. This is the single
    place the dry-run/live branch happens; every caller goes through here."""
    quantity = int(quantity)
    if quantity <= 0:
        return {"status": "SKIPPED", "reason": "quantity <= 0", "symbol": symbol, "quantity": quantity}

    if instruction == 'BUY':
        order_spec = equity_buy_market(symbol, quantity)
    elif instruction == 'SELL':
        order_spec = equity_sell_market(symbol, quantity)
    else:
        raise ValueError(f"instruction must be BUY or SELL, got {instruction!r}")

    if dry_run:
        return {
            "status": "DRY_RUN",
            "account_hash": account_hash,
            "symbol": symbol,
            "quantity": quantity,
            "instruction": instruction,
        }

    resp = client.place_order(account_hash, order_spec)
    resp.raise_for_status()
    result = {
        "status": "LIVE",
        "account_hash": account_hash,
        "symbol": symbol,
        "quantity": quantity,
        "instruction": instruction,
        "http_status": resp.status_code,
    }
    # HTTP 201 only means Schwab received the order -- it can still be
    # rejected moments later (e.g. "Opening transactions are now restricted
    # for this security"), so read back the order's actual status.
    order_id = Utils(client, account_hash).extract_order_id(resp)
    result["order_id"] = order_id
    if order_id is not None:
        result.update(_wait_for_order_status(client, account_hash, order_id))
    return result


TERMINAL_ORDER_STATUSES = {"FILLED", "REJECTED", "CANCELED", "EXPIRED", "REPLACED"}


def _wait_for_order_status(client, account_hash, order_id, timeout_seconds=15):
    order = {}
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        time.sleep(2)
        resp = client.get_order(order_id, account_hash)
        if resp.status_code != 200:
            continue
        order = resp.json()
        if order.get("status") in TERMINAL_ORDER_STATUSES:
            break
    return {
        "order_status": order.get("status", "UNKNOWN"),
        "filled_quantity": order.get("filledQuantity"),
        "status_description": order.get("statusDescription", ""),
    }
