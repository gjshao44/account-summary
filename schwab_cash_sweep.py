"""
Phase 2/3: twice-weekly (Tue/Fri by default, configurable) cash sweep into
the sweep ETF (sweep_symbol, SGOV by default) for the accounts marked Role=sweep_target in schwab_accounts.csv.

"Sweep to $0, no buffer" (the user's explicit choice) is implemented as
shares = floor(cash_balance / live_price) -- ETFs trade as whole shares
only, so a remainder smaller than one share's price is unavoidable and is
logged rather than force-zeroed.
"""
import math
from datetime import date, timedelta

from schwab_client import (
    load_automation_config, load_state, save_state,
    get_sweep_target_accounts, get_cash_balance, get_quote, place_equity_order,
)


def is_scheduled_sweep_day(today, cfg=None):
    cfg = cfg or load_automation_config()
    return today.strftime('%A') in cfg['sweep_days']


def is_in_liquidation_blackout(today, cfg=None):
    """True from the last day of the month preceding a liquidation month
    through day `sweep_blackout_end_day` of the liquidation month (e.g.
    Mar 31 - Apr 5), so the sweep doesn't buy the sweep ETF right before or right
    after the quarterly sell-off."""
    cfg = cfg or load_automation_config()
    if today.month in cfg['liquidate_months'] and today.day <= cfg['sweep_blackout_end_day']:
        return True
    tomorrow = today + timedelta(days=1)
    return tomorrow.day == 1 and tomorrow.month in cfg['liquidate_months']


def compute_sweep_order(client, account_hash, symbol):
    cash = get_cash_balance(client, account_hash)
    price = get_quote(client, symbol)
    shares = math.floor(cash / price) if price > 0 else 0
    remainder = cash - shares * price
    return {"shares": shares, "price": price, "cash_balance": cash, "remainder": remainder}


def run_sweep(client=None, today=None):
    cfg = load_automation_config()
    today = today or date.today()

    if not cfg['automation_enabled']:
        return {"status": "SKIPPED", "reason": "automation_enabled is false"}
    if not is_scheduled_sweep_day(today, cfg):
        return {"status": "SKIPPED", "reason": f"{today} is not a scheduled sweep day"}
    if is_in_liquidation_blackout(today, cfg):
        return {"status": "SKIPPED", "reason": f"{today} is in the quarterly liquidation blackout window"}
    # Idempotency: cron fires more than once a day; only one live sweep per day.
    last_sweep = load_state().get('last_sweep') or {}
    if last_sweep.get('date') == today.isoformat() and not last_sweep.get('dry_run'):
        return {"status": "SKIPPED", "reason": f"already swept on {today}"}

    from schwab_client import get_client
    client = client or get_client()

    symbol = cfg['sweep_symbol']
    results = {}
    for canonical_name, row in get_sweep_target_accounts().items():
        order_calc = compute_sweep_order(client, row['HashValue'], symbol)
        result = place_equity_order(
            client, row['HashValue'], symbol, order_calc['shares'], 'BUY', dry_run=cfg['dry_run']
        )
        result.update(order_calc)
        results[canonical_name] = result

    state = load_state()
    state['last_sweep'] = {
        "date": today.isoformat(),
        "dry_run": cfg['dry_run'],
        "results": results,
    }
    save_state(state)
    return {"status": "OK", "results": results}


if __name__ == "__main__":
    print(run_sweep())
