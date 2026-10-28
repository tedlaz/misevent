"""Χάρτης κανόνων: η ΡΟΗ του μήνα (γράφος υποχρεώσεων με την κατάσταση μιας
περιόδου) και οι ΕΛΕΓΧΟΙ ΕΓΚΥΡΟΤΗΤΑΣ ανά ενέργεια.

Και τα δύο ΠΑΡΑΓΟΝΤΑΙ από τον κώδικα — δεν υπάρχει χειρόγραφη λίστα να
παλιώσει: ο γράφος από το μητρώο `obligation_types` (ίδιο layout με τις
Παραμέτρους), οι έλεγχοι με AST των `misevent/app/services` και των views
(ποια μέθοδος καλεί ποιο `constraints.check_*` — μητρώο δεν υπάρχει, §6α-Β).
"""

import ast
import inspect
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from flask import Blueprint, g, render_template, request

import misevent.app.services as services_pkg
from misevent.domain import dependencies as deps
from misevent.rules import config, constraints
from misevent.rules import obligation_types as ot

from .. import queries as q
from ..clock import today
from ..system import proj
from .settings import _chain_graph

bp = Blueprint("rules", __name__, url_prefix="/rules")

_STATUS_TEXT = {"locked": "🔒 περιμένει προαπαιτούμενο", "none": "— όχι σε αυτή την περίοδο"}


def _period():
    ref = today()
    y = request.values.get("y", type=int) or ref.year
    m = request.values.get("m", type=int) or ref.month
    return y, min(max(m, 1), 12)


def period_status(y: int, m: int) -> dict:
    """code -> {status, text, id}: η υποχρέωση ΚΑΘΕ τύπου στην περίοδο (y, m)
    — οι ετήσιες στο (y, 0). Ίδιοι κανόνες κατάστασης με το χρονολόγιο."""
    ref = today()
    index = deps.build_index(proj.obligations.values())
    by_key = {(o.obligation_type, o.period_year, o.period_month): o
              for o in proj.obligations.values()}
    out = {}
    for code in ot.TYPES:
        o = by_key.get((code, y, m)) or by_key.get((code, y, 0))
        if o is None:
            out[code] = {"status": "none", "text": _STATUS_TEXT["none"], "id": None}
            continue
        st, _reason = q.obligation_status(o, ref)
        if st != "done" and q.is_locked(o, index):
            st = "locked"
        out[code] = {
            "status": st,
            "text": _STATUS_TEXT.get(st) or q.due_countdown(o, ref),
            "id": o.obligation_id,
        }
    return out


# ------------------------------------------------ Έλεγχοι εγκυρότητας (AST)
def _first_line(doc) -> str:
    return (doc or "").strip().splitlines()[0].strip() if doc else ""


def _checks_in(node) -> list[str]:
    """Οι κλήσεις `constraints.check_*` (ή σκέτο `check_*` μέσα στο ίδιο το
    constraints) σε ένα κόμβο AST, με τη σειρά εμφάνισης, χωρίς διπλά."""
    found = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = (f.attr if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id == "constraints" else f.id if isinstance(f, ast.Name) else "")
        if name.startswith("check_") and name not in found:
            found.append(name)
    return found


