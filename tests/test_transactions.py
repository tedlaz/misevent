"""
TRANSACTIONS στο event log: ό,τι γράφει ΜΙΑ εντολή (`run_command` ->
`event_store.transaction`) έχει κοινό `tx` και το audit το δείχνει ως ΜΙΑ
κίνηση με πολλές γραμμές.

- κοινό tx μέσα στο transaction (και φωλιασμένα), None έξω·
- παλιά βάση χωρίς στήλη `tx` -> migration· backup JSON κρατά το tx.

Εκτέλεση: uv run pytest tests/test_transactions.py
"""


from misevent.domain.events import MiscSettingsChanged
from misevent.persistence.database import Database
from misevent.persistence.event_store import transaction


def _txs(store):
    return [r["tx"] for r in store.load_log()]


def test_events_of_one_transaction_share_tx():
    store = Database().store("test")
    with transaction():
        store.append("MISC", MiscSettingsChanged({"save_zip": True}))
        with transaction():  # φωλιασμένο = το ίδιο
            store.append("MISC", MiscSettingsChanged({"save_zip": False}))
    store.append("MISC", MiscSettingsChanged({"save_zip": True}))
    a, b, c = _txs(store)
    assert a and a == b and c is None

    # backup JSON -> restore: το tx ταξιδεύει
    doc = store.export_backup()
    other = Database().store("other")
    other.restore_backup(doc)
    assert _txs(other) == [a, b, None]


def test_audit_groups_one_command_into_one_row(fresh_system):
    import _bootstrap

    from webapp import create_app
    from webapp.views.audit import _transactions

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert c.post("/seed").status_code == 200  # ΜΙΑ εντολή, πολλά events
    from webapp.system import store

    log = store.load_log()
    rows = [{"seq": r["seq"], "tx": r["tx"], "label": r["event_type"], "who": "",
             "aggregate": r["aggregate_id"]} for r in log]
    groups = _transactions(rows)
    big = max(groups, key=lambda g: len(g["lines"]))
    assert len(big["lines"]) > 10 and big["seq"] == big["lines"][-1]["seq"]
    assert sum(len(g["lines"]) for g in groups) == len(log)
    assert "γραμμές" in c.get("/audit/", headers={"HX-Request": "true"}).text


def test_audit_is_company_tab_5(fresh_system):
    """Το Audit log ζει ως καρτέλα ⑤ της Εταιρείας· τα παλιά URL πάνε εκεί."""
    from _bootstrap import ui_client

    from webapp import create_app

    c = ui_client(create_app())
    page = c.get("/company/?tab=audit").text
    assert "⑤ Audit log" in page and 'id="tab-audit" checked' in page
    assert 'hx-get="/audit/?tab=audit"' in page
    r = c.get("/audit/?type=X")
    assert r.status_code == 302 and "/company/?" in r.headers["Location"]
    assert "tab=audit" in r.headers["Location"] and "type=X" in r.headers["Location"]
    r = c.post("/audit/rollback", data={})
    assert "/company/?tab=audit" in r.headers["Location"]


def test_audit_pagination(fresh_system):
    from _bootstrap import ui_client

    from webapp import create_app
    from webapp.system import current_tenant

    app = create_app()
    c = ui_client(app)
    with app.test_request_context():
        svc = current_tenant().svc
        for i in range(60):  # 60 κινήσεις → 2 σελίδες των 50
            svc.register_company(ame="1234567890", afm="999888771", legal_name=f"ΑΕ {i}")
    hx = {"HX-Request": "true"}
    first = c.get("/audit/", headers=hx).text
    assert "Σελίδα 1 / " in first and "page=2" in first and "ΑΕ 59" in first
    last = c.get("/audit/?page=99", headers=hx).text  # εκτός ορίων → τελευταία
    assert "« Πρώτη" in last and "ΑΕ 59" not in last


def test_audit_long_fields_behind_button(fresh_system):
    from _bootstrap import ui_client

    from webapp import create_app
    from webapp.system import current_tenant

    app = create_app()
    c = ui_client(app)
    with app.test_request_context():
        current_tenant().svc.register_company(
            ame="1234567890", afm="999888771", legal_name="ΠΟΛΥ " * 20)
    page = c.get("/audit/", headers={"HX-Request": "true"}).text
    assert 'class="tag">afm=999888771<' in page  # σύντομο: inline
    assert "▸ +1 πεδίο: legal_name" in page      # μεγάλο: πίσω από κουμπί
    assert 'class="tag">legal_name=' not in page
