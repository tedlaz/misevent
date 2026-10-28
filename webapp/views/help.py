"""Βοήθεια: οδηγίες χρήσης για τον λογιστή/χρήστη.

Κάθε άρθρο είναι ΕΝΑ template `help/<slug>.html`· εδώ ζει μόνο το ΕΥΡΕΤΗΡΙΟ
(`TOPICS`): τίτλος, ομάδα και ποιες οθόνες καλύπτει (`endpoints`). Από αυτό
βγαίνουν το μενού της Βοήθειας, η σειρά «Προηγούμενο/Επόμενο» και το κουμπί
❓ της κεφαλίδας, που ανοίγει το άρθρο της τρέχουσας οθόνης (`help_for`).

Η αναζήτηση ψάχνει στο ΑΠΟΔΟΣΜΕΝΟ κείμενο κάθε άρθρου, χωρίς τόνους.
"""

import re
from functools import lru_cache

from flask import Blueprint, abort, render_template, request

from misevent.rules.constraints import norm_name

bp = Blueprint("help", __name__, url_prefix="/help")

# (slug, εικονίδιο, τίτλος, περίληψη, ομάδα, endpoints/προθέματα που καλύπτει)
# ➜ Πρόθεμα με τελεία («attendance.») = όλη η οθόνη· χωρίς = ακριβές endpoint.
_T = [
    ("start", "🚀", "Πρώτα βήματα", "Από την εγκατάσταση έως την πρώτη μισθοδοσία",
     "Ξεκινώντας", ("welcome.",)),
    ("overview", "📊", "Επισκόπηση & Υποχρεώσεις",
     "Τι πρέπει να γίνει τώρα: μετρητές, «Επόμενο βήμα», χρονολόγιο",
     "Ξεκινώντας", ("dashboard.index", "obligations.", "live.")),
    ("monthly-cycle", "🔁", "Ο μηνιαίος κύκλος",
     "Παρουσίες → Μισθοδοσία → Πληρωμές → ΑΠΔ → ΦΜΥ, με τη σειρά", "Ξεκινώντας", ("month.",)),
    ("company", "🏢", "Εταιρεία & παραρτήματα",
     "Στοιχεία εργοδότη, παραρτήματα, σύνδεση ΕΡΓΑΝΗ", "Ρυθμίσεις", ("company.index",)),
    ("specialties", "🧩", "Ειδικότητες & ασφάλιση",
     "Ο οδηγός κύριας / επικουρικής (ΕΦΚΑ ή ΤΕΚΑ) / πρόνοιας", "Ρυθμίσεις", ("company.",)),
    ("hire", "👤", "Πρόσληψη εργαζομένου",
     "Αναζήτηση, νέο πρόσωπο, σύμβαση, ασφάλιση, πρόγραμμα, Ε3", "Εργαζόμενοι",
     ("persons.", "employments.")),
    ("schedule", "🗓️", "Πρόγραμμα εργασίας",
     "Εβδομαδιαίο και ημερήσιο πρόγραμμα, δήλωση στο ΕΡΓΑΝΗ", "Εργαζόμενοι",
     ("work_schedules.", "employments.calendar_page")),
    ("leaves", "🏖️", "Άδειες & ασθένεια",
     "Καταχώρηση άδειας, ασθένεια, επίδομα ΕΦΚΑ", "Εργαζόμενοι", ("leaves.",)),
    ("changes", "✏️", "Αλλαγές σύμβασης & αποχώρηση",
     "Μεταβολή σύμβασης, αποχώρηση, αποζημιώσεις", "Εργαζόμενοι", ()),
    ("attendance", "⏱️", "Παρουσίες & κάρτα εργασίας",
     "Συγκεντρωτικό μήνα, εισαγωγή κάρτας, ημέρες ΕΦΚΑ, έγκριση", "Μηνιαίος κύκλος",
     ("attendance.",)),
    ("payroll", "🧮", "Μισθοδοσία",
     "Υπολογισμός, ανάλυση, Δώρα, Επίδομα Αδείας, διόρθωση", "Μηνιαίος κύκλος",
     ("payroll.",)),
    ("ergani", "🏛️", "ΕΡΓΑΝΗ", "Εκκρεμείς υποβολές, Ε3/Ε5/Ε7, προγράμματα, ιστορικό",
     "Μηνιαίος κύκλος", ("ergani.",)),
    ("apd", "📄", "ΑΠΔ e-ΕΦΚΑ & ΤΕΚΑ", "Παραγωγή και υποβολή αρχείων ΑΠΔ",
     "Μηνιαίος κύκλος", ("eapd.",)),
    ("fmy", "🧾", "ΦΜΥ", "Δήλωση παρακράτησης φόρου μισθωτών", "Μηνιαίος κύκλος", ("fmy.",)),
    ("payments", "💶", "Πληρωμές", "Μισθοί, εισφορές, φόρος, Δώρα", "Μηνιαίος κύκλος",
     ("payments.",)),
    ("reports", "📈", "Στατιστικά & εκτυπώσεις", "Κόστος, κόστος εργατοώρας, PDF",
     "Εργαλεία", ("dashboard.stats", "dashboard.print_cost_report",
                  "dashboard.print_hourly_cost")),
    ("rules", "🧭", "Χάρτης κανόνων", "Ροή μήνα, έλεγχοι, διαγράμματα υπολογισμών",
     "Εργαλεία", ("rules.",)),
    ("system", "🗂️", "Εταιρείες, παράμετροι, χρήστες, backup",
     "Πολλές εταιρείες, νομοθετικές παράμετροι, Μητρώο ΚΠΚ, αντίγραφα ασφαλείας",
     "Εργαλεία", ("companies.", "apd_import.", "settings.", "users.", "backup.", "audit.")),
    ("faq", "💬", "Συχνές ερωτήσεις", "Γιατί δεν μπορώ να…; — οι πιο συνηθισμένες απορίες",
     "Εργαλεία", ()),
]
TOPICS = [
    {"slug": s, "icon": i, "title": t, "summary": su, "group": g, "endpoints": e}
    for s, i, t, su, g, e in _T
]
BY_SLUG = {t["slug"]: t for t in TOPICS}


