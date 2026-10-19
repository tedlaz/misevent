"""Ο.Σ.Υ.Κ. του e-ΕΦΚΑ: ΚΑΔ – Ειδικότητες – ΚΠΚ της ΑΠΔ, από το ``osyk_29.zip``.

Το zip μένει ΑΥΤΟΥΣΙΟ όπως το δίνει ο e-ΕΦΚΑ (αρχεία κειμένου cp1253, πεδία
με «|»· βλ. ``readme.txt`` μέσα του) — ενημέρωση = αντικατάσταση του αρχείου
(και του `ZIP_PATH`, αν αλλάξει όνομα).

⚠️ Το ΚΑΔ εδώ είναι το ΚΑΔ της ΑΠΔ (No 31) για τη θέση εργασίας — ΚΑΜΙΑ
σχέση με το ΚΑΔ παραρτήματος της ΕΡΓΑΝΗ (``ergani_parameters``).

Χρησιμοποιούνται:
  dn_kad.txt        ΚΑΔ|Περιγραφή
  dn_eid.txt        Ειδικότητα|Περιγραφή
  dn_kadeidkpk.txt  ΚΑΔ|Ειδικότητα|ΚΠΚ|ΑπόYYYYMM|ΈωςYYYYMM
  dn_spc.txt        ΕιδικήΠερίπτωση|ΚΠΚ|ΚΠΚ στην ειδική περίπτωση|Από|Έως

⚠️ Ένα ζεύγος ΚΑΔ+ειδικότητας έχει συχνά ΠΟΛΛΑ ισχύοντα ΚΠΚ (το βασικό, π.χ.
101, και ΚΠΚ προγραμμάτων/μειώσεων εισφορών, π.χ. 3129, 1046) — γι' αυτό
όλα τα ΚΠΚ εδώ είναι ΛΙΣΤΕΣ, με τη σειρά του αρχείου (το βασικό πρώτο).

Τα ΚΠΚ με τα ποσοστά τους ΔΕΝ έρχονται από εδώ: δηλώνονται χειροκίνητα στο
Μητρώο ΚΠΚ (σελίδα «Παράμετροι»). Τα ΚΠΚ του συνδυασμού είναι μόνο πρόταση.
"""

import zipfile
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path

from misevent.rules.constraints import norm_name

ZIP_PATH = Path(__file__).resolve().with_name("osyk_29.zip")

# Οι καλύψεις που παίρνουν ΚΑΔ/ειδικότητα από το μητρώο: κύρια και πρόνοια
# e-ΕΦΚΑ. Το ΤΕΚΑ έχει σταθερό πακέτο (`config.TEKA_PACKAGE`).
FUNDS = ("efka_main", "efka_welfare")


@lru_cache(maxsize=None)
def _lines(member: str) -> tuple[tuple[str, ...], ...]:
    try:
        with zipfile.ZipFile(ZIP_PATH) as z:
            text = z.read(member).decode("cp1253")
    except (OSError, KeyError, zipfile.BadZipFile):
        return ()
    return tuple(
        tuple(p.strip() for p in line.split("|"))
        for line in text.splitlines()
        if line.strip()
    )


@lru_cache(maxsize=None)
def _descriptions(member: str) -> dict[str, str]:
    return {r[0]: r[1] for r in _lines(member) if len(r) >= 2}


@lru_cache(maxsize=None)
def _combos_by_kad() -> dict[str, tuple[tuple[str, ...], ...]]:
    """Οι συνδυασμοί ανά ΚΑΔ (το αρχείο έχει ~150.000 γραμμές)."""
    out = defaultdict(list)
    for r in _lines("dn_kadeidkpk.txt"):
        if len(r) >= 5:
            out[r[0]].append(r)
    return {k: tuple(v) for k, v in out.items()}


def kads() -> list[dict]:
    """Όλα τα ΚΑΔ: [{code, description}]."""
    return [{"code": c, "description": d}
            for c, d in sorted(_descriptions("dn_kad.txt").items())]


def kad_description(code: str) -> str:
    return _descriptions("dn_kad.txt").get(str(code or "").strip(), "")


def eid_description(code: str) -> str:
    return _descriptions("dn_eid.txt").get(str(code or "").strip(), "")


