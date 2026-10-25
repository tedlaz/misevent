"""
Ψηφιακή Αναγγελία Λήξης & Δήλωση Μεταβολής: η αποχώρηση / αλλαγή σύμβασης
ΚΑΤΑΓΡΑΦΕΤΑΙ χωρίς αποστολή — το έντυπο μένει ΕΚΚΡΕΜΕΣ και στέλνεται ΜΟΝΟ από
την οθόνη ΕΡΓΑΝΗ (`submit_exit` / `submit_metavoli`).

Ανά αιτία λήξης: οικειοθελής -> WebE5N (με τη δήλωση PDF από τα έγγραφα),
λήξη ορισμένου -> WebE7N, καταγγελία -> καμία αποστολή (δεν υπάρχει στο API·
σημειώνεται ως υποβληθείσα εκτός συστήματος).

Εκτέλεση: uv run pytest tests/test_ergani_exit.py
"""

import json
from datetime import date

import _bootstrap  # noqa: F401
import pytest
from test_exit_settlement import hire, make_svc

from misevent.domain.events import ErganiSubmissionRecorded
from misevent.integrations.ergani import ErganiClient

PDF = b"%PDF-1.4 fake"
TERM = date(2026, 9, 30)


def _sent(proj, doc="E5_LIXI"):
    """(doc_type, πρώτο κλειδί payload) των υποβολών του τύπου."""
    return [(s.doc_type, next(iter(json.loads(s.payload)), None))
            for s in proj.ergani_submissions if s.doc_type == doc]


def _real_client(svc, monkeypatch):
    """Πραγματικός (όχι simulate) client με ψεύτικο δίκτυο — καταγράφει το endpoint."""
    calls = []
    client = ErganiClient(username="u", password="p", environment="trial", simulate=False)
    monkeypatch.setattr(client, "authenticate", lambda: setattr(client, "_token", "T"))
    monkeypatch.setattr(client, "_post", lambda path, payload, auth=True:
                        calls.append(path) or {"protocol": "ΑΠ-1"})
    svc.ergani = client
    return calls


def test_voluntary_exit_is_pending_then_sent_with_resignation(monkeypatch):
    svc, proj, _ = make_svc()
    eid = hire(svc, proj, 0)
    calls = _real_client(svc, monkeypatch)
    with pytest.raises(ValueError, match="PDF"):
        svc.terminate(eid, TERM, "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ", resignation_pdf=b"GIF89a")

    # Η αποχώρηση ΔΕΝ στέλνει τίποτα — μένει εκκρεμής
    assert svc.terminate(eid, TERM, "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ") == ""
    assert calls == [] and _sent(proj) == [] and svc.exit_pending(eid)
    # Πραγματική αποστολή χωρίς δήλωση: απορρίπτεται, η εκκρεμότητα μένει
    with pytest.raises(ValueError, match="δήλωση αποχώρησης"):
        svc.submit_exit(eid)
    assert svc.exit_pending(eid)

    svc.documents.put(proj.employments[eid].person_id, "resignation", "r.pdf", PDF)
    svc.submit_exit(eid)
    assert calls[-1] == "/Documents/WebE5N"
    assert _sent(proj) == [("E5_LIXI", "AnaggeliesE5N")]
    assert "PDF 1 KB · sha256" in proj.ergani_submissions[-1].payload  # όχι base64
    assert not svc.exit_pending(eid)
    with pytest.raises(ValueError, match="δεν εκκρεμεί"):
        svc.submit_exit(eid)


def test_resignation_pdf_given_at_exit_is_kept_for_submission(monkeypatch):
    svc, proj, _ = make_svc()
    eid = hire(svc, proj, 0)
    calls = _real_client(svc, monkeypatch)
    svc.terminate(eid, TERM, "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ", resignation_pdf=PDF)
    assert calls == []
    svc.submit_exit(eid)                    # το PDF ήρθε από τα έγγραφα
    assert calls[-1] == "/Documents/WebE5N"


