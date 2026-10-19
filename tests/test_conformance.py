"""
GOLDEN CONFORMANCE TESTS — το δίχτυ ασφαλείας των υπολογισμών.

⚠️ ΤΙ ΕΙΝΑΙ ΑΥΤΟ ΚΑΙ ΤΙ ΔΕΝ ΕΙΝΑΙ
Τα υπόλοιπα tests κλειδώνουν **εσωτερική συνέπεια** (π.χ. «ο ΦΜΥ βγαίνει
θετικός», «η ΑΠΔ έχει σωστά μήκη πεδίων»). Αυτό εδώ κλειδώνει **ΑΚΡΙΒΗ ΠΟΣΑ σε
λεπτά του ευρώ** για συγκεκριμένα σενάρια, ώστε καμία αναδιοργάνωση κώδικα να
μην αλλάξει σιωπηλά έναν αριθμό που πληρώνεται σε εργαζόμενο.

**ΟΙ ΠΑΡΑΜΕΤΡΟΙ ΕΙΝΑΙ ΚΑΡΦΩΜΕΝΕΣ ΜΕΣΑ ΣΤΟ FIXTURE**, όχι διαβασμένες από το
`config`: το `_apply_params` τις περνά με `config.set_overrides()` πριν από κάθε
τρέξιμο. Αυτό είναι ΟΛΟ το νόημα — όταν αύριο βγει νέα ΓΣΣΕ ή μετακινηθεί ένας
hardcoded κανόνας σε μητρώο παραμέτρων, τα ποσά εδώ **πρέπει να μείνουν ίδια**.
Αν αλλάξουν, ή έσπασε κάτι ή άλλαξε πραγματικά ο κανόνας — και τότε το αλλάζεις
ΡΗΤΑ με `--update`, βλέποντας το diff.

⚠️ **ΠΡΟΕΛΕΥΣΗ ΤΩΝ ΑΡΙΘΜΩΝ**: κάθε σενάριο έχει πεδίο `source`. Σήμερα ΟΛΑ
γράφουν «τρέχουσα υλοποίηση — ΟΧΙ επίσημο»: είναι regression baseline, ΟΧΙ
απόδειξη νομικής ορθότητας. Όταν βρεθεί επίσημο λυμένο παράδειγμα (εγκύκλιος
ΕΦΚΑ / πίνακας ΑΑΔΕ / παράδειγμα ΣΕΠΕ), αντικατέστησε το ποσό ΚΑΙ το `source`.
Αν σκάσει το test, μάθαμε κάτι — μην το «διορθώσεις» με --update χωρίς να δεις
γιατί.

Χρήση:
    uv run tests/test_conformance.py            # έλεγχος
    uv run tests/test_conformance.py --update   # ξαναγράφει τα expected
"""

import json
import sys
from dataclasses import fields as dc_fields
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401  ΠΡΩΤΟ import (ρίζα στο sys.path + chdir)

from misevent.domain import payroll_engine as pe
from misevent.domain import work_hours
from misevent.rules import config

FIXTURE = Path("tests/conformance/cases.json")

# Ανοχή: τα ποσά είναι στρογγυλοποιημένα σε λεπτά, άρα η σύγκριση είναι ΑΚΡΙΒΗΣ.
# Το epsilon υπάρχει μόνο για την αναπαράσταση του float.
EPS = 0.005


# --------------------------------------------------------------- παράμετροι
def _to_json(v):
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v == float("inf"):
        return None  # το JSON δεν έχει άπειρο — το τελευταίο κλιμάκιο
    if isinstance(v, (list, tuple)):
        return [_to_json(x) for x in v]
    if hasattr(v, "__dataclass_fields__"):
        return {f.name: _to_json(getattr(v, f.name)) for f in dc_fields(v)}
    return v


def _from_json(cls, payload: dict):
    """Ανακατασκευή εγγραφής παραμέτρου από το fixture."""
    kw = {}
    for f in dc_fields(cls):
        if f.name not in payload:
            continue
        v = payload[f.name]
        if f.name == "valid_from":
            v = date.fromisoformat(v)
        elif f.name == "brackets":
            v = tuple(
                config.TaxBracket(
                    float("inf") if b["up_to"] is None else b["up_to"], b["rate"]
                )
                for b in v
            )
        elif isinstance(v, list):
            v = tuple(v)
        kw[f.name] = v
    return cls(**kw)


