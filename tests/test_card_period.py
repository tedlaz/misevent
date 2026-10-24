"""
ΨΗΦΙΑΚΗ ΚΑΡΤΑ ΣΤΗΝ ΑΛΥΣΙΔΑ («Διάφορα → digital_card», default ναι).

- κάθε περίοδος έχει «Εισαγωγή κάρτας περιόδου» (CARD_IMPORT), προαπαιτούμενο
  της έγκρισης παρουσιών·
- ο έλεγχος απορρίπτει όσο λείπει η κάρτα ή υπάρχει χτύπημα χωρίς ταίρι·
- απενεργοποίηση κλείνει τις εκκρεμείς, επανενεργοποίηση τις ξαναγεννά.

Εκτέλεση: uv run pytest tests/test_card_period.py
"""

from datetime import date

import pytest
from test_closed_period import setup

import misevent.domain.dependencies as deps

FEB = [date(2026, 2, d) for d in range(1, 29)]


def _ob(proj, code, y=2026, m=2):
    return next(o for o in proj.obligations.values()
                if o.obligation_type == code and (o.period_year, o.period_month) == (y, m))


def _approval_locked(proj):
    index = deps.build_index(proj.obligations.values())
    return bool(deps.unmet_prerequisites(_ob(proj, "ATTENDANCE_APPROVAL"), index))


def test_card_import_gates_approval(fresh_system):
    svc, proj, eid = setup()  # πρόγραμμα 09-17 Δε–Πα
    emp = proj.employments[eid]
    assert not _ob(proj, "CARD_IMPORT").fulfilled and _approval_locked(proj)

    with pytest.raises(ValueError, match="Κανένα χτύπημα"):
        svc.check_card_period(2026, 2)

    days = [d for d in FEB if emp.day_schedule(d)]
    for d in days[1:]:
        svc.record_attendance(eid, d, ("09:00-17:00",))
    # ορφανό (χωρίς έξοδο): μετρά με το πρόγραμμα, δεν μπλοκάρει
    svc.record_card_punch(eid, days[0], "09:00", "in")
    assert svc.check_card_period(2026, 2) == {"no_punch": 1}
    assert _ob(proj, "CARD_IMPORT").fulfilled and not _approval_locked(proj)


def test_toggle_digital_card(fresh_system):
    svc, proj, _eid = setup()
    svc.set_misc_settings(digital_card=False)
    assert _ob(proj, "CARD_IMPORT").fulfilled and not _approval_locked(proj)

    svc.set_misc_settings(digital_card=True)  # ξαναγεννιέται στην ανοιχτή περίοδο
    pending = [o for o in proj.obligations.values()
               if o.obligation_type == "CARD_IMPORT" and not o.fulfilled]
    assert [(o.period_year, o.period_month) for o in pending] == [(2026, 2)]
    assert _approval_locked(proj)


