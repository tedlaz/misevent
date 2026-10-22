"""
Ανάγνωση αρχείου «Κάρτας Εργασίας» ΕΡΓΑΝΗ (xlsx) — ΜΟΝΟ stdlib.

Το ΕΡΓΑΝΗ εξάγει τις πραγματικές παρουσίες μιας περιόδου σε xlsx με στήλες:

    ΑΑ Παραρτηματος | ΑΦΜ | Επώνυμο | Όνομα | Ημ/νία | Από | Έως

(ημερομηνία «Η/Μ/ΕΕΕΕ», π.χ. 14/9/2026· το παράρτημα αγνοείται — η
αντιστοίχιση γίνεται με ΑΦΜ). Η ανάγνωση είναι ΕΛΑΣΤΙΚΗ: στήλες με το όνομα
(οποιαδήποτε σειρά, χωρίς τόνους/κεφαλαία, «Είσοδος/Έξοδος» αντί «Από/Έως»),
ημερομηνίες και ώρες και σε μορφές Excel (π.χ. αρχείο ξανασωσμένο).

Κάθε γραμμή είναι ΜΙΑ διέλευση (βάρδια). Μια ημέρα μπορεί να έχει πολλές
γραμμές (π.χ. διακοπτόμενο ωράριο 08:00-12:00 + 17:00-19:00) — αθροίζονται.

Ο πυρήνας μένει stdlib-only: το xlsx είναι zip από XML, οπότε διαβάζεται με
`zipfile` + `xml.etree`, χωρίς εξάρτηση (openpyxl κ.λπ.). Η μετάφραση από
ΑΦΜ σε employment_id γίνεται στο command layer (`services.py`), όχι εδώ:
αυτό το module είναι καθαρή ανάγνωση, χωρίς πρόσβαση σε store/projections.
"""
from __future__ import annotations

import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Οι στήλες με τη σειρά που τις εξάγει το ΕΡΓΑΝΗ (A..G)
COLUMNS = ("ΑΑ Παραρτηματος", "ΑΦΜ", "Επώνυμο", "Όνομα", "Ημ/νία", "Από", "Έως")


@dataclass(frozen=True)
class KartaPunch:
    """Μία διέλευση (βάρδια) όπως καταγράφεται στην κάρτα."""

    afm: str
    last_name: str
    first_name: str
    day: date
    time_in: str  # "HH:MM"
    time_out: str  # "HH:MM"

    @property
    def minutes(self) -> int:
        """Διάρκεια βάρδιας σε λεπτά. Αν η ΕΞΟΔΟΣ είναι ≤ ΕΙΣΟΔΟ, η βάρδια
        περνά τα μεσάνυχτα και η έξοδος εννοείται την ΕΠΟΜΕΝΗ ημέρα (+24ω) —
        π.χ. 22:00→06:00 = 8 ώρες. Μη έγκυρο ωράριο ή είσοδος==έξοδος -> 0."""
        a, b = _hm_opt(self.time_in), _hm_opt(self.time_out)
        if a is None or b is None:
            return 0
        dur = b - a
        return dur if dur > 0 else (dur + 24 * 60 if dur < 0 else 0)

    @property
    def next_day(self) -> bool:
        """True αν η ώρα εξόδου βρίσκεται την επόμενη ημερολογιακή ημέρα."""
        a, b = _hm_opt(self.time_in), _hm_opt(self.time_out)
        return a is not None and b is not None and b < a


def _hm(t: str) -> int:
    """«HH:MM» -> λεπτά από τα μεσάνυχτα (0 σε κενό/λάθος μορφή — για σειρά)."""
    v = _hm_opt(t)
    return v if v is not None else 0


def _hm_opt(t: str):
    """«HH:MM» -> λεπτά από τα μεσάνυχτα, ή None αν το ωράριο είναι μη έγκυρο.
    (Χρειάζεται για να ξεχωρίζει το έγκυρο 00:00 από κενό/άκυρο πεδίο.)"""
    t = (t or "").strip()
    if not t or ":" not in t:
        return None
    hh, _, mm = t.partition(":")
    try:
        return int(hh) * 60 + int(mm)
    except ValueError:
        return None


