"""
Οθόνη ΕΡΓΑΝΗ (`/ergani/`) — η ΜΟΝΗ αποστολή στο ΕΡΓΑΝΗ.

  · η καρτέλα ΔΕΝ στέλνει (ούτε WebE3N, ούτε Ε4 με την αλλαγή σύμβασης)·
  · οι εκκρεμότητες εμφανίζονται εδώ και στέλνονται από εδώ·
  · οι υποχρεώσεις ΕΡΓΑΝΗ του χρονολογίου (`labels.ERGANI_ONLY`) ΔΕΝ
    ενεργοποιούνται από το χρονολόγιο.

⚠️ Ζητά `fresh_system`: στήνει εταιρεία, πρόσωπα, συμβάσεις.
"""

import json
from datetime import date, timedelta


def test_ergani_screen(fresh_system):
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from webapp import create_app, labels
    from webapp.actions import FULL_TIME
    from webapp.clock import today
    from webapp.system import proj, svc

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200
    assert c.get("/work-schedules/").status_code == 302      # → /ergani/
    assert "Πρόγραμμα → ΕΡΓΑΝΗ" not in c.get("/").get_data(as_text=True)

    def subs(doc):
        return [s for s in proj.ergani_submissions if s.doc_type == doc]

    # ── Ε3: νέα πρόσληψη -> εκκρεμεί εδώ, όχι κουμπί αποστολής στην καρτέλα
    pid = svc.register_person(
        last_name="ΝΕΟΣ", first_name="ΕΡΓΑΖΟΜΕΝΟΣ", father_name="Π",
        mother_name="Μ", birth_date=date(1990, 5, 5),
        afm="090034337", amka="05059012350", ama="123456788")
    with app.test_request_context("/"):
        start = today().replace(day=1)
    new = svc.start_employment(pid, start, monthly_gross=1300.0,
                               weekly_pattern=FULL_TIME)
    svc.assign_specialty(new, "ΠΩΛΗΤΗΣ", auxiliary_scheme="efka")
    card = c.get(f"/employments/{new}").get_data(as_text=True)
    assert f"/employments/{new}/ergani/e3\"" not in card
    assert "/ergani/#ergani-e3" in card
    page = c.get("/ergani/").get_data(as_text=True)
    assert f"/ergani/e3/{new}" in page
    c.post(f"/ergani/e3/{new}")
    assert proj.employments[new].hire_submitted
    assert f"/ergani/e3/{new}" not in c.get("/ergani/").get_data(as_text=True)

    # ── Ε4: η αλλαγή σύμβασης από την καρτέλα ΔΕΝ στέλνει — εκκρεμεί εδώ
    eid = next(e.employment_id for e in proj.employments.values()
               if e.active and e.hire_submitted and e.employment_id != new)
    before = len(subs("E4_METAVOLI"))
    nxt = (start + timedelta(days=40)).replace(day=1)
    r = c.post(f"/employments/{eid}/contract", data={
        "pay_type": "monthly", "amount": "2100", "effective_from": nxt.isoformat(),
        "weekly_hours": "40", "weekly_system": "5"})
    assert r.status_code == 200
    assert len(subs("E4_METAVOLI")) == before, "η αλλαγή σύμβασης έστειλε Ε4"
    n = svc.pending_metavoli(eid)[-1][0]
    assert f"/ergani/e4/{eid}/{n}" in c.get("/ergani/").get_data(as_text=True)
    c.post(f"/ergani/e4/{eid}/{n}")
    assert subs("E4_METAVOLI")[-1].subject == f"E4:{n}"
    assert not svc.pending_metavoli(eid)

    # ── Πρόγραμμα για ΔΥΟ εργαζόμενους = ΜΙΑ υποβολή, ΜΙΑ γραμμή στο ιστορικό
    with app.test_request_context("/"):
        ref = today()
    day = ref - timedelta(days=ref.weekday()) + timedelta(days=16)   # Τετάρτη
    monday = day - timedelta(days=day.weekday())
    two = [e for e in proj.employments.values()
           if e.active and e.hire_submitted and e.employment_id != new][:2]
    for e in two:
        svc.set_daily_schedule(e.employment_id, {day: "10:00-18:00"})
    n_before = len(proj.ergani_submissions)
    html = c.post("/ergani/schedules", data={
        "week": [f"{e.employment_id}|{monday.isoformat()}" for e in two]}).get_data(as_text=True)
    assert len(proj.ergani_submissions) == n_before + 1, "ΜΙΑ κλήση για όλους"
    assert all(e.day_status(day) == "submitted" for e in two)
    who = {", ".join(e.full_name for e in o) for o in (two, two[::-1])}
    assert any(w in html for w in who), "το «Αφορά» δείχνει και τους δύο"

    # ── JSON υποβολής σε MODAL: ΚΑΘΕ υποβολή του ιστορικού ανοίγει,
    #    χωρίς να φορτώνεται το JSON στη σελίδα
    import re

    page = c.get("/ergani/").get_data(as_text=True)
    assert "data-sortable data-filterable" in page       # ταξινόμηση + φίλτρα
    assert "data-nosort data-nofilter>JSON" in page
    seqs = re.findall(r'/ergani/submission/(\d+)', page)
    assert len(seqs) == len(proj.ergani_submissions)
    for seq in seqs:
        m = c.get(f"/ergani/submission/{seq}")
        assert m.status_code == 200
        body = m.get_data(as_text=True)
        assert 'id="ergani-json-raw"' in body and "Κλείσιμο" in body
    bodies = [c.get(f"/ergani/submission/{q}").get_data(as_text=True) for q in seqs]
    assert any("IsxysApo" in b for b in bodies)        # το Ε4 της αλλαγής σύμβασης
    assert c.get("/ergani/submission/999999").status_code == 404

    # ── Υποχρέωση ΕΡΓΑΝΗ: το χρονολόγιο ΔΕΝ την ενεργοποιεί
    ob = next(o for o in proj.obligations.values() if not o.fulfilled)
    labels.ERGANI_ONLY.add(ob.obligation_type)
    try:
        r = c.post(f"/obligations/{ob.obligation_id}/run")
        flash = json.loads(r.headers["HX-Trigger"])["flash"]
        assert flash["err"] and "ΜΟΝΟ από την οθόνη ΕΡΓΑΝΗ" in flash["msg"]
        assert not proj.obligations[ob.obligation_id].fulfilled
        modal = c.get(f"/obligations/modal?id={ob.obligation_id}").get_data(as_text=True)
        assert "Υποβολή από την οθόνη ΕΡΓΑΝΗ" in modal
    finally:
        labels.ERGANI_ONLY.discard(ob.obligation_type)
