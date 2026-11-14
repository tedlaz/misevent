"""Αποστολή email (stdlib `smtplib`) — προσκλήσεις, επαναφορά κωδικού, εγγραφή.

Ρυθμίζεται ΜΟΝΟ από το περιβάλλον (`.env` του docker compose):
    MISEVENT_SMTP_HOST  MISEVENT_SMTP_PORT (587)  MISEVENT_SMTP_USER
    MISEVENT_SMTP_PASSWORD  MISEVENT_SMTP_FROM
Χωρίς `MISEVENT_SMTP_HOST` δεν στέλνεται τίποτα (desktop): οι σύνδεσμοι
φαίνονται στην οθόνη, όπως πάντα.
"""

import os
import smtplib
from email.message import EmailMessage


def configured() -> bool:
    return bool(os.environ.get("MISEVENT_SMTP_HOST"))


def send(to: str, subject: str, body: str) -> bool:
    """True αν στάλθηκε. Σφάλμα SMTP -> False (ο σύνδεσμος φαίνεται στην
    οθόνη ως εναλλακτική) — ΠΟΤΕ αποτυχία της ίδιας της ενέργειας."""
    if not configured():
        return False
    env = os.environ
    msg = EmailMessage()
    msg["From"] = env.get("MISEVENT_SMTP_FROM") or env.get("MISEVENT_SMTP_USER", "")
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(env["MISEVENT_SMTP_HOST"],
                          int(env.get("MISEVENT_SMTP_PORT", 587)), timeout=15) as smtp:
            smtp.starttls()
            if env.get("MISEVENT_SMTP_USER"):
                smtp.login(env["MISEVENT_SMTP_USER"], env.get("MISEVENT_SMTP_PASSWORD", ""))
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException):
        return False
    return True
