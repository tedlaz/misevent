"""
TRANSACTIONS στο event log: ό,τι γράφει ΜΙΑ εντολή (`run_command` ->
`event_store.transaction`) έχει κοινό `tx` και το audit το δείχνει ως ΜΙΑ
κίνηση με πολλές γραμμές.

- κοινό tx μέσα στο transaction (και φωλιασμένα), None έξω·
- παλιά βάση χωρίς στήλη `tx` -> migration· backup JSON κρατά το tx.

Εκτέλεση: uv run pytest tests/test_transactions.py
"""

import sqlite3

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


def test_old_database_gets_tx_column(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE events (store TEXT NOT NULL, seq INTEGER NOT NULL,
        event_id TEXT NOT NULL, aggregate_id TEXT NOT NULL, event_type TEXT NOT NULL,
        occurred_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (store, seq))""")
    conn.execute("INSERT INTO events VALUES ('s', 1, 'e1', 'MISC', 'MiscSettingsChanged',"
                 " '2026-01-01T00:00:00+00:00', '{\"fields\": {}}')")
    conn.commit()
    conn.close()
    store = Database(path).store("s")
    assert _txs(store) == [None]
    with transaction():
        store.append("MISC", MiscSettingsChanged({"save_zip": True}))
    assert _txs(store)[1]


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
    assert "γραμμές" in c.get("/audit/").text
