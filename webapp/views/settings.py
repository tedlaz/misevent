"""Παράμετροι εφαρμογής: ΓΣΣΕ, ΕΦΚΑ, ΤΕΚΑ, φορολογική κλίμακα, ασθένεια,
προσαυξήσεις, όρια ωρών — ΟΛΑ ως events (*Defined), με πίνακες ιστορικότητας
(valid_from) και wizard πρώτου τρεξίματος που επιβεβαιώνει τις προτεινόμενες
τιμές του config. Επιπλέον: η ΑΛΥΣΙΔΑ των υποχρεώσεων (γράφος SVG, read-only)
και η παραμετροποίηση προθεσμιών/παρατάσεων ανά τύπο (ObligationTypeConfigured).
"""

from io import BytesIO

from flask import Blueprint, abort, render_template, request, send_file

from misevent.rules import config
from misevent.rules import obligation_types as ot

from ..clock import today
from ..helpers import (
    flash_to,
    form_date,
    form_float,
    form_int,
    form_optional_date,
    run_command,
)
from ..system import svc

bp = Blueprint("settings", __name__, url_prefix="/settings")

# Πόσες γραμμές χρονοσειράς ποσοστών δέχεται η φόρμα ΚΠΚ (ίδιο νούμερο και
# στο template — μία πηγή αλήθειας εδώ).
_KPK_RATE_ROWS = 8

# ΟΛΑ ΠΑΡΑΓΩΓΑ ΤΟΥ ΜΗΤΡΩΟΥ `config.FAMILIES` — καμία λίστα πεδίων εδώ.
# Πριν, τα πεδία/ετικέτες/κείμενα κάθε οικογένειας ζούσαν ΚΑΙ εδώ ΚΑΙ στο
# config, και μια νέα οικογένεια απαιτούσε αλλαγή και στα δύο.
#
# Το σχήμα μένει (όνομα, ετικέτα, τύπος-widget) ώστε το `_param_modal.html`
# να μην αλλάξει. Ο τύπος έρχεται από το `FIELD_KINDS[kind]["ui"]`:
# money (€), pct (δίνεται σε % στη φόρμα, αποθηκεύεται κλάσμα), int.
#
# ➜ Οι οικογένειες με `ui_mode="custom"` (φορολογική κλίμακα: κλιμάκια +
#   καθεστώς) ΔΕΝ χωρούν σε απλή λίστα πεδίων και έχουν δικό τους modal.
# ➜ Τα πεδία με `ui=False` δεν ζητούνται (π.χ. τα ποσοστά ΕΦΚΑ, που έρχονται
#   από το ΚΠΚ της κάλυψης και μεταφέρονται αυτούσια μέσω `carry_over`).
FAMILY_FIELDS = {
    key: [(f.name, f.label, config.FIELD_KINDS[f.kind]["ui"])
          for f in spec.ui_fields()]
    for key, spec in config.FAMILIES.items()
    if spec.ui_mode == "generic"
}

CARD_LABELS = {
    k: s.card_label for k, s in config.FAMILIES.items() if s.card_label
}
FAMILY_NOTES = {k: s.note for k, s in config.FAMILIES.items() if s.note}


def _row_values(entry, fields):
    return {name: getattr(entry, name) for name, _l, _t in fields}


def _tax_row(entry):
    """Σύνοψη μιας κλίμακας ΦΜΥ για τον πίνακα του UI."""
    parts = []
    for b in entry.brackets:
        up = "∞" if b.up_to == float("inf") else f"{b.up_to:,.0f}".replace(",", ".")
        parts.append(f"έως {up}: {b.rate * 100:g}%")
    return {
        "valid_from": entry.valid_from,
        "model": type(entry).model_key,
        "model_label": type(entry).model_label,
        "brackets": " · ".join(parts),
        "reductions": ", ".join(f"{r:g}" for r in entry.reductions),
        "repealed": getattr(entry, "repealed", False),
    }


SPAWN_LABELS = {
    "period": "μηνιαία", "period_teka": "μηνιαία (ΤΕΚΑ)",
    "period_card": "μηνιαία (ψηφιακή κάρτα)",
    "annual": "ετήσια", "reactive": "αντιδραστική",
    "on_source": "καταβολή",
}


def _due_text(rule) -> str:
    kind = rule[0]
    if kind in ("eom", "eom_cal"):
        month = {0: "του μήνα", 1: "του επόμενου μήνα",
                 2: "του μεθεπόμενου μήνα"}.get(rule[1], f"μήνα +{rule[1]}")
        return ("τελ. εργάσιμη " if kind == "eom" else "τέλος ") + month
    if kind == "fixed":
        return f"σταθερά {rule[2]}/{rule[1]}"
    if kind == "easter_minus":
        return f"{rule[1]} ημέρες πριν το Πάσχα"
    if kind == "as_source":
        return "όπως η υποχρέωση-πηγή"
    return str(rule)