def _apply_params(params: dict) -> None:
    """Καρφώνει ΟΛΕΣ τις οικογένειες του fixture ως overrides — ώστε το
    αποτέλεσμα να μην εξαρτάται από τα defaults του config.py."""
    config.clear_overrides()
    for family, rows in params.items():
        if family == "tax":
            # ⚠️ ΤΟ ΜΟΝΤΕΛΟ ΕΙΝΑΙ ΑΝΑ ΓΡΑΜΜΗ, ΟΧΙ ΑΝΑ ΟΙΚΟΓΕΝΕΙΑ: η κλίμακα
            # 2026 είναι TaxScaleYouth2026 (συντελεστές ανά τέκνα/ηλικία) ενώ
            # η προηγούμενη είναι standard. Χτίζοντας ΟΛΕΣ με το μοντέλο της
            # πρώτης, η κλίμακα 2026 έχανε σιωπηλά τη λογική της.
            entries = [
                _from_json(config.TAX_MODELS[r.get("model", "standard")], r)
                for r in rows
            ]
        else:
            entries = [_from_json(config.FAMILY_CLASSES[family], r) for r in rows]
        config.set_overrides(family, entries)


def _snapshot_defaults() -> dict:
    """Οι σημερινές τιμές του config, σε μορφή fixture — για το --update όταν
    προστίθεται νέο σενάριο."""
    out = {}
    for family, entries in config.FAMILY_DEFAULTS.items():
        rows = []
        for e in entries:
            row = _to_json(e)
            if family == "tax":
                row["model"] = getattr(type(e), "model_key", "standard")
            rows.append(row)
        out[family] = rows
    return out


# ------------------------------------------------------------------ σενάρια
# Κάθε σενάριο: όνομα -> callable που επιστρέφει dict {ετικέτα: ποσό}.
# ΚΑΘΑΡΕΣ ΣΥΝΑΡΤΗΣΕΙΣ ΜΟΝΟ — κανένα store, καμία βάση, κανένα event.


def _res(r) -> dict:
    return {
        "gross": r.gross,
        "efka_employee": r.efka_employee,
        "efka_employer": r.efka_employer,
        "taxable": r.taxable,
        "fmy": r.fmy,
        "net": r.net,
    }


def case_monthly_full(p):
    """Μισθωτός πλήρους απασχόλησης, 25 ημέρες."""
    return _res(pe.calc_monthly(
        p["monthly_gross"], p["children"], date.fromisoformat(p["period_end"]),
        birth_date=date.fromisoformat(p["birth_date"]) if p.get("birth_date") else None,
    ))


def case_monthly_partial(p):
    """Μερική απασχόληση: η ΒΑΣΗ ημερών = ημέρες εργασίας του μήνα κατά το
    πρόγραμμα (`month_days`), cap 25 — Εγκ. e-ΕΦΚΑ 13/2023."""
    base_days = pe.monthly_insurance_days(p["weekly_hours"], p["month_days"])
    r = pe.calc_monthly(
        p["monthly_gross"], p["children"], date.fromisoformat(p["period_end"]),
        unpaid_days=p.get("unpaid_days", 0), base_days=base_days,
    )
    return {"base_days": float(base_days), **_res(r)}


def case_daily(p):
    """Ημερομίσθιος: μικτά = ημερομίσθιο × πραγματικές ημέρες."""
    return _res(pe.calc_monthly_daily(
        p["daily_wage"], p["days_worked"], p["children"],
        date.fromisoformat(p["period_end"]),
    ))


def case_sick(p):
    """Ασθένεια: το τριήμερο κόβει ΜΙΣΟ ημερομίσθιο/ημέρα, οι υπόλοιπες
    ημέρες κόβονται κατά το επίδομα ΕΦΚΑ. Οι ημέρες ΑΣΦΑΛΙΣΗΣ δεν θίγονται."""
    daily = p["monthly_gross"] / 25.0
    sick = pe.sick_deduction(daily, p["half_days"], p.get("efka_benefit", 0.0))
    r = pe.calc_monthly(
        p["monthly_gross"], p["children"], date.fromisoformat(p["period_end"]),
        sick=sick,
    )
    return {"sick_deduction": sick, **_res(r)}


def case_christmas_gift(p):
    return _res(pe.calc_christmas_gift(
        p["monthly_gross"], p["children"], p["year"],
        date.fromisoformat(p["hire"]),
        date.fromisoformat(p["term"]) if p.get("term") else None,
    ))


def case_easter_gift(p):
    """⚠️ Ο μισθός είναι αυτός που ΙΣΧΥΕ 15 ημέρες πριν τη Μ. Τετάρτη."""
    r = pe.calc_easter_gift(
        p["monthly_gross"], p["children"], p["year"],
        date.fromisoformat(p["hire"]),
        date.fromisoformat(p["term"]) if p.get("term") else None,
    )
    return {
        "payment_date": pe.easter_gift_payment_date(p["year"]).isoformat(),
        "wage_date": pe.easter_gift_wage_date(p["year"]).isoformat(),
        **_res(r),
    }