def test_fixed_term_end_sends_web_e7n_only_for_fixed_term():
    svc, proj, _ = make_svc()
    eid = hire(svc, proj, 0)  # αορίστου
    with pytest.raises(ValueError, match="μόνο σύμβαση ορισμένου"):
        svc.terminate(eid, TERM, "ΛΗΞΗ_ΣΥΜΒΑΣΗΣ_ΟΡΙΣΜΕΝΟΥ")
    proj.employments[eid].employment_relationship = "1"  # ορισμένου (συντόμευση test)
    svc.terminate(eid, TERM, "ΛΗΞΗ_ΣΥΜΒΑΣΗΣ_ΟΡΙΣΜΕΝΟΥ")
    assert _sent(proj) == []
    svc.submit_exit(eid)
    assert _sent(proj) == [("E5_LIXI", "AnaggeliesE7N")]


def test_dismissal_is_not_sent_and_is_recorded_as_manual():
    svc, proj, _ = make_svc()
    eid = hire(svc, proj, 0)
    note = svc.terminate(eid, TERM, "ΚΑΤΑΓΓΕΛΙΑ_ΣΥΜΒΑΣΗΣ")
    assert "portal" in note and _sent(proj) == []
    assert svc.severance(eid) is not None  # η εκκαθάριση γίνεται κανονικά
    assert svc.exit_pending(eid)
    with pytest.raises(ValueError, match="portal"):
        svc.submit_exit(eid)
    with pytest.raises(ValueError, match="πρωτοκόλλου"):
        svc.record_external_exit_submission(eid, "  ")
    svc.record_external_exit_submission(eid, "ΑΠ-77")
    assert not svc.exit_pending(eid)
    last = proj.ergani_submissions[-1]
    assert (last.status, last.reference, last.environment) == ("manual", "ΑΠ-77", "portal")


def test_contract_change_metavoli_is_pending_with_its_own_terms():
    svc, proj, _ = make_svc()
    eid = hire(svc, proj, 0)
    svc.change_contract(eid, date(2026, 5, 1), "monthly", monthly_gross=1600.0)
    svc.change_contract(eid, date(2026, 6, 1), "monthly", monthly_gross=1700.0)
    assert _sent(proj, "E4_METAVOLI") == [], "η αλλαγή σύμβασης δεν στέλνει τίποτα"
    pending = svc.pending_metavoli(eid)
    assert [n for n, _ in pending] == [0, 1]

    # Η ΠΑΛΑΙΟΤΕΡΗ στέλνεται με τα ΔΙΚΑ ΤΗΣ ποσά, όχι της νεότερης
    svc.submit_metavoli(eid, 0)
    sent = json.loads(proj.ergani_submissions[-1].payload)
    assert sent["IsxysApo"] == "2026-05-01"
    assert sent["NeaStoixeia"]["MiktesApodoxes"] == 1600.0
    assert proj.ergani_submissions[-1].subject == "E4:0"
    assert [n for n, _ in svc.pending_metavoli(eid)] == [1]
    with pytest.raises(ValueError, match="δεν εκκρεμεί"):
        svc.submit_metavoli(eid, 0)


def test_legacy_metavoli_without_subject_covers_changes_in_order():
    """Παλιά Ε4 (αυτόματα, χωρίς `subject`) ΔΕΝ ξαναβγαίνουν ως εκκρεμή."""
    svc, proj, _ = make_svc()
    eid = hire(svc, proj, 0)
    svc.change_contract(eid, date(2026, 5, 1), "monthly", monthly_gross=1600.0)
    svc.store.append(eid, ErganiSubmissionRecorded(
        "E4_METAVOLI", eid, "{}", "simulated", "SIM-1", "trial"))
    svc.change_contract(eid, date(2026, 6, 1), "monthly", monthly_gross=1700.0)
    assert [n for n, _ in svc.pending_metavoli(eid)] == [1]


def test_change_before_hire_announcement_is_covered_by_e3():
    svc, proj, _ = make_svc()
    eid = hire(svc, proj, 0)
    proj.employments[eid].hire_submitted = False      # (συντόμευση: «πριν το Ε3»)
    svc.change_contract(eid, date(2026, 5, 1), "monthly", monthly_gross=1600.0)
    assert svc.pending_metavoli(eid) == []