# Χειροποίητη διάταξη (στήλη = βήμα, γραμμή) — οι ΑΚΜΕΣ έρχονται πάντα από
# το μητρώο. Τύπος που λείπει εδώ μπαίνει αυτόματα σε γραμμή στο τέλος.
CHAIN_COLS = ("Κάρτα", "Παρουσίες", "Μισθοδοσία & ΕΡΓΑΝΗ",
              "Πληρωμές & υποβολές", "Καταβολές")
CHAIN_LAYOUT = {
    "RETRO_SCHEDULE": (2, 0), "LEAVE_DECLARATION": (2, 1),
    "CARD_IMPORT": (0, 2), "ATTENDANCE_APPROVAL": (1, 2),
    "MONTHLY_PAYROLL": (2, 2), "SALARY_PAYMENT": (3, 2),
    "APD_SUBMISSION": (3, 3), "EFKA_PAYMENT": (4, 3),
    "APD_TEKA_SUBMISSION": (3, 4), "TEKA_PAYMENT": (4, 4),
    "FMY_SUBMISSION": (3, 5), "FMY_PAYMENT": (4, 5),
    "WAGE_ADJUSTMENT": (1, 6), "LEAVE_ALLOWANCE": (2, 6),
    "LEAVE_ALLOWANCE_PAYMENT": (3, 6),
    # ζώνη «ετήσιες» (κάτω από τη διαχωριστική)
    "EASTER_GIFT": (2, 7), "EASTER_GIFT_PAYMENT": (3, 7),
    "CHRISTMAS_GIFT": (2, 8), "CHRISTMAS_GIFT_PAYMENT": (3, 8),
}
CHAIN_SPINE = ("CARD_IMPORT", "ATTENDANCE_APPROVAL", "MONTHLY_PAYROLL",
               "SALARY_PAYMENT")
CHAIN_ANNUAL_ROW = 7  # από εδώ και κάτω: ετήσιες


def _chain_graph():
    """Layout του γράφου της αλυσίδας (SVG). Στήλες = βήματα του μήνα·
    ζωγραφίζονται ΜΟΝΟ οι ακμές ίδιας περιόδου, χωρίς τις μεταβατικές
    (Α→Γ όταν ήδη Α→Β→Γ)· οι εξαρτήσεις από ΑΛΛΗ περίοδο γίνονται λίστα
    (`cross`) και σήμα ↩ στον κόμβο. Κάθε κόμβος είναι clickable.
    Υπολογίζεται server-side — το template απλώς ζωγραφίζει."""
    codes = list(ot.TYPES)
    same = {c: {r[0] for r in ot.TYPES[c].requires if r[1] == "same"} for c in codes}

    def reach(a, seen=None):  # ό,τι προαπαιτεί (ίδια περίοδος) ο a, μεταβατικά
        seen = set() if seen is None else seen
        for p in same[a]:
            if p not in seen:
                seen.add(p)
                reach(p, seen)
        return seen

    pos = {c: CHAIN_LAYOUT[c] for c in codes if c in CHAIN_LAYOUT}
    extra = max((r for _, r in pos.values()), default=-1) + 1
    for c in codes:  # νέος τύπος χωρίς θέση: στήλη = βάθος, νέα γραμμή
        if c not in pos:
            depth = len(reach(c))
            pos[c] = (min(depth, len(CHAIN_COLS) - 1), extra)
            extra += 1

    W, H, GX, GY, PAD, TOP, BAND = 252, 54, 40, 14, 16, 34, 30
    rows = max(r for _, r in pos.values()) + 1

    def xy(c):
        col, row = pos[c]
        y = TOP + row * (H + GY) + (BAND if row >= CHAIN_ANNUAL_ROW else 0)
        return PAD + col * (W + GX), y

    ncols = max(len(CHAIN_COLS), max(col for col, _ in pos.values()) + 1)
    width = 2 * PAD + ncols * (W + GX) - GX
    height = TOP + rows * (H + GY) - GY + PAD + (BAND if rows > CHAIN_ANNUAL_ROW else 0)

    SEL = {"prev": "προηγ. μήνα", "year": "τρέχοντος έτους",
           "prev_year": "προηγ. έτους"}
    nodes, cross = [], []
    for c in codes:
        s, t = ot.spec(c), ot.TYPES[c]
        x, y = xy(c)
        others = []
        for r in t.requires:
            if r[1] == "same":
                continue
            sel = r[1]
            when = SEL.get(sel) or (f"μήνα {sel.split(':')[1]}"
                                    if sel.startswith("month:") else sel)
            dep = ot.TYPES[r[0]]
            others.append(f"{dep.icon} {dep.label} ({when}"
                          + (f" — μόνο για περίοδο {r[2]}" if len(r) > 2 else "")
                          + ")")
        if others:
            cross.append({"icon": t.icon, "label": t.label, "deps": others})
        ext = s.extension or {}
        nodes.append({
            "code": c, "label": t.label, "icon": t.icon,
            "short": t.label if len(t.label) <= 30 else t.label[:29] + "…",
            "x": x, "y": y, "spawn": t.spawn,
            "spawn_text": SPAWN_LABELS.get(t.spawn, t.spawn),
            "due_text": _due_text(s.due),
            "ext_text": (f"έως {ext.get('max_days', '—')} ημέρες"
                         if ext.get("allowed") else "όχι"),
            "cross": others,
            "spine": c in CHAIN_SPINE,
            "overridden": ot.is_overridden(c),
        })

    edges = []
    for c in codes:
        # μεταβατική αναγωγή: κρατάμε μόνο τις άμεσες προϋποθέσεις
        direct = {a for a in same[c]
                  if not any(a in reach(b) for b in same[c] if b != a)}
        for a in sorted(direct, key=codes.index):
            (x1, y1), (x2, y2) = xy(a), xy(c)
            if pos[a][0] == pos[c][0]:  # ίδια στήλη: κάθετο βέλος
                cx = x1 + W / 2
                d = f"M {cx} {y1 + H} L {cx} {y2 - 2}"
            else:
                x1, y1, x2, y2 = x1 + W, y1 + H / 2, x2 - 2, y2 + H / 2
                k = (x2 - x1) / 2
                d = f"M {x1} {y1} C {x1 + k} {y1}, {x2 - k} {y2}, {x2} {y2}"
            edges.append({"d": d,
                          "spine": a in CHAIN_SPINE and c in CHAIN_SPINE})

    cols = [{"label": f"{i + 1} · {lab}", "x": PAD + i * (W + GX) + W / 2}
            for i, lab in enumerate(CHAIN_COLS)]
    band_y = (TOP + CHAIN_ANNUAL_ROW * (H + GY) - GY / 2 + BAND / 2
              if rows > CHAIN_ANNUAL_ROW else None)
    return {"width": width, "height": height, "W": W, "H": H,
            "nodes": nodes, "edges": edges, "cols": cols,
            "band_y": band_y, "cross": cross}


