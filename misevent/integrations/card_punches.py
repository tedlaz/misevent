"""
ΨΗΦΙΑΚΗ ΚΑΡΤΑ ΕΡΓΑΣΙΑΣ — ΑΝΤΙΣΤΟΙΧΙΣΗ ΕΙΣΟΔΩΝ ΜΕ ΕΞΟΔΟΥΣ (καθαρές συναρτήσεις).

Η κάρτα καταγράφει ΩΜΑ ΓΕΓΟΝΟΤΑ: «ποιος · πότε · είσοδος ή έξοδος». Η βάρδια
ΔΕΝ καταγράφεται — **ΠΑΡΑΓΕΤΑΙ** από το ζευγάρωμα δύο διελεύσεων:

    Λάζαρος 05/08/2026 09:03 in  ┐
    Λάζαρος 05/08/2026 16:59 out ┘ ->  Λάζαρος 05/08/2026 09:03-16:59

⚠️ **ΤΑ ΧΤΥΠΗΜΑΤΑ ΔΕΝ «ΔΙΟΡΘΩΝΟΝΤΑΙ» ΓΙΑ ΝΑ ΒΓΕΙ ΖΕΥΓΑΡΙ.** Η πραγματικότητα
είναι βρόμικη (ξεχασμένη έξοδος, διπλό χτύπημα, έξοδος χωρίς είσοδο) και το
σύστημα οφείλει να τη ΔΕΙΞΕΙ, όχι να την κρύψει: κάθε διέλευση που δεν βρήκε
ταίρι επιστρέφεται σημαδεμένη (`open` / `orphan`) ώστε να φανεί στην οθόνη και
να τη διορθώσει άνθρωπος με νέα καταχώρηση.

ΟΙ ΩΡΕΣ ΕΙΝΑΙ ΑΠΟΛΥΤΑ ΛΕΠΤΑ από ένα κοινό σημείο μηδέν (π.χ. τα μεσάνυχτα της
ημέρας αναφοράς) — έτσι η νυχτερινή βάρδια 22:00→06:00 είναι ΕΝΑ διάστημα που
περνά τα μεσάνυχτα, χωρίς καμία ειδική περίπτωση.
"""

from __future__ import annotations

IN = "in"
OUT = "out"
KINDS = (IN, OUT)

# Ανοιχτή είσοδος πέρα από αυτό το όριο ΔΕΝ ζευγαρώνει: κανείς δεν δουλεύει
# 20 ώρες — μια είσοδος Δευτέρας δεν επιτρέπεται να «κλείσει» με την έξοδο της
# Τρίτης, γιατί τότε μια ξεχασμένη έξοδος θα εξαφάνιζε ολόκληρη ημέρα.
MAX_SHIFT_MIN = 16 * 60
# Δύο χτυπήματα ΙΔΙΟΥ τύπου μέσα σε αυτό το διάστημα = διπλοχτύπημα (ο
# εργαζόμενος πέρασε δεύτερη φορά την κάρτα), όχι νέα διέλευση.
DOUBLE_MIN = 2


def normalize(punches) -> list[dict]:
    """Ταξινόμηση κατά ώρα (και «in» πριν από «out» στην ίδια ακριβώς στιγμή:
    μια μηδενικής διάρκειας βάρδια είναι πιο λογική από ένα ορφανό ζεύγος)."""
    return sorted(
        (dict(p) for p in punches),
        key=lambda p: (p["m"], 0 if p.get("kind") == IN else 1),
    )


