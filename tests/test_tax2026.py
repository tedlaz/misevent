"""
Tests της φορολογικής κλίμακας 2026 (`config.TaxScaleYouth2026`) — ελέγχονται
ΑΝΑ ΚΕΛΙ οι τρεις βοηθητικές κλίμακες του `data/taxscales2026.md` (άνω των 30,
έως 25, 26–30 ετών), η μείωση φόρου ανά τέκνα με την απομείωση, και ότι η
κλίμακα επιβιώνει του κύκλου event -> projection (μητρώο παραμέτρων).

Εκτέλεση: uv run tests/test_tax2026.py
"""

from datetime import date

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.app.services import PayrollService
from misevent.domain.projections import Projections
from misevent.persistence.database import Database
from misevent.rules import config

# Οι πίνακες του data/taxscales2026.md: συντελεστές ανά κλιμάκιο (γραμμές) και
# αριθμό τέκνων 0..6 (στήλες).
OVER_30 = [
    (0.09, 0.09, 0.09, 0.09, 0.00, 0.00, 0.00),  # 0–10.000
    (0.20, 0.18, 0.16, 0.09, 0.00, 0.00, 0.00),  # 10.000–20.000
    (0.26, 0.24, 0.22, 0.20, 0.18, 0.16, 0.14),  # 20.000–30.000
    (0.34,) * 7,                                  # 30.000–40.000
    (0.39,) * 7,                                  # 40.000–60.000
    (0.44,) * 7,                                  # > 60.000
]
UPTO_25 = [
    (0.00,) * 7,
    (0.00,) * 7,
    (0.26, 0.24, 0.22, 0.20, 0.18, 0.16, 0.14),
    (0.34,) * 7,
    (0.39,) * 7,
    (0.44,) * 7,
]
FROM_26_TO_30 = [
    (0.09, 0.09, 0.09, 0.09, 0.00, 0.00, 0.00),
    (0.09, 0.09, 0.09, 0.09, 0.00, 0.00, 0.00),
    (0.26, 0.24, 0.22, 0.20, 0.18, 0.16, 0.14),
    (0.34,) * 7,
    (0.39,) * 7,
    (0.44,) * 7,
]


def scale_2026():
    s = config.tax_scale_for(date(2026, 6, 30))
    assert type(s).model_key == "youth2026", type(s).model_key
    assert s.valid_from == date(2026, 1, 1)
    return s


def test_bracket_rates():
    """Κάθε κελί των τριών βοηθητικών κλιμάκων."""
    s = scale_2026()
    assert len(s.brackets) == 6, len(s.brackets)
    for age, table in ((45, OVER_30), (22, UPTO_25), (28, FROM_26_TO_30)):
        for i, row in enumerate(table):
            for children, expected in enumerate(row):
                got = s.bracket_rate(i, children, age)
                assert abs(got - expected) < 1e-9, (
                    f"ηλικία {age}, κλιμάκιο {i}, τέκνα {children}: "
                    f"{got} != {expected}"
                )
    # Άγνωστη γέννηση (age=None) -> κλίμακα ενηλίκου (άνω των 30)
    for i, row in enumerate(OVER_30):
        for children, expected in enumerate(row):
            assert abs(s.bracket_rate(i, children, None) - expected) < 1e-9
    print("OK bracket_rates: 3 κλίμακες x 6 κλιμάκια x 7 περιπτώσεις τέκνων")