def _severance_row(entry):
    """Σύνοψη κανόνων αποζημίωσης για τον πίνακα του UI."""
    months = " · ".join(f"{y}ετ→{m}μ" for y, m in entry.months)
    tax = " · ".join(
        ("άνω" if up == float("inf") else f"έως {up:,.0f}".replace(",", "."))
        + f": {rate * 100:g}%"
        for up, rate in entry.tax_brackets
    )
    return {
        "valid_from": entry.valid_from,
        "months": months,
        "gift_factor": entry.gift_factor,
        "tax": tax,
        "repealed": getattr(entry, "repealed", False),
    }


def _mark_current(rows):
    """Σημειώνει (`current`) τη γραμμή που ΙΣΧΥΕΙ την ημερομηνία αναφοράς —
    καμία, αν η πιο πρόσφατη εφαρμοστέα είναι κατάργηση. Επιστρέφει τη γραμμή."""
    on = today()
    best = max((r for r in rows if r["valid_from"] <= on),
               key=lambda r: r["valid_from"], default=None)
    for r in rows:
        r["current"] = r is best and not r.get("repealed")
    return best if best and not best.get("repealed") else None


def _panel_ctx():
    families = []
    for fam, fields in FAMILY_FIELDS.items():
        entries, source = svc.parameter_entries(fam)
        families.append({
            "key": fam,
            "label": CARD_LABELS.get(fam, config.FAMILY_LABELS[fam]),
            "note": FAMILY_NOTES.get(fam, ""),
            "fields": fields,
            # Το work_hours έχει 15 πεδία — αποδίδεται ως ΚΑΡΤΕΛΕΣ ανά ισχύ
            # αντί για φαρδύ πίνακα (ξεχείλωνε το πλαίσιο).
            "as_cards": fam == "work_hours",
            "rows": [
                {
                    "valid_from": e.valid_from,
                    "values": _row_values(e, fields),
                    "repealed": getattr(e, "repealed", False),
                }
                for e in entries
            ],
            "source": source,
        })
        families[-1]["current"] = _mark_current(families[-1]["rows"])
    tax_entries, tax_source = svc.parameter_entries("tax")
    sev_entries, sev_source = svc.parameter_entries("severance")
    tax_rows = [_tax_row(e) for e in tax_entries]
    sev_rows = [_severance_row(e) for e in sev_entries]
    return {
        "families": families,
        "tax_rows": tax_rows,
        "tax_current": _mark_current(tax_rows),
        "tax_source": tax_source,
        "sev_rows": sev_rows,
        "sev_current": _mark_current(sev_rows),
        "sev_source": sev_source,
        "confirmed": svc.parameters_confirmed(),
        # Από πού θα γραφτούν οι αρχικές τιμές στο πρώτο τρέξιμο: το αρχείο
        # data/init_parameters.json ή, αν λείπει, οι τιμές του config.
        "init_file": bool(svc.init_parameters_doc()),
        "graph": _chain_graph(),
        # Τα ποσοστά που δείχνει ο πίνακας είναι αυτά που ΙΣΧΥΟΥΝ την
        # ημερομηνία αναφοράς του UI (ταξίδι στον χρόνο, βλ. webapp/clock.py).
        "kpk_entries": svc.kpk_entries(on=today()),
        "config": config,
    }


