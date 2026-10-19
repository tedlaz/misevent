"""Μητρώα ΟΣΥΚ/ΤΟΠΙΚ για lookup στις φόρμες του UI.

ΜΙΑ ΠΗΓΗ ΑΛΗΘΕΙΑΣ: τα δεδομένα διαβάζονται από τα **ίδια αρχεία** που
χρησιμοποιεί ο eΑΠΔ viewer (`webapp/eapd/*.js`) — καμία αντιγραφή σε Python.
Έτσι, όταν ενημερωθεί το μητρώο, ενημερώνονται μαζί viewer και φόρμες.

Τα αρχεία είναι της μορφής `window.<VAR> = [ {...}, ... ];` — καθαρό JSON
literal, οπότε διαβάζεται με `json.loads` (χωρίς εξάρτηση σε JS runtime).

Σήμερα χρησιμοποιείται το ΤΟΠΙΚ (Τοπικές Διευθύνσεις e-ΕΦΚΑ, πεδία ΑΠΔ
No 3/No 4 = «Κωδ./Ονομασία Τοπικής Δ/νσης» στα στοιχεία εργοδότη). Τα
`osyk_kad.js` / `osyk_eid.js` (ΚΑΔ No 31, ειδικότητα No 35) διαβάζονται με
τον ΙΔΙΟ loader όποτε χρειαστούν.
"""

import json
import os

from misevent.rules.constraints import norm_name

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eapd")
_cache: dict[str, list[dict]] = {}


def _load(filename: str, var: str) -> list[dict]:
    """Το JSON literal ενός `window.<var> = [...];` αρχείου (με cache)."""
    if var in _cache:
        return _cache[var]
    path = os.path.join(_DIR, filename)
    try:
        src = open(path, encoding="utf-8").read()
        body = src[src.index("[", src.index(var)) : src.rindex("]") + 1]
        _cache[var] = json.loads(body)
    except (OSError, ValueError, json.JSONDecodeError):
        # Το μητρώο είναι βοήθημα του UI — αν λείπει/χαλάσει, η φόρμα
        # εξακολουθεί να δέχεται χειροκίνητη πληκτρολόγηση.
        _cache[var] = []
    return _cache[var]


def topiks() -> list[dict]:
    """Οι Τοπικές Διευθύνσεις e-ΕΦΚΑ: [{code, per, oldCode}, ...]."""
    return _load("topic.js", "TOPIK_VALUES")


def search_topiks(q: str, limit: int = 40) -> list[dict]:
    """Αναζήτηση με κωδικό ή ονομασία, **χωρίς τόνους και ανεξαρτήτως πεζών/
    κεφαλαίων** (ίδια συμπεριφορά με το lookup του eΑΠΔ viewer)."""
    rows = topiks()
    term = norm_name(q)
    if term:
        rows = [
            r for r in rows
            if term in norm_name(r.get("per", ""))
            or term in str(r.get("code", ""))
            or term in str(r.get("oldCode", ""))
        ]
    return rows[:limit]


def topik(code: str) -> dict | None:
    """Η εγγραφή με αυτόν τον κωδικό, αλλιώς None."""
    code = str(code or "").strip()
    return next((r for r in topiks() if str(r.get("code")) == code), None)
