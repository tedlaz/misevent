"""
Έγγραφα προσώπου (βάση εγγράφων ΕΚΤΟΣ event log) -> αναγγελία πρόσληψης WebE3N.

Εκτέλεση: uv run pytest tests/test_person_documents.py
"""

import base64
import io
import json
import zipfile
from datetime import date

import _bootstrap  # noqa: F401
import pytest

from misevent.app.services import PayrollService
from misevent.domain.projections import Projections
from misevent.integrations import ergani_parameters
from misevent.persistence.database import Database
from misevent.persistence.documents import DocumentStore
from misevent.rules import constraints

WEEK = {day: "09:00-17:00" for day in ("MON", "TUE", "WED", "THU", "FRI")}
PDF = b"%PDF-1.4 fake"
NON_EU = next(r["code"] for r in ergani_parameters.nationalities()
              if not ergani_parameters.is_eu_nationality(r["code"]))


def _valid(prefix: str, length: int, ok) -> str:
    """Πρώτος έγκυρος αριθμός (ΑΦΜ/ΑΜΚΑ) με αυτό το πρόθεμα."""
    return next(x for x in (f"{prefix}{n:0{length - len(prefix)}d}"
                            for n in range(10 ** (length - len(prefix)))) if ok(x))


def test_store_isolation_replace_and_delete():
    db = DocumentStore()
    a, b = db.scope("company:a"), db.scope("company:b")
    a.put("P1", "foreign_file", "x.pdf", PDF)
    assert b.list("P1") == [] and b.get("P1", "foreign_file") is None
    a.put("P1", "foreign_file", "y.pdf", PDF + b"2")  # ίδιο είδος -> αντικατάσταση
    (meta,) = a.list("P1")
    assert meta["filename"] == "y.pdf" and meta["size"] == len(PDF) + 1
    assert a.get("P1", "foreign_file")["content"] == PDF + b"2"
    assert not b.delete("P1", "foreign_file") and a.delete("P1", "foreign_file")
    assert a.list("P1") == []

    # snapshot -> restore: ΟΛΑ τα έγγραφα αντικαθίστανται ατομικά
    a.put("P1", "young_file", "z.pdf", PDF)
    snap = db.snapshot()
    a.delete("P1", "young_file")
    assert db.restore_snapshot(snap) == 1 and a.get("P1", "young_file")
    with pytest.raises(ValueError, match="εγγράφων"):
        db.restore_snapshot(b"not a db")


def _hired(birth_date, nationality_code, **hire):
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    pid = svc.register_person(
        last_name="ΔΟΚΙΜΗ", first_name="ΜΑΡΙΑ", father_name="ΙΩΑΝΝΗΣ",
        mother_name="ΕΛΕΝΗ", birth_date=birth_date,
        afm=_valid("1112223", 9, constraints.afm_checksum_ok),
        amka=_valid(birth_date.strftime("%d%m%y"), 11, constraints.amka_checksum_ok),
        sex="1", nationality_code=nationality_code,
        identity_type=ergani_parameters.identity_types()[0]["code"],
        identity_number="ΑΒ123456",
    )
    hire.setdefault("minor_booklet_number", "ΒΑ-123")  # αγνοείται για ενήλικο
    eid = svc.start_employment(pid, date(2026, 1, 1), monthly_gross=1500,
                               weekly_pattern=WEEK, draft=True, **hire)
    svc.assign_insurance_profile(
        eid,
        ergani_specialty_code="419",
        ergani_specialty_description="ΛΟΙΠΟΙ ΥΠΑΛΛΗΛΟΙ ΓΡΑΦΕΙΟΥ",
        coverages=({"fund": "efka_main", "kad": "0000",
                         "specialty_code": "000000", "kpk": "0101"},),
        auxiliary_scheme="efka")
    return svc, proj, pid, eid


def _form(payload):
    return payload["AnaggeliesE3N"]["AnaggeliaE3N"][0]