def help_for(endpoint: str) -> str:
    """Το slug του άρθρου μιας οθόνης: πρώτα ακριβές endpoint, μετά το
    μακρύτερο πρόθεμα. Χωρίς αντιστοιχία, το ευρετήριο ("")."""
    endpoint = endpoint or ""
    for t in TOPICS:
        if endpoint in t["endpoints"]:
            return t["slug"]
    best = max(
        ((len(p), t["slug"]) for t in TOPICS for p in t["endpoints"]
         if p.endswith(".") and endpoint.startswith(p)),
        default=(0, ""),
    )
    return best[1]


def _groups():
    out = {}
    for t in TOPICS:
        out.setdefault(t["group"], []).append(t)
    return out


@lru_cache(maxsize=None)
def _plain(slug: str) -> str:
    """Το κείμενο ενός άρθρου χωρίς HTML — για την αναζήτηση. Τα άρθρα δεν
    αλλάζουν όσο τρέχει η εφαρμογή, άρα αρκεί μία απόδοση."""
    html = render_template(f"help/{slug}.html", topic=BY_SLUG[slug], body_only=True)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _search(q: str) -> list[dict]:
    words = norm_name(q).split()
    hits = []
    for t in TOPICS:
        text = _plain(t["slug"])
        key = norm_name(t["title"] + " " + t["summary"] + " " + text)
        if words and all(w in key for w in words):
            # απόσπασμα γύρω από την πρώτη λέξη (ίδια θέση στο κείμενο: η
            # norm_name δεν αλλάζει μήκος παρά μόνο τα διπλά κενά, ήδη ενωμένα)
            pos = norm_name(text).find(words[0])
            snippet = text[max(0, pos - 80): pos + 160] if pos >= 0 else t["summary"]
            hits.append({**t, "snippet": snippet})
    return hits


@bp.get("/")
def index():
    q = request.args.get("q", "").strip()
    return render_template(
        "help/index.html", groups=_groups(), q=q, results=_search(q) if q else None,
    )


@bp.get("/book.pdf")
def book():
    """Όλη η Βοήθεια ως βιβλίο PDF (ίδια άρθρα — `webapp/help_book.py`)."""
    from ..help_book import help_book_pdf
    from ..helpers import pdf_response

    return pdf_response(help_book_pdf, "misevent_odigos_xrisis.pdf")


@bp.get("/<slug>")
def article(slug):
    topic = BY_SLUG.get(slug)
    if topic is None:
        abort(404)
    i = TOPICS.index(topic)
    return render_template(
        f"help/{slug}.html", topic=topic, groups=_groups(),
        prev=TOPICS[i - 1] if i else None,
        next=TOPICS[i + 1] if i + 1 < len(TOPICS) else None,
    )
