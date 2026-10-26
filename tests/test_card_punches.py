"""
Tests ΨΗΦΙΑΚΗΣ ΚΑΡΤΑΣ ΕΡΓΑΣΙΑΣ (φάση 1: καταγραφή διελεύσεων & ζευγάρωμα).

Καλύπτει ό,τι είναι εύκολο να «απλοποιηθεί» λάθος:
  * το ΖΕΥΓΑΡΩΜΑ εισόδου/εξόδου (Λάζαρος 09:03 in + 16:59 out -> 09:03-16:59),
  * τα ΕΛΛΙΠΗ που ΔΕΝ κρύβονται: ανοιχτή βάρδια, ορφανή έξοδος, διπλοχτύπημα,
  * τη ΝΥΧΤΕΡΙΝΗ βάρδια που περνά τα μεσάνυχτα (ένα διάστημα, όχι δύο),
  * τους ελέγχους του command layer (διπλή καταγραφή, μη ενεργή ημέρα),
  * την ΑΚΥΡΩΣΗ ως αντισταθμιστικό event (το log μένει, το projection όχι),
  * ότι το read model ξαναχτίζεται ΙΔΙΟ από τα events (κανόνας #3).

Εκτέλεση: uv run tests/test_card_punches.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.app.services import PayrollService
from misevent.domain.projections import Projections
from misevent.integrations import card_punches as cp
from misevent.persistence.database import Database

FULL_WEEK = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}
DAY = date(2026, 8, 5)          # Τετάρτη


def _setup():
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    pid = svc.register_person(
        last_name="ΛΑΖΑΡΟΣ", first_name="ΤΕΝΤ", father_name="Α", mother_name="Β",
        birth_date=date(1990, 1, 1), afm="100011110", amka="01019012341",
        ama="123456789",
    )
    eid = svc.start_employment(pid, date(2026, 1, 5), monthly_gross=1400.0,
                               weekly_pattern=FULL_WEEK)
    svc.assign_insurance_profile(
        eid,
        ergani_specialty_code="419",
        ergani_specialty_description="ΛΟΙΠΟΙ ΥΠΑΛΛΗΛΟΙ ΓΡΑΦΕΙΟΥ",
        coverages=({"fund": "efka_main", "kad": "0000",
                    "specialty_code": "000000", "kpk": "0101"},),
    )
    svc.submit_hire_e3(eid)
    return store, proj, svc, eid


# --------------------------------------------------------- καθαρό ζευγάρωμα
def test_pairing_basic():
    """Το παράδειγμα της προδιαγραφής: μία είσοδος + μία έξοδος = μία βάρδια."""
    shifts = cp.pair_punches([
        {"m": 9 * 60 + 3, "kind": "in", "id": "a"},
        {"m": 16 * 60 + 59, "kind": "out", "id": "b"},
    ])
    assert len(shifts) == 1
    assert cp.shift_texts(shifts) == ("09:03-16:59",)
    assert shifts[0]["open"] is False and shifts[0]["orphan"] is False
    assert cp.worked_minutes(shifts) == 476
    print("OK ζευγάρωμα 09:03 in + 16:59 out -> 09:03-16:59")


def test_pairing_two_shifts_and_order():
    """Διάλειμμα = δύο βάρδιες. Η σειρά εισόδου των χτυπημάτων δεν μετράει."""
    shifts = cp.pair_punches([
        {"m": 16 * 60, "kind": "in"}, {"m": 20 * 60, "kind": "out"},
        {"m": 9 * 60, "kind": "in"}, {"m": 13 * 60, "kind": "out"},
    ])
    assert cp.shift_texts(shifts) == ("09:00-13:00", "16:00-20:00")
    print("OK δύο βάρδιες ημέρας, ανεξάρτητα από τη σειρά καταχώρησης")


def test_incomplete_stays_visible():
    """⚠️ Τα ελλιπή ΔΕΝ «διορθώνονται»: μένουν σημαδεμένα για να φανούν."""
    # είσοδος χωρίς έξοδο
    shifts = cp.pair_punches([{"m": 9 * 60, "kind": "in"}])
    assert shifts[0]["open"] and shifts[0]["e"] is None
    assert cp.shift_texts(shifts) == ()      # καμία κλειστή βάρδια
    # έξοδος χωρίς είσοδο
    shifts = cp.pair_punches([{"m": 17 * 60, "kind": "out"}])
    assert shifts[0]["orphan"] and shifts[0]["s"] is None
    # δύο είσοδοι στη σειρά: η πρώτη μένει ανοιχτή, η δεύτερη ζευγαρώνει
    shifts = cp.pair_punches([
        {"m": 8 * 60, "kind": "in"}, {"m": 12 * 60, "kind": "in"},
        {"m": 16 * 60, "kind": "out"},
    ])
    assert [s["open"] for s in shifts] == [True, False]
    assert cp.shift_texts(shifts) == ("12:00-16:00",)
    print("OK ανοιχτή βάρδια / ορφανή έξοδος / διπλή είσοδος παραμένουν ορατές")


def test_double_punch_ignored():
    """Δεύτερο πέρασμα κάρτας μέσα σε λεπτά = ο ΙΔΙΟΣ ΑΠΟΗΧΟΣ, όχι διέλευση."""
    shifts = cp.pair_punches([
        {"m": 9 * 60, "kind": "in"}, {"m": 9 * 60 + 1, "kind": "in"},
        {"m": 17 * 60, "kind": "out"},
    ])
    assert cp.shift_texts(shifts) == ("09:00-17:00",)
    print("OK διπλοχτύπημα ίδιου τύπου αγνοείται (κρατά την πρώτη ώρα)")


def test_forgotten_out_does_not_swallow_days():
    """Ξεχασμένη έξοδος ΔΕΝ ζευγαρώνει με την έξοδο της επόμενης ημέρας."""
    shifts = cp.pair_punches([
        {"m": 9 * 60, "kind": "in"},                 # Δευτέρα 09:00
        {"m": 24 * 60 + 17 * 60, "kind": "out"},     # Τρίτη 17:00
    ])
    assert [(s["open"], s["orphan"]) for s in shifts] == [(True, False), (False, True)]
    print("OK ξεχασμένη έξοδος: δύο ελλιπείς εγγραφές, όχι βάρδια 32 ωρών")


# ------------------------------------------------------------ command layer
def test_record_and_shifts():
    _store, proj, svc, eid = _setup()
    svc.record_card_punch(eid, DAY, "09:03", "in")
    svc.record_card_punch(eid, DAY, "16:59", "out")
    emp = proj.employments[eid]
    assert [p["m"] for p in emp.punches_on(DAY)] == [543, 1019]
    assert svc.card_shift_texts(eid, DAY) == ("09:03-16:59",)
    print("OK καταγραφή διελεύσεων -> βάρδιες από τα projections")


def test_night_shift_crosses_midnight():
    """22:00 -> 06:00 είναι ΕΝΑ διάστημα της ημέρας που ΑΡΧΙΣΕ."""
    _store, proj, svc, eid = _setup()
    svc.record_card_punch(eid, DAY, "22:00", "in")
    svc.record_card_punch(eid, date(2026, 8, 6), "06:00", "out")
    emp = proj.employments[eid]
    shifts = emp.card_shifts(DAY)
    assert len(shifts) == 1 and shifts[0]["s"] == 22 * 60
    assert shifts[0]["e"] == 30 * 60          # 06:00 της επομένης (+24ω)
    # η επόμενη ημέρα ΔΕΝ ξαναδηλώνει την ίδια βάρδια
    assert emp.card_shifts(date(2026, 8, 6)) == []
    print("OK νυχτερινή βάρδια = ΕΝΑ διάστημα που περνά τα μεσάνυχτα")


def test_live_board_keeps_night_shift_on_exit_day():
    """Το παράθυρο που αρχίζει την ημέρα εξόδου κρατά την ουρά της βάρδιας."""
    import webapp.system as system

    _store, proj, svc, eid = _setup()
    system.proj, system.svc = proj, svc
    import webapp.queries as q

    q.proj, q.svc = proj, svc
    next_day = date(2026, 8, 6)
    svc.record_card_punch(eid, DAY, "22:00", "in")
    svc.record_card_punch(eid, next_day, "06:00", "out")
    row = next(r for r in q.live_board(next_day, next_day, 1)["rows"]
               if r["eid"] == eid)
    assert row["actual"] == [{
        "s": 0, "e": 6 * 60, "open": False, "emax": 0,
        "text": "22:00-06:00", "h": 8.0,
    }]
    assert [(p["m"], p["kind"]) for p in row["punches"]] == [(360, "out")]
    assert row["ref"]["acts"] == [0]
    assert row["ref"]["acte"] == [360]
    print("OK η ημέρα εξόδου κρατά παρουσία 00:00-06:00 και το χτύπημα εξόδου")


def test_duplicate_and_inactive_rejected():
    _store, _proj, svc, eid = _setup()
    svc.record_card_punch(eid, DAY, "09:00", "in")
    for args, expect in (
        ((eid, DAY, "09:00", "in"), "ήδη"),          # ίδια στιγμή & τύπος
        ((eid, DAY, "25:00", "in"), "ώρα"),          # άκυρη ώρα
        ((eid, DAY, "09:00", "μέσα"), "είσοδος"),    # άκυρος τύπος
        ((eid, date(2025, 1, 5), "09:00", "in"), "ενεργή"),  # πριν την πρόσληψη
    ):
        try:
            svc.record_card_punch(*args)
        except ValueError as exc:
            assert expect in str(exc), (args, str(exc))
        else:
            raise AssertionError(f"έπρεπε να απορριφθεί: {args}")
    # ΑΝΤΙΘΕΤΩΣ: έξοδος χωρίς είσοδο ΕΠΙΤΡΕΠΕΤΑΙ (η κάρτα καταγράφει, δεν κρίνει)
    svc.record_card_punch(eid, DAY, "18:00", "out")
    print("OK απορρίπτονται μόνο τα αδύνατα· η ελλιπής καταγραφή περνά")


def test_cancel_is_compensating():
    """Η ακύρωση είναι ΝΕΟ event: το log κρατά και τα δύο, το projection όχι."""
    store, proj, svc, eid = _setup()
    pid = svc.record_card_punch(eid, DAY, "09:03", "in")
    svc.record_card_punch(eid, DAY, "16:59", "out")
    svc.cancel_card_punch(pid, "λάθος καταχώρηση")
    emp = proj.employments[eid]
    assert [p["m"] for p in emp.punches_on(DAY)] == [1019]
    assert emp.card_shifts(DAY)[0]["orphan"] is True   # έμεινε ορφανή έξοδος
    kinds = [type(ev).__name__ for _agg, ev in store.load()]
    assert kinds.count("CardPunchRecorded") == 2 and "CardPunchCancelled" in kinds
    print("OK ακύρωση = αντισταθμιστικό event (το log δεν πειράζεται)")


def test_projection_rebuild_drops_cancelled_punch():
    """Κανόνας #3: η προβολή ξαναχτίζεται ΙΔΙΑ από τα events — και η ακύρωση
    (αντισταθμιστικό event) εξαφανίζει τη διέλευση και μετά το replay."""
    store, _proj, svc, eid = _setup()
    pid = svc.record_card_punch(eid, DAY, "09:03", "in")
    svc.record_card_punch(eid, DAY, "16:59", "out")
    svc.record_card_punch(eid, DAY, "18:00", "in")
    svc.cancel_card_punch(pid)
    rebuilt = Projections()
    rebuilt.rebuild(store)
    punches = rebuilt.employments[eid].punches_on(DAY)
    assert [(p["m"], p["kind"]) for p in punches] == [
        (16 * 60 + 59, "out"), (18 * 60, "in")
    ]
    print("OK η προβολή card_punches ξαναχτίζεται σωστά με replay")


def test_live_board_shows_punches():
    """Το grid «Ζωντανά» δείχνει τα σημεία διέλευσης και την ΑΝΟΙΧΤΗ βάρδια."""
    import webapp.system as system

    store, proj, svc, eid = _setup()
    system.store, system.proj, system.svc = store, proj, svc
    import webapp.queries as q

    q.proj, q.svc = proj, svc
    svc.record_card_punch(eid, DAY, "09:03", "in")
    board = q.live_board(DAY, DAY, 1)
    row = next(r for r in board["rows"] if r["eid"] == eid)
    assert [p["kind"] for p in row["punches"]] == ["in"]
    assert row["punches"][0]["tone"] == "open"
    bar = row["actual"][0]
    assert bar["open"] and bar["s"] == 543 and bar["emax"] == 24 * 60
    # η ανοιχτή βάρδια μετρά ως «εν εξελίξει» για την κατάσταση της ώρας
    assert row["ref"]["acte"] == [2 * 24 * 60]
    svc.record_card_punch(eid, DAY, "16:59", "out")
    row = next(r for r in q.live_board(DAY, DAY, 1)["rows"] if r["eid"] == eid)
    assert [p["kind"] for p in row["punches"]] == ["in", "out"]
    assert row["actual"][0]["open"] is False
    assert row["actual"][0]["text"] == "09:03-16:59"
    assert row["actual"][0]["h"] == 7.93
    print("OK το grid δείχνει διελεύσεις, ανοιχτή βάρδια και το κλείσιμό της")


def test_attendance_derived_from_punches_only():
    """Οι παρουσίες ΒΓΑΙΝΟΥΝ από την κάρτα: κλειστή βάρδια = παρουσία,
    ανοιχτή/ακυρωμένη = καμία· η καταγραφή ημέρας γράφει χτυπήματα."""
    _store, proj, svc, eid = _setup()
    emp = proj.employments[eid]
    pin = svc.record_card_punch(eid, DAY, "09:00", "in")
    assert DAY not in emp.attendance  # ανοιχτή βάρδια δεν μετρά
    pout = svc.record_card_punch(eid, DAY, "17:00", "out")
    assert emp.attendance[DAY] == 8.0
    assert emp.attendance_shifts[DAY] == ["09:00-17:00"]
    svc.cancel_card_punch(pout)
    assert DAY not in emp.attendance
    svc.cancel_card_punch(pin)
    svc.record_attendance(eid, DAY, ["22:00-02:00"])
    assert emp.attendance_shifts[DAY] == ["22:00-02:00"]
    svc.record_attendance(eid, DAY, ["10:00-14:00"])  # αντικαθιστά
    assert emp.attendance_shifts[DAY] == ["10:00-14:00"]
    assert emp.attendance[DAY] == 4.0
    print("OK οι παρουσίες είναι παράγωγο των χτυπημάτων κάρτας")


def test_missing_in_or_out_become_lone_punches():
    """Γραμμή κάρτας χωρίς έξοδο/είσοδο ΔΕΝ χάνεται: γίνεται ανοιχτή βάρδια /
    ορφανή έξοδος — και δεν μετρά ως παρουσία."""
    from misevent.integrations import karta_import as ki

    rows = [ki.KartaPunch("1", "", "", DAY, "09:00", ""),
            ki.KartaPunch("1", "", "", DAY, "", "21:00"),
            ki.KartaPunch("2", "", "", DAY, "", "")]
    assert ki.daily_shifts(rows) == {("1", DAY): ["09:00-", "-21:00"]}
    _store, proj, svc, eid = _setup()
    emp = proj.employments[eid]
    svc.record_attendance(eid, DAY, ["09:00-"])  # λείπει η έξοδος
    assert [(s["open"], s["orphan"]) for s in emp.card_shifts(DAY)] == [(True, False)]
    assert DAY not in emp.attendance
    svc.record_attendance(eid, DAY, ["-17:00"])  # λείπει η είσοδος (αντικαθιστά)
    assert [(s["open"], s["orphan"]) for s in emp.card_shifts(DAY)] == [(False, True)]
    assert DAY not in emp.attendance
    print("OK τα ελλιπή χτυπήματα της κάρτας ΕΡΓΑΝΗ μένουν ορατά")


def test_orphan_completed_from_schedule_with_monthly_cap():
    """Ορφανό χτύπημα -> το ταίρι από το ΠΡΟΓΡΑΜΜΑ (09:00-17:00), έως
    `orphan_month_cap` (3) ανά εργαζόμενο τον μήνα."""
    _store, proj, svc, eid = _setup()
    emp = proj.employments[eid]
    lone = svc.record_card_punch(eid, DAY, "09:04", "in")      # χωρίς έξοδο
    svc.complete_punch_from_schedule(lone)
    assert emp.attendance_shifts[DAY] == ["09:04-17:00"]  # ακριβείς ώρες
    day2 = date(2026, 8, 6)
    lone = svc.record_card_punch(eid, day2, "17:20", "out")    # χωρίς είσοδο
    svc.complete_punch_from_schedule(lone)
    assert emp.attendance_shifts[day2] == ["09:00-17:20"]
    with_pair = svc.record_card_punch(eid, date(2026, 8, 7), "09:00", "in")
    svc.record_card_punch(eid, date(2026, 8, 7), "17:00", "out")
    try:
        svc.complete_punch_from_schedule(with_pair)
        raise AssertionError("πλήρης βάρδια δεν συμπληρώνεται")
    except ValueError:
        pass
    svc.complete_punch_from_schedule(
        svc.record_card_punch(eid, date(2026, 8, 10), "09:00", "in"))
    lone = svc.record_card_punch(eid, date(2026, 8, 11), "09:00", "in")
    try:
        svc.complete_punch_from_schedule(lone)  # 4ο του μήνα
        raise AssertionError("πέρασε 4ο ορφανό")
    except ValueError as exc:
        assert "Εξαντλήθηκαν τα 3" in str(exc)
    assert emp.schedule_completions(2026, 8) == 3
    print("OK συμπλήρωση ορφανών από το πρόγραμμα, έως 3 τον μήνα")


def _karta_xlsx(rows) -> bytes:
    """Ελάχιστο xlsx ΕΡΓΑΝΗ (μόνο το φύλλο) — αρκεί στο `parse_karta`."""
    import io
    import zipfile

    from misevent.integrations import karta_import as ki

    def row(i, vals):
        return f'<row r="{i}">' + "".join(
            f'<c r="{c}{i}" t="str"><v>{v}</v></c>' for c, v in zip("ABCDEFG", vals)
        ) + "</row>"

    # (ΑΦΜ, Επώνυμο, Όνομα, ISO ημ/νία, Από, Έως) -> γραμμή ΕΡΓΑΝΗ (Η/Μ/ΕΕΕΕ)
    rows = [("0", *r[:3], date.fromisoformat(r[3]).strftime("%d/%m/%Y"), *r[4:])
            for r in rows]
    xml = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
           '2006/main"><sheetData>'
           + "".join(row(i, r) for i, r in enumerate([ki.COLUMNS, *rows], 1))
           + "</sheetData></worksheet>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", xml)
    return buf.getvalue()


def test_import_corrects_blind_punches_and_keeps_the_fact():
    """Εισαγωγή: τα ΤΥΦΛΑ χτυπήματα διορθώνονται από το πρόγραμμα ΚΑΤΑ την
    εισαγωγή, και η πληροφορία «τυφλό» μένει μόνιμα στο χτύπημα."""
    from misevent.domain.events import LeaveType

    _store, proj, svc, eid = _setup()
    emp = proj.employments[eid]
    svc.record_leave(eid, LeaveType.SICK, date(2026, 8, 7), date(2026, 8, 7))
    res = svc.import_karta_attendance(_karta_xlsx([
        ("100011110", "Λ", "Τ", "2026-08-05", "09:02", ""),       # χωρίς έξοδο
        ("100011110", "Λ", "Τ", "2026-08-06", "", "17:04"),       # χωρίς είσοδο
        ("100011110", "Λ", "Τ", "2026-08-07", "09:00", "17:00"),
    ]), 2026, 8)
    assert [b["fixed"] for b in res["blind"]] == [True, True]
    assert emp.attendance_shifts[date(2026, 8, 5)] == ["09:02-17:00"]  # ακριβείς ώρες
    assert emp.attendance_shifts[date(2026, 8, 6)] == ["09:00-17:04"]
    blind = [p for d in emp.card_punches.values() for p in d if p["blind"]]
    assert [(p["kind"], cp.hhmm(p["m"])) for p in blind] == [
        ("in", "09:02"), ("out", "17:04")]
    assert emp.schedule_completions(2026, 8) == 2
    # χτύπημα σε ημέρα άδειας: καταγράφεται, αλλά βγαίνει προειδοποίηση
    assert [b["day"] for b in res["on_leave"]] == [date(2026, 8, 7)]
    assert emp.attendance_shifts[date(2026, 8, 7)] == ["09:00-17:00"]
    print("OK τα τυφλά χτυπήματα διορθώνονται στην εισαγωγή και μένουν σημαδεμένα")


def test_card_rounding_for_calculations_only():
    """Στρογγυλοποίηση κάρτας: η ΣΥΝΟΛΙΚΗ διάρκεια της ημέρας πάει στην
    κοντινότερη μισή ώρα (η διαφορά στη λήξη της τελευταίας βάρδιας)· η κάρτα
    κρατά την ακριβή ώρα."""
    t = cp.parse_time

    def hours(*pairs, step=30):
        out = cp.rounded([{"s": t(a), "e": t(b)} for a, b in pairs], step)
        return cp.worked_minutes(out) / 60

    assert hours(("09:00", "16:24")) == 7.5   # 7,4 -> 7,5
    assert hours(("09:00", "16:12")) == 7.0   # 7,2 -> 7
    assert hours(("09:00", "16:42")) == 7.5   # 7,7 -> 7,5 (κοντινότερη μισή)
    assert hours(("09:00", "16:48")) == 8.0   # 7,8 -> 8
    assert hours(("09:00", "17:06")) == 8.0   # 8,1 -> 8
    assert hours(("09:00", "17:12")) == 8.0   # 8,2 -> 8
    assert hours(("09:00", "17:15")) == 8.5   # μισό βήμα -> πάνω
    out = cp.rounded([{"s": t("08:52"), "e": t("17:12")}], 30)
    assert cp.shift_texts(out) == ("08:52-17:22",)  # προσέλευση ακριβής
    # σπαστό: το σύνολο μετρά· αρνητική διαφορά > τελευταίας βάρδιας -> προηγούμενη
    out = cp.rounded([{"s": 540, "e": 782}, {"s": 960, "e": 970}], 30)
    assert [(s["s"], s["e"]) for s in out] == [(540, 780)]
    assert cp.rounded([{"s": 601, "e": 604}], 15) == []  # μηδενίστηκε
    assert cp.rounded([{"s": 601, "e": None}], 15) == [{"s": 601, "e": None}]
    assert cp.rounded([{"s": 602, "e": 1088}], 0) == [{"s": 602, "e": 1088}]
    assert cp.rounded_texts(["09:00-17:12"], 30) == ["09:00-17:00"]
    assert cp.rounded_texts(["22:00-06:10"], 30) == ["22:00-06:00"]  # μεσάνυχτα
    print("OK στρογγυλοποίηση διάρκειας")


def test_rounding_only_in_sheet_proposals(fresh_system):
    """Παρουσίες/ΕΡΓΑΝΗ ΠΑΝΤΑ ακριβείς· η στρογγυλοποίηση (προεπιλογή 0)
    αγγίζει μόνο τις ΠΡΟΤΕΙΝΟΜΕΝΕΣ ώρες του φύλλου (hours_analysis)."""
    _store, proj, svc, eid = _setup()
    svc.record_card_punch(eid, DAY, "09:00", "in")
    svc.record_card_punch(eid, DAY, "17:12", "out")
    emp = proj.employments[eid]
    assert emp.attendance_shifts[DAY] == ["09:00-17:12"]
    assert emp.attendance[DAY] == 8.2
    assert svc.hours_analysis(2026, 8)[eid]["overwork"] == 0.2  # 9η ώρα: 12΄
    svc.define_parameters("work_hours", date(2026, 1, 1), night_start=22,
                          night_end=6, card_rounding_minutes=30)
    assert svc.hours_analysis(2026, 8)[eid]["overwork"] == 0.0  # 8,2ω -> 8ω
    assert emp.attendance_shifts[DAY] == ["09:00-17:12"]        # άθικτες
    print("OK στρογγυλοποίηση μόνο στις προτάσεις του φύλλου")