def test_hire_sends_only_relevant_documents_and_log_has_no_base64():
    svc, proj, pid, eid = _hired(date(1990, 5, 5), NON_EU)
    with pytest.raises(ValueError, match="PDF"):
        svc.attach_document(pid, "foreign_file", b"GIF89a")
    with pytest.raises(ValueError, match="είδος"):
        svc.attach_document(pid, "other", PDF)

    assert svc.missing_hire_documents(eid) == [
        "Νομιμοποιητικά έγγραφα πολίτη τρίτης χώρας"]
    svc.attach_document(pid, "foreign_file", PDF)
    svc.attach_document(pid, "young_file", PDF)  # ενήλικος: ΔΕΝ στέλνεται
    assert svc.missing_hire_documents(eid) == []
    form = _form(svc.build_hire_web_e3n(eid))
    assert base64.b64decode(form["f_foreign_file"]) == PDF
    assert form["f_young_file"] == "" and form["f_file"] == ""

    svc.submit_hire_e3(eid)
    logged = proj.ergani_submissions[-1].payload
    assert base64.b64encode(PDF).decode() not in logged
    assert _form(json.loads(logged))["f_foreign_file"].startswith("PDF 1 KB · sha256 ")


def test_minor_sends_booklet_eu_citizen_no_foreign_file():
    svc, _, pid, eid = _hired(date(2009, 6, 1), "048")  # 16 ετών στην πρόσληψη
    svc.attach_document(pid, "foreign_file", PDF)  # Έλληνας: ΔΕΝ στέλνεται
    assert svc.missing_hire_documents(eid) == ["Βιβλιάριο ανηλίκου"]
    svc.attach_document(pid, "young_file", PDF)
    form = _form(svc.build_hire_web_e3n(eid))
    assert form["f_foreign_file"] == "" and base64.b64decode(form["f_young_file"]) == PDF
    assert form["f_ar_vivliou_anilikou"] == "ΒΑ-123"


def test_minor_needs_booklet_number_and_adult_never_sends_one():
    with pytest.raises(ValueError, match="βιβλιαρίου ανηλίκου"):
        _hired(date(2009, 6, 1), "048", minor_booklet_number="")
    svc, _, _pid, eid = _hired(date(1990, 6, 1), "048",
                               unemployment_card_number="ΔΑ-77")
    form = _form(svc.build_hire_web_e3n(eid))
    assert form["f_ar_vivliou_anilikou"] == ""          # ενήλικος: σβήνεται
    assert form["f_code_anergias"] == "ΔΑ-77"


def test_unemployment_card_counts_only_when_was_unemployed():
    from webapp.helpers import form_hire_status_fields

    assert form_hire_status_fields({"f_code_anergias": "Χ"})["unemployment_card_number"] == ""
    with pytest.raises(ValueError, match="δελτίου ανεργίας"):
        form_hire_status_fields({"was_unemployed": "1", "f_code_anergias": " "})
    got = form_hire_status_fields({"was_unemployed": "1", "f_code_anergias": "ΔΑ-1"})
    assert got["unemployment_card_number"] == "ΔΑ-1"


def test_system_backup_carries_documents(fresh_system):
    from _bootstrap import ui_client

    from webapp import create_app
    from webapp.system import registry

    app = create_app()
    client = ui_client(app)
    docs = registry.documents.scope("company:x")
    docs.put("P1", "foreign_file", "a.pdf", PDF)
    backup = client.get("/backup/download").data
    with zipfile.ZipFile(io.BytesIO(backup)) as z:
        events_only = z.read("misevent.db")

    docs.delete("P1", "foreign_file")
    client.post("/backup/restore", data={"backup": (io.BytesIO(backup), "b.zip")})
    assert docs.get("P1", "foreign_file")["content"] == PDF

    # Παλιό backup σκέτο .db: επαναφέρει events, ΔΕΝ αγγίζει τα έγγραφα
    client = ui_client(app)
    docs.put("P2", "young_file", "b.pdf", PDF)
    r = client.post("/backup/restore",
                    data={"backup": (io.BytesIO(events_only), "old.db")})
    assert "/login?restored=1" in r.headers["Location"]
    assert docs.get("P2", "young_file") and docs.get("P1", "foreign_file")