def _panel(msg, ok):
    html = render_template("partials/_settings_panel.html", **_panel_ctx())
    return flash_to(html, msg, err=not ok)


def _modal_error(template, msg, **ctx):
    """Σφάλμα σε modal: μένει ανοιχτό με τα δεδομένα του χρήστη."""
    html = render_template(template, form=request.form, **ctx)
    resp = flash_to(html, msg, err=True)
    resp.headers["HX-Retarget"] = "#modal-host"
    resp.headers["HX-Reswap"] = "innerHTML"
    return resp


@bp.get("/")
def index():
    return render_template("settings.html", **_panel_ctx())


@bp.get("/modal/close")
def modal_close():
    return ""


# ------------------- Εξαγωγή/εισαγωγή ΟΛΩΝ των παραμέτρων (JSON)
# ΕΝΑ αρχείο για ό,τι δείχνει αυτή η σελίδα: οικογένειες παραμέτρων με το
# ιστορικό ισχύος τους, φορολογική κλίμακα, αποζημίωση,
# προθεσμίες τύπων υποχρεώσεων και Μητρώο ΚΠΚ (βλ. services/parameters.py).
@bp.get("/export")
def export_json():
    """Κατέβασμα — ο χρήστης διαλέγει πού θα αποθηκευτεί από τον browser."""
    return send_file(
        BytesIO(svc.export_parameters_bytes()), mimetype="application/json",
        as_attachment=True,
        download_name=f"parameters_{today().isoformat()}.json",
    )


@bp.post("/import")
def import_json():
    """Φόρτωση αρχείου εξαγωγής: κάθε γραμμή περνά από τα κανονικά commands
    (γράφονται events), ώστε να ισχύουν όλοι οι έλεγχοι."""

    def cmd():
        f = request.files.get("file")
        if f is None or not f.filename:
            raise ValueError("Επιλέξτε αρχείο JSON")
        res = svc.import_parameters(f.read())
        c = res["counts"]
        parts = [
            f"{c[key]} {label}"
            for key, label in (("parameters", "γραμμές παραμέτρων"),
                               ("kpks", "ΚΠΚ"),
                               ("obligation_types", "τύποι υποχρεώσεων"))
            if c[key]
        ]
        if not parts:
            raise ValueError(
                "Δεν εισήχθη τίποτα — " + (" | ".join(res["errors"][:3])
                                           or "το αρχείο δεν είχε καμία τιμή")
            )
        msg = "Εισήχθησαν: " + ", ".join(parts)
        if res["errors"]:
            msg += (f" · {len(res['errors'])} σφάλματα: "
                    + " | ".join(res["errors"][:3]))
        return msg

    ok, msg = run_command(cmd)
    return _panel(msg, ok)


# ------------------------------------------------- Wizard πρώτου τρεξίματος
@bp.post("/confirm")
def confirm():
    def cmd():
        n = svc.confirm_default_parameters()
        if not n:
            raise ValueError("Το μητρώο παραμέτρων είναι ήδη επιβεβαιωμένο")
        src = ("data/init_parameters.json" if svc.init_parameters_doc()
               else "τις προτεινόμενες τιμές του config")
        return (f"Καταχωρήθηκαν {n} εγγραφές παραμέτρων ως events από {src} — "
                "από εδώ και πέρα κάθε αλλαγή είναι απλή προσθήκη νέας γραμμής")

    ok, msg = run_command(cmd)
    return _panel(msg, ok)


# ------------------------------------------- Απλές οικογένειες παραμέτρων
def _parse_values(family, form):
    out = {}
    for name, label, typ in FAMILY_FIELDS[family]:
        if typ == "int":
            out[name] = form_int(form, name)
        elif typ == "pct":
            out[name] = form_float(form, name) / 100.0
        else:
            out[name] = form_float(form, name)
    return out


