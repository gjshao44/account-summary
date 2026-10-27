"""
Phase 2/3: on the first NYSE trading day of each configured quarter-start
month (Jan/Apr/Jul/Oct by default), sell 100% of the sweep ETF (sweep_symbol) position back to
cash in every Role=sweep_target account -- ahead of the user's recurring
quarterly transfer to Fidelity for expenses.

Uses pandas_market_calendars for the NYSE holiday calendar rather than a
hand-rolled holiday list, since "first trading day of the month" is wrong
if it naively assumes the 1st is always a trading day (e.g. Jan 1 is always
a market holiday).
"""
from datetime import date

import pandas_market_calendars as mcal

from schwab_client import (
    load_automation_config, load_state, save_state,
    get_sweep_target_accounts, get_position_quantity, place_equity_order,
)

_NYSE = mcal.get_calendar('NYSE')


def _quarter_label(today):
    return f"{today.year}-Q{(today.month - 1) // 3 + 1}"


def is_first_trading_day_of_quarter(today, cfg=None, state=None):
    cfg = cfg or load_automation_config()
    if today.month not in cfg['liquidate_months']:
        return False

    month_start = today.replace(day=1)
    # Look across a window wide enough to cover any holiday run (e.g. a
    # multi-day closure) at the start of the month.
    valid_days = _NYSE.valid_days(start_date=month_start.isoformat(),
                                   end_date=today.replace(day=10).isoformat())
    if len(valid_days) == 0 or valid_days[0].date() != today:
        return False

    # Idempotency: don't re-fire if this quarter was already liquidated
    # (e.g. cron checks run more than once on the same day, or the first
    # trading day was somehow re-evaluated).
    state = state or load_state()
    last_liquidation = state.get('last_liquidation') or {}
    if last_liquidation.get('quarter') == _quarter_label(today):
        return False

    return True


def run_liquidation(client=None, today=None):
    cfg = load_automation_config()
    today = today or date.today()
    state = load_state()

    if not cfg['automation_enabled']:
        return {"status": "SKIPPED", "reason": "automation_enabled is false"}
    if not is_first_trading_day_of_quarter(today, cfg, state):
        return {"status": "SKIPPED", "reason": f"{today} is not the first trading day of a liquidation quarter"}

    from schwab_client import get_client
    client = client or get_client()

    symbol = cfg['sweep_symbol']
    results = {}
    for canonical_name, row in get_sweep_target_accounts().items():
        shares = get_position_quantity(client, row['HashValue'], symbol)
        result = place_equity_order(
            client, row['HashValue'], symbol, shares, 'SELL', dry_run=cfg['dry_run']
        )
        results[canonical_name] = result

    state['last_liquidation'] = {
        "date": today.isoformat(),
        "quarter": _quarter_label(today),
        "dry_run": cfg['dry_run'],
        "results": results,
    }
    save_state(state)
    return {"status": "OK", "results": results}


if __name__ == "__main__":
    print(run_liquidation())
