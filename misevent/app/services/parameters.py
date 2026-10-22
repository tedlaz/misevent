"""
Νομοθετικές παράμετροι (μητρώο config.FAMILIES) + τύποι υποχρεώσεων

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

import json
import os
from dataclasses import fields as dc_fields
from datetime import date
from pathlib import Path

import misevent.rules.config as config
import misevent.rules.obligation_types as ot
from misevent.domain.events import (
    EVENT_TYPES,
    MiscSettingsChanged,
    ObligationExtended,
    ObligationTypeConfigured,
    SeveranceRulesDefined,
    SubmissionWithdrawn,
    TaxScaleDefined,
)

from ._base import _decode_import, _parse_import_date

# ΑΡΧΙΚΕΣ ΤΙΜΕΣ ΤΩΝ ΠΑΡΑΜΕΤΡΩΝ — ΑΡΧΕΙΟ, ΟΧΙ ΚΩΔΙΚΑΣ.
# Το πρώτο τρέξιμο (`confirm_default_parameters`) διαβάζει ΑΥΤΟ το αρχείο:
# νέα ΓΣΣΕ/φορολογική κλίμακα στις αρχικές τιμές = αλλαγή ενός JSON, χωρίς
# Python. Έχει την ΙΔΙΑ μορφή με το αρχείο εξαγωγής της σελίδας «Παράμετροι»
# (`export_parameters`), οπότε φτιάχνεται και με το κουμπί «⬇️ Εξαγωγή».
#
# ➜ Αν ΛΕΙΠΕΙ (ή δεν έχει γραμμές για μια οικογένεια), πέφτουμε στις
#   προτεινόμενες τιμές του `config.FAMILIES[...].defaults`, όπως πριν.
# ➜ Αν ΥΠΑΡΧΕΙ αλλά είναι χαλασμένο, ΣΚΑΕΙ με ελληνικό μήνυμα: ένα
#   μισοδιαβασμένο αρχείο αρχικοποίησης είναι λάθος ποσά, όχι «λεπτομέρεια».
INIT_PARAMETERS_FILE = (
    Path(__file__).resolve().parents[3] / "data" / "init_parameters.json"
)


def _init_parameters_path() -> Path:
    """Το αρχείο αρχικών τιμών. Το `MISEVENT_INIT_PARAMETERS` το μετακινεί
    (tests, εγκαταστάσεις με δικές τους τιμές)."""
    override = os.environ.get("MISEVENT_INIT_PARAMETERS")
    return Path(override) if override else INIT_PARAMETERS_FILE


def _json_value(v):
    """JSON-serializable τιμή παραμέτρου: ημερομηνία -> ISO, άπειρο -> `None`
    (το JSON δεν έχει άπειρο — έτσι γράφεται και μέσα στα events), πίνακες ->
    λίστες."""
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v == float("inf"):
        return None
    if isinstance(v, (list, tuple)):
        return [_json_value(x) for x in v]
    return v


class ParametersMixin:
    # --------------------------------------------- Παράμετροι εφαρμογής
    # Όλες οι «νομοθετικές» παράμετροι καταχωρούνται ως events (σελίδα
    # «Παράμετροι» του UI). Οι λίστες του config.py είναι ΜΟΝΟ προτεινόμενες
    # αρχικές τιμές: προσυμπληρώνουν το wizard πρώτου τρεξίματος.
    # Ίδιο (οικογένεια,
    # valid_from) = αντικατάσταση (αντισταθμιστικό event).

    @staticmethod
    def _check_valid_from(valid_from):
        if valid_from is None:
            raise ValueError("Δώστε ημερομηνία έναρξης ισχύος")

    @staticmethod
    def _check_pct(label, v, hi=1.0):
        if not (0.0 <= float(v) < hi):
            raise ValueError(f"Ποσοστό «{label}» εκτός ορίων")

    # =================================================== ΠΑΡΑΜΕΤΡΟΙ (μητρώο)
    def define_parameters(self, family, valid_from, **values):
        """ΜΙΑ ΕΝΤΟΛΗ ΓΙΑ ΚΑΘΕ ΟΙΚΟΓΕΝΕΙΑ ΠΑΡΑΜΕΤΡΩΝ.

        Τα πεδία, οι τύποι, τα όρια, οι διασταυρώσεις και το event έρχονται
        από το μητρώο `config.FAMILIES` — δεν επαναλαμβάνονται εδώ. Νέα
        οικογένεια = μία εγγραφή στο μητρώο + ένα event, ΧΩΡΙΣ νέα μέθοδο.

        Πεδίο που δεν δόθηκε παίρνει την προεπιλογή του event· τα πεδία
        `carry_over` της οικογένειας μεταφέρονται αυτούσια από την εγγραφή
        που ισχύει την `valid_from` (π.χ. τα fallback ποσοστά ΕΦΚΑ)."""
        spec = config.FAMILIES.get(family)
        if spec is None:
            raise ValueError(f"Άγνωστη οικογένεια παραμέτρων «{family}»")
        self._check_valid_from(valid_from)

        known = {f.name for f in spec.fields}
        unknown = set(values) - known
        if unknown:
            raise ValueError("Άγνωστα πεδία: " + ", ".join(sorted(unknown)))

        clean = {}
        for f in spec.fields:
            if f.name in values:
                clean[f.name] = config.check_field_value(f, values[f.name])
        for name in spec.carry_over:
            if name in clean:
                continue
            clean[name] = self._carried_value(spec, name, valid_from)

        if spec.check is not None and (msg := spec.check(clean)):
            raise ValueError(msg)

        self.store.append("COMPANY", EVENT_TYPES[spec.event](valid_from, **clean))

    def repeal_parameters(self, family, valid_from):
        """ΚΑΤΑΡΓΗΣΗ κανόνα από μια ημερομηνία και μετά.

        Σε event sourcing ΔΕΝ υπάρχει διαγραφή (κανόνας #2). Αυτό που λείπει
        πραγματικά είναι το «ο κανόνας έπαψε να ισχύει, χωρίς αντικαταστάτη»:
        γράφεται ως **κανονική εγγραφή με `valid_from` και `repealed=True`**.
        Οι τιμές αντιγράφονται από την εγγραφή που ίσχυε, ώστε το event να
        είναι πλήρες και να διαβάζεται μόνο του στο `/audit`.

        ⚠️ Από εκεί και μετά το `config._pick` **ΣΚΑΕΙ** — δεν υποχωρεί στην
        προηγούμενη γραμμή. Είναι σκόπιμο: ο χρήστης δήλωσε ότι δεν υπάρχει
        κανόνας, και μια μισθοδοσία με παλιό κανόνα θα ήταν λάθος σιωπηλά.

        ➜ «Έγραψα λάθος γραμμή» ΔΕΝ είναι αυτό: εκεί ξαναγράφεις με το ΙΔΙΟ
        `valid_from` (αντικατάσταση, latest-wins)."""
        spec = config.FAMILIES.get(family)
        if spec is None:
            raise ValueError(f"Άγνωστη οικογένεια παραμέτρων «{family}»")
        self._check_valid_from(valid_from)
        entries, _src = config.effective_entries(family)
        applicable = [e for e in entries if e.valid_from <= valid_from]
        cur = max(applicable, key=lambda e: e.valid_from, default=None)
        if cur is None:
            raise ValueError(
                f"Δεν υπάρχει κανόνας «{spec.label}» σε ισχύ την "
                f"{config.gr_date(valid_from)} για να καταργηθεί"
            )
        if getattr(cur, "repealed", False):
            raise ValueError(
                f"Ο κανόνας «{spec.label}» έχει ήδη καταργηθεί από "
                f"{config.gr_date(cur.valid_from)}"
            )
        if spec.ui_mode == "custom":
            # ⚠️ Οι «custom» οικογένειες ΔΕΝ αντιγράφονται πεδίο-πεδίο: κουβαλούν
            # ΠΙΝΑΚΕΣ που έχουν άλλη μορφή στο config απ' ό,τι στο event (π.χ.
            # TaxBracket vs dict). Περνούν από την ΙΔΙΑ διαδρομή με τον wizard.
            self._define_custom(family, cur, valid_from=valid_from, repealed=True)
            return
        ev_cls = EVENT_TYPES[spec.event]
        values = {
            f.name: getattr(cur, f.name)
            for f in dc_fields(ev_cls)
            if f.name not in ("valid_from", "repealed") and hasattr(cur, f.name)
        }
        self.store.append("COMPANY", ev_cls(valid_from, repealed=True, **values))

    def _carried_value(self, spec, name, valid_from):
        """Η τιμή ενός `carry_over` πεδίου από την εγγραφή που ισχύει."""
        entries, _src = config.effective_entries(spec.key)
        applicable = [e for e in entries if e.valid_from <= valid_from]
        cur = max(applicable, key=lambda e: e.valid_from, default=None)
        if cur is None:
            cur = max(entries, key=lambda e: e.valid_from, default=None)
        if cur is None:
            raise ValueError(
                f"Δεν υπάρχει εγγραφή «{spec.label}» για μεταφορά του «{name}»"
            )
        return getattr(cur, name)

    # ---- Λεπτοί wrappers: κρατούν τις ονομαστικές υπογραφές που καλούν
    # ---- τα tests, το demo και το seed. Η λογική ζει στο define_parameters.
    def define_minimum_wage(self, valid_from, monthly, daily):
        """Νέα ΓΣΣΕ/ΚΥΑ: κατώτατος μισθός & ημερομίσθιο με ημερομηνία ισχύος.
        Αν η περίοδος ισχύος έχει ήδη ανοίξει, ο scheduler γεννά αμέσως την
        υποχρέωση προσαρμογής (WAGE_ADJUSTMENT)."""
        self.define_parameters(
            "minimum_wage", valid_from,
            monthly=round(float(monthly), 2), daily=round(float(daily), 2),
        )

    def set_misc_settings(self, **fields):
        """Λειτουργικές προτιμήσεις («Διάφορα» στη σελίδα Παράμετροι) —
        αποθηκεύονται ως event (latest-wins). Δεκτά κλειδιά: όσα ορίζει το
        `projections.MISC_DEFAULTS`, με τον τύπο του default (bool/int)."""
        from misevent.domain.projections import MISC_DEFAULTS

        clean = {k: type(MISC_DEFAULTS[k])(v)
                 for k, v in fields.items() if k in MISC_DEFAULTS}
        if not clean:
            raise ValueError("Δεν δόθηκε καμία έγκυρη προτίμηση")
        if not 0 <= clean.get("obligations_horizon_months", 0) <= 24:
            raise ValueError("Ο ορίζοντας εκκρεμοτήτων πρέπει να είναι 0–24 μήνες")
        self.store.append("MISC", MiscSettingsChanged(clean))

    def misc_settings(self) -> dict:
        """Οι τρέχουσες λειτουργικές προτιμήσεις (defaults + όποιες events)."""
        return dict(self.proj.misc_settings)

    def define_efka_rates(self, valid_from, employee, employer, monthly_cap):
        """Καθολικές παράμετροι ΕΦΚΑ και πλαφόν ασφαλιστέων αποδοχών."""
        self.define_parameters(
            "efka", valid_from,
            employee=employee, employer=employer, monthly_cap=monthly_cap,
        )

    def define_tax_scale(
        self,
        valid_from,
        brackets,
        reductions=None,
        extra_child_reduction=None,
        phaseout_threshold=None,
        phaseout_per_1000=None,
        model="standard",
        model_params=None,
        repealed=False,
    ):
        """Φορολογική κλίμακα ΦΜΥ με ημερομηνία ισχύος.

        `brackets`: [{"up_to": 10000, "rate": 0.09}, ..., {"up_to": None,
        "rate": 0.44}] — αύξοντα όρια, το τελευταίο ανοιχτό (None).
        `model`: καθεστώς υπολογισμού (config.TAX_MODELS) — π.χ.
        "youth2026" = συντελεστές 2ου κλιμακίου ανά τέκνα/ηλικία, με τις
        παραμέτρους του στο `model_params`."""
        self._check_valid_from(valid_from)
        if model not in config.TAX_MODELS:
            known = ", ".join(config.TAX_MODELS)
            raise ValueError(f"Άγνωστο καθεστώς ΦΜΥ «{model}» (γνωστά: {known})")
        clean, prev = [], 0.0
        for i, b in enumerate(brackets or []):
            up_to = b.get("up_to")
            rate = float(b.get("rate", -1))
            if not (0.0 <= rate < 1.0):
                raise ValueError(f"Κλιμάκιο {i + 1}: συντελεστής εκτός ορίων")
            if up_to in (None, ""):
                if i != len(brackets) - 1:
                    raise ValueError(
                        "Μόνο το ΤΕΛΕΥΤΑΙΟ κλιμάκιο είναι ανοιχτό (χωρίς άνω όριο)"
                    )
                clean.append({"up_to": None, "rate": rate})
                continue
            up_to = float(up_to)
            if up_to <= prev:
                raise ValueError("Τα όρια των κλιμακίων πρέπει να είναι αύξοντα")
            prev = up_to
            clean.append({"up_to": up_to, "rate": rate})
        if len(clean) < 2:
            raise ValueError("Δώστε τουλάχιστον δύο κλιμάκια")
        if clean[-1]["up_to"] is not None:
            raise ValueError(
                "Το τελευταίο κλιμάκιο πρέπει να είναι ανοιχτό (χωρίς άνω όριο)"
            )
        defaults = TaxScaleDefined(valid_from, ())
        params = dict(model_params or {})
        allowed = set(config.tax_model_param_fields(model))
        unknown = set(params) - allowed
        if unknown:
            raise ValueError(
                "Άγνωστες παράμετροι καθεστώτος: " + ", ".join(sorted(unknown))
            )
        self.store.append(
            "COMPANY",
            TaxScaleDefined(
                valid_from,
                tuple(clean),
                tuple(reductions) if reductions is not None else defaults.reductions,
                float(extra_child_reduction)
                if extra_child_reduction is not None
                else defaults.extra_child_reduction,
                float(phaseout_threshold)
                if phaseout_threshold is not None
                else defaults.phaseout_threshold,
                float(phaseout_per_1000)
                if phaseout_per_1000 is not None
                else defaults.phaseout_per_1000,
                model,
                params,
                bool(repealed),
            ),
        )

    def define_sick_leave_rules(
        self, valid_from, half_wage_days, limit_under_year, limit_over_year
    ):
        """Κανόνες ασθενείας: τριήμερο εργοδότη + ετήσια όρια 13/26."""
        self.define_parameters(
            "sick_leave", valid_from,
            half_wage_days=half_wage_days,
            limit_under_year=limit_under_year,
            limit_over_year=limit_over_year,
        )

    def define_allowance_rules(
        self,
        valid_from,
        monthly_triennium_pct,
        monthly_max_triennia,
        daily_triennium_pct,
        daily_max_triennia,
        marriage_pct,
    ):
        """Προσαυξήσεις κατώτατου: τριετίες μισθωτών/ημερομισθίων + γάμος."""
        self.define_parameters(
            "allowance", valid_from,
            monthly_triennium_pct=monthly_triennium_pct,
            monthly_max_triennia=monthly_max_triennia,
            daily_triennium_pct=daily_triennium_pct,
            daily_max_triennia=daily_max_triennia,
            marriage_pct=marriage_pct,
        )

    def define_work_hour_rules(self, valid_from, **kw):
        """Όρια & προσαυξήσεις ωρών (νύχτα/αργία/υπερεργασία/υπερωρίες).
        Δεκτά ΜΟΝΟ τα πεδία του μητρώου — ό,τι λείπει παίρνει την προεπιλογή
        του event."""
        self.define_parameters("work_hours", valid_from, **kw)

    def parameter_entries(self, family):
        """Για το UI: οι εγγραφές σε ισχύ μιας οικογένειας + η πηγή τους
        ("events" = μητρώο παραμέτρων, "config" = προτεινόμενες τιμές)."""
        entries, source = config.effective_entries(family)
        return sorted(entries, key=lambda x: x.valid_from), source

    def parameters_confirmed(self) -> bool:
        """Έχει επιβεβαιωθεί το μητρώο παραμέτρων; (wizard πρώτου
        τρεξίματος). Αρκεί οι ΚΡΙΣΙΜΕΣ οικογένειες της μισθοδοσίας να έχουν
        έστω μία εγγραφή από event."""
        return all(self.proj.parameters.get(f) for f in config.CRITICAL_FAMILIES)

    def _define_custom(self, family, entry, valid_from=None, repealed=False):
        """Dispatcher των οικογενειών με `ui_mode="custom"` — αυτών που
        κουβαλούν ΠΙΝΑΚΕΣ και δεν χωρούν στο γενικό μονοπάτι. ΜΙΑ διαδρομή
        για τον wizard και για την κατάργηση."""
        if family == "tax":
            self._define_tax_from_entry(entry, valid_from, repealed)
        elif family == "severance":
            self._define_severance_from_entry(entry, valid_from, repealed)
        else:
            raise ValueError(f"Δεν υπάρχει custom διαδρομή για «{family}»")

    def _define_severance_from_entry(self, entry, valid_from=None, repealed=False):
        self.define_severance_rules(
            valid_from or entry.valid_from,
            months=entry.months,
            gift_factor=entry.gift_factor,
            tax_brackets=[
                {"up_to": (None if up == float("inf") else up), "rate": rate}
                for up, rate in entry.tax_brackets
            ],
            repealed=repealed,
        )

    def define_severance_rules(
        self, valid_from, months, gift_factor, tax_brackets, repealed=False
    ):
        """Αποζημίωση απόλυσης με ημερομηνία ισχύος.

        `months`: ((συμπληρωμένα έτη, μήνες), …) — αύξοντα κατώφλια.
        `tax_brackets`: [{"up_to": 60000, "rate": 0.0}, …] — το ΤΕΛΕΥΤΑΙΟ
        ανοιχτό (`up_to` None), όπως τα κλιμάκια ΦΜΥ."""
        self._check_valid_from(valid_from)

        clean_months = []
        prev_years = prev_mo = -1
        for years, mo in months:
            years, mo = int(years), int(mo)
            if years <= prev_years:
                raise ValueError("Τα έτη υπηρεσίας πρέπει να είναι αύξοντα")
            if mo < prev_mo:
                raise ValueError("Οι μήνες αποζημίωσης δεν μπορούν να μειώνονται")
            if years < 0 or mo < 0:
                raise ValueError("Αρνητικά έτη ή μήνες αποζημίωσης")
            prev_years, prev_mo = years, mo
            clean_months.append((years, mo))
        if not clean_months:
            raise ValueError("Δώστε τουλάχιστον μία γραμμή «έτη → μήνες»")

        if not (0.0 < float(gift_factor) <= 3.0):
            raise ValueError("Η προσαύξηση Δώρων είναι εκτός ορίων")

        clean_tax = []
        prev = 0.0
        for b in tax_brackets:
            up_to = b.get("up_to") if isinstance(b, dict) else b[0]
            rate = float(b.get("rate") if isinstance(b, dict) else b[1])
            if not (0.0 <= rate < 1.0):
                raise ValueError("Συντελεστής φόρου εκτός ορίων (0–100%)")
            if up_to in (None, ""):
                clean_tax.append((float("inf"), rate))
                continue
            up_to = float(up_to)
            if up_to <= prev:
                raise ValueError("Τα όρια των κλιμακίων πρέπει να είναι αύξοντα")
            prev = up_to
            clean_tax.append((up_to, rate))
        if not clean_tax or clean_tax[-1][0] != float("inf"):
            raise ValueError("Το ΤΕΛΕΥΤΑΙΟ κλιμάκιο φόρου πρέπει να είναι ανοιχτό")

        self.store.append(
            "COMPANY",
            SeveranceRulesDefined(
                valid_from,
                tuple(clean_months),
                float(gift_factor),
                # ⚠️ ΣΤΟ EVENT ΓΡΑΦΕΤΑΙ `None`, ΟΧΙ `inf`: το JSON δεν έχει
                # άπειρο και το log πρέπει να διαβάζεται από παντού.
                tuple(
                    (None if up == float("inf") else up, rate)
                    for up, rate in clean_tax
                ),
                bool(repealed),
            ),
        )

    def _define_tax_from_entry(self, entry, valid_from=None, repealed=False):
        """Η κλίμακα ΦΜΥ έχει ΔΙΚΗ ΤΗΣ φόρμα (κλιμάκια, καθεστώς): η ΛΟΓΙΚΗ
        κάθε καθεστώτος είναι κλάση (config.TAX_MODELS), δεν είναι δεδομένο —
        γι' αυτό η οικογένεια είναι `ui_mode="custom"` και δεν περνά από το
        γενικό μονοπάτι. Τη χρησιμοποιούν ο wizard (αντιγραφή defaults) και η
        κατάργηση (ίδια τιμή, νέα ισχύς, `repealed=True`)."""
        model = type(entry).model_key
        self.define_tax_scale(
            valid_from or entry.valid_from,
            [
                {"up_to": (None if b.up_to == float("inf") else b.up_to),
                 "rate": b.rate}
                for b in entry.brackets
            ],
            reductions=entry.reductions,
            extra_child_reduction=entry.extra_child_reduction,
            phaseout_threshold=entry.phaseout_threshold,
            phaseout_per_1000=entry.phaseout_per_1000,
            model=model,
            # οι ΕΠΙΠΛΕΟΝ παράμετροι του καθεστώτος (π.χ. οι συντελεστές
            # ανά τέκνα/ηλικία του "youth2026")
            model_params={
                f: getattr(entry, f) for f in config.tax_model_param_fields(model)
            },
            repealed=repealed,
        )

    def confirm_default_parameters(self) -> int:
        """ΑΡΧΙΚΟΠΟΙΗΣΗ: γράφει ως events τις ΑΡΧΙΚΕΣ ΤΙΜΕΣ για ΟΣΕΣ
        οικογένειες δεν έχουν ακόμη καμία εγγραφή. Από εκεί και πέρα κάθε
        αλλαγή είναι απλή προσθήκη νέας εγγραφής.

        ΠΗΓΗ: το αρχείο **`data/init_parameters.json`** (βλ.
        `INIT_PARAMETERS_FILE`) — μια νέα ΓΣΣΕ ή φορολογική κλίμακα στις
        αρχικές τιμές είναι αλλαγή ΑΡΧΕΙΟΥ, όχι κώδικα. Για όποια οικογένεια
        δεν αναφέρει το αρχείο (ή αν λείπει ολόκληρο), πέφτουμε στις
        προτεινόμενες τιμές του μητρώου `config.FAMILIES[...].defaults`.

        ΤΙ ΓΡΑΦΕΤΑΙ, ΟΛΕΣ ΟΙ ΕΝΟΤΗΤΕΣ ΤΟΥ ΑΡΧΕΙΟΥ:
          • `families` — οικογένειες ΧΩΡΙΣ events (βλ. παραπάνω)·
          • `obligation_types` — μόνο όσοι τύποι ΔΙΑΦΕΡΟΥΝ από τον κώδικα
            (ίδια τιμή = κανένα event)·
          • `kpks` — ΜΟΝΟ όταν το Μητρώο ΚΠΚ είναι ΑΔΕΙΟ: μια αρχικοποίηση
            δεν πατά πάνω σε μητρώο που ήδη συντηρεί ο χρήστης.
        Το `misc` ΔΕΝ γράφεται: οι λειτουργικές προτιμήσεις έχουν ήδη
        προεπιλογές (`MISC_DEFAULTS`) και δεν χρειάζονται event για να ισχύσουν.

        ⚠️ Η ΣΕΙΡΑ είναι αυτή του μητρώου `config.FAMILIES` — μην την αλλάξεις
        αυθαίρετα: καθορίζει τη σειρά των events στο log. Η σειρά ΜΕΣΑ στο
        αρχείο δεν μετράει.

        ⚠️ Λάθος γραμμή στο αρχείο ΣΚΑΕΙ (ValueError): οι προηγούμενες
        οικογένειες έχουν ήδη γραφτεί, αλλά μια δεύτερη κλήση μετά τη
        διόρθωση συνεχίζει από εκεί που έμεινε (τις προσπερνά ως γραμμένες).
        """
        doc = self.init_parameters_doc()
        written = 0
        for family, spec in config.FAMILIES.items():
            if self.proj.parameters.get(family):
                continue  # έχει ήδη events — δεν τα πειράζουμε
            rows = self._family_rows(doc, family) if doc else None
            if rows is not None:
                for row in rows:
                    self._write_family_row(family, row)
                    written += 1
                continue
            for entry in spec.defaults:
                if spec.ui_mode == "custom":
                    self._define_custom(family, entry)
                else:
                    self.define_parameters(
                        family, entry.valid_from,
                        **{f.name: getattr(entry, f.name) for f in spec.fields},
                    )
                written += 1
        if doc is None:
            return written

        for t in doc.get("obligation_types") or ():
            if self._write_obligation_type_row(t):
                written += 1
        if not self.proj.kpk_identity:  # ΑΔΕΙΟ μητρώο ΚΠΚ — μόνο τότε
            for k in doc.get("kpks") or ():
                self.import_kpk_row(k)
                written += 1
        return written

    # ------------------------- Εξαγωγή/εισαγωγή ΟΛΩΝ των παραμέτρων (JSON)
    # ΕΝΑ αρχείο για ό,τι ζει στη σελίδα «Παράμετροι»: οι οικογένειες του
    # μητρώου (ΓΣΣΕ, ΕΦΚΑ, ΤΕΚΑ, ΦΜΥ, ασθένεια, προσαυξήσεις, αποζημίωση,
    # όρια ωρών) με ΟΛΟ το ιστορικό ισχύος τους, οι προθεσμίες/παρατάσεις των
    # τύπων υποχρεώσεων, οι λειτουργικές προτιμήσεις («Διάφορα») και το
    # Μητρώο ΚΠΚ. Ίδιο μοτίβο με το αρχείο της εταιρείας (specialties.py):
    # η ΕΞΑΓΩΓΗ διαβάζει μόνο projections, η ΕΙΣΑΓΩΓΗ περνά από τα ΙΔΙΑ
    # commands — γράφονται κανονικά events, ισχύουν όλοι οι έλεγχοι.
    #
    # ⚠️ Οι παράμετροι είναι ΚΟΙΝΕΣ για όλες τις εταιρείες (shared store):
    # μια εισαγωγή αλλάζει ΟΛΟΚΛΗΡΟ το σύστημα, όχι την ενεργή εταιρεία.
    PARAMETERS_EXPORT_SCHEMA = "misevent.parameters"
    PARAMETERS_EXPORT_VERSION = 1

    def _export_custom(self, family, entry) -> dict:
        """Οι οικογένειες με `ui_mode="custom"` κουβαλούν ΠΙΝΑΚΕΣ — δικό τους
        σχήμα, ακριβώς ό,τι δέχονται τα αντίστοιχα commands."""
        if family == "tax":
            model = type(entry).model_key
            return {
                "model": model,
                "brackets": [
                    {"up_to": _json_value(b.up_to), "rate": b.rate}
                    for b in entry.brackets
                ],
                "reductions": [float(r) for r in entry.reductions],
                "extra_child_reduction": entry.extra_child_reduction,
                "phaseout_threshold": entry.phaseout_threshold,
                "phaseout_per_1000": entry.phaseout_per_1000,
                "model_params": {
                    f: _json_value(getattr(entry, f))
                    for f in config.tax_model_param_fields(model)
                },
            }
        if family == "severance":
            return {
                "months": [[int(y), int(m)] for y, m in entry.months],
                "gift_factor": entry.gift_factor,
                "tax_brackets": [
                    {"up_to": _json_value(up), "rate": rate}
                    for up, rate in entry.tax_brackets
                ],
            }
        raise ValueError(f"Δεν υπάρχει custom διαδρομή για «{family}»")

    def _import_custom(self, family, valid_from, row) -> None:
        """Το αντίστροφο του `_export_custom` — μία γραμμή αρχείου -> event."""
        if family == "tax":
            self.define_tax_scale(
                valid_from,
                row.get("brackets") or [],
                reductions=row.get("reductions") or None,
                extra_child_reduction=row.get("extra_child_reduction"),
                phaseout_threshold=row.get("phaseout_threshold"),
                phaseout_per_1000=row.get("phaseout_per_1000"),
                model=row.get("model", "standard"),
                model_params=row.get("model_params") or {},
            )
        elif family == "severance":
            self.define_severance_rules(
                valid_from,
                months=[tuple(m) for m in row.get("months") or ()],
                gift_factor=row.get("gift_factor", 7 / 6),
                tax_brackets=row.get("tax_brackets") or [],
            )
        else:
            raise ValueError(f"Δεν υπάρχει custom διαδρομή για «{family}»")

    def _write_family_row(self, family, row) -> None:
        """Μία γραμμή αρχείου (αρχικοποίησης ή εξαγωγής) -> event.

        ΜΙΑ διαδρομή για τις δύο εισόδους: το `import_parameters` και το
        πρώτο τρέξιμο. Ό,τι έλεγχος έχει το command ισχύει και στα δύο."""
        spec = config.FAMILIES[family]
        valid_from = _parse_import_date(str(row.get("valid_from", "")))
        if row.get("repealed"):
            # Η κατάργηση αντιγράφει τις τιμές του κανόνα που ισχύει — ίδια
            # διαδρομή με το κουμπί του UI.
            self.repeal_parameters(family, valid_from)
        elif spec.ui_mode == "custom":
            self._import_custom(family, valid_from, row)
        else:
            self.define_parameters(family, valid_from, **(row.get("values") or {}))

    def _write_obligation_type_row(self, t) -> bool:
        """Μία γραμμή «τύπος υποχρέωσης» αρχείου -> event. Επιστρέφει False
        όταν η τιμή είναι ΗΔΗ η ισχύουσα (κανένα event, καθαρό log)."""
        code = t.get("code", "")
        if code not in ot.TYPES:
            raise ValueError("άγνωστος τύπος υποχρέωσης")
        due = tuple(t.get("due") or ())
        extension = dict(t.get("extension") or {})
        # Οι καταβολές Δώρων/Επιδόματος κληρονομούν την προθεσμία της πηγής
        # τους — δεν παραμετροποιούνται.
        if ot.TYPES[code].due == ("as_source",):
            due = ()
        current = ot.spec(code)
        same_due = not due or tuple(current.due) == due
        if same_due and dict(current.extension) == extension:
            return False
        self.configure_obligation_type(code, due=due or None, extension=extension)
        return True

    @staticmethod
    def _family_rows(doc, family) -> list | None:
        """Οι γραμμές μιας οικογένειας μέσα σε αρχείο παραμέτρων, ή None αν
        το αρχείο δεν λέει τίποτα γι' αυτήν."""
        block = (doc.get("families") or {}).get(family)
        if block is None:
            return None
        rows = block.get("entries") if isinstance(block, dict) else block
        return list(rows or []) or None

    def init_parameters_doc(self) -> dict | None:
        """Το αρχείο ΑΡΧΙΚΩΝ ΤΙΜΩΝ (`data/init_parameters.json`), ή None αν
        δεν υπάρχει. Χαλασμένο αρχείο = ValueError, όχι σιωπηλή υποχώρηση."""
        path = _init_parameters_path()
        if not path.exists():
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise ValueError(
                f"Χαλασμένο αρχείο αρχικών τιμών «{path.name}»: {exc}"
            ) from None
        if not isinstance(doc, dict) or not isinstance(doc.get("families"), dict):
            raise ValueError(
                f"Το «{path.name}» δεν είναι αρχείο παραμέτρων "
                "(λείπει το πεδίο «families»)"
            )
        return doc

    def export_parameters(self) -> dict:
        """ΟΛΕΣ οι τιμές της σελίδας «Παράμετροι» ως JSON-serializable dict.

        Εξάγεται ό,τι ΒΛΕΠΕΙ ο χρήστης: αν μια οικογένεια δεν έχει ακόμη
        events, ταξιδεύουν οι προτεινόμενες τιμές του config (το `source` το
        λέει) — η εισαγωγή τις γράφει ως events, όπως ο wizard."""
        families = {}
        for family, spec in config.FAMILIES.items():
            entries, source = config.effective_entries(family)
            rows = []
            for e in sorted(entries, key=lambda x: x.valid_from):
                row = {
                    "valid_from": e.valid_from.isoformat(),
                    "repealed": bool(getattr(e, "repealed", False)),
                }
                if spec.ui_mode == "custom":
                    row.update(self._export_custom(family, e))
                else:
                    # ⚠️ ΜΟΝΟ ΤΑ ΠΕΔΙΑ ΤΗΣ ΦΟΡΜΑΣ (`ui_fields`). Τα `ui=False`
                    # είναι fallback του πυρήνα, όχι τιμές που ρυθμίζει ο
                    # χρήστης (π.χ. τα ποσοστά ΕΦΚΑ: έρχονται από τα ΚΠΚ) — δεν
                    # έχουν θέση σε αρχείο που ο χρήστης διαβάζει και επεξεργάζεται.
                    # Στην εισαγωγή τα συμπληρώνει το `carry_over` του μητρώου.
                    row["values"] = {
                        f.name: _json_value(getattr(e, f.name))
                        for f in spec.ui_fields()
                    }
                rows.append(row)
            families[family] = {"source": source, "entries": rows}

        obligation_types = [
            {
                "code": code,
                "due": list(ot.spec(code).due),
                "extension": dict(ot.spec(code).extension),
                # Πληροφοριακό: τιμή του κώδικα ή δηλωμένη από event;
                "overridden": ot.is_overridden(code),
            }
            for code in ot.TYPES
        ]

        return {
            "schema": self.PARAMETERS_EXPORT_SCHEMA,
            "version": self.PARAMETERS_EXPORT_VERSION,
            "exported_at": date.today().isoformat(),
            "families": families,
            "obligation_types": obligation_types,
            "misc": self.misc_settings(),
            "kpks": self.kpk_export_rows(),
        }

    def export_parameters_bytes(self) -> bytes:
        """Το αρχείο εξαγωγής (UTF-8, αναγνώσιμο ελληνικά)."""
        return json.dumps(
            self.export_parameters(), ensure_ascii=False, indent=2
        ).encode("utf-8")

    def import_parameters(self, data) -> dict:
        """Εισάγει αρχείο εξαγωγής παραμέτρων. Κάθε γραμμή περνά από το
        αντίστοιχο command — ίδια (οικογένεια, ισχύς από) = αντικατάσταση,
        ό,τι δεν υπάρχει στο αρχείο ΜΕΝΕΙ ως έχει (καμία διαγραφή). Οι λάθος
        γραμμές επιστρέφονται ως σφάλματα, δεν χάνονται σιωπηλά.

        ΣΕΙΡΑ: ΚΠΚ -> οικογένειες (χρονολογικά, ώστε οι καταργήσεις να
        βρίσκουν τον κανόνα που καταργούν) -> τύποι υποχρεώσεων ->
        προτιμήσεις."""
        text = _decode_import(data)
        try:
            doc = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Μη έγκυρο αρχείο JSON: {exc}") from None
        if not isinstance(doc, dict):
            raise ValueError("Μη έγκυρο αρχείο εξαγωγής (αναμένεται αντικείμενο JSON)")
        schema = doc.get("schema")
        if schema and schema != self.PARAMETERS_EXPORT_SCHEMA:
            raise ValueError(f"Άγνωστος τύπος αρχείου «{schema}»")

        counts = {"parameters": 0, "obligation_types": 0, "kpks": 0, "misc": 0}
        errors: list[str] = []

        for k in doc.get("kpks") or ():
            kpk = k.get("kpk", "?") if isinstance(k, dict) else "?"
            try:
                self.import_kpk_row(k)
                counts["kpks"] += 1
            except (ValueError, TypeError, AttributeError) as exc:
                errors.append(f"ΚΠΚ {kpk}: {exc}")

        families = doc.get("families") or {}
        if not isinstance(families, dict):
            raise ValueError("Το πεδίο «families» δεν είναι αντικείμενο JSON")
        for family, block in families.items():
            spec = config.FAMILIES.get(family)
            if spec is None:
                errors.append(f"άγνωστη οικογένεια «{family}»")
                continue
            for row in self._family_rows(doc, family) or ():
                raw_vf = str(row.get("valid_from", ""))
                try:
                    self._write_family_row(family, row)
                    counts["parameters"] += 1
                except (ValueError, TypeError, AttributeError) as exc:
                    errors.append(f"{spec.label} ({raw_vf or '—'}): {exc}")

        for t in doc.get("obligation_types") or ():
            code = t.get("code", "?") if isinstance(t, dict) else "?"
            try:
                if self._write_obligation_type_row(t):
                    counts["obligation_types"] += 1
            except (ValueError, TypeError, AttributeError) as exc:
                errors.append(f"τύπος υποχρέωσης {code}: {exc}")

        misc = doc.get("misc")
        if isinstance(misc, dict) and misc:
            try:
                self.set_misc_settings(**misc)
                counts["misc"] = 1
            except (ValueError, TypeError) as exc:
                errors.append(f"προτιμήσεις (Διάφορα): {exc}")

        return {"counts": counts, "errors": errors}

    def _require_parameters(self):
        """Η μισθοδοσία απαιτεί ΕΠΙΒΕΒΑΙΩΜΕΝΟ μητρώο παραμέτρων (events) —
        όπως το μητρώο ΚΠΚ: οι τιμές του config είναι ενδεικτικές και δεν
        χρησιμοποιούνται σιωπηλά σε υπολογισμό πληρωμών."""
        if not self.parameters_confirmed():
            missing = ", ".join(
                config.FAMILIES[f].label
                for f in config.CRITICAL_FAMILIES
                if not self.proj.parameters.get(f)
            )
            raise ValueError(
                f"Δεν έχει επιβεβαιωθεί το μητρώο παραμέτρων ({missing}) — "
                "σελίδα «Παράμετροι», κουμπί «Επιβεβαίωση αρχικών τιμών» ή "
                "χειροκίνητη καταχώρηση"
            )

    # ------------------------------- Παραμετροποίηση τύπων υποχρεώσεων
    def configure_obligation_type(self, code, due=None, extension=None):
        """Αλλάζει τα ΔΕΔΟΜΕΝΑ ενός τύπου υποχρέωσης: κανόνα προθεσμίας
        ή/και πολιτική παράτασης. ΔΕΝ είναι αναδρομικό (οι προγραμματισμένες
        υποχρεώσεις κρατούν την προθεσμία τους)."""
        if code not in ot.TYPES:
            raise ValueError(f"Άγνωστος τύπος υποχρέωσης «{code}»")
        if ot.TYPES[code].due == ("as_source",) and due:
            raise ValueError(
                "Οι καταβολές Δώρων/Επιδόματος κληρονομούν την προθεσμία "
                "της πηγής τους — δεν παραμετροποιούνται"
            )
        if due is not None:
            ot.validate_due_rule(tuple(due))
        ext: dict = {}
        if extension is not None:
            ext = {"allowed": bool(extension.get("allowed"))}
            if ext["allowed"]:
                md = int(extension.get("max_days", 0))
                if md <= 0 or md > 365:
                    raise ValueError("Όριο παράτασης εκτός ορίων (1–365 ημέρες)")
                ext["max_days"] = md
        current = ot.spec(code)
        self.store.append(
            "COMPANY",
            ObligationTypeConfigured(
                code,
                tuple(due) if due is not None else tuple(current.due),
                ext if extension is not None else dict(current.extension),
            ),
        )

    # Ποια καταβολή «κλειδώνει» κάθε υποβολή, και ποια υποβολή εξαρτάται από
    # ποια — ΜΙΑ πηγή για τους ελέγχους της ανάκλησης.
    SUBMISSION_PAYMENT = {
        "APD_SUBMISSION": "EFKA_PAYMENT",
        "APD_TEKA_SUBMISSION": "TEKA_PAYMENT",
        "FMY_SUBMISSION": "FMY_PAYMENT",
    }
    SUBMISSION_FUND = {"APD_SUBMISSION": "efka", "APD_TEKA_SUBMISSION": "teka"}

    def withdraw_submission(self, obligation_type, year, month, reason):
        """ΔΗΛΩΣΗ ΛΑΝΘΑΣΜΕΝΗΣ ΥΠΟΒΟΛΗΣ — η υποβολή παύει να στέκει.

        Το αρχείο ανεβαίνει ΧΕΙΡΟΚΙΝΗΤΑ στον φορέα: η «υποβολή» του συστήματος
        είναι δήλωση του χρήστη, άρα ανακαλείται όταν ο φορέας την απορρίψει.
        Η υποχρέωση ξαναγίνεται εκκρεμής και ξεκλειδώνει το άνοιγμα της
        μισθοδοσίας (`reopen_payroll_period`).

        ⚠️ Η ΚΑΤΑΒΟΛΗ δεν αντιστρέφεται: αν έχει γίνει, η ανάκληση κόβεται."""
        if obligation_type not in self.SUBMISSION_PAYMENT:
            raise ValueError(f"Δεν είναι υποβολή: {obligation_type}")
        if not (reason or "").strip():
            raise ValueError(
                "Η αιτιολογία είναι υποχρεωτική — δηλώνεται γεγονός "
                "(π.χ. «απορρίφθηκε από ΕΦΚΑ», «λάθος αρχείο»)"
            )
        ob = self._obligation(obligation_type, year, month)
        if ob is None or not ob.fulfilled:
            raise ValueError(
                f"Η «{ot.LABELS.get(obligation_type)}» {month}/{year} δεν έχει "
                "υποβληθεί"
            )
        paid = self._obligation(
            self.SUBMISSION_PAYMENT[obligation_type], year, month)
        if paid is not None and paid.fulfilled:
            raise ValueError(
                f"Έχει ήδη γίνει η «{ot.LABELS.get(paid.obligation_type)}» "
                f"{month}/{year} — η καταβολή δεν αντιστρέφεται"
            )
        # Η ΑΠΔ ΤΕΚΑ στηρίζεται στην ΑΠΔ ΕΦΚΑ (μητρώο τύπων): ανακαλείται πρώτη
        if obligation_type == "APD_SUBMISSION":
            teka = self._obligation("APD_TEKA_SUBMISSION", year, month)
            if teka is not None and teka.fulfilled:
                raise ValueError(
                    f"Στέκει η «{ot.LABELS.get('APD_TEKA_SUBMISSION')}» "
                    f"{month}/{year} — ανακάλεσε πρώτα εκείνη"
                )
        self.store.append(
            "COMPANY",
            SubmissionWithdrawn(
                obligation_type, year, month, reason.strip(),
                self.SUBMISSION_FUND.get(obligation_type, ""),
            ),
        )
        return ob.obligation_id

    def extend_obligation(self, obligation_id, new_due_date, reason=""):
        """Παράταση προθεσμίας συγκεκριμένης υποχρέωσης — μόνο αν ο τύπος
        της το επιτρέπει (extension.allowed) και έως max_days από την
        ΑΡΧΙΚΗ προθεσμία.

        Δεύτερο, ΔΗΛΩΤΙΚΟ όριο: `within_period_year` — η παράταση δεν βγαίνει
        ποτέ έξω από το έτος της περιόδου. Το θέλει το Επίδομα Αδείας, που
        αναβάλλεται για να μαθευτεί αν ο εργαζόμενος θα μείνει ως το τέλος
        του έτους: πέρα από την 31/12 η αναβολή δεν έχει νόημα."""
        ob = self.proj.obligations.get(obligation_id)
        if ob is None:
            raise ValueError("Άγνωστη υποχρέωση")
        if ob.fulfilled:
            raise ValueError("Η υποχρέωση έχει ήδη ολοκληρωθεί")
        pol = ot.spec(ob.obligation_type).extension
        if not pol.get("allowed"):
            raise ValueError(
                f"Ο τύπος «{ot.LABELS.get(ob.obligation_type)}» δεν "
                "επιδέχεται παράταση (ρυθμίζεται στη σελίδα Παράμετροι)"
            )
        base = ob.original_due or ob.due_date
        if new_due_date <= ob.due_date:
            raise ValueError("Η νέα προθεσμία πρέπει να είναι μεταγενέστερη")
        max_days = int(pol.get("max_days", 0) or 0)
        if max_days and (new_due_date - base).days > max_days:
            raise ValueError(
                f"Η παράταση ξεπερνά το όριο των {max_days} ημερών από την "
                f"αρχική προθεσμία ({base})"
            )
        if pol.get("within_period_year") and ob.period_year:
            limit = date(ob.period_year, 12, 31)
            if new_due_date > limit:
                raise ValueError(
                    f"Η «{ot.LABELS.get(ob.obligation_type)}» δεν παρατείνεται "
                    f"πέρα από το τέλος του έτους της περιόδου ({limit})"
                )
        self.store.append(
            "COMPANY", ObligationExtended(obligation_id, new_due_date, reason)
        )
