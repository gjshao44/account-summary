"""
Email alerting for the one failure mode that can't be engineered away:
Schwab's OAuth refresh token hard-expires 7 days after creation, with no
extension mechanism. The only remedy is re-running schwab_auth_init.py
(a fresh interactive browser login with MFA) before that happens.

Uses stdlib smtplib + a Gmail App Password (no new dependency). Called from
schwab_cron_driver.py on every run, independent of whether today is a
scheduled trading day, so there's maximal warning time.
"""
import json
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText

from financial_paths import SCHWAB_NOTIFY_CONFIG_PATH
from schwab_client import token_expiry_info, load_automation_config, load_state, save_state


def _load_notify_config():
    with open(SCHWAB_NOTIFY_CONFIG_PATH, 'r') as f:
        cfg = json.load(f)
    missing = [k for k in ('sender_address', 'app_password', 'recipient') if not cfg.get(k)]
    if missing:
        raise RuntimeError(
            f"schwab_notify.json is missing: {', '.join(missing)}. "
            f"Fill in {SCHWAB_NOTIFY_CONFIG_PATH} with your Gmail App Password."
        )
    return cfg


def send_email(subject, body):
    cfg = _load_notify_config()
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = cfg['sender_address']
    msg['To'] = cfg['recipient']

    with smtplib.SMTP(cfg['smtp_host'], cfg['smtp_port']) as server:
        server.starttls()
        server.login(cfg['sender_address'], cfg['app_password'])
        server.send_message(msg)


def check_token_health_and_alert_if_expiring(warn_threshold_days=None):
    cfg = load_automation_config()
    threshold = warn_threshold_days if warn_threshold_days is not None else cfg['warn_token_days']

    info = token_expiry_info()
    state = load_state()
    state['token_expires_utc'] = info.get('expires_at_utc')

    if not info['exists']:
        save_state(state)
        return info

    if info['days_left'] is not None and info['days_left'] <= threshold:
        last_sent = state.get('last_alert_sent_utc')
        already_sent_recently = False
        if last_sent:
            hours_since = (datetime.now(timezone.utc) - datetime.fromisoformat(last_sent)).total_seconds() / 3600
            already_sent_recently = hours_since < 20  # at most once per day

        if not already_sent_recently:
            send_email(
                subject="Schwab API token expiring soon",
                body=(
                    f"The Schwab refresh token expires in {info['days_left']:.1f} day(s) "
                    f"(at {info['expires_at_utc']}).\n\n"
                    f"Run schwab_auth_init.py to re-authenticate before then, or the "
                    f"Tue/Fri cash sweep and quarterly sweep-ETF liquidation will silently stop working."
                ),
            )
            state['last_alert_sent_utc'] = datetime.now(timezone.utc).isoformat()

    save_state(state)
    return info


if __name__ == "__main__":
    print(check_token_health_and_alert_if_expiring())