def _define(family, vf, v):
    """ΜΙΑ διαδρομή για ΟΛΕΣ τις οικογένειες. Ό,τι ήταν ειδική περίπτωση εδώ
    (π.χ. η μεταφορά των ποσοστών ΕΦΚΑ) είναι πλέον ΔΕΔΟΜΕΝΟ του μητρώου
    (`carry_over`) και το χειρίζεται το command layer."""
    svc.define_parameters(family, vf, **v)


def _family_ctx(family):
    if family not in FAMILY_FIELDS:
        raise ValueError(f"Άγνωστη οικογένεια παραμέτρων «{family}»")
    return {
        "family": family,
        "label": config.FAMILY_LABELS[family],
        "fields": FAMILY_FIELDS[family],
    }


@bp.get("/param/modal")
def param_modal():
    """Modal νέας γραμμής — ή επεξεργασίας (ίδιο valid_from = αντικατάσταση),
    αν δοθεί ?valid_from."""
    family = request.args.get("family", "")
    ctx = _family_ctx(family)
    entry = None
    vf = request.args.get("valid_from")
    if vf:
        entries, _src = svc.parameter_entries(family)
        entry = next(
            (e for e in entries if e.valid_from.isoformat() == vf), None
        )
    return render_template("partials/_param_modal.html", entry=entry, **ctx)


@bp.post("/param/<family>")
def param_save(family):
    def cmd():
        vf = form_date(request.form, "valid_from")
        _define(family, vf, _parse_values(family, request.form))
        return (f"Καταχωρήθηκε εγγραφή «{config.FAMILY_LABELS[family]}» "
                f"με ισχύ από {vf.strftime('%d/%m/%Y')}")

    ok, msg = run_command(cmd)
    if ok:
        return _panel(msg, ok)
    return _modal_error(
        "partials/_param_modal.html", msg, entry=None, **_family_ctx(family)
    )


@bp.get("/param/repeal/modal")
def param_repeal_modal():
    """Modal ΚΑΤΑΡΓΗΣΗΣ κανόνα. Χωριστό από το «Νέα ισχύς» επίτηδες: δεν
    είναι διόρθωση τιμής, είναι δήλωση ότι ο κανόνας παύει να υπάρχει."""
    family = request.args.get("family", "")
    if family not in config.FAMILIES:
        raise ValueError(f"Άγνωστη οικογένεια παραμέτρων «{family}»")
    return render_template(
        "partials/_param_repeal_modal.html",
        family=family,
        label=config.FAMILIES[family].label,
    )


@bp.post("/param/<family>/repeal")
def param_repeal(family):
    def cmd():
        vf = form_date(request.form, "valid_from")
        svc.repeal_parameters(family, vf)
        return (f"Ο κανόνας «{config.FAMILY_LABELS[family]}» καταργήθηκε από "
                f"{vf.strftime('%d/%m/%Y')} — από εκείνη την ημερομηνία και "
                "μετά η μισθοδοσία θα ζητά νέα εγγραφή")

    ok, msg = run_command(cmd)
    if ok:
        return _panel(msg, ok)
    return _modal_error(
        "partials/_param_repeal_modal.html", msg,
        family=family, label=config.FAMILY_LABELS[family],
    )


# --------------------------------------------------- Φορολογική κλίμακα
def _mp_name(model: str, name: str) -> str:
    """Όνομα input παραμέτρου καθεστώτος — με πρόθεμα, ώστε δύο καθεστώτα
    με ίδιο πεδίο να μη συγκρούονται στη φόρμα."""
    return f"{model}__{name}"


def _mp_str(value, pct: bool) -> str:
    k = 100 if pct else 1
    if isinstance(value, (tuple, list)):
        return ";".join(f"{v * k:g}" for v in value)
    return f"{value * k:g}"


def _tax_model_forms() -> list[dict]:
    """Οι ενότητες «Παράμετροι καθεστώτος» του modal — μία ανά καθεστώς με
    πεδία (config.tax_model_ui_fields)· παράγονται από τις κλάσεις."""
    out = []
    for model, cls in config.TAX_MODELS.items():
        flds = [
            {
                "name": _mp_name(model, f.name),
                "label": f.metadata["label"],
                "list": f.type in (tuple, "tuple"),
                "step": "1" if f.type in (int, "int") else "0.1",
            }
            for f in config.tax_model_ui_fields(model)
        ]
        if flds:
            out.append({"key": model, "label": cls.model_label, "fields": flds})
    return out


