"""
Βεβαίωση Α.Μ.Κ.Α. (PDF) -> προσυμπλήρωση φόρμας νέου εργαζομένου.

Συνθετικό κείμενο στη μορφή που δίνει το `pypdf` (layout mode) για τη
βεβαίωση του amka.gr — με τον «μ» ως σύμβολο micro (U+00B5), όπως στο PDF.

Εκτέλεση: uv run pytest tests/test_amka_pdf.py
"""

import json

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd
import pytest

from webapp import amka_pdf

SAMPLE = """
Ηµεροµηνία εκτύπωσης:01/02/2026
ΕΛΛΗΝΙΚΗ ΔΗΜΟΚΡΑΤΙΑ
                   ΒΕΒΑΙΩΣΗ Α.Μ.Κ.Α. - ΥΠΕΥΘΥΝΗ ΔΗΛΩΣΗ (άρθρο 8 Ν.1599/1986)
 Βεβαιώνεται ότι ο πιο κάτω αιτών διαθέτει τον ενεργό Α.Μ.Κ.Α.:      05059012343
 ΑΦΜ:                      111222336
 Φύλο:                     ΑΡΡΕΝ                                 ΑΜΚΑ Πατέρα:
 Χώρα Υπηκοότητας:         ΕΛΛΑΔΑ                                ΑΜΚΑ Μητέρας:
 Επώνυµο Γέννησης:               ΠΑΠΑΔΑΚΗΣ                                      PAPADAKIS
 Επώνυµο Σηµερινό:               ΠΑΠΑΔΟΠΟΥΛΟΣ                                   PAPADOPOULOS
 Όνοµα:                          ΙΩΑΝΝΗΣ                                        IOANNIS
 Όνοµα Πατέρα:                   ΝΙΚΟΛΑΟΣ                                       NIKOLAOS
 Όνοµα Μητέρας:                  ΜΑΡΙΑ                                          MARIA
 Ηµ/νία Γέννησης:                05/05/1990                Δήµος Γέννησης:      ΑΘΗΝΑΙΩΝ
 Νοµός Γέννησης:                 ΑΤΤΙΚΗΣ                   Χώρα Γέννησης:       ΕΛΛΑΔΑ
 Οδός /Αρ. Διεύθ.:    ΟΔΟΣ 1                                                    Τ.Κ:      10000
 Δήµος Διεύθυνσης:    ΑΘΗΝΑΙΩΝ                                                  Νοµός:    ΑΤΤΙΚΗΣ
 Τηλέφωνα:            2100000000                                                Email:    a@b.gr
"""


def test_parse_certificate():
    assert amka_pdf.parse_text(SAMPLE) == {
        "last_name": "ΠΑΠΑΔΟΠΟΥΛΟΣ",  # σημερινό, ΟΧΙ γέννησης
        "first_name": "ΙΩΑΝΝΗΣ",
        "father_name": "ΝΙΚΟΛΑΟΣ",
        "mother_name": "ΜΑΡΙΑ",
        "afm": "111222336",
        "amka": "05059012343",
        "email": "a@b.gr",
        "birth_date": "1990-05-05",
        "sex": "0",
        "nationality_code": "048",
        "address": "ΟΔΟΣ 1",
        "postal_code": "10000",
        "municipality": "ΑΘΗΝΑΙΩΝ",
        "prefecture": "ΑΤΤΙΚΗΣ",  # της διεύθυνσης, ΟΧΙ της γέννησης
        "phone": "2100000000",
        "birth_place": "ΑΘΗΝΑΙΩΝ, ΑΤΤΙΚΗΣ, ΕΛΛΑΔΑ",
    }


def test_empty_fields_and_wrong_files():
    # Κενό πεδίο δεν «κλέβει» την επόμενη ετικέτα ως τιμή
    text = SAMPLE.replace("ΑΡΡΕΝ   ", "         ").replace("a@b.gr", "")
    found = amka_pdf.parse_text(text)
    assert "sex" not in found and "email" not in found
    with pytest.raises(ValueError, match="Βεβαίωση"):
        amka_pdf.parse_text("Κάποιο άλλο έγγραφο\n ΑΦΜ:   111222336")
    with pytest.raises(ValueError, match="PDF"):
        amka_pdf.read(b"not a pdf")


def _candidate(fields: dict) -> bytes:
    return json.dumps({"schema": amka_pdf.CANDIDATE_SCHEMA, "fields": fields}).encode()


