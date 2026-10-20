"""
Τα payload ΕΡΓΑΝΗ ΑΠΕΝΑΝΤΙ στα ΕΠΙΣΗΜΑ σχήματα υποβολής.

Τα `data/ergani_schemas/*.json` είναι ό,τι επιστρέφει το ίδιο το ΕΡΓΑΝΗ στο
`GET /Documents/{τύπος}` (δοκιμαστικό, με τα στοιχεία σύνδεσης μιας εταιρείας)
— `json`: δείγμα δομής, `propertiesInfo`: περιορισμοί ανά πεδίο. Ο έλεγχος:
ίδια κλειδιά σε κάθε επίπεδο, και κάθε τιμή περνά τους περιορισμούς της.
Για ανανέωση: ξανά GET από το ΕΡΓΑΝΗ και αντικατάσταση του αρχείου.

Εκτέλεση: uv run pytest tests/test_ergani_schemas.py
"""

import json
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import _bootstrap  # noqa: F401

from misevent.integrations import ergani

SCHEMAS = Path("data/ergani_schemas")
WORK_TIME_TYPES = {r["Code"] for r in json.loads(
    (SCHEMAS / "WorkTimeType.json").read_text(encoding="utf-8"))}


def _check(payload, template, rules: dict, path="") -> list[str]:
    """Λάθη δομής/περιορισμών (κενή λίστα = έγκυρο)."""
    errors = []
    if isinstance(template, dict):
        if not isinstance(payload, dict) or set(payload) != set(template):
            return [f"{path}: κλειδιά {sorted(payload) if isinstance(payload, dict) else payload}"
                    f" != {sorted(template)}"]
        for key in template:
            errors += _check(payload[key], template[key], rules, f"{path}.{key}")
    elif isinstance(template, list):
        if not isinstance(payload, list) or not payload:
            return [f"{path}: αναμένεται μη κενή λίστα"]
        for i, item in enumerate(payload):
            errors += _check(item, template[0], rules, f"{path}[{i}]")
    else:
        name = path.rsplit(".", 1)[-1]
        value = "" if payload is None else str(payload)
        enum = [r["value"] for r in rules.get(name, []) if r["name"] == "enumeration"]
        for r in rules.get(name, []):
            ok = {
                "pattern": lambda v=r["value"]: re.fullmatch(v, value),
                "maxLength": lambda v=r["value"]: len(value) <= int(v),
                "minLength": lambda v=r["value"]: len(value) >= int(v),
                "length": lambda v=r["value"]: len(value) == int(v),
            }.get(r["name"], lambda: True)()
            if not ok:
                errors.append(f"{path}={value!r}: {r['name']} {r['value']}")
        if enum and value not in enum:
            errors.append(f"{path}={value!r}: εκτός {enum}")
    return errors


def _valid(doc_type: str, payload: dict) -> list[str]:
    schema = json.loads((SCHEMAS / f"{doc_type}.json").read_text(encoding="utf-8"))
    rules = {p["name"]: p["restrictions"] for p in schema["propertiesInfo"]}
    return _check(payload, schema["json"], rules)


EMP = SimpleNamespace(afm="046949583", last_name="ΛΑΖΑΡΟΣ", first_name="ΘΕΟΔΩΡΟΣ",
                      insurance_profile=SimpleNamespace(branch_no=0))


def test_wto_week_matches_schema():
    schedule = {"MON": "09:00-17:00", "TUE": "09:00-13:00+16:00-20:00",
                "WED": "Τ09:00-17:00", "THU": "22:00-06:00", "FRI": "09:00-17:00"}
    payload = ergani.build_wto(None, EMP, schedule, date(2026, 1, 1))
    assert _valid("WTOWeek", payload) == []
    rows = payload["WTOS"]["WTO"][0]["Ergazomenoi"]["ErgazomenoiWTO"]
    by_day = {r["f_day"]: r["ErgazomenosAnalytics"]["ErgazomenosWTOAnalytics"] for r in rows}
    assert len(rows) == 7 and by_day["0"] == [{"f_type": "ΑΝ", "f_from": "", "f_to": ""}]
    assert [a["f_type"] for a in by_day["2"]] == ["ΕΡΓ", "ΕΡΓ"]  # σπαστό
    assert by_day["3"][0]["f_type"] == "ΤΗΛ"
    assert by_day["4"][0] == {"f_type": "ΕΡΓ", "f_from": "22:00", "f_to": "06:00"}
    assert {a["f_type"] for r in by_day.values() for a in r} <= WORK_TIME_TYPES


