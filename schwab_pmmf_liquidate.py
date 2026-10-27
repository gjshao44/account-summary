"""
Phase 2/3: from the first NYSE trading day of each configured quarter-start
month (Jan/Apr/Jul/Oct by default) through day `liquidate_last_day` (the
4th by default -- the user's quarterly transfer to Fidelity pulls the cash
on the 5th), sell 100% of the sweep ETF (sweep_symbol) position back to cash
in every Role=sweep_target account.

Each cron run retries any account whose sell hasn't FILLED yet this quarter
(laptop off, crash, rejected order...), so one missed run doesn't skip the
whole quarter. Failures still email via schwab_cron_driver.py.

Uses pandas_market_calendars for the NYSE holiday calendar rather than a
hand-rolled holiday list, since "first trading day of the month" is wrong
if it naively assumes the 1st is always a trading day (e.g. Jan 1 is always
a market holiday).
"""
from datetime import date, datetime

import pandas_market_calendars as mcal

from schwab_client import (
    load_automation_config, load_state, save_state,
    get_sweep_target_accounts, get_position_quantity, place_equity_order,
    TERMINAL_ORDER_STATUSES,
)

_NYSE = mcal.get_calendar('NYSE')


def _quarter_label(today):
    return f"{today.year}-Q{(today.month - 1) // 3 + 1}"


def is_in_liquidation_window(today, cfg=None):
    """True on any NYSE trading day from the 1st through liquidate_last_day
    of a liquidation month."""
    cfg = cfg or load_automation_config()
    if today.month not in cfg['liquidate_months'] or today.day > cfg['liquidate_last_day']:
        return False
    return len(_NYSE.valid_days(start_date=today.isoformat(), end_date=today.isoformat())) > 0


def _has_open_sell(client, account_hash, symbol, today):
    """Guards against stacking a second sell on top of one still working
    from an earlier run today."""
    resp = client.get_orders_for_account(
        account_hash, from_entered_datetime=datetime.combine(today, datetime.min.time()))
    resp.raise_for_status()
    for o in resp.json():
        leg = o['orderLegCollection'][0]
        if (leg['instrument'].get('symbol') == symbol and leg['instruction'] == 'SELL'
                and o.get('status') not in TERMINAL_ORDER_STATUSES):
            return True
    return False


def run_liquidation(client=None, today=None):
    cfg = load_automation_config()
    today = today or date.today()
    state = load_state()
    quarter = _quarter_label(today)

    if not cfg['automation_enabled']:
        return {"status": "SKIPPED", "reason": "automation_enabled is false"}
    if not is_in_liquidation_window(today, cfg):
        return {"status": "SKIPPED", "reason": f"{today} is not in a quarterly liquidation window"}

    # Carry over this quarter's progress; a new quarter starts fresh.
    last = state.get('last_liquidation') or {}
    if last.get('quarter') != quarter or last.get('dry_run') != cfg['dry_run']:
        last = {"quarter": quarter, "dry_run": cfg['dry_run'], "done_accounts": [], "results": {}}
    done = set(last.get('done_accounts', []))

    pending = {n: r for n, r in get_sweep_target_accounts().items() if n not in done}
    if not pending:
        return {"status": "SKIPPED", "reason": f"{quarter} already liquidated"}

    from schwab_client import get_client
    client = client or get_client()

    symbol = cfg['sweep_symbol']
    results = {}
    for canonical_name, row in pending.items():
        if not cfg['dry_run'] and _has_open_sell(client, row['HashValue'], symbol, today):
            results[canonical_name] = {"status": "SKIPPED", "reason": "an earlier sell is still open"}
            continue
        shares = get_position_quantity(client, row['HashValue'], symbol)
        result = place_equity_order(
            client, row['HashValue'], symbol, shares, 'SELL', dry_run=cfg['dry_run']
        )
        results[canonical_name] = result
        # Done = nothing left to sell, a dry run, or a live sell that FILLED.
        # Anything else (rejected, still working) is retried next run.
        if result['status'] != 'LIVE' or result.get('order_status') == 'FILLED':
            done.add(canonical_name)

    last.update({"date": today.isoformat(), "done_accounts": sorted(done)})
    last['results'].update(results)
    state['last_liquidation'] = last
    save_state(state)
    return {"status": "OK", "results": results}


if __name__ == "__main__":
    print(run_liquidation())
