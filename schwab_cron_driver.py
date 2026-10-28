"""
The single entrypoint invoked by cron (once installed -- see Phase 4 in the
plan; NOT installed automatically). Fans out to the token-health check and,
if today qualifies, the cash sweep and/or quarterly liquidation. Idempotent:
safe to run more than once in a day, or more than once a week, without
double-acting (each sub-module tracks its own "already did this" state).

Crontab line -- hourly, 6:35-12:35 US/Pacific (9:35-15:35 ET), so a laptop
that's asleep for part of the day still gets a run in. Trading is skipped
whenever NYSE isn't open (holidays, early closes):
    35 6-12 * * 1-5  cd /home/gjshao/git/account-summary && .venv/bin/python schwab_cron_driver.py >> financial_data/output/schwab_cron.log 2>&1
"""
import sys
import traceback
from datetime import date, datetime, timezone

from schwab_client import load_automation_config, load_state, save_state, get_client
from schwab_alerts import check_token_health_and_alert_if_expiring, send_email
from schwab_cash_sweep import run_sweep
from schwab_pmmf_liquidate import run_liquidation, _NYSE


def _order_problems(job, run_result):
    """Live orders that didn't fill (rejected, canceled, still working...)."""
    problems = []
    for account, r in (run_result.get('results') or {}).items():
        if r.get('status') == 'LIVE' and r.get('order_status') != 'FILLED':
            problems.append(
                f"{job} {account}: {r['instruction']} {r['quantity']} {r['symbol']} -> "
                f"{r.get('order_status')} (order {r.get('order_id')}) {r.get('status_description', '')}"
            )
    return problems


def _market_is_open(now_utc):
    sched = _NYSE.schedule(start_date=now_utc.date().isoformat(), end_date=now_utc.date().isoformat())
    if sched.empty:
        return False
    return sched.iloc[0]['market_open'] <= now_utc <= sched.iloc[0]['market_close']


def _alert_problems(problems):
    print("PROBLEMS:\n" + "\n".join(problems), file=sys.stderr)
    # Cron runs hourly, so a lasting problem (e.g. expired token) would
    # otherwise email every run -- send each distinct problem once per day.
    state = load_state()
    summary = " | ".join(p.splitlines()[0] for p in problems)
    last = state.get('last_problem_alert') or {}
    if last.get('summary') == summary and last.get('date') == date.today().isoformat():
        print("(same problem already emailed today -- not re-sending)", file=sys.stderr)
        return
    try:
        send_email(
            subject="Schwab automation: action needed",
            body="The scheduled Schwab run hit problems:\n\n" + "\n\n".join(problems)
                 + "\n\nSee financial_data/output/schwab_cron.log for details.",
        )
    except Exception as e:
        print(f"Failed to send problem alert email: {e}", file=sys.stderr)
        return
    state['last_problem_alert'] = {"date": date.today().isoformat(), "summary": summary}
    save_state(state)


def _run_job(job, fn, client, today, problems):
    try:
        result = fn(client=client, today=today)
    except Exception:
        problems.append(f"{job} crashed:\n{traceback.format_exc()}")
        return None
    print(f"{job}: {result}")
    problems.extend(_order_problems(job, result))
    return result


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
    if not _market_is_open(datetime.now(timezone.utc)):
        save_state(state)
        print("NYSE is closed right now -- skipping sweep/liquidation.")
        return

    try:
        client = get_client()
    except Exception as e:
        state['last_error'] = f"{datetime.now(timezone.utc).isoformat()}: get_client() failed: {e}"
        save_state(state)
        print(f"FATAL: could not build Schwab client: {e}", file=sys.stderr)
        traceback.print_exc()
        _alert_problems([f"Could not connect to Schwab (token expired?): {e}"])
        return

    problems = []
    _run_job("Sweep", run_sweep, client, today, problems)
    _run_job("Liquidation", run_liquidation, client, today, problems)

    state = load_state()  # re-read since run_sweep/run_liquidation already wrote state
    state['last_checked_utc'] = datetime.now(timezone.utc).isoformat()
    state['last_error'] = (
        f"{datetime.now(timezone.utc).isoformat()}: " + " | ".join(p.splitlines()[0] for p in problems)
        if problems else None
    )
    save_state(state)
    if problems:
        _alert_problems(problems)


if __name__ == "__main__":
    main()
