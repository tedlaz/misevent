"""
Μητρώο ΚΠΚ: ταυτότητα + χρονοσειρά ποσοστών κρατήσεων

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

import misevent.rules.config as config
import misevent.rules.constraints as constraints
from misevent.domain.events import KPKDefined, KPKRatesDefined

from ._base import _decode_import, _parse_import_date, _pct4


class KpkMixin:
    # ---------------------------------------- Μητρώο ΚΠΚ (ποσοστά κρατήσεων)
    @staticmethod
    def parse_kpk_components(value) -> tuple:
        """Ανάλυση της συμπαγούς TXT μορφής:
        ``main_pension=6,67/13,33|healthcare=2,55/4,55``.
        Δέχεται επίσης ήδη δομημένη sequence από dicts.
        """
        if not value:
            return ()
        if not isinstance(value, str):
            return tuple(dict(x) for x in value)

        def pct(raw):
            return float(raw.strip().replace("%", "").replace(",", ".")) / 100.0

        out = []
        for item in value.split("|"):
            item = item.strip()
            if not item:
                continue
            try:
                branch, rates = item.split("=", 1)
                emp, er = rates.split("/", 1)
            except ValueError as exc:
                raise ValueError(
                    "Μη έγκυρη ανάλυση ΚΠΚ — αναμένεται "
                    "κλάδος=εργαζ.%/εργοδ.% με διαχωριστικό |"
                ) from exc
            branch = branch.strip().lower()
            if branch not in config.INSURANCE_COMPONENTS:
                known = ", ".join(config.INSURANCE_COMPONENTS)
                raise ValueError(f"Άγνωστος ασφαλιστικός κλάδος «{branch}» ({known})")
            out.append({"branch": branch, "employee": pct(emp), "employer": pct(er)})
        return tuple(out)

    @staticmethod
    def format_kpk_components(components) -> str:
        def p(v):
            return (f"{v * 100:.4f}".rstrip("0").rstrip(".")).replace(".", ",")

        return "|".join(
            f"{c['branch']}={p(float(c['employee']))}/{p(float(c['employer']))}"
            for c in components or ()
        )

    # ------------------------------------------- ΚΠΚ: ταυτότητα (identity)
    def define_kpk(self, carrier, kpk, name, tier):
        """Δηλώνει την ΤΑΥΤΟΤΗΤΑ ενός ΚΠΚ: φορέας (ΕΦΚΑ/ΤΕΚΑ), κωδικός,
        ονομασία και ΒΑΘΜΙΔΑ ασφάλισης (config.TIERS). Το ταμείο (κλειδί
        config.FUNDS, δηλαδή η δρομολόγηση προς ΑΠΔ) είναι ΠΑΡΑΓΩΓΟ του
        ζεύγους φορέας+βαθμίδα — έτσι ο κανόνας «ΤΕΚΑ μόνο επικουρική»
        επιβάλλεται από το μητρώο, χωρίς ειδικό έλεγχο.

        ⚠️ ΚΑΝΕΝΑ ΚΑΔ/ΕΙΔΙΚΟΤΗΤΑ ΕΔΩ. Το ΚΠΚ είναι το πακέτο κάλυψης· η
        τριάδα ΚΑΔ/ειδικότητα/ΚΠΚ της ΑΠΔ (No 31/35/37) εξαρτάται από το
        ΕΠΑΓΓΕΛΜΑ, οπότε συμπληρώνεται στην ΕΙΔΙΚΟΤΗΤΑ — για κάθε ρόλο.

        Ίδιο (ταμείο, ΚΠΚ) = αντικατάσταση (αντισταθμιστικό event)."""
        carrier = str(carrier or "").strip().lower()
        tier = str(tier or "").strip().lower()
        if carrier not in config.CARRIERS:
            raise ValueError(f"Άγνωστος φορέας «{carrier}»")
        if tier not in config.TIERS:
            raise ValueError(f"Άγνωστη βαθμίδα ασφάλισης «{tier}»")
        fund = config.fund_for(carrier, tier)  # ρίχνει ValueError στα ασύμβατα
        kpk = constraints.check_numeric_code(kpk, "ΚΠΚ (ΑΠΔ No 37)", 4)
        name = str(name or "").strip()
        if not name:
            raise ValueError("Δώστε ονομασία ΚΠΚ")
        self.store.append("COMPANY", KPKDefined(fund, kpk, name, tier, carrier))
        return fund

    def kpk_identity(self, fund, kpk):
        """Η ταυτότητα ενός ΚΠΚ, ή None."""
        return self.proj.kpk_identity.get((fund, kpk))

    def require_kpk_identity(self, fund, kpk):
        ident = self.kpk_identity(fund, kpk)
        if ident is None:
            raise ValueError(
                f"Άγνωστο ΚΠΚ {kpk} — δηλώστε πρώτα την ταυτότητά του "
                "(ονομασία, βαθμίδα) στο Μητρώο ΚΠΚ"
            )
        return ident

    def kpks_by_tier(self, tier):
        """Οι ταυτότητες ΚΠΚ μιας βαθμίδας — τα dropdowns των ειδικοτήτων
        δείχνουν ΜΟΝΟ ΚΠΚ της βαθμίδας που δέχεται ο ρόλος."""
        return sorted(
            (i for i in self.proj.kpk_identity.values() if i.tier == tier),
            key=lambda i: (i.carrier, i.kpk),
        )

    def define_kpk_rates(
        self, fund, kpk, valid_from, employee, employer, components=()
    ):
        """Δηλώνει/διορθώνει ΜΙΑ ΓΡΑΜΜΗ της χρονοσειράς ποσοστών ενός ΚΠΚ
        (ΚΛΑΣΜΑΤΑ, π.χ. 0.1337). Κάθε γραμμή ισχύει από την `valid_from` μέχρι
        να αρχίσει η επόμενη· ίδιο (ταμείο, ΚΠΚ, ισχύς από) = αντικατάσταση
        της προηγούμενης δήλωσης (αντισταθμιστικό event).

        Απαιτεί δηλωμένη ΤΑΥΤΟΤΗΤΑ ΚΠΚ (`define_kpk`) — ποσοστά χωρίς
        ταυτότητα δεν μπορούν να μπουν σε πακέτο ειδικότητας.

        ➜ Η **ανάλυση ασφαλιστικών κλάδων (`components`) είναι ΠΡΟΑΙΡΕΤΙΚΗ**:
        η μισθοδοσία/ΑΠΔ χρειάζονται μόνο τα συνολικά ποσοστά. Όταν δίνεται,
        επικυρώνεται (κανένας διπλός κλάδος, αθροίσματα = σύνολα) και
        ενεργοποιεί τον έλεγχο διπλοχρέωσης μεταξύ πακέτων
        (`_validate_package_components`). Όταν λείπει, ο έλεγχος αυτός απλώς
        δεν εφαρμόζεται — δεν σκάει."""
        if fund not in config.FUNDS:
            raise ValueError(f"Άγνωστο ταμείο «{fund}»")
        # Το ΚΠΚ γράφεται ΩΣ ΑΡΙΘΜΟΣ στην ΑΠΔ (No 37) — βλ. check_numeric_code
        kpk = constraints.check_numeric_code(kpk, "ΚΠΚ (ΑΠΔ No 37)", 4)
        self.require_kpk_identity(fund, kpk)
        if valid_from is None:
            raise ValueError("Δώστε ημερομηνία έναρξης ισχύος")
        for label, v in (("εργαζομένου", employee), ("εργοδότη", employer)):
            if not (0.0 <= v < 1.0):
                raise ValueError(f"Ποσοστό {label} εκτός ορίων (0 έως <100%)")
        components = self.parse_kpk_components(components)
        seen = set()
        for c in components:
            branch = str(c.get("branch", "")).strip().lower()
            if branch in seen:
                raise ValueError(f"Διπλός κλάδος «{branch}» στην ανάλυση ΚΠΚ")
            seen.add(branch)
            for key, label in (("employee", "εργαζομένου"), ("employer", "εργοδότη")):
                value = float(c.get(key, -1))
                if not (0.0 <= value < 1.0):
                    raise ValueError(f"Ποσοστό {label} κλάδου «{branch}» εκτός ορίων")
        if components:
            ce = sum(float(c["employee"]) for c in components)
            cr = sum(float(c["employer"]) for c in components)
            if abs(ce - employee) > 0.0000001 or abs(cr - employer) > 0.0000001:
                raise ValueError(
                    "Τα αθροίσματα της ανάλυσης ΚΠΚ "
                    f"({ce * 100:.2f}% / {cr * 100:.2f}%) δεν συμφωνούν με "
                    f"τα συνολικά ({employee * 100:.2f}% / {employer * 100:.2f}%)"
                )
        self.store.append(
            "COMPANY",
            KPKRatesDefined(fund, kpk, valid_from, employee, employer, components),
        )

    def import_kpk_rates(self, data) -> dict:
        """Μαζική εισαγωγή ΚΠΚ (ταυτότητα + χρονοσειρά ποσοστών) από CSV.

        Μορφή γραμμής (διαχωριστικό «;» ή TAB, προαιρετική επικεφαλίδα):
            φορέας;ΚΠΚ;ονομασία;βαθμίδα;ισχύς από;εργαζ.%;εργοδ.%;ανάλυση
            ΕΦΚΑ;0101;ΙΚΑ-ΤΕΑΜ ΜΙΚΤΑ;main_auxiliary;2025-01-01;13,37;21,79;...
            ΤΕΚΑ;4169;ΤΕΚΑ Επικουρική;auxiliary;2022-06-01;3;3;...

        Φορέας: ΕΦΚΑ/ΤΕΚΑ (ή efka/teka). Βαθμίδα: κλειδί config.TIERS ή η
        ελληνική ονομασία του. ΚΑΝΕΝΑ ΚΑΔ/ΕΙΔ — ζουν στην ειδικότητα.
        ΠΟΛΛΕΣ ΓΡΑΜΜΕΣ ΤΟΥ ΙΔΙΟΥ ΚΠΚ = χρονοσειρά (μία ανά ημερομηνία
        ισχύος)· η ταυτότητα δηλώνεται από την πρώτη τους.
        Ημερομηνία: 2025-01-01 ή 01/01/2025. Δεκαδικά με «,» ή «.».
        Οι λάθος γραμμές επιστρέφονται ως σφάλματα (δεν χάνονται σιωπηλά)."""
        text = _decode_import(data)

        def pct(s):
            return float(s.replace("%", "").replace(",", ".").strip()) / 100.0

        carriers = {
            **{k: k for k in config.CARRIERS},
            **{v.upper(): k for k, v in config.CARRIERS.items()},
        }
        tiers = {
            **{k: k for k in config.TIERS},
            **{v.upper(): k for k, v in config.TIERS.items()},
        }

        imported, errors, declared = 0, [], set()
        for i, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.replace("\t", ";").split(";")]
            carrier = carriers.get(parts[0].upper(), carriers.get(parts[0].lower()))
            if carrier is None:
                if i == 1:
                    continue  # γραμμή επικεφαλίδων
                errors.append(f"γραμμή {i}: άγνωστος φορέας «{parts[0]}»")
                continue
            if len(parts) < 8:
                errors.append(
                    f"γραμμή {i}: αναμένονται 8 πεδία (φορέας;ΚΠΚ;ονομασία;"
                    "βαθμίδα;ισχύς από;εργαζ.%;εργοδ.%;ανάλυση)"
                )
                continue
            tier = tiers.get(parts[3].upper(), tiers.get(parts[3].lower()))
            if tier is None:
                errors.append(f"γραμμή {i}: άγνωστη βαθμίδα «{parts[3]}»")
                continue
            try:
                vf = _parse_import_date(parts[4])
                if (carrier, parts[1]) not in declared:
                    fund = self.define_kpk(carrier, parts[1], parts[2], tier)
                    declared.add((carrier, parts[1]))
                else:
                    fund = config.fund_for(carrier, tier)
                self.define_kpk_rates(
                    fund,
                    parts[1],
                    vf,
                    pct(parts[5]),
                    pct(parts[6]),
                    parts[7],
                )
                imported += 1
            except (ValueError, IndexError) as exc:
                errors.append(f"γραμμή {i}: {exc}")
        if imported == 0 and not errors:
            raise ValueError("Κενό αρχείο — καμία γραμμή ΚΠΚ")
        return {"imported": imported, "errors": errors}

    def _kpk_registry(self):
        """Το μητρώο ΚΠΚ — ΜΟΝΟ από events (KPKRatesDefined): χειροκίνητες
        δηλώσεις στο UI ή εισαγωγή από αρχείο. Καμία προεπιλογή στο config —
        ό,τι δεν έχει καταγραφεί ως event δεν υπάρχει."""
        return {
            (ev.fund, ev.kpk, ev.valid_from): (
                ev.employee,
                ev.employer,
                tuple(ev.components),
            )
            for ev in self.proj.kpk_rates.values()
        }

    def kpk_rates_for(self, fund, kpk, on):
        """Τα ποσοστά του ΚΠΚ που ίσχυαν την `on` (από το μητρώο events)."""
        hits = [
            (vf, emp, er, components)
            for (f, k, vf), (emp, er, components) in self._kpk_registry().items()
            if f == fund and k == kpk and vf <= on
        ]
        if not hits:
            raise ValueError(
                f"Άγνωστο ΚΠΚ {kpk} για ταμείο "
                f"«{config.FUNDS[fund].label}» — καταχωρήστε τα ποσοστά του "
                "στο Μητρώο ΚΠΚ (σελίδα Παράμετροι, χειροκίνητα ή από αρχείο)"
            )
        vf, emp, er, components = max(hits, key=lambda x: x[0])
        return config.KPKRates(vf, fund, kpk, emp, er, components)

    def known_kpks(self, fund):
        """Τα δηλωμένα ΚΠΚ ενός ταμείου."""
        return sorted({k for (f, k, _vf) in self._kpk_registry() if f == fund})

    def kpk_rate_series(self, fund, kpk):
        """Η ΧΡΟΝΟΣΕΙΡΑ ποσοστών ενός ΚΠΚ, χρονολογικά."""
        rows = [
            {
                "valid_from": vf,
                "employee": emp,
                "employer": er,
                "components": components,
                "components_text": self.format_kpk_components(components),
            }
            for (f, k, vf), (emp, er, components) in self._kpk_registry().items()
            if f == fund and k == kpk
        ]
        rows.sort(key=lambda r: r["valid_from"])
        return rows

    def kpk_entries(self, on=None):
        """Για το UI: ΜΙΑ γραμμή ΑΝΑ ΚΠΚ (ταυτότητα + χρονοσειρά ποσοστών).

        `current` = η γραμμή ποσοστών που ισχύει την `on` (ή η τελευταία που
        έχει αρχίσει) — None όσο δεν έχει αρχίσει καμία."""
        order = list(config.FUNDS)
        rows = []
        for (fund, kpk), ident in self.proj.kpk_identity.items():
            series = self.kpk_rate_series(fund, kpk)
            started = [r for r in series if on is None or r["valid_from"] <= on]
            rows.append(
                {
                    "fund": fund,
                    "label": config.FUNDS[fund].label if fund in config.FUNDS else fund,
                    "kpk": kpk,
                    "name": ident.name,
                    "tier": ident.tier,
                    "tier_label": config.TIERS.get(ident.tier, ident.tier),
                    "carrier": ident.carrier,
                    "carrier_label": config.CARRIERS.get(ident.carrier, ident.carrier),
                    "series": series,
                    "current": started[-1] if started else None,
                }
            )
        rows.sort(
            key=lambda r: (
                order.index(r["fund"]) if r["fund"] in order else 99,
                r["kpk"],
            )
        )
        return rows

    # ------------------------------------- ΚΠΚ σε/από αρχείο εξαγωγής JSON
    # ΜΙΑ πηγή αλήθειας για το σχήμα των ΚΠΚ μέσα στα αρχεία εξαγωγής: το
    # μητρώο ταξιδεύει ΚΑΙ στο αρχείο της εταιρείας (specialties.py) ΚΑΙ σε
    # αυτό των παραμέτρων (parameters.py) — ίδια μορφή, ένα σημείο αλλαγής.
    def kpk_export_rows(self, with_components=False) -> list:
        """Το Μητρώο ΚΠΚ ως JSON-serializable λίστα (ταυτότητα + χρονοσειρά).

        Η ΑΝΑΛΥΣΗ ΚΛΑΔΩΝ ΔΕΝ ΕΞΑΓΕΤΑΙ (`with_components=False`): η
        μισθοδοσία/ΑΠΔ χρειάζονται μόνο τα συνολικά ποσοστά και το αρχείο του
        χρήστη μένει αναγνώσιμο. Το αρχείο ΑΡΧΙΚΟΠΟΙΗΣΗΣ τη θέλει
        (`with_components=True`): ενεργοποιεί τον έλεγχο διπλοχρέωσης μεταξύ
        πακέτων ειδικότητας (`_validate_package_components`)."""
        return [
            {
                "carrier": row["carrier"],
                "kpk": row["kpk"],
                "name": row["name"],
                "tier": row["tier"],
                "rates": [
                    {
                        "valid_from": r["valid_from"].isoformat(),
                        # Στρογγυλοποίηση στα 4 δεκαδικά (= 2 δεκαδικά επί τοις
                        # εκατό, όση ακρίβεια έχουν και οι πηγές)· αλλιώς το
                        # float θα έγραφε 0.13369999999999999.
                        "employee": _pct4(r["employee"]),
                        "employer": _pct4(r["employer"]),
                        **(
                            {"components": [
                                {"branch": c["branch"],
                                 "employee": _pct4(c["employee"]),
                                 "employer": _pct4(c["employer"])}
                                for c in r["components"]
                            ]}
                            if with_components and r["components"] else {}
                        ),
                    }
                    for r in row["series"]
                ],
            }
            for row in self.kpk_entries()
        ]

    def import_kpk_row(self, k) -> None:
        """Μία εγγραφή ΚΠΚ αρχείου εξαγωγής -> events (ταυτότητα + γραμμές).
        Περνά από τα ΚΑΝΟΝΙΚΑ commands, άρα ισχύουν όλοι οι έλεγχοι."""
        fund = self.define_kpk(
            k.get("carrier", ""), k.get("kpk", ""), k.get("name", ""),
            k.get("tier", ""),
        )
        rates = k.get("rates") or ()
        if not rates:
            raise ValueError("καμία γραμμή ποσοστών")
        for r in rates:
            self.define_kpk_rates(
                fund,
                k.get("kpk", ""),
                _parse_import_date(str(r.get("valid_from", ""))),
                float(r.get("employee", 0.0)),
                float(r.get("employer", 0.0)),
                # Ανοχή σε αρχεία που κουβαλούν ακόμη ανάλυση κλάδων (δεν την
                # παράγουμε πια, αλλά δεν τη ρίχνουμε).
                list(r.get("components") or ()),
            )

    def kpk_usage(self, fund, kpk):
        """Οι ειδικότητες που χρησιμοποιούν ένα ΚΠΚ (έλεγχος πριν αλλαγές)."""
        return sorted(
            sp.name
            for sp in self.proj.specialties.values()
            if any(c.get("fund") == fund and c.get("kpk") == kpk for c in sp.coverages)
        )