def pair_punches(punches, max_shift: int = MAX_SHIFT_MIN) -> list[dict]:
    """Διελεύσεις -> ΒΑΡΔΙΕΣ.

    `punches`: [{"m": λεπτά, "kind": "in"|"out", "id": …}, …] (οποιαδήποτε
    σειρά — ταξινομούνται εδώ).

    Επιστρέφει λίστα βαρδιών κατά ώρα έναρξης:
      {"s", "e", "open", "orphan", "in_id", "out_id"}
    - κανονική βάρδια: `s` < `e`, `open` = `orphan` = False
    - **ανοιχτή** (είσοδος χωρίς έξοδο — εργάζεται τώρα ή ξέχασε να χτυπήσει):
      `e` = None, `open` = True
    - **ορφανή έξοδος** (έξοδος χωρίς είσοδο): `s` = None, `orphan` = True
    """
    shifts: list[dict] = []
    open_in: dict | None = None
    last: dict | None = None

    def close_open():
        nonlocal open_in
        if open_in is not None:
            shifts.append({"s": open_in["m"], "e": None, "open": True,
                           "orphan": False, "in_id": open_in.get("id"),
                           "out_id": None})
            open_in = None

    for p in normalize(punches):
        kind = p.get("kind")
        if kind not in KINDS:
            continue
        # Διπλοχτύπημα ίδιου τύπου: κρατάμε το ΠΡΩΤΟ (η ώρα που όντως πέρασε
        # την πόρτα) και αγνοούμε τον απόηχο.
        if last and last["kind"] == kind and p["m"] - last["m"] <= DOUBLE_MIN:
            continue
        last = p
        if kind == IN:
            close_open()          # προηγούμενη είσοδος χωρίς έξοδο: μένει ανοιχτή
            open_in = p
            continue
        # OUT
        if open_in is None or p["m"] - open_in["m"] > max_shift:
            # Έξοδος χωρίς (εύλογη) είσοδο. Η προηγούμενη ανοιχτή είσοδος
            # ΔΕΝ ζευγαρώνει αναδρομικά — παραμένει ανοιχτή, ορατή ως λάθος.
            close_open()
            shifts.append({"s": None, "e": p["m"], "open": False,
                           "orphan": True, "in_id": None,
                           "out_id": p.get("id")})
            continue
        shifts.append({"s": open_in["m"], "e": p["m"], "open": False,
                       "orphan": False, "in_id": open_in.get("id"),
                       "out_id": p.get("id")})
        open_in = None
    close_open()
    shifts.sort(key=lambda s: (s["s"] if s["s"] is not None else s["e"]))
    return shifts


def hhmm(minute) -> str:
    """Λεπτά -> «ΩΩ:ΛΛ» (24ωρη μορφή· τα λεπτά πέρα από το 24ωρο γυρίζουν)."""
    m = int(minute) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def parse_time(text: str) -> int:
    """«ΩΩ:ΛΛ» -> λεπτά από τα μεσάνυχτα. Σφάλμα στα ελληνικά αν δεν είναι ώρα."""
    try:
        hh, _sep, mm = str(text).strip().partition(":")
        h, m = int(hh), int(mm)
    except ValueError:
        raise ValueError(f"Μη έγκυρη ώρα: {text!r} (μορφή ΩΩ:ΛΛ)") from None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Μη έγκυρη ώρα: {text!r} (μορφή ΩΩ:ΛΛ)")
    return h * 60 + m


def shift_texts(shifts) -> tuple:
    """Οι ΚΛΕΙΣΤΕΣ βάρδιες ως «ΩΩ:ΛΛ-ΩΩ:ΛΛ» — η μορφή που περιμένει το
    `AttendanceRecorded.shifts` (και μέσω αυτού η κατάταξη ωρών)."""
    return tuple(
        f"{hhmm(s['s'])}-{hhmm(s['e'])}"
        for s in shifts
        if s["s"] is not None and s["e"] is not None
    )


def worked_minutes(shifts) -> int:
    """Άθροισμα των ΚΛΕΙΣΤΩΝ βαρδιών σε λεπτά (οι ανοιχτές δεν μετρούν:
    δεν ξέρουμε ακόμη πότε τελείωσαν)."""
    return sum(
        s["e"] - s["s"] for s in shifts
        if s["s"] is not None and s["e"] is not None
    )