def _tax_model_params(form, model: str) -> dict:
    """Τα `model_params` του επιλεγμένου καθεστώτος από τη φόρμα."""
    out = {}
    for f in config.tax_model_ui_fields(model):
        raw = form.get(_mp_name(model, f.name), "")
        k = 100.0 if f.metadata["pct"] else 1.0
        if f.type in (tuple, "tuple"):
            vals = [v / k for v in _parse_numbers(raw, f.metadata["label"])]
            if vals:
                out[f.name] = vals
            continue
        nums = _parse_numbers(raw, f.metadata["label"])
        if not nums:
            continue
        out[f.name] = int(nums[0]) if f.type in (int, "int") else nums[0] / k
    return out


def _tax_prefill(entry):
    """Προσυμπλήρωση της φόρμας κλίμακας (strings) — από υπάρχουσα εγγραφή
    ή τις ενδεικτικές προεπιλογές."""
    p = {
        "valid_from": "",
        "model": "standard",
        "brackets": "10000;9\n20000;22\n30000;28\n40000;36\n;44",
        "reductions": "777;900;1120;1340;1580",
        "extra_child_reduction": "220",
        "phaseout_threshold": "12000",
        "phaseout_per_1000": "20",
    }
    # Πεδία καθεστώτων: οι προεπιλογές της κλάσης, ή οι τιμές της εγγραφής.
    for model in config.TAX_MODELS:
        for fld in config.tax_model_ui_fields(model):
            src = entry if entry is not None and type(entry).model_key == model else None
            value = getattr(src, fld.name) if src else fld.default
            p[_mp_name(model, fld.name)] = _mp_str(value, fld.metadata["pct"])
    if entry is None:
        return p
    lines = []
    for b in entry.brackets:
        up = "" if b.up_to == float("inf") else f"{b.up_to:g}"
        lines.append(f"{up};{b.rate * 100:g}")
    p.update({
        "valid_from": entry.valid_from.isoformat(),
        "model": type(entry).model_key,
        "brackets": "\n".join(lines),
        "reductions": ";".join(f"{r:g}" for r in entry.reductions),
        "extra_child_reduction": f"{entry.extra_child_reduction:g}",
        "phaseout_threshold": f"{entry.phaseout_threshold:g}",
        "phaseout_per_1000": f"{entry.phaseout_per_1000:g}",
    })
    return p


@bp.get("/tax/modal")
def tax_modal():
    entry = None
    vf = request.args.get("valid_from")
    if vf:
        entries, _src = svc.parameter_entries("tax")
        entry = next(
            (e for e in entries if e.valid_from.isoformat() == vf), None
        )
    return render_template(
        "partials/_tax_modal.html",
        edit=entry is not None,
        p=_tax_prefill(entry),
        models=config.TAX_MODELS, model_forms=_tax_model_forms(),
    )


@bp.get("/severance/modal")
def severance_modal():
    """Modal αποζημίωσης απόλυσης — δύο ΠΙΝΑΚΕΣ (μήνες ανά έτη, κλιμάκια
    αυτοτελούς φόρου), γι' αυτό δεν χωρά στο γενικό modal παραμέτρων."""
    entry = None
    vf = request.args.get("valid_from")
    entries, _src = svc.parameter_entries("severance")
    if vf:
        entry = next((e for e in entries if e.valid_from.isoformat() == vf), None)
    src = entry or (max(entries, key=lambda e: e.valid_from) if entries else None)
    return render_template(
        "partials/_severance_modal.html",
        edit=entry is not None,
        p={
            "valid_from": entry.valid_from.isoformat() if entry else "",
            "months": "\n".join(f"{y};{m}" for y, m in (src.months if src else ())),
            "gift_factor": f"{src.gift_factor:.6g}" if src else "1.166667",
            "tax_brackets": "\n".join(
                ("" if up == float("inf") else f"{up:g}") + f";{rate * 100:g}"
                for up, rate in (src.tax_brackets if src else ())
            ),
        },
    )


@bp.post("/severance")
def severance_save():
    def cmd():
        f = request.form
        vf = form_date(f, "valid_from")
        months = []
        for i, raw in enumerate((f.get("months") or "").splitlines(), 1):
            line = raw.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(";")]
            if len(parts) != 2:
                raise ValueError(f"Πίνακας μηνών, γραμμή {i}: μορφή «έτη;μήνες»")
            try:
                months.append((int(parts[0]), int(parts[1])))
            except ValueError:
                raise ValueError(
                    f"Πίνακας μηνών, γραμμή {i}: μη έγκυρος ακέραιος"
                ) from None
        svc.define_severance_rules(
            vf,
            months=months,
            gift_factor=form_float(f, "gift_factor", 7 / 6),
            tax_brackets=_parse_lines(f.get("tax_brackets", ""), "Κλιμάκια φόρου"),
        )
        return ("Καταχωρήθηκαν κανόνες αποζημίωσης απόλυσης με ισχύ από "
                f"{vf.strftime('%d/%m/%Y')}")

    ok, msg = run_command(cmd)
    if ok:
        return _panel(msg, ok)
    return _modal_error("partials/_severance_modal.html", msg, edit=False, p=request.form)


