"""
MARK XLIX — Email integration plugin

Send email over SMTP and read the inbox over IMAP using only the standard
library. Works with Gmail (app password), Outlook, Yahoo, and most providers.

Configure in config/api_keys.json:
    "email_address":       "you@gmail.com",
    "email_app_password":  "your-16-char-app-password",
    "email_smtp":          "smtp.gmail.com",      (optional)
    "email_smtp_port":     587,                   (optional)
    "email_imap":          "imap.gmail.com",      (optional)
    "email_imap_port":     993                    (optional)

Gmail requires an App Password (Google Account → Security → App passwords)
— your normal login password will NOT work when 2-Step Verification is on.
Tool name: email_assistant
"""

import email as email_parser
import imaplib
import smtplib
import ssl
from email.header import decode_header
from email.message import EmailMessage
from email.utils import formatdate

from utils import load_config

PLUGIN = {
    "name": "email_assistant",
    "description": (
        "Sends and reads email via the user's configured email account. "
        "Use when the user asks to send an email or check/read their inbox. "
        "Requires email_address and email_app_password in config."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "send | inbox | status",
            },
            "to": {
                "type": "STRING",
                "description": "Recipient email address for 'send'"
            },
            "subject": {
                "type": "STRING",
                "description": "Email subject for 'send'"
            },
            "body": {
                "type": "STRING",
                "description": "Email body text for 'send'"
            },
            "count": {
                "type": "INTEGER",
                "description": "How many recent emails to read for 'inbox' (default: 5)"
            },
            "sender": {
                "type": "STRING",
                "description": "Optional filter — only show emails from this sender"
            },
        },
        "required": ["action"],
    },
}

_TIMEOUT = 20


def _port(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _cfg() -> dict:
    c = load_config()
    return {
        "address":   (c.get("email_address") or "").strip(),
        "password":  (c.get("email_app_password") or "").strip(),
        "smtp":      (c.get("email_smtp") or "smtp.gmail.com").strip(),
        "smtp_port": _port(c.get("email_smtp_port"), 587),
        "imap":      (c.get("email_imap") or "imap.gmail.com").strip(),
        "imap_port": _port(c.get("email_imap_port"), 993),
    }


def _configured() -> bool:
    c = _cfg()
    return bool(c["address"] and c["password"])


def _not_configured_msg() -> str:
    return (
        "Email isn't configured yet. Add 'email_address' and 'email_app_password' "
        "to config/api_keys.json. For Gmail, create an App Password in Google "
        "Account security settings — your normal password won't work."
    )


def _decode(value) -> str:
    if not value:
        return ""
    try:
        parts = decode_header(value)
        return "".join(
            part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part
            for part, enc in parts
        )
    except Exception:  # noqa: BLE001
        return str(value)


def _send_mail(to: str, subject: str, body: str) -> str:
    c = _cfg()
    msg = EmailMessage()
    msg["From"] = c["address"]
    msg["To"] = to
    msg["Subject"] = subject or "(no subject)"
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(body or "")

    context = ssl.create_default_context()
    with smtplib.SMTP(c["smtp"], c["smtp_port"], timeout=_TIMEOUT) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(c["address"], c["password"])
        server.send_message(msg)
    return f"Email sent to {to}."


def _read_inbox(count: int, sender: str | None) -> str:
    c = _cfg()
    context = ssl.create_default_context()
    with imaplib.IMAP4_SSL(c["imap"], c["imap_port"], ssl_context=context) as mail:
        mail.login(c["address"], c["password"])
        mail.select("INBOX")

        if sender:
            status, data = mail.search(None, "FROM", f'"{sender}"')
        else:
            status, data = mail.search(None, "ALL")

        if status != "OK" or not data or not data[0]:
            return "Your inbox is empty."

        ids = data[0].split()
        ids = ids[-max(1, min(count, 20)):]  # newest first slice

        lines = []
        for num in reversed(ids):  # newest → oldest
            ok, msg_data = mail.fetch(num, "(BODY.PEEK[HEADER])")
            if ok != "OK" or not msg_data:
                continue
            raw = msg_data[0][1]
            msg = email_parser.message_from_bytes(raw)
            subj = _decode(msg.get("Subject"))
            frm = _decode(msg.get("From"))
            date = _decode(msg.get("Date"))
            lines.append(f"• {subj or '(no subject)'} — from {frm}")
        return "\n".join(lines) if lines else "I couldn't read any messages."


def handle(args: dict, ctx: dict) -> str:
    ui = (ctx or {}).get("ui")
    action = (args or {}).get("action", "").strip().lower()

    if action == "status":
        if _configured():
            c = _cfg()
            return f"Email is configured for {c['address']}. I can send and read email."
        return _not_configured_msg()

    if not _configured():
        return _not_configured_msg()

    if action == "send":
        to = (args or {}).get("to", "").strip()
        if not to or "@" not in to:
            return "Who should I send it to? I need the recipient's email address."
        subject = (args or {}).get("subject", "").strip() or "(no subject)"
        body = (args or {}).get("body", "").strip()
        if not body:
            return "What should the email say? I need some message text."
        if ui and hasattr(ui, "write_log"):
            try:
                ui.write_log(f"[Email] 📧 → {to}: {subject[:40]}")
            except Exception:
                pass
        try:
            return _send_mail(to, subject, body)
        except Exception as e:  # noqa: BLE001
            return (
                f"I couldn't send the email: {e}. Check that the app password is "
                "correct and SMTP access is enabled for the account."
            )

    if action in ("inbox", "read", "check"):
        try:
            count = max(1, min(20, int((args or {}).get("count") or 5)))
        except (TypeError, ValueError):
            count = 5
        sender = (args or {}).get("sender", "").strip() or None
        try:
            result = _read_inbox(count, sender)
        except Exception as e:  # noqa: BLE001
            return (
                f"I couldn't read your inbox: {e}. Check the app password and "
                "that IMAP access is enabled."
            )
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content("INBOX", result)
            except Exception:
                pass
        if sender:
            return f"Here are the latest {count} emails from {sender}: {result}"
        return f"Here are your latest {count} emails: {result}"

    return "Unknown email action. Try: send, inbox, status."
