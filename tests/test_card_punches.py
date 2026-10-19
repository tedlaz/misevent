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
