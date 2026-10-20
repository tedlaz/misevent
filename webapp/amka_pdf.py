"""
Προσυμπλήρωση της φόρμας νέου εργαζομένου από αρχείο — δύο πηγές:
  - η «Βεβαίωση Α.Μ.Κ.Α.» (PDF του amka.gr), από το κείμενό της·
  - η «Αίτηση στοιχείων» του υποψηφίου: αυτόνομο HTML (`candidate_form.html`)
    που ανοίγει σε όποιον browser, και κατεβάζει .json (`CANDIDATE_SCHEMA`)·
    ονόματα πεδίων = τα `name=` του modal. (Όχι fillable PDF: κάθε PDF viewer
    τρέχει άλλο κομμάτι JS — κεφαλαία/έλεγχοι/κλείδωμα δούλευαν αλλιώς ανά browser.)
Ζει στο webapp — ο πυρήνας μένει stdlib-only.

Το PDF έχει πραγματικό κείμενο: σε layout mode κάθε γραμμή είναι
«Ετικέτα:  τιμή  [Ετικέτα:  τιμή]», με 2+ κενά ανάμεσα. Ο «μ» γράφεται με το
σύμβολο micro (U+00B5). Το αρχείο διαβάζεται ΜΟΝΟ στη μνήμη — έχει
προσωπικά δεδομένα και δεν αποθηκεύεται πουθενά.
"""

import json
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
        raise ValueError("Το αρχείο δεν μοιάζει με Βεβαίωση Α.Μ.Κ.Α. "
                         "ούτε με συμπληρωμένη αίτηση στοιχείων")
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
    """Μόνο τα πεδία (χωρίς συνημμένα) — βλ. `read_upload`."""
    return read_upload(data)[0]


def read_upload(data: bytes) -> tuple[dict, dict]:
    """(πεδία, έγγραφα): PDF -> βεβαίωση ΑΜΚΑ (χωρίς έγγραφα)· αλλιώς .json
    της αίτησης υποψηφίου, με τα συνημμένα της ως `{είδος: PDF bytes}`."""
    if not data.lstrip().startswith(b"%PDF"):
        return read_candidate(data)
    from pypdf import PdfReader  # ΜΟΝΟ εδώ — η εξάρτηση αφορά μόνο το UI

    try:
        page = PdfReader(BytesIO(data)).pages[0]
        text = page.extract_text(extraction_mode="layout")
    except Exception as exc:  # χαλασμένο/μη-PDF αρχείο: μήνυμα, όχι 500
        raise ValueError(f"Δεν διαβάστηκε το PDF ({exc})") from exc
    return parse_text(text), {}


# ------------------------------------------- Αίτηση στοιχείων υποψηφίου
CANDIDATE_SCHEMA = "misevent.candidate/1"


# Συνημμένα της αίτησης -> πεδία PDF της αναγγελίας πρόσληψης (WebE3N).
ATTACHMENT_KINDS = ("foreign_file", "young_file")
MAX_ATTACHMENTS_BYTES = 20 * 1024 * 1024


def read_candidate(data: bytes) -> tuple[dict, dict]:
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        doc = None
    if not isinstance(doc, dict) or doc.get("schema") != CANDIDATE_SCHEMA \
            or not isinstance(doc.get("fields"), dict):
        raise ValueError("Το αρχείο δεν είναι PDF βεβαίωσης ΑΜΚΑ ούτε αίτηση "
                         "στοιχείων υποψηφίου (.json)")
    fields = parse_form(doc["fields"])
    attachments = doc.get("attachments") or {}
    if not isinstance(attachments, dict):
        raise ValueError("Μη έγκυρα συνημμένα στην αίτηση")
    docs = {}
    for kind in ATTACHMENT_KINDS:
        # Νομιμοποιητικά: μόνο για τρίτες χώρες, όπως και τα πεδία τους
        if kind == "foreign_file" and not _third_country(fields.get("nationality_code", "")):
            continue
        if attachments.get(kind):
            docs[kind] = to_pdf(attachments[kind])
    return fields, docs


