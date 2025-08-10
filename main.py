import os
import json
from datetime import datetime, timezone, date
from typing import List, Dict, Any
from zoneinfo import ZoneInfo

import gspread
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail


# =========================
# Config (env or defaults)
# =========================
SHEET_NAME   = os.getenv("SHEET_NAME", "Trading Log")
LOG_TAB      = os.getenv("LOG_TAB", "log")

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
EMAIL_FROM       = os.getenv("EMAIL_FROM")          # e.g. alerts@yourdomain.com
EMAIL_TO         = [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()]
SUBJECT_PREFIX   = os.getenv("SUBJECT_PREFIX", "Aletheia")
LOCAL_TZ         = os.getenv("LOCAL_TZ", "America/Denver")
EXIT_IF_EMPTY    = os.getenv("EXIT_IF_EMPTY", "false").lower() in ("1","true","yes")  # default: always send


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
            dt_utc = parse_iso_z(ts)  # sheet writes Zulu
            dt_local = dt_utc.astimezone(tz)
            if dt_local.date() == today_local:
                out.append(row)
        except Exception:
            continue
    return out


def parse_gain_pct_from_note(note: str) -> float | None:
    # expects like "Gain 5.23%" anywhere in the note
    if not note:
        return None
    note = note.strip()
    # very small parser; no regex to keep deps minimal
    lower = note.lower()
    if "gain" in lower and "%" in lower:
        try:
            frag = lower.split("gain", 1)[1]
            num = ""
            for ch in frag:
                if ch in "0123456789.-":
                    num += ch
                elif num and ch not in "0123456789.-":
                    break
            if num:
                return float(num)
        except Exception:
            return None
    return None


def profit_from_sell_row(row: Dict[str, Any]) -> float | None:
    """
    Estimate profit from a SELL log row.
    Uses NotionalUSD (market_value) and Note "Gain X%".
    Profit = market_value * (g / (1+g)) where g = X/100.
    """
    action = (row.get("Action") or "").upper()
    if action != "SELL":
        return None
    notional = row.get("NotionalUSD") or ""
    note = row.get("Note") or ""
    try:
        mv = float(notional.replace("$","").replace(",",""))
    except Exception:
        return None
    g_pct = parse_gain_pct_from_note(note)
    if g_pct is None:
        return None
    g = g_pct / 100.0
    return mv * (g / (1.0 + g))


def format_usd(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):.2f}"


def send_email_sendgrid(subject: str, html_body: str = "&nbsp;"):
    if not SENDGRID_API_KEY:
        raise RuntimeError("Missing SENDGRID_API_KEY env var.")
    if not EMAIL_FROM or not EMAIL_TO:
        raise RuntimeError("EMAIL_FROM and EMAIL_TO are required.")

    sg = SendGridAPIClient(SENDGRID_API_KEY)
    mail = Mail(
        from_email=EMAIL_FROM,
        to_emails=EMAIL_TO,
        subject=subject,
        html_content=html_body  # minimal body so notifications focus on subject
    )
    resp = sg.client.mail.send.post(request_body=mail.get())
    if resp.status_code >= 300:
        raise RuntimeError(f"SendGrid
