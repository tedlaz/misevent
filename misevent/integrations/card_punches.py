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


def completion_minute(shift: dict, segs) -> int | None:
    """Το ΤΑΙΡΙ ορφανού χτυπήματος από το ΠΡΟΓΡΑΜΜΑ (`segs` = τμήματα φυσικής
    παρουσίας της ημέρας, `constraints.parse_day_schedule`): η ανοιχτή
    είσοδος παίρνει τη ΛΗΞΗ του πλησιέστερου τμήματος, η ορφανή έξοδος την
    ΑΡΧΗ του. None αν δεν υπάρχει πρόγραμμα ή το ταίρι πέφτει ανάποδα."""
    if not segs:
        return None
    if shift["open"]:
        at = shift["s"]
        minute = min(segs, key=lambda s: abs(s["start"] - at))["end"]
        return minute if minute > at else None
    at = shift["e"]
    minute = min(segs, key=lambda s: abs(s["end"] - at))["start"]
    return minute if minute < at else None


def rounded(shifts, step: int) -> list[dict]:
    """Οι βάρδιες της ΗΜΕΡΑΣ με ΣΤΡΟΓΓΥΛΕΜΕΝΗ ΣΥΝΟΛΙΚΗ ΔΙΑΡΚΕΙΑ — ΜΟΝΟ για
    τις ΠΡΟΤΕΙΝΟΜΕΝΕΣ ώρες του φύλλου· παρουσίες, ΕΡΓΑΝΗ και προβολή κρατούν
    τις ακριβείς ώρες (ο νόμος δεν προβλέπει στρογγυλοποίηση/ανοχή).

    Το σύνολο των κλειστών βαρδιών πάει στο κοντινότερο πολλαπλάσιο του
    `step` (μισό βήμα προς τα πάνω· με 30΄: 7,2ω -> 7, 7,4ω -> 7,5, 8,2ω -> 8)
    και η διαφορά μπαίνει στη ΛΗΞΗ της τελευταίας βάρδιας (η προσέλευση μένει
    η πραγματική): 08:52-17:12 (8ω20΄) -> 08:52-17:22 (8,5ω).
    Βάρδια που μηδενίζεται από τη στρογγυλοποίηση αφαιρείται."""
    out = [dict(s) for s in shifts]
    if step <= 1:
        return out
    closed = [s for s in out if s["s"] is not None and s["e"] is not None]
    total = sum(s["e"] - s["s"] for s in closed)
    delta = round_total(total, step) - total
    for s in reversed(closed):  # αρνητική διαφορά μεγαλύτερη από βάρδια -> προηγούμενη
        new_e = max(s["e"] + delta, s["s"])
        delta -= new_e - s["e"]
        s["e"] = new_e
        if delta == 0:
            break
    return [s for s in out if s["s"] is None or s["e"] is None or s["s"] < s["e"]]


def rounded_texts(texts, step: int) -> list[str]:
    """`rounded` πάνω σε βάρδιες «ΩΩ:ΛΛ-ΩΩ:ΛΛ» (λήξη <= έναρξης = πέρασμα
    μεσανύχτων)."""
    shifts = []
    for t in texts:
        a, _, b = t.partition("-")
        s, e = parse_time(a), parse_time(b)
        shifts.append({"s": s, "e": e + 24 * 60 if e <= s else e})
    return list(shift_texts(rounded(shifts, step)))


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


# ------------------------------------- Συμφωνία προγράμματος ↔ κάρτας (ημέρα)
def schedule_text(actual) -> str:
    """Οι πραγματικές βάρδιες («ΩΩ:ΛΛ-ΩΩ:ΛΛ») ως ωράριο ημέρας. Αν δεν χωρούν
    στη μορφή του προγράμματος (> 2 τμήματα ή σπαστό με κενό < 3ω, π.χ.
    διάλειμμα), δίνεται το ενιαίο διάστημα πρώτη είσοδος–τελευταία έξοδος."""
    from misevent.rules import constraints

    if not actual:
        return ""
    text = "+".join(actual)
    try:
        constraints.parse_day_schedule(text)
        return text
    except ValueError:
        return f"{actual[0].split('-')[0]}-{actual[-1].split('-')[1]}"