def test_candidate_json():
    """Το .json της αίτησης υποψηφίου -> πεδία του modal (κανονικοποιημένα)."""
    values = {"last_name": "Παπαδόπουλος", "afm": "111 222 336",
              "birth_date": "1990-05-05",  # ISO, όπως το δίνει το <input type="date">
              "identity_issue_date": "31/02/2020",  # ανύπαρκτη -> αγνοείται
              "identity_expiry_date": "01/01/30",  # διψήφιο έτος -> αγνοείται
              "sex": "1", "nationality_code": "048", "education_level": "999",
              "email": "A@b.gr", "unknown": "x"}
    assert amka_pdf.read(_candidate(values)) == {
        "last_name": "ΠΑΠΑΔΟΠΟΥΛΟΣ", "afm": "111222336", "birth_date": "1990-05-05",
        "sex": "1", "nationality_code": "048", "email": "A@b.gr",
    }
    for bad in (b'{"schema": "other", "fields": {}}', b"[1]", "όχι json".encode()):
        with pytest.raises(ValueError, match="αίτηση"):
            amka_pdf.read(bad)

    # Τρίτες χώρες: τα πεδία κρατιούνται ΜΟΝΟ για υπηκοότητα εκτός ΕΕ (όπως το modal)
    tc = {"res_permit_inst": "1", "res_permit_inst_number": "ab123",
          "res_permit_inst_type": "8",
          "res_permit_ap_type": "8",  # τύπος «άμεσης» στη λίστα «με έγκριση» -> άκυρο
          "res_permit_inst_expiry_date": "1/6/2027", "res_permit_ap": "2"}
    assert amka_pdf.parse_form({**tc, "nationality_code": "048"}) == {
        "nationality_code": "048"}
    non_eu = next(r["code"] for r in amka_pdf.ergani_params.nationalities()
                  if r["code"] not in amka_pdf.ergani_params.eu_nationality_codes())
    assert amka_pdf.parse_form({**tc, "nationality_code": non_eu}) == {
        "nationality_code": non_eu, "res_permit_inst": "1", "res_permit_inst_type": "8",
        "res_permit_inst_number": "AB123", "res_permit_inst_expiry_date": "2027-06-01",
    }  # «2» εκτός λίστας Ναι/Όχι -> αγνοείται

    # Αλγοριθμικοί έλεγχοι στο ανέβασμα: έγκυρα -> τίποτα, λάθος ψηφίο -> μήνυμα
    assert amka_pdf.problems({"afm": "111222336", "amka": "05059012343"}) == []
    bad = amka_pdf.problems({"afm": "111222337", "amka": "05059012344"})
    assert len(bad) == 2 and "ΑΦΜ" in bad[0] and "ΑΜΚΑ" in bad[1]


def test_candidate_checks_js_match_python():
    """Οι έλεγχοι ΑΦΜ/ΑΜΚΑ της αίτησης (JS) == constraints.py, σε τυχαία δείγματα."""
    import random
    import shutil
    import subprocess
    from pathlib import Path

    from misevent.rules import constraints as c

    if not shutil.which("node"):
        pytest.skip("χρειάζεται node")
    rnd = random.Random(1)
    afms = [f"{rnd.randrange(10**9):09d}" for _ in range(3000)] + ["111222336", "0" * 9]
    amkas = [f"{rnd.randrange(10**11):011d}" for _ in range(3000)]
    amkas += ["05059012343", "29020012345", "0" * 11]
    for _ in range(2000):  # + έγκυρα (σωστό ψηφίο ελέγχου) για τη θετική πλευρά
        a = (f"{rnd.randint(1, 31):02d}{rnd.randint(1, 12):02d}"
             f"{rnd.randrange(100):02d}{rnd.randrange(10**4):04d}")
        amkas += [a + d for d in "0123456789" if c.amka_checksum_ok(a + d)]
    js = Path("webapp/templates/candidate_checks.js").read_text(encoding="utf-8")
    js += (f"\nconsole.log(JSON.stringify([{json.dumps(afms)}.map(afmOk),"
           f" {json.dumps(amkas)}.map(amkaOk)]));")
    got = json.loads(subprocess.run(["node"], input=js, capture_output=True,
                                    text=True, encoding="utf-8", check=True).stdout)
    assert got[0] == [c.afm_checksum_ok(x) for x in afms]
    assert got[1] == [c.amka_checksum_ok(x) and c.amka_birth_ok(x) for x in amkas]


def test_candidate_attachments_become_one_pdf():
    """Συνημμένα της αίτησης: εικόνες + PDF -> ΕΝΑ PDF ανά είδος."""
    import base64
    from io import BytesIO

    from PIL import Image
    from pypdf import PdfReader

    img = BytesIO()
    Image.new("RGB", (800, 500), "white").save(img, "JPEG")
    jpg = {"name": "a.jpg", "data": base64.b64encode(img.getvalue()).decode()}
    pdf = {"name": "b.pdf", "data": base64.b64encode(amka_pdf.to_pdf([jpg])).decode()}
    non_eu = next(r["code"] for r in amka_pdf.ergani_params.nationalities()
                  if r["code"] not in amka_pdf.ergani_params.eu_nationality_codes())

    doc = {"schema": amka_pdf.CANDIDATE_SCHEMA,
           "fields": {"last_name": "Α", "nationality_code": non_eu},
           "attachments": {"foreign_file": [jpg, pdf], "young_file": [jpg]}}
    fields, docs = amka_pdf.read_upload(json.dumps(doc).encode())
    assert fields["nationality_code"] == non_eu
    assert len(PdfReader(BytesIO(docs["foreign_file"])).pages) == 2
    assert len(PdfReader(BytesIO(docs["young_file"])).pages) == 1

    # Νομιμοποιητικά για πολίτη ΕΕ: απορρίπτονται (όπως τα πεδία τρίτων χωρών)
    doc["fields"]["nationality_code"] = "048"
    assert set(amka_pdf.read_upload(json.dumps(doc).encode())[1]) == {"young_file"}

    for bad, msg in (([{"name": "x.gif", "data": base64.b64encode(b"GIF89a").decode()}],
                      "δεκτά μόνο"),
                     ([{"name": "x.pdf", "data": "όχι base64"}], "Χαλασμένο")):
        doc["attachments"] = {"young_file": bad}
        with pytest.raises(ValueError, match=msg):
            amka_pdf.read_upload(json.dumps(doc).encode())