def case_leave_allowance(p):
    """Επίδομα Αδείας: η αναλογία μηνών μετριέται από την πρόσληψη έως τον
    Δεκέμβριο — ή έως την αποχώρηση (`term`), όταν υπάρχει. Ο μήνας λήψης
    της άδειας ΔΕΝ κόβει την αναλογία."""
    term = p.get("term")
    return _res(pe.calc_leave_allowance(
        p["monthly_gross"], p["children"], p["year"],
        date.fromisoformat(p["hire"]),
        term=date.fromisoformat(term) if term else None,
        full_year=p.get("full_year", False),
    ))


def case_severance(p):
    """Αποζημίωση απόλυσης: μήνες × μηνιαίες αποδοχές × 7/6, αυτοτελής φόρος,
    ΚΑΜΙΑ ασφαλιστική κράτηση."""
    months = config.severance_months(p["service_years"], p.get("with_notice", False))
    gross = round(months * p["monthly_gross"] * config.SEVERANCE_GIFT_FACTOR, 2)
    tax = config.severance_tax(gross)
    return {"months": months, "gross": gross, "tax": tax, "net": round(gross - tax, 2)}


def case_hours(p):
    """Κατάταξη ωρών: οι άξονες ΔΕΝ αθροίζονται — η κλάση πολλαπλασιάζεται
    με (1 + νύχτα + αργία)."""
    shifts = {date.fromisoformat(d): v for d, v in p["shifts"].items()}
    buckets = work_hours.classify_shifts(
        shifts, p["weekly_hours"], p.get("weekly_system", 5)
    )
    fields = work_hours.sheet_fields_from_buckets(buckets)
    rules = config.work_hour_rules_for(date.fromisoformat(p["on"]))
    rate = work_hours.hourly_rate("monthly", p["monthly_gross"], 0.0, p["weekly_hours"])
    sup = work_hours.pay_supplement(rate, fields, rules)
    out = {k: round(float(v), 4) for k, v in sorted(fields.items()) if v}
    out["hourly_rate"] = round(rate, 4)
    out["supplement_total"] = round(sup["total"], 2)
    return out


def case_minimum_floor(p):
    """Κατώτατο όριο ΜΕ προσαυξήσεις (τριετίες/γάμος/λοιπές)."""
    return {"floor": round(config.minimum_floor_value(
        p["pay_type"], p.get("weekly_hours", 40.0), date.fromisoformat(p["on"]),
        p["service_years"], p.get("married", False), p.get("extra_pct", 0.0),
    ), 2)}


SCENARIOS = {
    "monthly_full": case_monthly_full,
    "monthly_partial": case_monthly_partial,
    "daily": case_daily,
    "sick": case_sick,
    "christmas_gift": case_christmas_gift,
    "easter_gift": case_easter_gift,
    "leave_allowance": case_leave_allowance,
    "severance": case_severance,
    "hours": case_hours,
    "minimum_floor": case_minimum_floor,
}


# -------------------------------------------------------------------- runner
def run(update: bool = False) -> int:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    _apply_params(data["params"])

    failures = []
    for case in data["cases"]:
        fn = SCENARIOS.get(case["scenario"])
        if fn is None:
            failures.append(f"{case['name']}: άγνωστο σενάριο «{case['scenario']}»")
            continue
        got = fn(case["input"])
        if update:
            case["expect"] = got
            continue
        exp = case.get("expect") or {}
        for key in sorted(set(exp) | set(got)):
            a, b = exp.get(key), got.get(key)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                ok = abs(float(a) - float(b)) <= EPS
            else:
                ok = a == b
            if not ok:
                failures.append(f"{case['name']} · {key}: περίμενα {a}, πήρα {b}")

    config.clear_overrides()

    if update:
        FIXTURE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"OK - ξαναγράφτηκαν {len(data['cases'])} σενάρια στο {FIXTURE}")
        return 0

    if failures:
        print(f"FAIL - {len(failures)} αποκλίσεις:")
        for f in failures:
            print("  " + f)
        return 1

    print(f"OK - {len(data['cases'])} σενάρια συμμόρφωσης, ακριβή ποσά")
    return 0


if __name__ == "__main__":
    if "--snapshot-params" in sys.argv:
        print(json.dumps(_snapshot_defaults(), ensure_ascii=False, indent=2))
        sys.exit(0)
    sys.exit(run(update="--update" in sys.argv))


# --------------------------------------------------------------- pytest
def test_conformance():
    """Τα 27 golden σενάρια, ως ΕΝΑ test για το `uv run pytest`.

    Η `run()` τυπώνει την πλήρη ανάλυση των αποκλίσεων στο stdout· εδώ μένει
    μόνο το πράσινο/κόκκινο. Για ενημέρωση του fixture:
    `uv run tests/test_conformance.py --update`.
    """
    assert run() == 0, "αποκλίσεις συμμόρφωσης — βλ. την έξοδο παραπάνω"
