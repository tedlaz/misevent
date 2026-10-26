"""
Γέφυρα των tests με τη ρίζα του project.

Τα tests ζουν στο `tests/`, ενώ τα πακέτα `misevent/` και `webapp/` στη
ρίζα. Δύο πράγματα χρειάζονται για να τρέχουν από ΟΠΟΥΔΗΠΟΤΕ
(`uv run tests/test_x.py`, `uv run -m pytest`, από τον φάκελο tests/):

1. η **ρίζα στο `sys.path`** — για να γίνονται import τα modules του πυρήνα·
2. **cwd = ρίζα** — γιατί πολλά μονοπάτια είναι σχετικά με αυτήν
   (`data/karta_2026-03-05.xlsx`, `webapp/eapd/*.js`, `payroll.db`).

⚠️ 3. **ΔΙΚΟΣ ΤΟΥΣ ΦΑΚΕΛΟΣ ΔΕΔΟΜΕΝΩΝ** (`MISEVENT_DATA_DIR`): από τότε που
   υπάρχουν ΠΟΛΛΕΣ ΕΤΑΙΡΕΙΕΣ (ARCH_REF.md §1α), σκέτο `import webapp` στήνει
   χρήστες + μητρώο εταιρειών στη `misevent.db`. Δεκάδες tests κάνουν import
   το webapp — χωρίς αυτό θα πείραζαν τα ΠΡΑΓΜΑΤΙΚΑ δεδομένα του
   προγραμματιστή και θα κληρονομούσαν εταιρείες/κωδικούς προηγούμενης
   εκτέλεσης. Ο φάκελος είναι προσωρινός, ένας ανά process, και σβήνεται στο
   τέλος.

Χρήση: **πρώτο import** κάθε test — `import _bootstrap  # noqa: F401`.
"""

import atexit
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

if not os.environ.get("MISEVENT_DATA_DIR"):
    DATA_DIR = tempfile.mkdtemp(prefix="misevent-test-")
    os.environ["MISEVENT_DATA_DIR"] = DATA_DIR
    atexit.register(shutil.rmtree, DATA_DIR, ignore_errors=True)

# Ο διαχειριστής των tests: νέα εγκατάσταση = ΚΑΝΕΝΑΣ χρήστης, οπότε ο
# πρώτος `ui_client` τον δημιουργεί από τη σελίδα «Αρχική ρύθμιση».
TEST_EMAIL = "admin@example.com"
TEST_PASSWORD = "misevent-test"


def ui_client(app):
    """Test client ΣΥΝΔΕΔΕΜΕΝΟΣ ως ο διαχειριστής των tests.

    ΜΙΑ πηγή αλήθειας για ΟΛΑ τα tests που χτυπούν routes: από τότε που
    υπάρχουν χρήστες (ARCH_REF.md §1α), σκέτο `app.test_client()` παίρνει 302
    προς `/login` σε κάθε σελίδα. Είναι idempotent — ο λογαριασμός
    δημιουργείται μόνο την πρώτη φορά μέσα στο ίδιο σύστημα.
    """
    client = app.test_client()
    ok = client.post("/login", data={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    if ok.status_code != 302:
        client.post("/setup", data={"email": TEST_EMAIL, "password": TEST_PASSWORD,
                                    "repeat": TEST_PASSWORD})
    return client


def seed(client):
    """POST /seed (δοκιμαστικά δεδομένα) ΧΩΡΙΣ ψηφιακή κάρτα: το demo δεν
    έχει αρχείο κάρτας, οπότε η «Εισαγωγή κάρτας περιόδου» θα κλείδωνε την
    έγκριση παρουσιών. Για tests που τρέχουν την αλυσίδα και δεν αφορούν την
    κάρτα (η ροή της κάρτας: tests/test_card_period.py)."""
    from webapp.system import svc

    resp = client.post("/seed")
    if resp.status_code == 200:
        svc.set_misc_settings(digital_card=False)
    return resp