def _col_letter(ref: str) -> str:
    """«B10» -> «B» (μόνο τα γράμματα του cell reference)."""
    return "".join(ch for ch in ref if ch.isalpha())


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    """Ο πίνακας κοινών συμβολοσειρών, αν υπάρχει (t="s" cells)."""
    try:
        raw = z.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    out = []
    for si in root.findall(_NS + "si"):
        out.append("".join(t.text or "" for t in si.iter(_NS + "t")))
    return out


def _cell_text(c: ET.Element, shared: list[str]) -> str:
    """Το κείμενο ενός cell, ανεξάρτητα από τον τρόπο αποθήκευσης
    (κοινή συμβολοσειρά / inline / string result / αριθμός)."""
    t = c.get("t")
    if t == "s":  # δείκτης σε sharedStrings
        v = c.find(_NS + "v")
        idx = int(v.text) if v is not None and v.text else -1
        return shared[idx] if 0 <= idx < len(shared) else ""
    if t == "inlineStr":
        is_ = c.find(_NS + "is")
        return "".join(e.text or "" for e in is_.iter(_NS + "t")) if is_ is not None else ""
    v = c.find(_NS + "v")  # t="str" ή αριθμός
    return (v.text or "").strip() if v is not None else ""


def parse_karta(source) -> list[KartaPunch]:
    """Διαβάζει το xlsx (διαδρομή αρχείου ή bytes) και επιστρέφει τις
    διελεύσεις. Πετά `ValueError` αν το αρχείο δεν έχει την αναμενόμενη
    γραμμογράφηση — να μη «περνά» σιωπηλά λάθος αρχείο."""
    import io

    try:
        z = zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source)
    except zipfile.BadZipFile:
        raise ValueError("Το αρχείο δεν είναι έγκυρο xlsx") from None

    with z:
        shared = _shared_strings(z)
        try:
            sheet = z.read("xl/worksheets/sheet1.xml")
        except KeyError:
            raise ValueError("Το xlsx δεν περιέχει φύλλο εργασίας") from None
        root = ET.fromstring(sheet)
        data = root.find(_NS + "sheetData")
        rows = data.findall(_NS + "row") if data is not None else []

    if not rows:
        raise ValueError("Το αρχείο καρτών είναι κενό")

    def cells(row) -> dict[str, str]:
        return {
            _col_letter(c.get("r", "")): _cell_text(c, shared).strip()
            for c in row.findall(_NS + "c")
        }

    # Επικεφαλίδα: η πρώτη από τις 10 πρώτες γραμμές που έχει τις
    # υποχρεωτικές στήλες (πάνω της μπορεί να υπάρχουν τίτλοι). Οι στήλες
    # βρίσκονται ΜΕ ΤΟ ΟΝΟΜΑ — σειρά, τόνοι, κεφαλαία και έξτρα στήλες
    # (π.χ. παράρτημα) δεν παίζουν ρόλο.
    for h, row in enumerate(rows[:10]):
        col = {}
        for letter, text in cells(row).items():
            field = _HEADER_ALIASES.get(_norm(text))
            if field and field not in col:
                col[field] = letter
        if _REQUIRED <= col.keys():
            break
    else:
        raise ValueError(
            "Μη αναμενόμενη γραμμογράφηση καρτών ΕΡΓΑΝΗ — δεν βρέθηκαν οι "
            f"στήλες ΑΦΜ, Ημ/νία, Από, Έως (αναμενόμενες: {', '.join(COLUMNS)})"
        )

    punches = []
    for i, row in enumerate(rows[h + 1:], start=h + 2):
        v = cells(row)
        afm, d = v.get(col["afm"], ""), v.get(col["day"], "")
        if not afm and not d:
            continue  # κενή γραμμή
        day = _parse_day(d)
        if day is None:
            raise ValueError(
                f"Μη έγκυρη ημερομηνία «{d}» στη γραμμή {i} του αρχείου καρτών"
            )
        punches.append(
            KartaPunch(
                afm="".join(ch for ch in afm if ch.isdigit()),
                last_name=v.get(col.get("last"), ""),
                first_name=v.get(col.get("first"), ""),
                day=day,
                time_in=_parse_time(v.get(col["in"], "")),
                time_out=_parse_time(v.get(col["out"], "")),
            )
        )
    return punches