def _parse_lines(text, what):
    """Κλιμάκια από γραμμές «όριο;συντελεστής%» (κενό όριο = ανοιχτό)."""
    out = []
    for i, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip().replace(",", ".") for p in line.split(";")]
        if len(parts) != 2:
            raise ValueError(f"{what}, γραμμή {i}: μορφή «όριο;συντελεστής %»")
        try:
            up_to = float(parts[0]) if parts[0] else None
            rate = float(parts[1].replace("%", "")) / 100.0
        except ValueError:
            raise ValueError(f"{what}, γραμμή {i}: μη έγκυρος αριθμός") from None
        out.append({"up_to": up_to, "rate": rate})
    return out


def _parse_numbers(text, what):
    out = []
    for p in (text or "").replace("\n", ";").split(";"):
        p = p.strip().replace(",", ".")
        if not p:
            continue
        try:
            out.append(float(p))
        except ValueError:
            raise ValueError(f"{what}: μη έγκυρος αριθμός «{p}»") from None
    return out


@bp.post("/tax")
def tax_save():
    def cmd():
        f = request.form
        vf = form_date(f, "valid_from")
        model = f.get("model", "standard")
        model_params = _tax_model_params(f, model)
        svc.define_tax_scale(
            vf,
            _parse_lines(f.get("brackets", ""), "Κλιμάκια"),
            reductions=_parse_numbers(f.get("reductions", ""), "Μειώσεις τέκνων")
            or None,
            extra_child_reduction=form_float(f, "extra_child_reduction", 220.0),
            phaseout_threshold=form_float(f, "phaseout_threshold", 12000.0),
            phaseout_per_1000=form_float(f, "phaseout_per_1000", 20.0),
            model=model,
            model_params=model_params,
        )
        return (f"Καταχωρήθηκε φορολογική κλίμακα με ισχύ από "
                f"{vf.strftime('%d/%m/%Y')}")

    ok, msg = run_command(cmd)
    if ok:
        return _panel(msg, ok)
    return _modal_error(
        "partials/_tax_modal.html", msg,
        edit=False, p=_tax_prefill(None), models=config.TAX_MODELS,
        model_forms=_tax_model_forms(),
    )


# --------------------------------------- Τύποι υποχρεώσεων (προθεσμίες)
@bp.get("/obtype/modal")
def obtype_modal():
    code = request.args.get("code", "")
    if code not in ot.TYPES:
        raise ValueError(f"Άγνωστος τύπος «{code}»")
    s = ot.spec(code)
    return render_template(
        "partials/_obtype_modal.html",
        code=code,
        label=ot.TYPES[code].label,
        icon=ot.TYPES[code].icon,
        due=s.due,
        extension=s.extension,
        editable=ot.TYPES[code].due != ("as_source",),
    )


@bp.post("/obtype/<code>")
def obtype_save(code):
    def cmd():
        f = request.form
        due = None
        if ot.TYPES.get(code) and ot.TYPES[code].due != ("as_source",):
            kind = f.get("due_kind", "")
            if kind in ("eom", "eom_cal"):
                due = (kind, form_int(f, "due_k"))
            elif kind == "fixed":
                due = ("fixed", form_int(f, "due_month"), form_int(f, "due_day"))
            elif kind == "easter_minus":
                due = ("easter_minus", form_int(f, "due_k"))
        extension = {"allowed": f.get("ext_allowed") == "1"}
        if extension["allowed"]:
            extension["max_days"] = form_int(f, "ext_max_days", 10)
        svc.configure_obligation_type(code, due=due, extension=extension)
        return (f"Αποθηκεύτηκε η παραμετροποίηση του τύπου "
                f"«{ot.TYPES[code].label}» — ισχύει για ό,τι προγραμματιστεί "
                "από εδώ και πέρα")

    ok, msg = run_command(cmd)
    if ok:
        return _panel(msg, ok)
    s = ot.spec(code) if code in ot.TYPES else None
    return _modal_error(
        "partials/_obtype_modal.html", msg,
        code=code,
        label=ot.TYPES[code].label if code in ot.TYPES else code,
        icon=ot.TYPES[code].icon if code in ot.TYPES else "📌",
        due=s.due if s else ("eom", 0),
        extension=s.extension if s else {"allowed": False},
        editable=code in ot.TYPES and ot.TYPES[code].due != ("as_source",),
    )