@lru_cache(maxsize=1)
def validation_map() -> dict:
    """{groups: [{title, actions: [{name, doc, private, checks}]}],
        checks: {όνομα: {doc, nested}}, unused: [...]}.

    Σαρώνει τις υπηρεσίες (η αυθεντική επικύρωση) και τα views (η
    προ-επικύρωση των φορμών). Ο κώδικας δεν αλλάζει όσο τρέχει η εφαρμογή,
    άρα αρκεί ένας υπολογισμός."""
    checks = {
        name: {"doc": _first_line(inspect.getdoc(fn)), "nested": []}
        for name, fn in inspect.getmembers(constraints, inspect.isfunction)
        if name.startswith("check_") and fn.__module__ == constraints.__name__
    }
    tree = ast.parse(Path(inspect.getfile(constraints)).read_text(encoding="utf-8"))
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef) and fn.name in checks:
            checks[fn.name]["nested"] = [c for c in _checks_in(fn) if c != fn.name]

    sources = [
        *sorted(Path(services_pkg.__file__).parent.glob("*.py")),
        *sorted(Path(__file__).parent.glob("*.py")),
    ]
    groups, called = [], set()
    for path in sources:
        mod = ast.parse(path.read_text(encoding="utf-8"))
        # Συναρτήσεις του module + μέθοδοι κλάσεων· οι εμφωλευμένες (π.χ. τα
        # `cmd()` των views) μετρούν στη συνάρτηση που τις περιέχει.
        fns = [n for top in mod.body
               for n in (top.body if isinstance(top, ast.ClassDef) else [top])
               if isinstance(n, ast.FunctionDef)]
        actions = []
        for fn in fns:
            found = [c for c in _checks_in(fn) if c in checks]
            if found:
                actions.append({
                    "name": fn.name,
                    "doc": _first_line(ast.get_docstring(fn)),
                    "private": fn.name.startswith("_"),
                    "checks": found,
                })
                called.update(found)
        if actions:
            groups.append({
                "title": _first_line(ast.get_docstring(mod)) or path.stem,
                "where": "Οθόνη" if path.parent.name == "views" else "Υπηρεσία",
                "file": f"{path.parent.name}/{path.name}",
                "actions": actions,
            })
    for name in list(called):
        called.update(checks[name]["nested"])
    return {
        "groups": groups,
        "checks": checks,
        "unused": sorted(c for c in checks if c not in called),
    }


# ------------------------------------------- Διαγράμματα ροής υπολογισμών
# Κόμβος: {"k": start|q|calc|end|note, "t": κείμενο, "d": λεπτομέρεια,
# "br": [(ετικέτα κλάδου, [κόμβοι…]), …] μόνο στο q}. Μια ΑΚΟΛΟΥΘΙΑ είναι
# λίστα κόμβων· μετά από ένα q η ακολουθία συνεχίζει για όσους κλάδους δεν
# τελείωσαν σε `end`.
#
# ➜ Η ΔΟΜΗ (ποιες εναλλακτικές) αντιγράφει τη λογική του κώδικα που
#   αναφέρεται σε κάθε διάγραμμα (`src`) — αν αλλάξει εκείνη, αλλάζει κι εδώ.
#   Οι ΤΙΜΕΣ (ποσοστά, όρια, πίνακας μηνών) διαβάζονται από τις παραμέτρους
#   που ισχύουν την ημ. αναφοράς — ποτέ γραμμένες με το χέρι.
def _n(k, t, d="", br=None):
    return {"k": k, "t": t, "d": d, "br": br or []}


def _pct(x) -> str:
    return f"{x * 100:g}%".replace(".", ",")


def _rules(reader, history, on):
    """Οι κανόνες σε ισχύ· αν δεν έχουν δηλωθεί ακόμη, οι προτεινόμενοι
    (με σημαία, ώστε η σελίδα να το πει)."""
    try:
        return reader(on), False
    except ValueError:
        return history[-1], True


def _eur(x) -> str:
    return f"{x:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".").removesuffix(",00")


