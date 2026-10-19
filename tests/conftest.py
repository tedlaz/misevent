"""Γέφυρα των tests με τη ρίζα του project + απομόνωση καθολικής κατάστασης.

Το `_bootstrap` κάνει `os.chdir(ROOT)` και δίνει έναν ΠΡΟΣΩΡΙΝΟ
`MISEVENT_DATA_DIR` για ΟΛΗ τη σουίτα, ώστε το `import webapp` να μην αγγίξει
τα πραγματικά δεδομένα του προγραμματιστή.

⚠️ ΓΙΑΤΙ ΧΡΕΙΑΖΕΤΑΙ ΤΟ `fresh_system`: παλιότερα κάθε test αρχείο έτρεχε ως
ξεχωριστό `uv run tests/test_x.py`, δηλαδή σε ΔΙΚΗ ΤΟΥ ΔΙΕΡΓΑΣΙΑ, και η
καθολική κατάσταση καθαριζόταν «δωρεάν». Με ΜΙΑ εντολή `uv run pytest` όλα
μοιράζονται μία διεργασία, και δύο πράγματα διαρρέουν:

  • το `webapp.system.registry` — singleton που στήνεται ΚΑΤΑ ΤΟ IMPORT και
    κρατά χρήστες, κωδικούς και μητρώο εταιρειών·
  • τα `config._OVERRIDES` / `obligation_types._OVERRIDES` — κατάσταση
    ΔΙΕΡΓΑΣΙΑΣ που γεμίζει από events (ARCH_REF.md §1α).

Συγκεκριμένο παράδειγμα: το `test_auth.py` αλλάζει τον κωδικό του αρχικού
διαχειριστή. Χωρίς απομόνωση, ο `_bootstrap.ui_client` δεν μπορεί πια να
συνδεθεί και ΚΑΘΕ επόμενο test που χτυπά route παίρνει 302 προς `/login`.
"""


import os
import sys

import _bootstrap  # noqa: F401  # ρίζα στο sys.path + cwd + MISEVENT_DATA_DIR
import pytest


@pytest.fixture
def fresh_system(tmp_path):
    """Καθαρό σύστημα: δικός του φάκελος δεδομένων και ΝΕΟ `webapp.system`.

    Χρήση σε ΟΠΟΙΟ test μεταβάλλει καθολική κατάσταση (χρήστες, κωδικούς,
    εταιρείες, νομοθετικές παραμέτρους) και θα μόλυνε τα επόμενα.
    Το test πρέπει να κάνει import το `webapp` ΜΕΣΑ του, όχι σε module level.
    """
    previous_dir = os.environ.get("MISEVENT_DATA_DIR")
    os.environ["MISEVENT_DATA_DIR"] = str(tmp_path)
    saved = _detach_webapp()
    _clear_overrides()
    try:
        yield tmp_path
    finally:
        if previous_dir is None:
            os.environ.pop("MISEVENT_DATA_DIR", None)
        else:
            os.environ["MISEVENT_DATA_DIR"] = previous_dir
        _detach_webapp()
        _clear_overrides()
        # ⚠️ ΕΠΑΝΑΦΟΡΑ ΤΩΝ ΙΔΙΩΝ ΑΝΤΙΚΕΙΜΕΝΩΝ, ΟΧΙ ΝΕΟ IMPORT: άλλα test
        # modules κρατούν ήδη αναφορές (π.χ. `from webapp.views import
        # employments`) από τη ΣΥΛΛΟΓΗ. Ένα φρέσκο import θα έφτιαχνε ΔΕΥΤΕΡΑ
        # module objects, οπότε το monkeypatch τους δεν θα έπιανε την
        # εφαρμογή που τρέχει — 404 αντί για 200, χωρίς κανένα μήνυμα.
        sys.modules.update(saved)


def _detach_webapp() -> dict:
    """Βγάζει το `webapp` από το `sys.modules` (και το επιστρέφει), ώστε το
    επόμενο import να ξαναφτιάξει το `Registry` πάνω στον τρέχοντα
    `MISEVENT_DATA_DIR`."""
    names = [n for n in sys.modules if n == "webapp" or n.startswith("webapp.")]
    return {n: sys.modules.pop(n) for n in names}


def _clear_overrides() -> None:
    for module in ("misevent.rules.config", "misevent.rules.obligation_types"):
        loaded = sys.modules.get(module)
        if loaded is not None:
            loaded.clear_overrides()