def round_total(minutes: int, step: int) -> int:
    """Σύνολο λεπτών στο κοντινότερο πολλαπλάσιο του `step` (μισό βήμα προς
    τα πάνω· 30΄: 7,9ω -> 8, 8,2ω -> 8, 8,25ω -> 8,5). `step` <= 1 = καμία."""
    return (minutes + step // 2) // step * step if step > 1 else minutes


def day_matches(declared: str, actual: str, flex: int, tol: int = 0,
                step: int = 0) -> bool:
    """Συμφωνεί η κάρτα (`actual`, ωράριο ημέρας) με το δηλωμένο; ΜΙΑ πηγή
    για τη μισθοδοσία (ποια ημέρα πληρώνεται με το πρόγραμμα) ΚΑΙ για την
    απολογιστική πρόταση (ποια ημέρα δηλώνεται) — ARCH_REF §5.11.

    - ΑΝΟΧΗ `tol` (`card_tolerance_minutes`, πολιτική ΕΤΑΙΡΕΙΑΣ — ο νόμος δεν
      την προβλέπει): ΚΑΘΕ άκρο ±tol, ανεξάρτητα. 09:00-17:00 με 10΄ ->
      08:50-17:10, 09:10-16:50 εντάξει. 0 = καμία.
    - ΕΥΕΛΙΚΤΗ ΠΡΟΣΕΛΕΥΣΗ `flex` (Εγκ. 37271/2024 §Δ): μετρά ΜΕΤΑ την ανοχή.
      Καθυστέρηση > tol -> ΟΛΟ το πρόγραμμα μετατίθεται κατά την καθυστέρηση
      (έως flex) και τα υπόλοιπα άκρα κρίνονται ±tol από τα μετατεθειμένα:
      09:00-17:00, flex 30, tol 10 -> 09:15-17:18 εντάξει (17:15 + 3΄),
      09:15-17:00 όχι. Προσέλευση έως έναρξη + flex + tol.
    - ΣΤΡΟΓΓΥΛΟΠΟΙΗΣΗ `step` (`card_rounding_minutes`, πολιτική ΕΤΑΙΡΕΙΑΣ):
      με προσέλευση εντός του ευέλικτου παραθύρου συμφωνεί ΚΑΙ όταν η
      συνολική διάρκεια, στρογγυλεμένη στο `step`, ισούται με την
      προγραμματισμένη: 09:00-17:00, κάρτα 09:10-17:05 (7ω55΄) με 30΄ -> 8ω."""
    from misevent.rules import constraints

    try:
        a = constraints.parse_day_schedule(declared)
        b = constraints.parse_day_schedule(actual)
    except ValueError:
        return False
    if len(a) != len(b):
        return False
    if not a:
        return True
    delay = b[0]["start"] - a[0]["start"]
    if not -tol <= delay <= flex + tol:
        return False
    # εντός ανοχής = καμία μετάθεση· πέρα από αυτήν = ευέλικτη (έως flex)
    shift = 0 if delay <= tol else min(delay, flex)
    if step > 1:
        def total(segs):
            return sum(s["end"] - s["start"] for s in segs)
        if round_total(total(b), step) == round_total(total(a), step):
            return True
    rest = [(x["end"], y["end"]) for x, y in zip(a, b)]
    rest += [(x["start"], y["start"]) for x, y in zip(a[1:], b[1:])]
    return all(abs(y - x - shift) <= tol for x, y in rest)


def absence_gaps(declared: str, actual) -> list[tuple[int, int]]:
    """ΑΠΟΥΣΙΑ μέσα στο ωράριο: τα διαστήματα της ΦΥΣΙΚΗΣ παρουσίας του
    δηλωμένου (`declared`) που ΔΕΝ καλύπτει η κάρτα (`actual`, «ΩΩ:ΛΛ-ΩΩ:ΛΛ»)
    — μόνο όταν η κάρτα έχει ΛΙΓΟΤΕΡΕΣ ώρες από το πρόγραμμα (αλλιώς είναι
    μετάθεση ωραρίου, όχι απουσία). 09:00-17:00, κάρτα 09:00-15:00 ->
    [(15:00, 17:00)]."""
    from misevent.rules import constraints

    try:
        plan = [(s["start"], s["end"])
                for s in constraints.parse_day_schedule(declared) if not s["telework"]]
    except ValueError:
        return []
    work = []
    for t in actual:
        a, _, b = t.partition("-")
        s, e = parse_time(a), parse_time(b)
        work.append((s, e + 24 * 60 if e <= s else e))
    if sum(e - s for s, e in work) >= sum(e - s for s, e in plan):
        return []
    gaps = []
    for s, e in plan:
        cur = s
        for ws, we in sorted(work):
            if we <= cur or ws >= e:
                continue
            if ws > cur:
                gaps.append((cur, ws))
            cur = max(cur, we)
        if cur < e:
            gaps.append((cur, e))
    return gaps


def split_telework(day_text: str) -> tuple[str, list[str]]:
    """Ωράριο ημέρας -> (τμήματα ΦΥΣΙΚΗΣ παρουσίας ως ωράριο, τμήματα
    ΤΗΛΕΕΡΓΑΣΙΑΣ). Η τηλεεργασία ΔΕΝ χτυπά κάρτα — ισχύει ό,τι λέει το
    πρόγραμμα· με την κάρτα συγκρίνεται ΜΟΝΟ η φυσική παρουσία."""
    from misevent.rules import constraints

    try:
        segs = constraints.parse_day_schedule(day_text)
    except ValueError:
        return day_text, []
    return ("+".join(s["text"] for s in segs if not s["telework"]),
            [s["text"] for s in segs if s["telework"]])


def with_telework(actual_text: str, declared: str) -> str:
    """Το ωράριο της απολογιστικής δήλωσης: η ΚΑΡΤΑ για τη φυσική παρουσία +
    η ΤΗΛΕΕΡΓΑΣΙΑ του δηλωμένου αυτούσια. Αν ο συνδυασμός δεν χωρά στη μορφή
    ωραρίου (> 2 τμήματα, κενό < 3ω), μένει μόνο η κάρτα."""
    from misevent.rules import constraints

    tele = split_telework(declared)[1]
    if not tele:
        return actual_text
    parts = [actual_text] if actual_text else []
    text = "+".join(parts + ["Τ" + t for t in tele])
    try:
        segs = constraints.parse_day_schedule(text)
    except ValueError:
        return actual_text
    if [s["start"] for s in segs] != sorted(s["start"] for s in segs):
        return actual_text
    return text