def _more_flows(on) -> list[dict]:
    """Δώρα, Επίδομα Αδείας, κατώτατος μισθός, ΦΜΥ (payroll_engine/config)."""
    mw, s1 = _rules(config.minimum_wage_for, config.MINIMUM_WAGE_HISTORY, on)
    al, s2 = _rules(config.allowance_rules_for, config.ALLOWANCE_HISTORY, on)
    tx, s3 = _rules(config.tax_scale_for, config.TAX_HISTORY, on)
    coeff = f"{1 + config.GIFT_LEAVE_COEFFICIENT:g}".replace(".", ",")
    year_x, half_x = config.ANNUAL_SALARIES, 2 * config.ANNUAL_SALARIES

    def withholding(periods_text):
        return [
            _n("calc", "Εισφορές ΕΦΚΑ εργαζομένου / εργοδότη",
               "ποσοστά του ΚΠΚ στην ημερομηνία καταβολής"),
            _n("calc", "ΦΜΥ με αναγωγή σε ετήσιο", periods_text),
            _n("end", "Καθαρό = μικτό − εισφορές − ΦΜΥ"),
        ]

    gifts = [
        _n("start", "Δώρο εορτών", "μετράει ο χρόνος απασχόλησης μέσα στην περίοδο του Δώρου"),
        _n("q", "Ποιο Δώρο;", br=[
            ("Χριστουγέννων", [
                _n("note", "Περίοδος 1/5 – 31/12", "καταβολή 21/12"),
                _n("q", "Απασχολήθηκε σε όλη την περίοδο;", br=[
                    ("ναι", [_n("calc", "1 μισθός", "ημερομίσθιοι: 25 ημερομίσθια")]),
                    ("όχι", [_n("calc", "2/25 του μισθού ανά 19 ημέρες",
                                "έως 1 μισθό · ημερομίσθιοι: 1 ημερομίσθιο ανά 8 ημέρες, έως 25")]),
                ]),
                _n("note", f"ΦΜΥ: × {year_x} … ÷ {year_x}", "ολόκληρος μισθός"),
            ]),
            ("Πάσχα", [
                _n("note", "Περίοδος 1/1 – 30/4",
                   "καταβολή Μ. Τετάρτη · μετρά ο μισθός 15 ημέρες πριν"),
                _n("q", "Απασχολήθηκε σε όλη την περίοδο;", br=[
                    ("ναι", [_n("calc", "½ μισθός", "ημερομίσθιοι: 15 ημερομίσθια")]),
                    ("όχι", [_n("calc", "1/15 του ½ μισθού ανά 8 ημέρες",
                                "έως ½ μισθό · ημερομίσθιοι: 1 ημερομίσθιο ανά 8 ημέρες, έως 15")]),
                ]),
                _n("note", f"ΦΜΥ: × {half_x} … ÷ {half_x}", "περίπου μισός μισθός"),
            ]),
        ]),
        _n("calc", f"× {coeff} (προσαύξηση συντελεστή αδείας)"),
        _n("note", "Σε αποχώρηση: pro-rata έως την ημ. λήξης",
           "Πάσχα αν η λήξη είναι έως 30/4, αλλιώς Χριστουγέννων"),
        *withholding("ο ετήσιος φόρος της κλίμακας διαιρείται με τον ίδιο αριθμό"),
    ]

    leave = [
        _n("start", "Επίδομα Αδείας", "ΕΝΑ ανά εργαζόμενο και έτος"),
        _n("note", "Γεννιέται τον μήνα της πρώτης κανονικής άδειας του έτους",
           "ο μήνας ορίζει μόνο πότε πληρώνεται, όχι το ποσό"),
        _n("q", "Ποιο ημερολογιακό έτος υπηρεσίας;", br=[
            ("3ο και μετά", [_n("calc", "Ολόκληρο: ½ μισθός", "ημερομίσθιοι: 13 ημερομίσθια")]),
            ("1ο ή 2ο", [_n("calc", "2 ημερομίσθια (μισθός ÷ 25) ανά μήνα υπηρεσίας στο έτος",
                            "από τον μήνα πρόσληψης έως τον Δεκέμβριο — ή έως την αποχώρηση · "
                            "ανώτατο ½ μισθός / 13 ημερομίσθια")]),
        ]),
        _n("note", "Χωρίς την προσαύξηση συντελεστή αδείας των Δώρων"),
        *withholding(f"× {half_x} … ÷ {half_x} (περίπου μισός μισθός)"),
    ]

    freeze_to = config.TRIENNIUM_FREEZE_RESUME - timedelta(days=1)
    minimum = [
        _n("start", "Έλεγχος κατώτατου μισθού",
           "στην πρόσληψη, σε μεταβολή σύμβασης και σε κάθε μισθοδοσία"),
        _n("calc", "Βασικός κατώτατος ΓΣΣΕ/ΚΥΑ",
           f"{_eur(mw.monthly)} € μισθός · {_eur(mw.daily)} € ημερομίσθιο "
           f"(από {mw.valid_from.strftime('%d/%m/%Y')})"),
        _n("q", "Τύπος αμοιβής;", br=[
            ("Μισθωτός", [
                _n("q", "Πλήρης απασχόληση (40ω);", br=[
                    ("ναι", [_n("note", "χωρίς αναλογία")]),
                    ("όχι", [_n("calc", "× εβδομαδιαίες ώρες ÷ 40")]),
                ]),
                _n("calc", f"Τριετίες: +{_pct(al.monthly_triennium_pct)} η καθεμία",
                   f"έως {al.monthly_max_triennia}"),
            ]),
            ("Ημερομίσθιος", [
                _n("calc", f"Τριετίες: +{_pct(al.daily_triennium_pct)} η καθεμία",
                   f"έως {al.daily_max_triennia} · χωρίς αναλογία ωρών"),
            ]),
        ]),
        _n("note", "Τριετίες = συμπληρωμένα έτη προϋπηρεσίας ÷ 3",
           "δηλωμένη + χρόνος στον εργοδότη · ο χρόνος "
           f"{config.TRIENNIUM_FREEZE_START.strftime('%d/%m/%Y')} – "
           f"{freeze_to.strftime('%d/%m/%Y')} δεν μετρά (πάγωμα)"),
        _n("q", "Δηλώθηκε γάμος;", br=[
            ("ναι", [_n("calc", f"+{_pct(al.marriage_pct)}")]),
            ("όχι", [_n("note", "—")]),
        ]),
        _n("calc", "+ λοιπές προσαυξήσεις %", "πτυχίο κ.λπ. — όπως δηλώθηκαν στην πρόσληψη"),
        _n("calc", "Όριο = βασικός × αναλογία ωρών × (1 + προσαυξήσεις)"),
        _n("q", "Αμοιβή ≥ όριο;", br=[
            ("ναι", [_n("end", "Εντάξει")]),
            ("όχι", [_n("end", "Απορρίπτεται / μπλοκάρει τη μισθοδοσία",
                        "οι τριετίες ωριμάζουν με τον χρόνο — ένας νόμιμος μισθός "
                        "μπορεί να πέσει κάτω από το όριο αργότερα")]),
        ]),
    ]

    lower, bands = 0.0, []
    for b in tx.brackets:
        span = (f"πάνω από {_eur(lower)}" if b.up_to == float("inf")
                else f"{_eur(lower)}–{_eur(b.up_to)}")
        bands.append(f"{span} €: {_pct(b.rate)}")
        lower = b.up_to
    reductions = " / ".join(_eur(r) for r in tx.reductions)
    fmy = [
        _n("start", "Φορολογητέο = μικτά − εισφορές εργαζομένου"),
        _n("q", "Τι πληρώνεται;", br=[
            ("μισθός, Δώρο Χριστουγέννων", [_n("calc", f"× {year_x} = ετήσιο")]),
            ("Δώρο Πάσχα, Επίδομα Αδείας", [_n("calc", f"× {half_x} = ετήσιο")]),
        ]),
        _n("calc", f"Κλίμακα «{tx.model_label}»", " · ".join(bands)),
    ]
    if isinstance(tx, config.TaxScaleYouth2026):
        fmy.append(_n("note", "Συντελεστές 1ου–3ου κλιμακίου ανά τέκνα και ηλικία",
                      f"1ο–2ο κλιμάκιο: έως {tx.youth_zero_max_age} ετών 0%, "
                      f"έως {tx.youth_low_max_age} ετών έως {_pct(tx.youth_low_rate)} · "
                      "με 4+ τέκνα 0%"))
    fmy += [
        _n("calc", "Μείωση φόρου βάσει τέκνων",
           f"{'/'.join(str(i) for i in range(len(tx.reductions)))} τέκνα: {reductions} € · "
           f"+{_eur(tx.extra_child_reduction)} € ανά επιπλέον"),
        _n("q", f"Ετήσιο εισόδημα > {_eur(tx.phaseout_threshold)} €;", br=[
            ("ναι", [_n("calc", f"η μείωση μικραίνει {_eur(tx.phaseout_per_1000)} € ανά 1.000 €",
                        "όχι με 5+ τέκνα")]),
            ("όχι", [_n("note", "πλήρης μείωση")]),
        ]),
        _n("calc", "Ετήσιος φόρος = κλίμακα − μείωση", "ποτέ κάτω από 0"),
        _n("end", f"Παρακράτηση = ετήσιος φόρος ÷ {year_x} (ή ÷ {half_x})"),
    ]

    return [
        {"key": "minimum", "icon": "⚖️", "title": "Κατώτατος μισθός με τριετίες",
         "src": "config.minimum_floor_value · seniority_factor",
         "seq": minimum, "suggested": s1 or s2},
        {"key": "gifts", "icon": "🎁", "title": "Δώρα Χριστουγέννων / Πάσχα",
         "src": "payroll_engine.calc_christmas_gift · calc_easter_gift",
         "seq": gifts, "suggested": False},
        {"key": "leave_allowance", "icon": "🏖️", "title": "Επίδομα Αδείας",
         "src": "payroll_engine.calc_leave_allowance", "seq": leave, "suggested": False},
        {"key": "fmy", "icon": "🧾", "title": "ΦΜΥ (παρακράτηση φόρου)",
         "src": "payroll_engine.periodic_fmy · config.TaxScale",
         "seq": fmy, "suggested": s3},
    ]