def test_annual_tax():
    """Ο φόρος = άθροισμα κλιμακίων μείον τη μείωση τέκνων."""
    s = scale_2026()

    # 15.000 €, χωρίς τέκνα, 45 ετών: 10.000x9% + 5.000x20% = 1.900
    # μείωση 777 − 20 x (15.000−12.000)/1.000 = 717 -> 1.183
    assert abs(s.annual_tax(15000, 0, date(1980, 1, 1), date(2026, 12, 31))
               - 1183.0) < 0.01

    # Ίδιο εισόδημα, 24 ετών: τα δύο πρώτα κλιμάκια μηδενίζονται -> 0
    assert s.annual_tax(15000, 0, date(2002, 4, 1), date(2026, 12, 31)) == 0.0

    # 15.000 €, 27 ετών: 15.000 x 9% = 1.350 − 717 = 633
    assert abs(s.annual_tax(15000, 0, date(1999, 4, 1), date(2026, 12, 31))
               - 633.0) < 0.01

    # 25.000 €, 2 τέκνα, 45 ετών: 10.000x9% + 10.000x16% + 5.000x22% = 3.600
    # μείωση 1.120 − 20 x 13 = 860 -> 2.740
    assert abs(s.annual_tax(25000, 2, date(1980, 1, 1), date(2026, 12, 31))
               - 2740.0) < 0.01

    # 25.000 €, 5 τέκνα: τα δύο πρώτα κλιμάκια 0%, το τρίτο 16%
    # -> 5.000 x 16% = 800· μείωση 1.780 ΧΩΡΙΣ απομείωση (5+ τέκνα) -> 0
    assert s.annual_tax(25000, 5, date(1980, 1, 1), date(2026, 12, 31)) == 0.0

    # 70.000 €, χωρίς τέκνα: 900 + 2.000 + 2.600 + 3.400 + 7.800 + 4.400
    # = 21.100· η μείωση έχει μηδενιστεί από την απομείωση
    assert abs(s.annual_tax(70000, 0, date(1980, 1, 1), date(2026, 12, 31))
               - 21100.0) < 0.01
    print("OK annual_tax: κλιμάκια + μείωση τέκνων + απομείωση")


def test_reductions():
    """Μείωση φόρου: 777/900/1.120/1.340/1.580/1.780 (+220 ανά επόμενο)."""
    s = scale_2026()
    assert s.reductions == (777.0, 900.0, 1120.0, 1340.0, 1580.0, 1780.0)
    # Εισόδημα κάτω του ορίου απομείωσης -> η μείωση εφαρμόζεται ακέραιη.
    # 11.000 €, 45 ετών, 6 τέκνα: 10.000x0% + 1.000x0% = 0 -> 0 (χωρίς
    # αρνητικό φόρο)
    assert s.annual_tax(11000, 6, date(1980, 1, 1), date(2026, 12, 31)) == 0.0
    # 6 τέκνα, 40.000 €: 20.000x0% + 10.000x14% + 10.000x34% = 4.800·
    # μείωση 1.780 + 220 = 2.000 (χωρίς απομείωση) -> 2.800
    assert abs(s.annual_tax(40000, 6, date(1980, 1, 1), date(2026, 12, 31))
               - 2800.0) < 0.01
    print("OK reductions: 5 τέκνα = 1.780 €, +220 € ανά επόμενο")


def test_roundtrip_events():
    """Το μητρώο παραμέτρων (events) διατηρεί ΚΑΙ τις παραμέτρους καθεστώτος."""
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()

    entries, source = svc.parameter_entries("tax")
    assert source == "events", source
    s = config.tax_scale_for(date(2026, 6, 30))
    assert type(s).model_key == "youth2026", type(s).model_key
    assert s.band1_child_rates == (0.09, 0.09, 0.09, 0.09, 0.0)
    assert s.band3_child_rates == (0.26, 0.24, 0.22, 0.20, 0.18)
    assert abs(s.band3_extra_child_step - 0.02) < 1e-9
    # Ίδια αποτελέσματα με την εγγραφή του config
    assert abs(s.bracket_rate(2, 6, 45) - 0.14) < 1e-9
    assert s.bracket_rate(1, 0, 22) == 0.0
    # Το 2025 μένει στο καθεστώς «standard»
    old = config.tax_scale_for(date(2025, 6, 30))
    assert type(old).model_key == "standard", type(old).model_key
    assert len(entries) >= 2
    print("OK roundtrip: TaxScaleDefined -> projection -> config overrides")
