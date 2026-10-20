"""
Ψηφιακή Αναγγελία Λήξης ανά αιτία: οικειοθελής -> WebE5N (με δήλωση PDF),
λήξη ορισμένου -> WebE7N, καταγγελία -> καμία αποστολή (δεν υπάρχει στο API).

Εκτέλεση: uv run pytest tests/test_ergani_exit.py
"""

import json
from datetime import date

import _bootstrap  # noqa: F401
import pytest
from test_exit_settlement import hire, make_svc

from misevent.integrations.ergani import ErganiClient

PDF = b"%PDF-1.4 fake"
TERM = date(2026, 9, 30)


def _sent(proj):
    """(doc_type, πρώτο κλειδί payload) των υποβολών λήξης."""
    return [(s.doc_type, next(iter(json.loads(s.payload))))
            for s in proj.ergani_submissions if s.doc_type == "E5_LIXI"]


def _real_client(svc, monkeypatch):
    """Πραγματικός (όχι simulate) client με ψεύτικο δίκτυο — καταγράφει το endpoint."""
    calls = []
    client = ErganiClient(username="u", password="p", environment="trial", simulate=False)
    monkeypatch.setattr(client, "authenticate", lambda: setattr(client, "_token", "T"))
    monkeypatch.setattr(client, "_post", lambda path, payload, auth=True:
                        calls.append(path) or {"protocol": "ΑΠ-1"})
    svc.ergani = client
    return calls


def test_voluntary_sends_web_e5n_with_resignation(monkeypatch):
    svc, proj, _ = make_svc()
    eid = hire(svc, proj, 0)
    calls = _real_client(svc, monkeypatch)
    # Πραγματική αποστολή χωρίς δήλωση: ΑΠΟΡΡΙΠΤΕΤΑΙ πριν καταχωρηθεί τίποτα
    with pytest.raises(ValueError, match="δήλωση αποχώρησης"):
        svc.terminate(eid, TERM, "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ")
    assert proj.employments[eid].active_on(TERM)
    with pytest.raises(ValueError, match="PDF"):
        svc.terminate(eid, TERM, "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ", resignation_pdf=b"GIF89a")

    assert svc.terminate(eid, TERM, "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ", resignation_pdf=PDF) == ""
    assert calls[-1] == "/Documents/WebE5N"
    assert _sent(proj) == [("E5_LIXI", "AnaggeliesE5N")]
    assert "PDF 1 KB · sha256" in proj.ergani_submissions[-1].payload  # όχι base64


def test_fixed_term_end_sends_web_e7n_only_for_fixed_term():
    svc, proj, _ = make_svc()
    eid = hire(svc, proj, 0)  # αορίστου
    with pytest.raises(ValueError, match="μόνο σύμβαση ορισμένου"):
        svc.terminate(eid, TERM, "ΛΗΞΗ_ΣΥΜΒΑΣΗΣ_ΟΡΙΣΜΕΝΟΥ")
    proj.employments[eid].employment_relationship = "1"  # ορισμένου (συντόμευση test)
    svc.terminate(eid, TERM, "ΛΗΞΗ_ΣΥΜΒΑΣΗΣ_ΟΡΙΣΜΕΝΟΥ")
    assert _sent(proj) == [("E5_LIXI", "AnaggeliesE7N")]


def test_dismissal_is_not_sent_and_says_so():
    svc, proj, _ = make_svc()
    eid = hire(svc, proj, 0)
    note = svc.terminate(eid, TERM, "ΚΑΤΑΓΓΕΛΙΑ_ΣΥΜΒΑΣΗΣ")
    assert "portal" in note and _sent(proj) == []
    assert svc.severance(eid) is not None  # η εκκαθάριση γίνεται κανονικά
