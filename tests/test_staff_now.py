"""
«Προσωπικό τώρα» της Επισκόπησης (`queries.staff_now`): κατάσταση από το
πρόγραμμα και επόμενη βάρδια.

⚠️ Ζητά `fresh_system`: στήνει εταιρεία και χρήστες.
"""


from datetime import timedelta


def test_staff_now(fresh_system):
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from webapp import create_app
    from webapp import queries as q
    from webapp.clock import today

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200

    body = c.get("/").get_data(as_text=True)
    assert 'data-fold="staff"' in body
    assert "data-sortable" in body, "ταξινόμηση με κλικ στην κεφαλίδα"
    assert "/employments/calendar?eid=" in body, "η κατάσταση ανοίγει το ημερολόγιο"
    assert "Μηνιαία εξέλιξη" not in body, "τα γραφήματα κόστους είναι στα Στατιστικά"
    assert "Μηνιαία εξέλιξη" in c.get("/stats").get_data(as_text=True)

    # «Ζωντανά» ως πτυσσόμενη κάρτα, ΕΝΑΣ #modal-host στη σελίδα, και το
    # context του widget δεν πατά το `detail` του χρονολογίου (modal `?id=`)
    assert 'id="lboard"' in body and 'data-fold="live"' in body
    assert body.count('id="modal-host"') == 1
    from webapp.system import proj
    ob = next(iter(proj.obligations))
    assert 'modal wide detail' in c.get(f"/?id={ob}").get_data(as_text=True)
    assert c.get("/live/").status_code == 302, "η σελίδα «Ζωντανά» καταργήθηκε"

    with app.test_request_context("/"):
        day = today()
        rows = q.staff_now(day, 0)
        assert rows, "το seed έχει ενεργούς εργαζόμενους"
        for r in rows:
            assert r["state"] in ("on", "soon", "done", "rest", "leave")
            if r["next"]:
                assert r["next"].split()[0] in ("Σήμερα", "Αύριο") or "/" in r["next"]
        # Μια ημέρα (εντός εβδομάδας) με ημερήσια βάρδια: πριν / στην έναρξη /
        # στη λήξη. Ακριβώς στην έναρξη, η επόμενη βάρδια ΔΕΝ είναι η τρέχουσα.
        cases = [(d, r) for d in (day + timedelta(days=i) for i in range(7))
                 for r in q.staff_now(d, 0)
                 if r["state"] != "leave" and (r["next"] or "").startswith("Σήμερα")]
        checked = False
        for day, r in cases:
            span = r["next"].split()[1]
            (h1, m1), (h2, m2) = (t.split(":") for t in span.split("-"))
            start, end = int(h1) * 60 + int(m1), int(h2) * 60 + int(m2)
            if end <= start:
                continue                  # νυχτερινή: απλούστερο σενάριο αλλού

            def at(m, eid=r["eid"]):
                return next(x for x in q.staff_now(day, m) if x["eid"] == eid)

            assert at(start)["state_text"].endswith(f"({span})")
            assert at(start)["next"] != r["next"]
            if start >= 120:
                assert at(start - 120)["state_text"] == "Εργασία σε 2 ώρες"
            assert at(end)["state_text"] == f"Εργάστηκε ({span})"
            checked = True
            break
        assert checked, "καμία ημερήσια βάρδια στην εβδομάδα του seed"
        # Κλειδί ταξινόμησης «Επόμενη βάρδια»: ISO, ίδια ώρα με το κείμενο
        for r in rows:
            if r["next"]:
                assert r["next_at"][11:] == r["next"].split()[-1][:5]
        # Στήλες άδειας
        for r in rows:
            assert r["remaining"] == r["entitlement"] - r["taken"]