def calc_flows(on) -> list[dict]:
    sick, s1 = _rules(config.sick_leave_rules_for, config.SICK_LEAVE_HISTORY, on)
    wh, s2 = _rules(config.work_hour_rules_for, config.WORK_HOUR_HISTORY, on)
    sev, s3 = _rules(config.severance_rules_for, config.SEVERANCE_HISTORY, on)

    months, parts = list(sev.months), []
    for i, (years, mo) in enumerate(months):
        last = months[i + 1][0] - 1 if i + 1 < len(months) else None
        span = f"{years}+" if last is None else f"{years}" if last == years else f"{years}–{last}"
        parts.append(f"{span} ετ. → {mo} μ.")
    sev_table = " · ".join(parts)
    tax, prev = [], 0
    for up, rate in sev.tax_brackets:
        span = f"πάνω από {prev:,.0f}" if up == float("inf") else f"{prev:,.0f}–{up:,.0f}"
        tax.append(f"{span.replace(',', '.')} €: {_pct(rate)}")
        prev = up
    tax_text = " · ".join(tax)

    termination = [
        _n("start", "Αποχώρηση εργαζομένου",
           "η ημ. λήξης πρέπει να είναι μέσα στον τρέχοντα μήνα μισθοδοσίας"),
        _n("calc", "Μισθοδοσία μήνα αποχώρησης — μόνο έως την ημ. λήξης",
           "pro-rata ημέρες· αν υπάρχει ήδη εγκεκριμένο φύλλο: min(φύλλο, pro-rata)"),
        _n("calc", "Pro-rata Δώρο + Επίδομα Αδείας",
           "Δώρο Πάσχα αν η λήξη είναι έως 30/4, αλλιώς Χριστουγέννων — "
           "όσα δεν έχουν ήδη υπολογιστεί"),
        _n("q", "Έχει υπόλοιπο κανονικής άδειας;", br=[
            ("ναι", [_n("calc", "Αποζημίωση μη ληφθείσας άδειας")]),
            ("όχι", [_n("note", "—")]),
        ]),
        _n("q", "Είδος αποχώρησης;", br=[
            ("Οικειοθελής αποχώρηση", [_n("end", "Χωρίς αποζημίωση απόλυσης")]),
            ("Λήξη σύμβασης ορισμένου χρόνου", [_n("end", "Χωρίς αποζημίωση απόλυσης")]),
            ("Καταγγελία (απόλυση)", [
                _n("q", "Χρόνος στον εργοδότη (πρόσληψη → απόλυση) ≥ 1 έτος;",
                   "μετρά ΜΟΝΟ ο χρόνος στον ίδιο εργοδότη, όχι η δηλωμένη προϋπηρεσία", br=[
                       ("όχι", [_n("end", "Καμία αποζημίωση")]),
                       ("ναι", [
                           _n("calc", "Μήνες αποζημίωσης από τον πίνακα", sev_table),
                           _n("q", "Δόθηκε προειδοποίηση;", br=[
                               ("ναι", [_n("calc", "μήνες ÷ 2")]),
                               ("όχι", [_n("calc", "πλήρεις μήνες")]),
                           ]),
                           _n("calc", "Μικτή = μήνες × μηνιαίες αποδοχές × "
                              f"{sev.gift_factor:.4g}".replace(".", ","),
                              "ημερομίσθιοι: ημερομίσθιο × 25 · ο συντελεστής "
                              "είναι η προσαύξηση 1/6 (Δώρα/επίδομα)"),
                           _n("calc", "Αυτοτελής φόρος (κλιμακωτά)", tax_text),
                           _n("end", "Καθαρή = μικτή − φόρος", "χωρίς ασφαλιστικές κρατήσεις"),
                       ]),
                   ]),
            ]),
        ]),
    ]

    sickness = [
        _n("start", "Ημέρα ασθενείας"),
        _n("q", "Την προβλέπει το πρόγραμμα ως εργάσιμη;", br=[
            ("όχι (ρεπό/αργία)", [_n("end", "Δεν μετρά",
                                      "ούτε κόστος για τον εργοδότη ούτε στο όριο")]),
            ("ναι", [_n("note", "Κυλιόμενο έτος",
                         "ανοίγει με την 1η ημέρα ασθενείας και διαρκεί 365 ημέρες")]),
        ]),
        _n("q", f"Είναι μία από τις πρώτες {sick.half_wage_days} ημέρες του κυλιόμενου έτους;",
           "το τριήμερο δίνεται ΜΙΑ φορά ανά κυλιόμενο έτος και μετρά μέσα στο όριο", br=[
               ("ναι", [_n("calc", "Μισό ημερομίσθιο από τον εργοδότη",
                           "δεν αφαιρείται επίδομα ΕΦΚΑ")]),
               ("όχι", [_n("q", "Μέσα στο όριο ημερών;",
                           f"{sick.limit_under_year} ημέρες αν η προϋπηρεσία στον "
                           "εργοδότη είναι < 1 έτος, "
                           f"αλλιώς {sick.limit_over_year}", br=[
                               ("ναι", [_n("calc", "Πλήρες ημερομίσθιο μείον επίδομα ΕΦΚΑ",
                                           "το επίδομα καταχωρείται χειροκίνητα και "
                                           "μοιράζεται στους μήνες")]),
                               ("όχι", [_n("calc", "Άνευ αποδοχών",
                                           "κόβει και ημέρες ασφάλισης ΕΦΚΑ")]),
                           ])]),
           ]),
        _n("calc", "Περικοπή μικτών = ½ ημερομίσθιο × ημέρες τριημέρου + επίδομα ΕΦΚΑ",
           "οι ημέρες ΕΦΚΑ μένουν ίδιες — εκτός από τις ημέρες πάνω από το όριο"),
        _n("end", "ΑΠΔ: οι αποδοχές ασθενείας δηλώνονται χωριστά (τύπος 008)"),
    ]

    premiums = [
        _n("start", "Κάθε ώρα εργασίας", "από τις βάρδιες της κάρτας — τρεις ανεξάρτητοι άξονες"),
        _n("q", "Άξονας 1 — τι ώρα;", br=[
            (f"{wh.night_start}:00–{wh.night_end:02d}:00",
             [_n("calc", f"Νύχτα +{_pct(wh.night_premium)}")]),
            ("άλλη ώρα", [_n("calc", "Ημέρα +0%")]),
        ]),
        _n("q", "Άξονας 2 — τι ημέρα;", br=[
            ("Κυριακή / αργία", [_n("calc", f"Αργία +{_pct(wh.holiday_premium)}")]),
            ("εργάσιμη", [_n("calc", "+0%")]),
        ]),
        _n("q", "Άξονας 3 — πού πέφτει μέσα στην εβδομάδα (Δευ–Κυρ);",
           "Σάββατο πενθημέρου και Κυριακή δεν μετρούν στην υπερεργασία", br=[
               ("μέσα στη σύμβαση", [_n("calc", "Κανονική 0%", "την καλύπτει ο μισθός")]),
               ("μερική: σύμβαση → 40ω", [_n("calc", f"Πρόσθετη +{_pct(wh.extra_premium)}")]),
               (f"40 → {wh.weekly_legal_5}ω (πενθ.) / {wh.weekly_legal_6}ω (εξαήμ.) ή πάνω από "
                f"{wh.daily_overwork_start}ω/ημέρα",
                [_n("calc", f"Υπερεργασία +{_pct(wh.overwork_premium)}")]),
               (f"πάνω από {wh.daily_legal_5}ω/ημέρα (πενθ.) / {wh.daily_legal_6}ω (εξαήμ.) "
                "ή το εβδομαδιαίο νόμιμο", [
                    _n("q", f"Μέσα στα όρια (≤ {wh.overtime_day_cap}ω/ημέρα, "
                            f"≤ {wh.overtime_year_cap}ω/έτος);", br=[
                        ("ναι", [_n("calc", f"Νόμιμη υπερωρία +{_pct(wh.overtime_premium)}")]),
                        ("όχι", [_n("calc", f"Παράνομη υπερωρία +{_pct(wh.illegal_premium)}")]),
                    ]),
                ]),
           ]),
        _n("calc", "Ωρομίσθιο = μισθός ÷ 25 × 6 ÷ εβδομαδιαίες ώρες",
           "ημερομίσθιοι: ημερομίσθιο × 6 ÷ 40"),
        _n("calc", "Ποσό ώρας",
           "μέσα στο ωράριο: μόνο ωρομίσθιο × (νύχτα + αργία) · έξω από το ωράριο: ωρομίσθιο × "
           "(1 + κλάση) × (1 + νύχτα + αργία) — π.χ. υπερεργασία νύχτα = "
           f"{1 + wh.overwork_premium:g} × {1 + wh.night_premium:g} = "
           f"{(1 + wh.overwork_premium) * (1 + wh.night_premium):g}".replace(".", ",")),
        _n("end", "Προστίθεται στα μικτά της μισθοδοσίας"),
    ]

    return [
        {"key": "termination", "icon": "🚪", "title": "Αποχώρηση / απόλυση",
         "src": "services/contract.terminate · entitlements.severance_preview",
         "seq": termination, "suggested": s3},
        {"key": "sickness", "icon": "🤒", "title": "Ασθένεια εργαζομένου",
         "src": "EmploymentState.sick_day_map · payroll_engine.sick_deduction",
         "seq": sickness, "suggested": s1},
        {"key": "premiums", "icon": "🌙", "title": "Προσαυξήσεις ωρών (νύχτα / αργία / υπερωρίες)",
         "src": "domain/work_hours.py", "seq": premiums, "suggested": s2},
        *_more_flows(on),
    ]


@bp.get("/")
def index():
    y, m = _period()
    return render_template(
        "rules_map.html", y=y, m=m, graph=_chain_graph(),
        status=period_status(y, m), vmap=validation_map(),
        flows=calc_flows(today()), ref=today(),
        is_admin=bool(getattr(g, "user", None) and g.user.is_admin),
    )


@bp.get("/flow")
def flow():
    """htmx: ο γράφος για άλλη περίοδο."""
    y, m = _period()
    return render_template(
        "partials/_rules_flow.html", y=y, m=m, graph=_chain_graph(),
        status=period_status(y, m),
        is_admin=bool(getattr(g, "user", None) and g.user.is_admin),
    )
