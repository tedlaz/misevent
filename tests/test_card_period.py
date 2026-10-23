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


def test_obligation_modal_uploads_card(fresh_system):
    """UI: το modal της CARD_IMPORT έχει upload· χωρίς κάρτα η ενεργοποίηση
    απορρίπτεται με τον λόγο, με το αρχείο κάρτας ολοκληρώνεται."""
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

    assert 'name="karta"' in c.get(f"/obligations/modal?id={ob.obligation_id}").text

    def flash(r):
        return json.loads(r.headers["HX-Trigger"])["flash"]

    f = flash(c.post(f"/obligations/{ob.obligation_id}/run"))
    assert f["err"] and "κάρτα" in f["msg"].lower()

    # το «ιδανικό» αρχείο κάρτας κάθε εργαζομένου (ακριβώς το πρόγραμμα), σε ΕΝΑ
    punches = []
    for e in emps:
        r = c.get(f"/employments/{e.employment_id}/calendar/karta.xlsx?m={y}-{m:02d}")
        punches += karta_import.parse_karta(r.data)
    data = {"karta": (io.BytesIO(karta_import.build_karta(punches)), "karta.xlsx")}
    f = flash(c.post(f"/obligations/{ob.obligation_id}/run", data=data,
                     content_type="multipart/form-data"))
    assert not f["err"], f["msg"]
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