def to_pdf(files: list) -> bytes:
    """Ένα PDF από τα αρχεία ενός είδους (`[{name, data: base64}]`): τα PDF
    συνενώνονται, οι εικόνες (JPEG/PNG) γίνονται σελίδες A4. Η ΕΡΓΑΝΗ δέχεται
    ΜΟΝΟ PDF, ενώ ο υποψήφιος συνήθως έχει φωτογραφίες από το κινητό."""
    import base64

    from fpdf import FPDF
    from pypdf import PdfReader, PdfWriter

    if not isinstance(files, list):
        raise ValueError("Μη έγκυρα συνημμένα στην αίτηση")
    writer, total = PdfWriter(), 0
    for f in files:
        name = str(f.get("name") or "αρχείο") if isinstance(f, dict) else "αρχείο"
        try:
            raw = base64.b64decode(f["data"], validate=True)
        except (TypeError, KeyError, ValueError):  # binascii.Error ⊂ ValueError
            raise ValueError(f"Χαλασμένο συνημμένο «{name}»") from None
        total += len(raw)
        if total > MAX_ATTACHMENTS_BYTES:
            raise ValueError("Τα συνημμένα ξεπερνούν τα 20 MB")
        if raw[:3] == b"\xff\xd8\xff" or raw[:8] == b"\x89PNG\r\n\x1a\n":
            page = FPDF(unit="mm", format="A4")
            page.add_page()
            page.image(BytesIO(raw), x=10, y=10, w=190, h=277, keep_aspect_ratio=True)
            raw = bytes(page.output())
        elif not raw.startswith(b"%PDF"):
            raise ValueError(f"«{name}»: δεκτά μόνο PDF, JPEG ή PNG")
        try:
            writer.append(PdfReader(BytesIO(raw)))
        except Exception as exc:
            raise ValueError(f"Δεν διαβάστηκε το «{name}» ({exc})") from exc
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


# (πεδίο modal, ετικέτα) — με τη σειρά της αίτησης.
FORM_FIELDS = (
    ("last_name", "Επώνυμο *"), ("first_name", "Όνομα *"),
    ("father_name", "Πατρώνυμο *"), ("mother_name", "Μητρώνυμο *"),
    ("afm", "Α.Φ.Μ. (9 ψηφία) *"), ("amka", "Α.Μ.Κ.Α. (11 ψηφία) *"),
    ("ama", "Α.Μ.Α. ΕΦΚΑ (αν υπάρχει)"), ("birth_date", "Ημ. γέννησης *"),
    ("sex", "Φύλο *"), ("marital_status", "Οικογενειακή κατάσταση *"),
    ("children", "Αριθμός τέκνων"), ("education_level", "Επίπεδο μόρφωσης *"),
    ("nationality_code", "Υπηκοότητα *"),
    ("birth_place", "Τόπος γέννησης (Δήμος, Νομός, Χώρα)"),
    ("identity_type", "Τύπος ταυτότητας *"), ("identity_number", "Αριθμός ταυτότητας *"),
    ("identity_issuer", "Εκδούσα αρχή"),
    ("identity_issue_date", "Ημ. έκδοσης ταυτότητας"),
    ("identity_expiry_date", "Ημ. λήξης ταυτότητας"),
    ("email", "Email"), ("phone", "Τηλέφωνο"), ("address", "Οδός / αριθμός"),
    ("postal_code", "Τ.Κ."), ("municipality", "Δήμος"), ("prefecture", "Νομός"),
)
# Πολίτες τρίτων χωρών (καρτέλα ② του modal): ενεργά ΜΟΝΟ για υπηκοότητα εκτός ΕΕ.
TC_GROUPS = (
    ("Άδεια διαμονής με άμεση πρόσβαση στην αγορά εργασίας", (
        ("res_permit_inst", "Αφορά"), ("res_permit_inst_type", "Είδος άδειας"),
        ("res_permit_inst_number", "Αριθμός άδειας"),
        ("res_permit_inst_expiry_date", "Ημ. λήξης"))),
    ("Άδεια διαμονής που απαιτεί πρόσθετη έγκριση", (
        ("res_permit_ap", "Αφορά"), ("res_permit_ap_type", "Είδος άδειας"),
        ("res_permit_ap_number", "Αριθμός άδειας"),
        ("res_permit_ap_expiry_date", "Ημ. λήξης"))),
    ("Εποχική απασχόληση με θεώρηση εισόδου", (
        ("res_permit_visa", "Αφορά"), ("res_permit_visa_number", "Αριθμός θεώρησης"),
        ("res_permit_visa_from", "Ισχύς από"),
        ("res_permit_visa_to", "Ισχύς έως"))),
)
TC_FIELDS = tuple(f for _, fields in TC_GROUPS for f in fields)
_DIGITS = {"afm", "amka", "ama", "postal_code", "children"}
DATE_FIELDS = {"birth_date", "identity_issue_date", "identity_expiry_date",
          "res_permit_inst_expiry_date", "res_permit_ap_expiry_date",
          "res_permit_visa_from", "res_permit_visa_to"}


