import os
import json
from datetime import datetime, timezone
from typing import List, Dict, Any
from zoneinfo import ZoneInfo

import gspread
import requests


# =========================
# Config (env or defaults)
# =========================
SHEET_NAME   = os.getenv("SHEET_NAME", "Trading Log")
LOG_TAB      = os.getenv("LOG_TAB", "log")

# Mailgun
MAILGUN_API_KEY  = os.getenv("MAILGUN_API_KEY")
MAILGUN_DOMAIN   = os.getenv("MAILGUN_DOMAIN")  # e.g. mg.yourdomain.com
MAILGUN_BASE_URL = os.getenv("MAILGUN_BASE_URL", "https://api.mailgun.net")  # use https://api.eu.mailgun.net if needed
EMAIL_FROM       = os.getenv("EMAIL_FROM")  # e.g. alerts@yourdomain.com
EMAIL_TO         = [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()]

# Timezone & sending behavior
LOCAL_TZ      = os.getenv("LOCAL_TZ", "America/Denver")
EXIT_IF_EMPTY = os.getenv("EXIT_IF_EMPTY", "false").lower() in ("1","true","yes")


# =========================
# Helpers
# =========================
def now_iso_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_z(s: str) -> datetime:
    # Accepts "YYYY-MM-DDTHH:MM:SSZ"
    if not s:
        raise ValueError("Empty timestamp")
    if s.endswith("Z"):
        s = s[:-1]
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def get_google_client():
    raw = os.getenv("GOOGLE_CREDS_JSON")
    if not raw:
        raise RuntimeError("Missing GOOGLE_CREDS_JSON env var.")
    creds = json.loads(raw)
    return gspread.service_account_from_dict(creds)


def _get_ws(gc, sheet_name, tab):
    sh = gc.open(sheet_name)
    try:
        return sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=tab, rows="2000", cols="20")


def read_log_rows(ws_log) -> List[Dict[str, Any]]:
    values = ws_log.get_all_values()
    if not values:
        return []
    header = [h.strip() for h in values[0]]
    rows = []
    for r in values[1:]:
        if not any(r):
            continue
        obj = {header[i]: (r[i] if i < len(r) else "") for i in range(len(header))}
        rows.append(obj)
    return rows


def rows_for_today_local(rows: List[Dict[str, Any]], tzname: str) -> List[Dict[str, Any]]:
    tz = ZoneInfo(tzname)
    today_local = datetime.now(tz).date()
    out = []
    for row in rows:
        ts = row.get("Timestamp") or row.get("Time") or ""
        try:
            dt_utc = parse_iso_z(ts)  # sheet writes Z (UTC)
            dt_local = dt_utc.astimezone(tz)
            if dt_local.date() == today_local:
                out.append(row)
        except Exception:
            continue
    return out


def parse_gain_pct_from_note(note: str) -> float | None:
    # expects "Gain X%" anywhere in the note
    if not note:
        return None
    lower = note.lower()
    if "gain" not in lower or "%" not in lower:
        return None
    try:
        frag = lower.split("gain", 1)[1]
        num = ""
        for ch in frag:
            if ch in "0123456789.-":
                num += ch
            elif num and ch not in "0123456789.-":
                break
        return float(num) if num else None
    except Exception:
        return None


def profit_from_sell_row(row: Dict[str, Any]) -> float | None:
    """
    Estimate profit from a SELL log row using:
      NotionalUSD (market_value) and Note "Gain X%"
    Profit = market_value * (g / (1+g)) where g = X/100.
    """
    if (row.get("Action") or "").upper() != "SELL":
        return None
    try:
        mv = float((row.get("NotionalUSD") or "").replace("$","").replace(",",""))
    except Exception:
        return None
    g_pct = parse_gain_pct_from_note(row.get("Note") or "")
    if g_pct is None:
        return None
    g = g_pct / 100.0
    return mv * (g / (1.0 + g))


def format_usd(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):.2f}"


def send_mailgun(subject: str, html_body: str = "&nbsp;"):
    if not (MAILGUN_API_KEY and MAILGUN_DOMAIN and EMAIL_FROM and EMAIL_TO):
        raise RuntimeError("Missing one of MAILGUN_API_KEY, MAILGUN_DOMAIN, EMAIL_FROM, or EMAIL_TO.")

    url = f"{MAILGUN_BASE_URL}/v3/{MAILGUN_DOMAIN}/messages"
    data = {
        "from": EMAIL_FROM,
        "to": EMAIL_TO,           # list or comma-separated OK
        "subject": subject,
        "text": " ",              # minimal text part
        "html": html_body,        # minimal html part
    }
    resp = requests.post(url, auth=("api", MAILGUN_API_KEY), data=data, timeout=30)
    if resp.status_code >= 300:
        raise RuntimeError(f"Mailgun send failed: {resp.status_code} {resp.text}")


# =========================
# Main
# =========================
def main():
    print("✉️  Mailgun notifier (subject-only summary) starting")

    gc = get_google_client()
    ws_log = _get_ws(gc, SHEET_NAME, LOG_TAB)

    rows = read_log_rows(ws_log)
    today_rows = rows_for_today_local(rows, LOCAL_TZ)

    buys = [r for r in today_rows if (r.get("Action","").upper() == "BUY")]
    sell_profits = [p for p in (profit_from_sell_row(r) for r in today_rows) if p is not None]
    total_profit = sum(sell_profits) if sell_profits else 0.0

    bought_count = len(buys)
    profit_str = format_usd(total_profit)

    if EXIT_IF_EMPTY and bought_count == 0 and abs(total_profit) < 0.005:
        print("ℹ️ No buys today and $0 profit — skipping email (EXIT_IF_EMPTY=true).")
        return

    subject = f"bought {bought_count} stocks, sold {profit_str} profit"
    send_mailgun(subject, html_body="&nbsp;")

    print(f"✅ Email sent with subject: {subject}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print("❌ Fatal error:", e)
        traceback.print_exc()
