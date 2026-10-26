"""
The single entrypoint invoked by cron (once installed -- see Phase 4 in the
plan; NOT installed automatically). Fans out to the token-health check and,
if today qualifies, the cash sweep and/or quarterly liquidation. Idempotent:
safe to run more than once in a day, or more than once a week, without
double-acting (each sub-module tracks its own "already did this" state).

Intended crontab line (installed manually, after Phase 3 validation):
    0 9,15 * * 1-5  cd /home/gjshao/git/account-summary && .venv/bin/python schwab_cron_driver.py >> financial_data/output/schwab_cron.log 2>&1
"""
import sys
import traceback
from datetime import date, datetime, timezone

from schwab_client import load_automation_config, load_state, save_state, get_client
from schwab_alerts import check_token_health_and_alert_if_expiring
from schwab_cash_sweep import run_sweep
from schwab_pmmf_liquidate import run_liquidation


def main():
    cfg = load_automation_config()
    state = load_state()
    state['last_checked_utc'] = datetime.now(timezone.utc).isoformat()

    if not cfg['automation_enabled']:
        state['last_error'] = None
        save_state(state)
        print("automation_enabled is false -- skipping (token health check still runs).")
        try:
            check_token_health_and_alert_if_expiring()
        except Exception as e:
            print(f"Token health check failed: {e}")
        return

    try:
        check_token_health_and_alert_if_expiring()
    except Exception as e:
        print(f"Token health check failed: {e}")

    today = date.today()
    try:
        client = get_client()
    except Exception as e:
        state['last_error'] = f"{datetime.now(timezone.utc).isoformat()}: get_client() failed: {e}"
        save_state(state)
        print(f"FATAL: could not build Schwab client: {e}", file=sys.stderr)
        traceback.print_exc()
        return

    sweep_result = run_sweep(client=client, today=today)
    print(f"Sweep: {sweep_result}")

    liquidation_result = run_liquidation(client=client, today=today)
    print(f"Liquidation: {liquidation_result}")

    state = load_state()  # re-read since run_sweep/run_liquidation already wrote state
    state['last_error'] = None
    save_state(state)


if __name__ == "__main__":
    main()