def choices() -> dict:
    """Πεδία-λίστες (`<select>` της αίτησης): {πεδίο: [(κωδικός, περιγραφή)]}."""
    def rows(items, blank=None):
        return ([("", blank)] if blank else []) + [
            (r["code"], r["description"]) for r in items]
    yes_no = [("", "— Δεν αφορά —"), ("0", "Όχι"), ("1", "Ναι")]
    return {
        "res_permit_inst": yes_no, "res_permit_ap": yes_no, "res_permit_visa": yes_no,
        "res_permit_inst_type": rows(ergani_params.residence_permit_types(True), "—"),
        "res_permit_ap_type": rows(ergani_params.residence_permit_types(False), "—"),
        "sex": [("0", "Άνδρας"), ("1", "Γυναίκα")],
        "marital_status": [("0", "Άγαμος/η"), ("1", "Έγγαμος/η"),
                           ("2", "Διαζευγμένος/η"), ("3", "Χήρος/α")],
        "education_level": rows(ergani_params.education_levels()),
        "nationality_code": rows(ergani_params.nationalities()),
        "identity_type": rows(ergani_params.identity_types()),
    }


def parse_form(values: dict) -> dict:
    """Πεδία του modal από τις τιμές της αίτησης ({όνομα πεδίου: τιμή}).
    Αγνοεί άγνωστα πεδία, άκυρες ημερομηνίες και κωδικούς εκτός λίστας."""
    lists = choices()
    out = {}
    for name, _ in FORM_FIELDS + TC_FIELDS:
        v = str(values.get(name) or "").strip()
        if name in _DIGITS:
            v = constraints.norm_digits(v)
        elif name in DATE_FIELDS:
            # ISO (`<input type="date">`) ή ΗΗ/ΜΜ/ΕΕΕΕ· έτος ΤΕΤΡΑΨΗΦΙΟ 1900–2100
            # (το «90» δεν μαντεύεται). Ανύπαρκτη ημερομηνία -> κενό.
            v = re.sub(r"\s", "", v)
            iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", v)
            dmy = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})", v)
            try:
                ymd = iso.groups() if iso else dmy.groups()[::-1]
                y, m, d = (int(x) for x in ymd)
                v = datetime(y, m, d).date().isoformat() if 1900 <= y <= 2100 else ""
            except (AttributeError, ValueError):
                v = ""
        elif name in lists:
            v = v if v in {c for c, _ in lists[name]} else ""
        elif name != "email":
            v = constraints.norm_name(v)  # κεφαλαία χωρίς τόνους, όπως αποθηκεύονται
        if v:
            out[name] = v
    if not _third_country(out.get("nationality_code", "")):
        for name, _ in TC_FIELDS:  # όπως το modal: απενεργά για ΕΕ/χωρίς υπηκοότητα
            out.pop(name, None)
    return out


def _third_country(code: str) -> bool:
    """Ίδιος κανόνας με το `third-country-tab.js` του modal."""
    return bool(code) and code not in ergani_params.eu_nationality_codes()


def problems(found: dict) -> list[str]:
    """Αλγοριθμικοί έλεγχοι ΑΦΜ/ΑΜΚΑ πάνω στα στοιχεία που διαβάστηκαν — για
    προειδοποίηση ΠΡΙΝ την καταχώρηση (που τους ξανακάνει ούτως ή άλλως)."""
    out = []
    for key, check in (("afm", constraints.check_afm), ("amka", constraints.check_amka)):
        try:
            if key in found:
                check(found[key])
        except ValueError as exc:
            out.append(str(exc))
    return out
