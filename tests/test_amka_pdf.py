"""
Βεβαίωση Α.Μ.Κ.Α. (PDF) -> προσυμπλήρωση φόρμας νέου εργαζομένου.

Συνθετικό κείμενο στη μορφή που δίνει το `pypdf` (layout mode) για τη
βεβαίωση του amka.gr — με τον «μ» ως σύμβολο micro (U+00B5), όπως στο PDF.

Εκτέλεση: uv run pytest tests/test_amka_pdf.py
"""

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