def test_wto_daily_matches_schema():
    days = {"2026-07-14": "09:00-17:00", "2026-07-13": "", "2026-07-15": "Τ10:00-14:00"}
    payload = ergani.build_wto_variable(None, EMP, days, date(2026, 7, 13))
    assert _valid("WTODaily", payload) == []
    wto = payload["WTOS"]["WTO"][0]
    assert (wto["f_from_date"], wto["f_to_date"]) == ("13/07/2026", "15/07/2026")
    assert [r["f_date"] for r in wto["Ergazomenoi"]["ErgazomenoiWTO"]] == [
        "13/07/2026", "14/07/2026", "15/07/2026"]


def _person(**over):
    """Πλήρης (συνθετική) απασχόληση για τα έντυπα πρόσληψης/λήξης."""
    return SimpleNamespace(**{
        "last_name": "Α", "first_name": "Β", "father_name": "Γ", "mother_name": "Δ",
        "birth_date": date(1990, 2, 3), "afm": "123456789", "amka": "03029012345",
        "ama": "", "children": 0, "sex": "1", "nationality_code": "048",
        "identity_type": "ΑΔΤ", "identity_number": "ΑΒ1", "identity_issuer": "",
        "identity_issue_date": None, "identity_expiry_date": None, "marital_status": "0",
        "education_level": "12", "res_permit_inst": "", "res_permit_inst_type": "",
        "res_permit_inst_number": "", "res_permit_inst_expiry_date": None,
        "res_permit_ap": "", "res_permit_ap_type": "", "res_permit_ap_number": "",
        "res_permit_ap_expiry_date": None, "res_permit_visa": "",
        "res_permit_visa_number": "", "res_permit_visa_from": None,
        "res_permit_visa_to": None, "hire_date": date(2026, 7, 31), "pay_type": "monthly",
        "monthly_gross": 1200, "daily_wage": 0, "weekly_hours": 40, "weekly_system": 5,
        "service_years": 0, "married": False, "schedule_mode": "fixed",
        "schedule": {"MON": "09:00-17:00"}, "employment_relationship": "0", **over})


COMPANY = SimpleNamespace(ame="1234567890", afm="123456789", legal_name="ACME")
PROFILE = SimpleNamespace(branch_no=0, kad="1234", specialty_code="123456",
                          specialty_name="Χ", kpk="101", auxiliary_scheme="efka")
BRANCH = SimpleNamespace(sepe_code="13010", oaed_code="101233", kad_code="2521",
                         kallikratis_code="92210101")


def _keys(x):
    if isinstance(x, dict):
        return {k: _keys(v) for k, v in x.items()}
    return [_keys(x[0])] if isinstance(x, list) and x else None


def test_web_e3n_matches_schema():
    """Το WebE3N ήταν ήδη από την προδιαγραφή — εδώ κλειδώνει απέναντι στο
    σχήμα που δίνει σήμερα το ίδιο το ΕΡΓΑΝΗ (μόνο ΔΟΜΗ)."""
    payload = ergani.build_web_e3n(COMPANY, _person(), PROFILE, {"FRI": "08:30-16:30"})
    schema = json.loads((SCHEMAS / "WebE3N.json").read_text(encoding="utf-8"))
    assert _keys(payload) == _keys(schema["json"])


def test_web_e5n_resignation_matches_schema():
    pdf = b"%PDF-1.4 fake"
    payload = ergani.build_web_e5n(COMPANY, _person(), PROFILE, date(2026, 9, 30),
                                   branch=BRANCH, resignation_pdf=pdf)
    assert _valid("WebE5N", payload) == []
    form = payload["AnaggeliesE5N"]["AnaggeliaE5N"][0]
    assert form["f_apoxwrisidate"] == "30/09/2026" and form["f_kad_pararthmatos"] == "2521"
    assert form["f_apodoxes"] == "1200,00"
    import base64
    assert base64.b64decode(form["f_file"]) == pdf


def test_web_e7n_fixed_term_end_matches_schema():
    emp = _person(employment_relationship="1", fixed_term_from=date(2026, 7, 31),
                  fixed_term_to=date(2026, 9, 30))
    payload = ergani.build_web_e7n(COMPANY, emp, PROFILE, date(2026, 9, 30), branch=BRANCH)
    assert _valid("WebE7N", payload) == []
    form = payload["AnaggeliesE7N"]["AnaggeliaE7N"][0]
    assert (form["f_sxeshapasxolisis"], form["f_logosperatosis"]) == ("1", "0")
    assert form["f_lixisymbashdate"] == form["f_apolysisdate"] == "30/09/2026"
