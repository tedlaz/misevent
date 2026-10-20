"""
ΜΕΤΑΒΟΛΗ στοιχείων προσώπου με ημ/νία ισχύος (PersonDetailsChanged).

Η διόρθωση (✏️) ισχύει «από πάντα»· η μεταβολή (🔄) ΑΠΟ μια ημερομηνία. Ο
λόγος που μετράει: τα ΤΕΚΝΑ μπαίνουν στον ΦΜΥ — κάθε υπολογισμός πρέπει να
παίρνει όσα ίσχυαν στην περίοδό του, όχι τα σημερινά.

Εκτέλεση: uv run pytest tests/test_person_changes.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd
import pytest
from test_leave_allowance import setup

from misevent.domain.projections import Projections


def test_children_change_applies_from_its_date():
    svc, proj, eid = setup(date(2026, 1, 5), schedule=False)
    emp = proj.employments[eid]
    pid = emp.person_id
    before = svc.gift_preview_for(emp, "christmas", 2026).fmy

    svc.change_person_details(pid, date(2026, 6, 1), children=2)
    assert emp.children == 2
    assert emp.children_on(date(2026, 5, 31)) == 0
    assert emp.children_on(date(2026, 6, 1)) == 2
    # Το Δώρο Χριστουγέννων (καταβολή 21/12) παίρνει τα 2 τέκνα -> λιγότερος ΦΜΥ
    assert svc.gift_preview_for(emp, "christmas", 2026).fmy < before

    # Αναδρομική καταχώρηση ΔΕΝ πατά νεότερη μεταβολή του ίδιου πεδίου
    svc.change_person_details(pid, date(2026, 3, 1), children=1)
    assert emp.children == 2
    assert emp.children_on(date(2026, 4, 1)) == 1

    # Διόρθωση = λάθος στην τρέχουσα τιμή -> αλλάζει μόνο η τελευταία εγγραφή
    svc.correct_person(pid, children=3)
    assert emp.children_on(date(2026, 7, 1)) == 3
    assert emp.children_on(date(2026, 4, 1)) == 1

    rebuilt = Projections()
    rebuilt.rebuild(svc.store)
    p = rebuilt.persons[pid]
    assert [p.children_on(d) for d in (date(2026, 2, 1), date(2026, 4, 1),
                                       date(2026, 7, 1))] == [0, 1, 3]
    assert len(p.changes) == 2


def test_change_rejections():
    svc, proj, eid = setup(date(2026, 1, 5), schedule=False)
    pid = proj.employments[eid].person_id
    with pytest.raises(ValueError, match="δεν μεταβάλλονται"):
        svc.change_person_details(pid, date(2026, 6, 1), afm="123456789")
    with pytest.raises(ValueError, match="Καμία αλλαγή"):
        svc.change_person_details(pid, date(2026, 6, 1), children=0)
    # Τα τέκνα αγγίζουν τον ΦΜΥ: όχι μέσα σε κλεισμένη μισθοδοσία
    svc._latest_closed_payroll = lambda: (2026, 3)
    with pytest.raises(ValueError, match="έχει ήδη κλείσει"):
        svc.change_person_details(pid, date(2026, 3, 15), children=1)
    # …ενώ μη οικονομικά στοιχεία επιτρέπονται
    svc.change_person_details(pid, date(2026, 3, 15), email="new@example.com")
    assert proj.persons[pid].email == "new@example.com"
    # Μετακόμιση: η διεύθυνση μεταβάλλεται, ο τόπος γέννησης ΜΟΝΟ διορθώνεται
    svc.change_person_details(pid, date(2026, 4, 1), address="  ΝΕΑ   ΟΔΟΣ 5 ",
                              postal_code="190 16")
    assert (proj.persons[pid].address, proj.persons[pid].postal_code) == (
        "ΝΕΑ ΟΔΟΣ 5", "19016")
    with pytest.raises(ValueError, match="δεν μεταβάλλονται"):
        svc.change_person_details(pid, date(2026, 4, 1), birth_place="ΑΘΗΝΑ")
    svc.correct_person(pid, birth_place="ΑΘΗΝΑ")
    assert proj.persons[pid].birth_place == "ΑΘΗΝΑ"