def special_case_description(code: str) -> str:
    """Η περιγραφή μιας ειδικής περίπτωσης ασφάλισης (No 36, π.χ. «09»)."""
    try:
        return _descriptions("dn_spc_desc.txt").get(str(int(code or 0)), "")
    except ValueError:
        return ""


def _ym(on: date | None) -> str:
    return (on or date.today()).strftime("%Y%m")


def specialties_for(kad: str, on: date | None = None) -> list[dict]:
    """Οι ειδικότητες που ισχύουν για το ΚΑΔ τη μέρα `on` (default σήμερα):
    [{code, description, kpks}], ταξινομημένες κατά κωδικό."""
    ym = _ym(on)
    kpks: dict[str, list[str]] = {}
    for r in _combos_by_kad().get(str(kad or "").strip(), ()):
        if r[3] <= ym <= r[4]:
            kpks.setdefault(r[1], []).append(r[2].zfill(4))
    eids = _descriptions("dn_eid.txt")
    return [{"code": c, "description": eids.get(c, ""), "kpks": kpks[c]}
            for c in sorted(kpks)]


def combination_kpks(kad: str, specialty_code: str, on: date | None = None) -> list[str]:
    """Τα ΚΠΚ του συνδυασμού ΚΑΔ+ειδικότητας (4ψήφια, όπως στο Μητρώο ΚΠΚ)·
    κενή λίστα = ο συνδυασμός δεν ισχύει."""
    code = str(specialty_code or "").strip()
    return next((r["kpks"] for r in specialties_for(kad, on) if r["code"] == code), [])


def special_case_kpk(special_case: str, kpk: str, on: date | None = None) -> str | None:
    """Το ΚΠΚ που δηλώνεται για ειδική περίπτωση ασφάλισης (ΑΠΔ No 36), π.χ.
    09 (ΤΕΚΑ) + 0101 -> 0103. «00» = καμία: το ίδιο ΚΠΚ. None αν το μητρώο
    δεν προβλέπει το ζεύγος."""
    case = int(special_case or 0)
    if not case:
        return kpk
    ym = _ym(on)
    return next(
        (
            r[2].zfill(4)
            for r in _lines("dn_spc.txt")
            if len(r) >= 5 and int(r[0]) == case and int(r[1]) == int(kpk)
            and r[3] <= ym <= r[4]
        ),
        None,
    )


def main_special_case(
    carrier: str, kad: str, specialty_code: str, welfare: bool = False,
    on: date | None = None,
) -> str:
    """Η ειδική περίπτωση (No 36) της ΚΥΡΙΑΣ γραμμής e-ΕΦΚΑ, από το πού έχει
    επικουρική ο εργαζόμενος:

      ΤΕΚΑ -> 09 (εξαίρεση από την επικουρική του e-ΕΦΚΑ· π.χ. 103 μένει 103)
      ΕΦΚΑ -> 10 όταν το βασικό ΚΠΚ του συνδυασμού ΔΕΝ έχει επικουρική (το
              dn_spc.txt το αλλάζει: π.χ. 5242/532030 103 -> 101) ή όταν
              υπάρχει πρόνοια στον e-ΕΦΚΑ· αλλιώς 00 (π.χ. 101 έχει ήδη)."""
    if carrier == "teka":
        return "09"
    if welfare:
        return "10"
    base = combination_kpks(kad, specialty_code, on)
    if base and special_case_kpk("10", base[0], on) not in (None, base[0]):
        return "10"
    return "00"


def suggested_kpks(
    kad: str, specialty_code: str, special_case: str = "00", on: date | None = None
) -> list[str]:
    """Τα ΚΠΚ του συνδυασμού, προσαρμοσμένα στην ειδική περίπτωση (χωρίς
    διπλά, με τη σειρά του αρχείου)."""
    out = []
    for kpk in combination_kpks(kad, specialty_code, on):
        mapped = special_case_kpk(special_case, kpk, on)
        if mapped and mapped not in out:
            out.append(mapped)
    return out


def available() -> bool:
    return bool(_lines("dn_kadeidkpk.txt"))


def search_kads(q: str, limit: int = 40) -> list[dict]:
    """Αναζήτηση ΚΑΔ με κωδικό ή λέξεις περιγραφής (χωρίς τόνους)."""
    term = norm_name(q)
    rows = kads()
    if term:
        rows = [r for r in rows if r["code"].startswith(term)
                or term in norm_name(r["description"])]
    return rows[:limit]
