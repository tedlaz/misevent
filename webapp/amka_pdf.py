"""
Ανάγνωση της «Βεβαίωσης Α.Μ.Κ.Α.» (PDF του amka.gr) για προσυμπλήρωση της
φόρμας νέου εργαζομένου. Ζει στο webapp — ο πυρήνας μένει stdlib-only.

Το PDF έχει πραγματικό κείμενο: σε layout mode κάθε γραμμή είναι
«Ετικέτα:  τιμή  [Ετικέτα:  τιμή]», με 2+ κενά ανάμεσα. Ο «μ» γράφεται με το
σύμβολο micro (U+00B5). Το αρχείο διαβάζεται ΜΟΝΟ στη μνήμη — έχει
προσωπικά δεδομένα και δεν αποθηκεύεται πουθενά.
"""

import re
from datetime import datetime
from io import BytesIO

from misevent.integrations import ergani_parameters as ergani_params
from misevent.rules import constraints


def _pairs(text: str) -> dict:
    """{ΕΤΙΚΕΤΑ (κεφαλαία, χωρίς τόνους): τιμή} — κενή ετικέτα = καμία τιμή."""
    out = {}
    for line in text.replace("µ", "μ").splitlines():
        tokens = [t.strip() for t in re.split(r"\s{2,}", line) if t.strip()]
        for i, tok in enumerate(tokens):
            if not tok.endswith(":"):
                continue
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            out[constraints.norm_name(tok[:-1])] = "" if nxt.endswith(":") else nxt
    return out


def parse_text(text: str) -> dict:
    """Πεδία της φόρμας `_person_modal.html` από το κείμενο της βεβαίωσης.
    Επιστρέφει ΜΟΝΟ όσα βρέθηκαν· ValueError αν δεν είναι βεβαίωση ΑΜΚΑ."""
    if "ΒΕΒΑΙΩΣΗ Α.Μ.Κ.Α" not in constraints.norm_name(text):
        raise ValueError("Το αρχείο δεν μοιάζει με Βεβαίωση Α.Μ.Κ.Α.")
    kv = _pairs(text)
    out = {
        # Επώνυμο ΣΗΜΕΡΙΝΟ, όχι γέννησης
        "last_name": kv.get("ΕΠΩΝΥΜΟ ΣΗΜΕΡΙΝΟ", ""),
        "first_name": kv.get("ΟΝΟΜΑ", ""),
        "father_name": kv.get("ΟΝΟΜΑ ΠΑΤΕΡΑ", ""),
        "mother_name": kv.get("ΟΝΟΜΑ ΜΗΤΕΡΑΣ", ""),
        "afm": constraints.norm_digits(kv.get("ΑΦΜ", "")),
        "amka": next((constraints.norm_digits(v) for k, v in kv.items()
                      if "ΕΝΕΡΓΟ" in k), ""),
        "email": kv.get("EMAIL", ""),
        # Στοιχεία επικοινωνίας στην Ελλάδα
        "address": kv.get("ΟΔΟΣ /ΑΡ. ΔΙΕΥΘ.", ""),
        "postal_code": constraints.norm_digits(kv.get("Τ.Κ", "")),
        "municipality": kv.get("ΔΗΜΟΣ ΔΙΕΥΘΥΝΣΗΣ", ""),
        "prefecture": kv.get("ΝΟΜΟΣ", ""),
        "phone": kv.get("ΤΗΛΕΦΩΝΑ", ""),
        "birth_place": ", ".join(
            v for v in (kv.get("ΔΗΜΟΣ ΓΕΝΝΗΣΗΣ"), kv.get("ΝΟΜΟΣ ΓΕΝΝΗΣΗΣ"),
                        kv.get("ΧΩΡΑ ΓΕΝΝΗΣΗΣ")) if v
        ),
    }
    try:
        born = datetime.strptime(kv.get("ΗΜ/ΝΙΑ ΓΕΝΝΗΣΗΣ", ""), "%d/%m/%Y")
        out["birth_date"] = born.date().isoformat()  # <input type="date">
    except ValueError:
        pass
    sex = constraints.norm_name(kv.get("ΦΥΛΟ", ""))
    if sex.startswith("ΘΗΛ"):
        out["sex"] = "1"
    elif sex.startswith("ΑΡΡ"):
        out["sex"] = "0"
    country = constraints.norm_name(kv.get("ΧΩΡΑ ΥΠΗΚΟΟΤΗΤΑΣ", ""))
    code = next((r["code"] for r in ergani_params.nationalities()
                 if country and constraints.norm_name(r["description"]) == country),
                None)
    if code:
        out["nationality_code"] = code
    return {k: v for k, v in out.items() if v}


def read(data: bytes) -> dict:
    from pypdf import PdfReader  # ΜΟΝΟ εδώ — η εξάρτηση αφορά μόνο το UI

    try:
        page = PdfReader(BytesIO(data)).pages[0]
        text = page.extract_text(extraction_mode="layout")
    except Exception as exc:  # χαλασμένο/μη-PDF αρχείο: μήνυμα, όχι 500
        raise ValueError(f"Δεν διαβάστηκε το PDF ({exc})") from exc
    return parse_text(text)
