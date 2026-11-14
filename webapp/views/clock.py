"""Ημερομηνία αναφοράς: ορισμός/επαναφορά του «σήμερα» του UI (δοκιμές)."""

from flask import Blueprint, redirect, request

from ..clock import COOKIE, parse_date

bp = Blueprint("clock", __name__, url_prefix="/clock")

MAX_AGE = 60 * 60 * 24 * 30  # 30 ημέρες


def _back() -> str:
    from .auth import safe_next

    return safe_next(request.form.get("next"), "/")


@bp.post("/set")
def set_ref():
    resp = redirect(_back())
    d = parse_date(request.form.get("ref"))
    if d is None:
        resp.delete_cookie(COOKIE, path="/")
    else:
        resp.set_cookie(COOKIE, d.isoformat(), max_age=MAX_AGE, path="/",
                        samesite="Lax")
    return resp


@bp.post("/reset")
def reset():
    resp = redirect(_back())
    resp.delete_cookie(COOKIE, path="/")
    return resp