# --------------------------------------- Μητρώο ΚΠΚ (ταυτότητα + ποσοστά)
# ΘΕΣΜΙΚΟ, ΟΧΙ ΕΤΑΙΡΙΚΟ: οι κωδικοί πακέτων κάλυψης και τα ποσοστά κρατήσεων
# ορίζονται από ΕΦΚΑ/ΤΕΚΑ και ισχύουν για ΟΛΟΥΣ τους εργοδότες, γι' αυτό τα
# events είναι στα `SHARED_EVENT_TYPES` και το UI ζει εδώ και όχι στη σελίδα
# «Εταιρεία». Εκεί μένουν μόνο οι ΕΙΔΙΚΟΤΗΤΕΣ, που διαβάζουν αυτό το μητρώο.
def _kpk_entry(fund, kpk):
    for r in svc.kpk_entries(on=today()):
        if r["fund"] == fund and r["kpk"] == kpk:
            return r
    return None


def _kpk_ctx(entry=None, form=None):
    return {"entry": entry, "form": form, "config": config,
            "usage": svc.kpk_usage(entry["fund"], entry["kpk"]) if entry else []}


@bp.get("/kpk/modal")
def kpk_modal():
    """Modal ΚΠΚ: ταυτότητα (φορέας/κωδικός/ονομασία/βαθμίδα), ΚΑΔ/ΕΙΔ όπου
    προβλέπεται, και ΧΡΟΝΟΣΕΙΡΑ ποσοστών. Επεξεργασία αν δοθούν ?fund&kpk."""
    entry = None
    if request.args.get("fund"):
        entry = _kpk_entry(request.args.get("fund", ""), request.args.get("kpk", ""))
        if entry is None:
            abort(404)
    return render_template("partials/_kpk_modal.html", **_kpk_ctx(entry))


@bp.post("/kpk/modal/refresh")
def kpk_modal_refresh():
    """Ξαναχτίζει το modal όταν αλλάξει φορέας: η βαθμίδα κλειδώνει για ΤΕΚΑ
    (επιτρέπει μόνο επικουρική). Καθαρό htmx — οι τιμές που έχει ήδη
    πληκτρολογήσει ο χρήστης έρχονται στη φόρμα."""
    fund = request.form.get("fund", "")
    entry = _kpk_entry(fund, request.form.get("kpk", "")) if fund else None
    return render_template("partials/_kpk_modal.html",
                           **_kpk_ctx(entry, request.form))


@bp.post("/kpk")
def kpk_save():
    """Αποθηκεύει ΜΑΖΙ την ταυτότητα και ΟΛΕΣ τις γραμμές της χρονοσειράς
    (κάθε γραμμή = ένα KPKRatesDefined· ίδια ισχύς = αντικατάσταση)."""

    def cmd():
        f = request.form
        kpk = f.get("kpk", "").strip()
        # Πρώτα ΣΥΓΚΕΝΤΡΩΣΗ των γραμμών: η ταυτότητα δεν πρέπει να γραφτεί αν
        # η χρονοσειρά είναι κενή (θα έμενε ΚΠΚ χωρίς ποσοστά).
        # Μόνο τα ΣΥΝΟΛΙΚΑ ποσοστά — η ανάλυση ασφαλιστικών κλάδων έχει
        # καταργηθεί από το UI (η μισθοδοσία/ΑΠΔ δεν τη χρειάζονται).
        rows = [
            (
                form_optional_date(f, f"rate{i}_valid_from"),
                form_float(f, f"rate{i}_employee") / 100.0,
                form_float(f, f"rate{i}_employer") / 100.0,
            )
            for i in range(_KPK_RATE_ROWS)
            if form_optional_date(f, f"rate{i}_valid_from") is not None
        ]
        if not rows:
            raise ValueError("Δώστε τουλάχιστον μία γραμμή ποσοστών (ισχύς από)")
        fund = svc.define_kpk(
            f.get("carrier", ""), kpk, f.get("name", ""), f.get("tier", ""),
        )
        for valid_from, employee, employer in rows:
            svc.define_kpk_rates(fund, kpk, valid_from, employee, employer)
        return f"Αποθηκεύτηκε το ΚΠΚ {kpk} ({len(rows)} γραμμές ισχύος)"

    ok, msg = run_command(cmd)
    if ok:
        return _panel(msg, ok)

    html = render_template("partials/_kpk_modal.html",
                           **_kpk_ctx(form=request.form))
    resp = flash_to(html, msg, err=True)
    resp.headers["HX-Retarget"] = "#modal-host"
    resp.headers["HX-Reswap"] = "innerHTML"
    return resp