def test_ui_candidate_json_to_person_documents(fresh_system):
    """.json αίτησης με συνημμένο -> modal (hidden) -> καταχώρηση -> Έγγραφα
    στις πληροφορίες -> λήψη -> διαγραφή."""
    import re

    from _bootstrap import ui_client

    from webapp import amka_pdf, create_app
    from webapp.system import proj, svc

    app = create_app()
    c = ui_client(app)
    born = date(2009, 6, 1)  # ανήλικος -> βιβλιάριο
    fields = {
        "last_name": "Δοκιμή", "first_name": "Νίκος", "father_name": "Ιωάννης",
        "mother_name": "Ελένη", "birth_date": born.isoformat(),
        "afm": _valid("1112223", 9, constraints.afm_checksum_ok),
        "amka": _valid(born.strftime("%d%m%y"), 11, constraints.amka_checksum_ok),
        "sex": "0", "nationality_code": "048", "marital_status": "0",
        "identity_type": ergani_parameters.identity_types()[0]["code"],
        "identity_number": "ΑΒ123456", "education_level": "1",
    }
    from fpdf import FPDF

    page = FPDF()
    page.add_page()
    pdf = bytes(page.output())
    doc = {"schema": amka_pdf.CANDIDATE_SCHEMA, "fields": fields,
           "attachments": {"young_file": [
               {"name": "b.pdf", "data": base64.b64encode(pdf).decode()}]}}
    r = c.post("/persons/new/amka", content_type="multipart/form-data", data={
        "amka_pdf": (io.BytesIO(json.dumps(doc).encode()), "aitisi.json")})
    html = r.get_data(as_text=True)
    assert "και 1 συνημμένα" in json.loads(r.headers["HX-Trigger"])["flash"]["msg"]
    hidden = re.search(r'name="doc_young_file" value="([^"]+)"', html).group(1)

    r = c.post("/persons/", data={**fields, "doc_young_file": hidden})
    assert r.status_code == 200, r.get_data(as_text=True)
    pid = next(p.person_id for p in proj.persons.values() if p.afm == fields["afm"])
    (meta,) = svc.person_documents(pid)
    assert meta["kind"] == "young_file"

    info = c.get(f"/persons/{pid}/info").get_data(as_text=True)
    assert "Βιβλιάριο ανηλίκου" in info
    r = c.get(f"/persons/{pid}/documents/young_file")
    assert r.status_code == 200 and r.data.startswith(b"%PDF")

    # Ανέβασμα από τις πληροφορίες: εικόνα -> PDF· άκυρο είδος -> μήνυμα
    from PIL import Image

    img = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(img, "PNG")
    r = c.post(f"/persons/{pid}/documents", content_type="multipart/form-data",
               data={"kind": "foreign_file", "file": (io.BytesIO(img.getvalue()), "p.png")})
    assert "Νομιμοποιητικά" in r.get_data(as_text=True)
    r = c.post(f"/persons/{pid}/documents", content_type="multipart/form-data",
               data={"kind": "nope", "file": (io.BytesIO(pdf), "x.pdf")})
    assert json.loads(r.headers["HX-Trigger"])["flash"]["err"]

    c.post(f"/persons/{pid}/documents/young_file/delete")
    assert [d["kind"] for d in svc.person_documents(pid)] == ["foreign_file"]
    assert c.get(f"/persons/{pid}/documents/young_file").status_code == 404


def test_wto_requires_hire_announcement():
    """Το ΕΡΓΑΝΗ απαντά «Δεν υπάρχει ενεργή σχέση εργασίας» σε ΟΧΕ πριν το
    WebE3N — η εφαρμογή δεν τη στέλνει καν."""
    svc, proj, _, eid = _hired(date(1990, 5, 5), "048")
    svc.correct_hire_details(eid, date(2026, 1, 1), monthly_gross=1500,
                             weekly_pattern=WEEK, schedule_mode="fixed")
    with pytest.raises(ValueError, match="πρώτα την Αναγγελία Πρόσληψης"):
        svc.submit_hire_wto(eid)
    assert not any(s.doc_type == "WTO" for s in proj.ergani_submissions)
    svc.submit_hire_e3(eid)
    svc.submit_hire_wto(eid)
    assert proj.ergani_submissions[-1].doc_type == "WTO"


def test_rejected_wto_is_not_recorded_as_declared():
    """Απόρριψη από το ΕΡΓΑΝΗ: καταγράφεται ως failed, ΚΑΜΙΑ κάλυψη."""
    from misevent.integrations.ergani import ErganiError

    svc, proj, _, eid = _hired(date(1990, 5, 5), "048")
    svc.correct_hire_details(eid, date(2026, 1, 1), monthly_gross=1500,
                             weekly_pattern=WEEK, schedule_mode="fixed")
    svc.submit_hire_e3(eid)

    def reject(doc_type, payload, endpoint=""):
        raise ErganiError("Δεν υπάρχει ενεργή σχέση εργασίας")
    svc.ergani.submit = reject
    svc.submit_hire_wto(eid)
    assert proj.ergani_submissions[-1].status == "failed"
    assert not svc.ergani_hire_submitted(eid, "WTO")  # μπορεί να ξαναγίνει
    assert proj.employments[eid].standing_history == []  # δεν «καλύπτει» ημέρες