def test_card_import_from_attendance(fresh_system):
    """UI: η κάρτα εισάγεται ΜΟΝΟ στις Παρουσίες — το modal της CARD_IMPORT
    δείχνει εκεί· χωρίς κάρτα η ενεργοποίηση απορρίπτεται με τον λόγο, η
    εισαγωγή στις Παρουσίες τρέχει τον έλεγχο και την ολοκληρώνει."""
    import io
    import json

    import _bootstrap

    from misevent.integrations import karta_import
    from webapp import create_app
    from webapp.system import proj

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert c.post("/seed").status_code == 200  # ΜΕ ψηφιακή κάρτα (default)
    with app.test_request_context("/"):
        index = deps.build_index(proj.obligations.values())
        ob = min((o for o in proj.obligations.values()
                  if o.obligation_type == "CARD_IMPORT" and not o.fulfilled
                  and not deps.unmet_prerequisites(o, index)),
                 key=lambda o: (o.period_year, o.period_month))
        emps = [e for e in proj.employments.values() if e.hire_submitted]
    y, m = ob.period_year, ob.period_month

    modal = c.get(f"/obligations/modal?id={ob.obligation_id}").text
    assert 'name="karta"' not in modal and "/attendance" in modal

    def flash(r):
        return json.loads(r.headers["HX-Trigger"])["flash"]

    f = flash(c.post(f"/obligations/{ob.obligation_id}/run"))
    assert f["err"] and "κάρτα" in f["msg"].lower()

    # το «ιδανικό» αρχείο κάρτας κάθε εργαζομένου (ακριβώς το πρόγραμμα), σε ΕΝΑ
    punches = []
    for e in emps:
        r = c.get(f"/employments/{e.employment_id}/calendar/karta.xlsx?m={y}-{m:02d}")
        punches += karta_import.parse_karta(r.data)
    data = {"karta": (io.BytesIO(karta_import.build_karta(punches)), "karta.xlsx"),
            "y": str(y), "m": str(m)}
    covered = "Εισαγωγή κάρτας ✓ εντάξει"
    assert covered not in c.get(f"/attendance/?y={y}&m={m}").text
    r = c.post("/attendance/import", data=data, content_type="multipart/form-data")
    f = flash(r)
    assert not f["err"] and "ελέγχθηκε" in f["msg"], f["msg"]
    # σημαία: η κάρτα καλύπτει ΟΛΕΣ τις ημέρες εργασίας — συνολικά και ανά άτομο
    assert covered in r.text and r.text.count("💳 πλήρης κάρτα") == len(emps)

    # Κενό ΜΟΝΟ σε αργία = εντάξει, με το όνομά της· κενό σε εργάσιμη = «λείπουν»
    from datetime import date

    from webapp import queries as q
    from webapp.system import svc

    with app.test_request_context("/"):
        eid = emps[0].employment_id
        d = dict(svc.attendance_draft(y, m)[eid], source="recorded", card_needed=1)
        rules = q.config.work_hour_rules_for(date(y, m, 1))
        hol = q._attendance_row(y, m, eid, {**d, "no_punch_days": [date(y, 5, 1)]},
                                {}, rules)
        gap = q._attendance_row(y, m, eid, {**d, "no_punch_days": [
            date(y, 5, 1), date(y, 5, 4)]}, {}, rules)
    assert hol["card_cover"] == "holiday" and hol["missing_holidays"] == ["Πρωτομαγιά"]
    assert hol["no_punch_hint"] == f"01/05/{y} (Πρωτομαγιά)"
    assert gap["card_cover"] == "partial" and gap["missing_holidays"] == ["Πρωτομαγιά"]
    assert proj.obligations[ob.obligation_id].fulfilled

    # η «Έγκριση παρουσιών» δείχνει αναλυτικά ανά εργαζόμενο
    appr = next(o for o in proj.obligations.values()
                if o.obligation_type == "ATTENDANCE_APPROVAL"
                and (o.period_year, o.period_month) == (y, m))
    html = c.get(f"/obligations/modal?id={appr.obligation_id}").text
    assert html.count('class="ob-block"') == len(emps) and "Ημέρες εργασίας" in html


def test_attendance_items_only_nonzero():
    """«Έγκριση παρουσιών» αναλυτικά: μόνο τα στοιχεία με τιμή."""
    from webapp.queries import attendance_items

    row = {"planned": 20, "worked": 20, "unpaid": 0, "leave": 0, "efka_days": 25,
           "sick_half": 0, "sick_full": 0, "sick_benefit": 0.0,
           "normal": 160.0, "overwork": 1.5, "extra_n": 2.0, "overtime_h": 0.0}
    assert attendance_items(row) == [
        ("Προγραμματισμένες ημέρες", "20 ημ."), ("Ημέρες εργασίας", "20 ημ."),
        ("Ημέρες ΕΦΚΑ", "25 ημ."),
        ("Εντός ωραρίου (ημέρα, εργάσιμη)", "160 ώρ."),
        ("Πρόσθετη εργασία (νύχτα)", "2 ώρ."),
        ("Υπερεργασία (ημέρα, εργάσιμη)", "1,5 ώρ."),
    ]