def _norm(text: str) -> str:
    """Κείμενο επικεφαλίδας χωρίς τόνους, κεφαλαία, κενά και σύμβολα:
    «Ημ/νία» -> «ημνια», «ΑΑ Παραρτηματος» -> «ααπαραρτηματος»."""
    t = unicodedata.normalize("NFD", text or "")
    return "".join(ch for ch in t if ch.isalnum()).lower()


# Ονόματα στηλών (κανονικοποιημένα με `_norm`) -> πεδίο
_HEADER_ALIASES = {
    "αφμ": "afm",
    "επωνυμο": "last",
    "ονομα": "first",
    "ημνια": "day", "ημερομηνια": "day", "ημερα": "day",
    "απο": "in", "εισοδος": "in", "ωραεισοδου": "in",
    "εως": "out", "εξοδος": "out", "ωραεξοδου": "out",
}
_REQUIRED = {"afm", "day", "in", "out"}
_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%y")


def _parse_day(text: str) -> date | None:
    """Ημερομηνία σε όποια μορφή τη γράψει το ΕΡΓΑΝΗ ή το Excel: Η/Μ/ΕΕΕΕ
    (και με - ή .), ISO, με ώρα στο τέλος, ή αριθμός ημερομηνίας Excel."""
    t = (text or "").split()[0] if (text or "").strip() else ""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    try:  # αριθμός Excel: ημέρες από 30/12/1899
        return date(1899, 12, 30) + timedelta(days=int(float(t)))
    except (ValueError, OverflowError):
        return None


def _parse_time(text: str) -> str:
    """Ώρα -> «ΩΩ:ΛΛ»: «8:01», «08:01:00», «14/9/2026 08:01» ή κλάσμα
    ημέρας του Excel (0.5 = 12:00). Ό,τι δεν είναι ώρα μένει ως έχει
    (κενό = τυφλό χτύπημα, §5.11)."""
    t = (text or "").strip()
    if not t:
        return ""
    t = t.split()[-1]
    if ":" in t:
        hh, mm = t.split(":")[:2]
    else:
        try:
            m = round(float(t) % 1 * 24 * 60) % (24 * 60)
        except ValueError:
            return t
        hh, mm = m // 60, m % 60
    try:
        return f"{int(hh):02d}:{int(mm):02d}"
    except ValueError:
        return t


def daily_hours(punches: list[KartaPunch]) -> dict[tuple[str, date], float]:
    """Αθροίζει τις βάρδιες σε ώρες εργασίας ανά (ΑΦΜ, ημέρα).

    Οι μη έγκυρες/μηδενικές βάρδιες αγνοούνται. Το κλειδί είναι ο ΑΦΜ (η μόνη
    αξιόπιστη ταυτότητα του αρχείου· τα ονόματα είναι συχνά μεταγραμμένα)."""
    out: dict[tuple[str, date], int] = {}
    for p in punches:
        if p.minutes <= 0:
            continue
        out[(p.afm, p.day)] = out.get((p.afm, p.day), 0) + p.minutes
    return {k: round(mins / 60.0, 2) for k, mins in out.items()}


def daily_shifts(punches: list[KartaPunch]) -> dict[tuple[str, date], list[str]]:
    """Οι αναλυτικές βάρδιες ανά (ΑΦΜ, ημέρα), ως "HH:MM-HH:MM" με χρονική
    σειρά — τροφοδοτούν την κατάταξη ωρών (work_hours.py).

    ⚠️ Γραμμή με ΜΟΝΟ είσοδο ("HH:MM-") ή ΜΟΝΟ έξοδο ("-HH:MM") ΚΡΑΤΙΕΤΑΙ:
    γίνεται μοναχικό χτύπημα κάρτας (ανοιχτή βάρδια / ορφανή έξοδος, §5.11),
    ορατό ως λάθος — όχι σιωπηλή απώλεια."""
    out: dict[tuple[str, date], list[tuple[int, str]]] = {}
    for p in punches:
        a, b = _hm_opt(p.time_in), _hm_opt(p.time_out)
        if a is None and b is None or a == b:
            continue
        tin = p.time_in.strip() if a is not None else ""
        tout = p.time_out.strip() if b is not None else ""
        out.setdefault((p.afm, p.day), []).append(
            (a if a is not None else b, f"{tin}-{tout}")
        )
    return {k: [s for _, s in sorted(v)] for k, v in out.items()}
