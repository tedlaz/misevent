"""Read-only κατάλογοι παραμέτρων του ΠΣ ΕΡΓΑΝΗ.

Η ``ergani_parameters.db`` είναι χωριστή βάση αναφοράς. Οι κωδικοί
αποθηκεύονται αυτούσιοι στα domain events και στο WebE3N· οι περιγραφές
χρησιμοποιούνται μόνο για την επιλογή/εμφάνιση στο UI.
"""

import sqlite3
import unicodedata
from functools import lru_cache
from pathlib import Path

DB_PATH = Path(__file__).resolve().with_name("ergani_parameters.db")

_TABLES = {
    "identity_types": "typoi_taftotitas",
    "nationalities": "nationalities",
    "education_levels": "epipeda_morfosis",
    "specialties": "eidikotites",
    "sepe": "sepe",
    "oaed": "oaed",
    "kad": "kad",
    "koinotites": "koinotites",
    "residence_permit_types": "typoi_adeias_paramonis",
    "work_time_types": "typoi_parousias",
}

# Κωδικοί υπηκοότητας της τρέχουσας ΕΕ-27, όπως αποθηκεύονται στον πίνακα
# ``nationalities``. Η βάση αναφοράς δεν διαθέτει δική της ένδειξη ΕΕ.
_EU_NATIONALITY_CODES = frozenset({
    "017", "019", "026", "029", "030", "041", "048", "050", "062",
    "066", "068", "084", "085", "088", "093", "095", "102", "135",
    "139", "151", "152", "156", "165", "166", "170", "186", "191",
})


@lru_cache(maxsize=None)
def _rows(kind: str, extra: str | None = None) -> tuple[dict, ...]:
    table = _TABLES[kind]
    if not DB_PATH.is_file():
        return ()
    try:
        uri = DB_PATH.as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            where = " WHERE Extra = ?" if extra else ""
            rows = conn.execute(
                f"SELECT code, description FROM {table}{where} ORDER BY id",
                (extra,) if extra else (),
            ).fetchall()
    except sqlite3.Error:
        return ()
    return tuple(
        {"code": str(code).strip(), "description": str(description).strip()}
        for code, description in rows
    )


def identity_types() -> tuple[dict, ...]:
    return _rows("identity_types")


def nationalities() -> tuple[dict, ...]:
    return _rows("nationalities")


def residence_permit_types(direct: bool) -> tuple[dict, ...]:
    """Είδη άδειας διαμονής τρίτων χωρών (WebE3N `f_res_permit_*_type`):
    `direct` -> άμεση πρόσβαση στην αγορά εργασίας (inst), αλλιώς απαιτεί
    πρόσθετη έγκριση (ap). «ΕΓΓΡΙΣΗ» [sic] όπως είναι γραμμένο στη βάση."""
    return _rows("residence_permit_types", "ΑΜΕΣΗ" if direct else "ΕΓΓΡΙΣΗ")


def absence_reasons() -> tuple[dict, ...]:
    """Λόγοι ΑΠΟΥΣΙΑΣ ΜΕΣΑ ΣΤΟ ΩΡΑΡΙΟ για την ημερήσια δήλωση (WTODaily,
    `f_type` με ώρες από-έως): «ΜΕ» μη εργασία + οι ΩΡΟΑΔΕΙΕΣ (ΩΑ…) της
    λίστας WorkTimeType."""
    return (tuple(r for r in _rows("work_time_types") if r["code"] == "ΜΕ")
            + _rows("work_time_types", "ΑΔΕΙΑ-ΩΡΟΑΔΕΙΑ"))


def education_levels() -> tuple[dict, ...]:
    return tuple(sorted(_rows("education_levels"), key=lambda row: int(row["code"])))


def specialties() -> tuple[dict, ...]:
    """Το μητρώο ειδικοτήτων ΕΡΓΑΝΗ (code/description)."""
    return _rows("specialties")


def catalog(kind: str) -> tuple[dict, ...]:
    """Κατάλογος code/description για πεδίο παραρτήματος."""
    if kind not in {"sepe", "oaed", "kad", "koinotites"}:
        raise ValueError(f"Άγνωστος κατάλογος ΕΡΓΑΝΗ: {kind}")
    return _rows(kind)


def description(kind: str, code: str) -> str:
    code = str(code or "").strip()
    return next((r["description"] for r in catalog(kind) if r["code"] == code), "")


@lru_cache(maxsize=1)
def _specialties_by_code() -> dict[str, dict]:
    return {row["code"]: row for row in specialties()}


def specialty(code: str) -> dict | None:
    """Η ειδικότητα ΕΡΓΑΝΗ με τον συγκεκριμένο κωδικό."""
    return _specialties_by_code().get(str(code or "").strip())


def _search_text(value: str) -> str:
    value = unicodedata.normalize("NFD", str(value or "").upper())
    return "".join(ch for ch in value if unicodedata.category(ch) != "Mn")


@lru_cache(maxsize=1)
def _specialty_search_rows() -> tuple[tuple[str, str, dict], ...]:
    """Προϋπολογισμένο άτονο index· χτίζεται μία φορά ανά process."""
    return tuple(
        (row["code"], _search_text(row["description"]), row)
        for row in specialties()
    )


def search_specialties(q: str, limit: int = 30) -> tuple[dict, ...]:
    """Άτονη αναζήτηση σε κωδικό/περιγραφή, με τα καλύτερα matches πρώτα."""
    term = _search_text(q).strip()
    if not term:
        return ()
    matches = []
    for code, description, row in _specialty_search_rows():
        if term in code or term in description:
            rank = (0 if code == term else 1 if code.startswith(term)
                    else 2 if description.startswith(term) else 3)
            matches.append((rank, len(code), code, row))
    matches.sort(key=lambda item: item[:3])
    return tuple(item[3] for item in matches[:limit])


def eu_nationality_codes() -> tuple[str, ...]:
    """Οι κωδικοί υπηκοότητας των κρατών-μελών της ΕΕ."""
    return tuple(sorted(_EU_NATIONALITY_CODES))


def is_eu_nationality(code: str) -> bool:
    return str(code or "").strip() in _EU_NATIONALITY_CODES


def contains(kind: str, code: str) -> bool:
    """True αν ο κωδικός υπάρχει. Κενός κατάλογος = μη διαθέσιμη βάση."""
    rows = _rows(kind)
    return not rows or any(row["code"] == str(code or "").strip() for row in rows)
