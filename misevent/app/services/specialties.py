"""
Ειδικότητες, ασφαλιστικά πακέτα/καλύψεις, εξαγωγή-εισαγωγή εταιρείας

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

import json
from datetime import date

import misevent.integrations.ergani_parameters as ergani_parameters
import misevent.integrations.kpk_registry as kpk_registry
import misevent.rules.config as config
import misevent.rules.constraints as constraints
from misevent.domain.events import CompanySpecialtyDefined, InsuranceProfileAssigned
from misevent.domain.projections import profile_coverages

from ._base import _decode_import


class SpecialtiesMixin:
    # ------------------------------------- Ειδικότητες (θέσεις εργασίας)
    def define_specialty(
        self,
        name,
        packages,
        ergani_specialty_code="",
        ergani_specialty_description="",
        epik_carriers=None,
        pron=None,
    ):
        """Δηλώνει ειδικότητα (θέση εργασίας) με τον ΑΣΦΑΛΙΣΤΙΚΟ ΣΥΝΔΥΑΣΜΟ της.

        `packages`: dict ΡΟΛΟΣ -> {"kpk", "kad", "specialty_code"} (ή sequence
        dicts με κλειδί "role"). Οι ρόλοι είναι του config.SPECIALTY_ROLES:
        MAIN_EPIK (επικουρική στον ΕΦΚΑ), MAIN+TEKA (επικουρική στο ΤΕΚΑ),
        PRON_NEW/PRON_OLD ή PRON_UNIFORM (πρόνοια).

        ➜ Τα ΚΑΔ/ειδικότητα δίνονται σε ΚΑΘΕ ρόλο, χωρίς εξαιρέσεις: η τριάδα
        ΚΑΔ/ειδικότητα/ΚΠΚ της ΑΠΔ (No 31/35/37) εξαρτάται από το επάγγελμα
        και τη δραστηριότητα, όχι από το πακέτο κάλυψης. Το ΚΠΚ δεν κουβαλά
        κωδικούς.

        `epik_carriers`: επιτρεπτοί φορείς επικουρικής· κενό = παράγονται από
        τους ρόλους που δηλώθηκαν. `pron`: ρυθμίσεις πρόνοιας
        (config.PRON_DEFAULTS) — κενό = παράγονται από τους ρόλους πρόνοιας.
        Ίδιο όνομα = αντικατάσταση."""
        name = constraints.norm_name(name)
        if not name:
            raise ValueError("Δώστε ονομασία ειδικότητας (θέσης εργασίας)")

        ergani_code = str(ergani_specialty_code or "").strip()
        ergani_description = str(ergani_specialty_description or "").strip()
        if bool(ergani_code) != bool(ergani_description):
            raise ValueError(
                "Επιλέξτε κωδικό και περιγραφή ειδικότητας ΕΡΓΑΝΗ από την αναζήτηση"
            )
        if ergani_code:
            registry_row = ergani_parameters.specialty(ergani_code)
            if (
                registry_row is None
                or registry_row["description"] != ergani_description
            ):
                raise ValueError(
                    "Η ειδικότητα ΕΡΓΑΝΗ δεν αντιστοιχεί στο μητρώο — "
                    "επιλέξτε την ξανά από τα αποτελέσματα"
                )
            ergani_description = registry_row["description"]

        # --- Κανονικοποίηση εισόδου σε {ρόλος: πακέτο} -------------------
        if isinstance(packages, dict):
            given = {str(r).upper(): dict(p or {}) for r, p in packages.items()}
        else:
            given = {}
            for p in packages or ():
                p = dict(p or {})
                role = str(p.pop("role", "")).upper()
                if not role:
                    raise ValueError("Κάθε πακέτο ειδικότητας απαιτεί ΡΟΛΟ")
                given[role] = p
        # Κενά πακέτα (χωρίς ΚΠΚ) αγνοούνται — ο χρήστης απλώς δεν τα δήλωσε.
        given = {r: p for r, p in given.items() if str(p.get("kpk") or "").strip()}
        for role in given:
            if role not in config.SPECIALTY_ROLES:
                raise ValueError(f"Άγνωστος ρόλος πακέτου «{role}»")

        pron = config.normalize_pron(pron) if pron is not None else None
        if pron is None:
            pron_roles = [
                r for r in given if config.SPECIALTY_ROLES[r].tier == "welfare"
            ]
            pron = config.normalize_pron(
                {
                    "enabled": bool(pron_roles),
                    "differentiated": any(r != "PRON_UNIFORM" for r in pron_roles),
                    "name": next(
                        (
                            given[r].get("label", "")
                            for r in pron_roles
                            if given[r].get("label")
                        ),
                        "",
                    ),
                }
            )
        if epik_carriers is None:
            epik_carriers = [
                c
                for c, roles in config.CARRIER_ROLES.items()
                if all(r in given for r in roles)
            ]
        carriers = tuple(
            c for c in config.CARRIER_ROLES if c in set(epik_carriers or ())
        )
        if not carriers:
            raise ValueError(
                "Δηλώστε τουλάχιστον έναν φορέα επικουρικής (ΕΦΚΑ ή ΤΕΚΑ) "
                "με τα πακέτα του"
            )

        # Το ΤΕΚΑ έχει σταθερό πακέτο: ό,τι κι αν δόθηκε, μπαίνει αυτό.
        if "teka" in carriers:
            given["TEKA"] = dict(config.TEKA_PACKAGE)

        # --- Ποιοι ρόλοι ΠΡΕΠΕΙ να υπάρχουν ------------------------------
        required = [r for c in carriers for r in config.CARRIER_ROLES[c]]
        if pron["enabled"]:
            required += (
                ["PRON_NEW", "PRON_OLD"] if pron["differentiated"] else ["PRON_UNIFORM"]
            )
        missing = [r for r in required if r not in given]
        if missing:
            raise ValueError(
                "Λείπουν πακέτα για τους ρόλους: "
                + ", ".join(config.SPECIALTY_ROLES[r].label for r in missing)
            )
        extra = [r for r in given if r not in required]
        if extra:
            raise ValueError(
                "Δηλώθηκαν πακέτα που δεν αντιστοιχούν στον συνδυασμό: "
                + ", ".join(config.SPECIALTY_ROLES[r].label for r in extra)
            )

        clean = []
        for role in required:
            clean.append(self._build_package(role, given[role], pron))

        self.store.append(
            "COMPANY",
            CompanySpecialtyDefined(
                name,
                tuple(clean),
                ergani_code,
                ergani_description,
                carriers,
                pron,
            ),
        )
        return name

    def _build_package(self, role, given, pron):
        """Ένα πακέτο ειδικότητας: επιλύει ΚΠΚ/ΚΑΔ/ειδικότητα και ΠΑΡΑΓΕΙ τη
        συνθήκη επιλογής από τον ρόλο (δεν γράφεται πια στο χέρι)."""
        spec_role = config.SPECIALTY_ROLES[role]
        fund = spec_role.fund
        kpk = str(given.get("kpk") or "").strip()
        label = config.role_label(role, pron.get("name", ""))
        ident = self.require_kpk_identity(fund, kpk)
        if ident.tier != spec_role.tier:
            raise ValueError(
                f"«{label}»: το ΚΠΚ {kpk} είναι βαθμίδας "
                f"«{config.TIERS[ident.tier]}», ενώ ο ρόλος απαιτεί "
                f"«{config.TIERS[spec_role.tier]}»"
            )
        kad = str(given.get("kad") or "").strip()
        code = str(given.get("specialty_code") or "").strip()
        if not (kad and code):
            raise ValueError(f"«{label}»: συμπληρώστε ΚΑΔ και κωδικό ειδικότητας")
        # Γράφονται ΩΣ ΑΡΙΘΜΟΙ στην ΑΠΔ (No 31/35/37) — κείμενο εδώ θα
        # έσκαγε αργότερα, κατά την παραγωγή του αρχείου.
        constraints.check_coverage_codes(kad, code, kpk, label)
        # Κύρια/πρόνοια e-ΕΦΚΑ: μόνο συνδυασμοί που ισχύουν στον Ο.Σ.Υ.Κ.
        # (`kpk_registry.FUNDS`). Το ΤΕΚΑ έχει σταθερό πακέτο (config.TEKA_PACKAGE).
        if fund in kpk_registry.FUNDS and not kpk_registry.combination_kpks(kad, code):
            raise ValueError(
                f"«{label}»: ο συνδυασμός ΚΑΔ {kad} – ειδικότητα {code} δεν "
                "ισχύει στο μητρώο ΚΑΔ/Ειδικοτήτων/ΚΠΚ του e-ΕΦΚΑ"
            )
        # Το ΚΠΚ πρέπει να έχει και ΠΟΣΟΣΤΑ — αλλιώς η μισθοδοσία δεν
        # μπορεί να υπολογίσει κρατήσεις.
        if kpk not in self.known_kpks(fund):
            raise ValueError(
                f"«{label}»: το ΚΠΚ {kpk} δεν έχει καταχωρημένα ποσοστά — "
                "προσθέστε γραμμή ισχύος στο Μητρώο ΚΠΚ (σελίδα Παράμετροι)"
            )
        package = {
            "role": role,
            "fund": fund,
            "label": label,
            "kad": kad,
            "specialty_code": code,
            "kpk": kpk,
            "when": config.role_when(role),
        }
        # ΑΠΔ No 36 της ΚΥΡΙΑΣ γραμμής: ΠΑΡΑΓΩΓΟ του φορέα επικουρικής του
        # ρόλου και του συνδυασμού (Ο.Σ.Υ.Κ.) — ΤΕΚΑ 09, ΕΦΚΑ 10/00. Χωρίς την
        # πρόνοια: αυτή εξαρτάται από τον εργαζόμενο και την προσθέτει η ΑΠΔ.
        if spec_role.tier in ("main", "main_auxiliary"):
            package["special_case"] = kpk_registry.main_special_case(
                config.role_when(role).get("auxiliary", ""), kad, code
            )
        return package

    def import_specialties(self, data) -> dict:
        """Μαζική εισαγωγή ειδικοτήτων (θέσεων εργασίας) από αρχείο CSV.

        ΜΙΑ ΓΡΑΜΜΗ ΑΝΑ ΠΑΚΕΤΟ (οι γραμμές της ίδιας ειδικότητας
        ομαδοποιούνται κατά όνομα)· διαχωριστικό «;» ή TAB, προαιρετική
        επικεφαλίδα:
            ειδικότητα;ρόλος;ΚΑΔ;κωδ. ειδικότητας;ΚΠΚ;ονομασία πρόνοιας;προαιρετική
            ΣΕΡΒΙΤΟΡΟΣ;MAIN_EPIK;5510;513100;0101
            ΣΕΡΒΙΤΟΡΟΣ;MAIN;5510;513100;0103
            ΣΕΡΒΙΤΟΡΟΣ;TEKA;0083;000860;4169
            ΣΕΡΒΙΤΟΡΟΣ;PRON_NEW;0013;000106;0024;Πρόνοια Εμπόρων
            ΣΕΡΒΙΤΟΡΟΣ;PRON_OLD;0013;000108;0026;Πρόνοια Εμπόρων

        Ρόλος: κλειδί του config.SPECIALTY_ROLES. Τα ΚΑΔ/κωδ. ειδικότητας
        είναι ΥΠΟΧΡΕΩΤΙΚΑ σε ΚΑΘΕ ρόλο — η τριάδα της ΑΠΔ (No 31/35/37)
        εξαρτάται από το επάγγελμα, όχι από το πακέτο κάλυψης. Η «ονομασία
        πρόνοιας» (6ο πεδίο) και το «προαιρετική» (7ο· 1/ναι/yes) διαβάζονται
        από τις γραμμές πρόνοιας.

        ➜ Οι επιτρεπτοί φορείς επικουρικής και οι ρυθμίσεις πρόνοιας
        ΠΑΡΑΓΟΝΤΑΙ από τους ρόλους που δηλώθηκαν (MAIN+TEKA -> επιτρέπεται
        ΤΕΚΑ· PRON_NEW+PRON_OLD -> πρόνοια διαφοροποιημένη).

        Κάθε ειδικότητα γίνεται ΕΝΑ event CompanySpecialtyDefined (ίδιο όνομα
        αντικαθιστά την προηγούμενη δήλωση)· τα ΚΠΚ πρέπει να είναι ήδη
        δηλωμένα στο Μητρώο ΚΠΚ. Οι λάθος γραμμές/ειδικότητες επιστρέφονται
        ως σφάλματα — δεν χάνονται σιωπηλά."""
        text = _decode_import(data)

        groups: dict[str, list] = {}
        pron_by_name: dict[str, dict] = {}
        errors = []
        for i, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.replace("\t", ";").split(";")]
            if len(parts) < 5:
                errors.append(
                    f"γραμμή {i}: αναμένονται 5+ πεδία (ειδικότητα;ρόλος;"
                    "ΚΑΔ;κωδ. ειδικότητας;ΚΠΚ[;ονομασία πρόνοιας;προαιρετική])"
                )
                continue
            name, role = parts[0], parts[1].upper()
            if role not in config.SPECIALTY_ROLES:
                if i == 1:
                    continue  # γραμμή επικεφαλίδων
                errors.append(f"γραμμή {i}: άγνωστος ρόλος «{parts[1]}»")
                continue
            groups.setdefault(name, []).append(
                {
                    "role": role,
                    "kad": parts[2],
                    "specialty_code": parts[3],
                    "kpk": parts[4],
                }
            )
            if config.SPECIALTY_ROLES[role].tier == "welfare":
                pron = pron_by_name.setdefault(
                    name, {"enabled": True, "differentiated": False}
                )
                if len(parts) > 5 and parts[5]:
                    pron["name"] = parts[5]
                if len(parts) > 6:
                    pron["mandatory"] = parts[6].strip().lower() not in {
                        "1",
                        "ναι",
                        "yes",
                        "true",
                        "προαιρετική",
                    }
                if role != "PRON_UNIFORM":
                    pron["differentiated"] = True

        imported = 0
        for name, packages in groups.items():
            try:
                self.define_specialty(name, packages, pron=pron_by_name.get(name))
                imported += 1
            except ValueError as exc:
                errors.append(f"ειδικότητα «{name}»: {exc}")
        if imported == 0 and not errors:
            raise ValueError("Κενό αρχείο — καμία γραμμή ειδικότητας")
        return {"imported": imported, "errors": errors}

    # ------------------------- Εξαγωγή/εισαγωγή ΟΛΩΝ των στοιχείων εταιρείας
    # ΕΝΑ αρχείο JSON για ό,τι ζει στη σελίδα «Εταιρεία»: στοιχεία εργοδότη,
    # παραρτήματα, Μητρώο ΚΠΚ (ταυτότητα + χρονοσειρά ποσοστών) και
    # ειδικότητες. Η εξαγωγή διαβάζει ΜΟΝΟ projections· η εισαγωγή περνά από
    # τα ΙΔΙΑ commands (register_company/define_branch/define_kpk/
    # define_kpk_rates/define_specialty), άρα ισχύουν όλοι οι έλεγχοι και
    # γράφονται κανονικά events — καμία απευθείας εγγραφή σε πίνακα.
    COMPANY_EXPORT_SCHEMA = "misevent.company"
    COMPANY_EXPORT_VERSION = 1

    def export_company(self) -> dict:
        """Όλα τα στοιχεία της εταιρείας ως JSON-serializable dict."""
        c = self.proj.company
        company = None
        if c is not None:
            company = {
                "ame": c.ame,
                "afm": c.afm,
                "legal_name": c.legal_name,
                "first_name": c.first_name,
                "father_name": c.father_name,
                "local_office_code": c.local_office_code,
                "local_office_name": c.local_office_name,
                "activity": c.activity,
            }
        branches = [
            {
                "code": b.code,
                "street": b.street,
                "street_no": b.street_no,
                "postal_code": b.postal_code,
                "city": b.city,
                "sepe_code": b.sepe_code,
                "oaed_code": b.oaed_code,
                "kad_code": b.kad_code,
                "kallikratis_code": b.kallikratis_code,
            }
            for b in sorted(self.proj.branches.values(), key=lambda x: x.code)
        ]
        # Το Μητρώο ΚΠΚ έχει ΜΙΑ μορφή σε όλα τα αρχεία εξαγωγής (βλ. kpk.py)
        kpks = self.kpk_export_rows()
        specialties = []
        for sp in sorted(self.proj.specialties.values(), key=lambda s: s.name):
            specialties.append(
                {
                    "name": sp.name,
                    "ergani_specialty_code": sp.ergani_specialty_code,
                    "ergani_specialty_description": sp.ergani_specialty_description,
                    "epik_carriers": list(sp.epik_carriers),
                    "pron": dict(sp.pron_settings),
                    # Ένα πακέτο ανά ΡΟΛΟ — ό,τι ακριβώς δέχεται η define_specialty.
                    "packages": {
                        c["role"]: {
                            "kpk": c.get("kpk", ""),
                            "kad": c.get("kad", ""),
                            "specialty_code": c.get("specialty_code", ""),
                        }
                        for c in sp.coverages
                    },
                }
            )
        return {
            "schema": self.COMPANY_EXPORT_SCHEMA,
            "version": self.COMPANY_EXPORT_VERSION,
            "exported_at": date.today().isoformat(),
            "company": company,
            "branches": branches,
            "kpks": kpks,
            "specialties": specialties,
        }

    def export_company_bytes(self) -> bytes:
        """Το αρχείο εξαγωγής (UTF-8, αναγνώσιμο ελληνικά)."""
        return json.dumps(self.export_company(), ensure_ascii=False, indent=2).encode(
            "utf-8"
        )

    def import_company(self, data) -> dict:
        """Εισάγει αρχείο εξαγωγής. Κάθε ενότητα περνά από το αντίστοιχο
        command — ίδιο κλειδί = αντικατάσταση (αντισταθμιστικό event), ό,τι
        δεν υπάρχει στο αρχείο μένει ως έχει. Οι λάθος εγγραφές επιστρέφονται
        ως σφάλματα, δεν χάνονται σιωπηλά.

        ΣΕΙΡΑ: εταιρεία -> παραρτήματα -> ΚΠΚ -> ειδικότητες (οι ειδικότητες
        απαιτούν δηλωμένα ΚΠΚ με ποσοστά)."""
        text = _decode_import(data)
        try:
            doc = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Μη έγκυρο αρχείο JSON: {exc}") from None
        if not isinstance(doc, dict):
            raise ValueError("Μη έγκυρο αρχείο εξαγωγής (αναμένεται αντικείμενο JSON)")
        schema = doc.get("schema")
        if schema and schema != self.COMPANY_EXPORT_SCHEMA:
            raise ValueError(f"Άγνωστος τύπος αρχείου «{schema}»")

        counts = {"company": 0, "branches": 0, "kpks": 0, "specialties": 0}
        errors: list[str] = []

        c = doc.get("company")
        if isinstance(c, dict):
            try:
                self.register_company(
                    ame=c.get("ame", ""),
                    afm=c.get("afm", ""),
                    legal_name=c.get("legal_name", ""),
                    first_name=c.get("first_name", ""),
                    father_name=c.get("father_name", ""),
                    local_office_code=c.get("local_office_code") or "0000000000",
                    local_office_name=c.get("local_office_name", ""),
                    activity=c.get("activity", ""),
                )
                counts["company"] = 1
            except ValueError as exc:
                errors.append(f"στοιχεία εργοδότη: {exc}")

        for b in doc.get("branches") or ():
            try:
                self.define_branch(
                    code=b.get("code", 0),
                    street=b.get("street", ""),
                    street_no=b.get("street_no", ""),
                    postal_code=b.get("postal_code") or "00000",
                    city=b.get("city", ""),
                    sepe_code=b.get("sepe_code", ""),
                    oaed_code=b.get("oaed_code", ""),
                    kad_code=b.get("kad_code", ""),
                    kallikratis_code=b.get("kallikratis_code", ""),
                )
                counts["branches"] += 1
            except (ValueError, AttributeError) as exc:
                errors.append(f"παράρτημα {b.get('code', '?')}: {exc}")

        for k in doc.get("kpks") or ():
            kpk = k.get("kpk", "?") if isinstance(k, dict) else "?"
            try:
                self.import_kpk_row(k)
                counts["kpks"] += 1
            except (ValueError, TypeError, AttributeError) as exc:
                errors.append(f"ΚΠΚ {kpk}: {exc}")

        for s in doc.get("specialties") or ():
            name = s.get("name", "?") if isinstance(s, dict) else "?"
            try:
                self.define_specialty(
                    s.get("name", ""),
                    s.get("packages") or {},
                    s.get("ergani_specialty_code", ""),
                    s.get("ergani_specialty_description", ""),
                    epik_carriers=s.get("epik_carriers") or None,
                    pron=s.get("pron"),
                )
                counts["specialties"] += 1
            except (ValueError, TypeError, AttributeError) as exc:
                errors.append(f"ειδικότητα «{name}»: {exc}")

        if not any(counts.values()) and not errors:
            raise ValueError("Το αρχείο δεν περιέχει στοιχεία εταιρείας")
        return {"counts": counts, "errors": errors}

    def get_specialty(self, name):
        """Η δηλωμένη ειδικότητα εταιρείας — ValueError αν δεν υπάρχει."""
        sp = self.proj.specialties.get(constraints.norm_name(name or ""))
        if sp is None:
            raise ValueError(
                "Άγνωστη ειδικότητα «%s» — δηλώστε πρώτα τις ειδικότητες "
                "της εταιρείας (σελίδα Εταιρεία)" % (name or "")
            )
        return sp

    def specialty_options(self, specialty_name) -> dict:
        """Οι ΕΠΙΛΟΓΕΣ που προσφέρει μια ειδικότητα στην πρόσληψη — ΜΙΑ πηγή
        αλήθειας για τη φόρμα και για την επικύρωση:

        - `carriers`: επιτρεπτοί φορείς επικουρικής (ένας ή δύο).
        - `pron`   : ρυθμίσεις πρόνοιας.
        - `pron_choices`: οι επιτρεπτές επιλογές πρόνοιας του εργαζομένου
          (κενή λίστα = δεν ρωτιέται· η τιμή προκύπτει μόνη της)."""
        sp = self.get_specialty(specialty_name)
        pron = sp.pron_settings
        carriers = [c for c in config.CARRIER_ROLES if c in (sp.epik_carriers or ())]
        choices = []
        if pron["enabled"]:
            choices = ["new", "old"] if pron["differentiated"] else ["yes"]
            if not pron["mandatory"]:
                choices.append("no")
        return {
            "specialty": sp,
            "carriers": carriers,
            "carrier_labels": {c: config.CARRIERS[c] for c in carriers},
            "pron": pron,
            # Ρωτάμε ΜΟΝΟ όταν υπάρχει πραγματική επιλογή (ενιαία και
            # υποχρεωτική πρόνοια δεν αφήνει τίποτα να διαλέξει ο χρήστης).
            "pron_choices": choices if len(choices) > 1 else [],
        }

    def resolve_insurance_choice(
        self, specialty_name, auxiliary_scheme, pron_choice=None
    ):
        """Επικυρώνει τις επιλογές πρόσληψης και επιστρέφει
        (ειδικότητα, φορέας, επιλογή πρόνοιας, σημαίες επίλυσης).

        Ο φορέας επικουρικής είναι ΥΠΟΧΡΕΩΤΙΚΟΣ και πρέπει να ανήκει στους
        επιτρεπτούς της ειδικότητας· η επιλογή πρόνοιας πρέπει να είναι
        συμβατή με τις ρυθμίσεις της."""
        opts = self.specialty_options(specialty_name)
        sp, pron = opts["specialty"], opts["pron"]
        carrier = str(auxiliary_scheme or "").lower()
        # Κενή επιλογή = η πρώτη επιτρεπτή (όπως το προεπιλεγμένο option της
        # φόρμας)· λάθος επιλογή απορρίπτεται.
        if not carrier and opts["carriers"]:
            carrier = opts["carriers"][0]
        if carrier not in opts["carriers"]:
            allowed = " ή ".join(config.CARRIERS[c] for c in opts["carriers"]) or "—"
            raise ValueError(
                f"Η ειδικότητα «{sp.name}» επιτρέπει επικουρική σε: {allowed}"
            )
        choice = str(pron_choice or "").lower()
        if not pron["enabled"]:
            choice = "no"
        elif opts["pron_choices"]:
            if not choice:
                choice = opts["pron_choices"][0]
            elif choice not in opts["pron_choices"]:
                raise ValueError(
                    f"Μη έγκυρη επιλογή πρόνοιας για την ειδικότητα «{sp.name}»"
                )
        else:
            choice = "new" if pron["differentiated"] else "yes"
        # Οι «σημαίες» που καταναλώνει η γενική config.resolve_coverages.
        flags = {
            "auxiliary": carrier,
            "teka_member": carrier == "teka",
            "efka_aux_member": carrier == "efka",
            "welfare": "no" if choice == "no" else "yes",
            "insurance_class": choice if choice in {"new", "old"} else "new",
        }
        return sp, carrier, choice, flags

    def specialty_lines(
        self, specialty_name, auxiliary_scheme, pron_choice=None, on=None
    ):
        """ΟΙ ΓΡΑΜΜΕΣ ΑΠΔ ενός συνδυασμού (ειδικότητα + φορέας + πρόνοια), με
        επιλυμένα ΚΑΔ/ειδικότητα/ΚΠΚ και τα ποσοστά που ΙΣΧΥΟΥΝ την `on`.

        ΜΙΑ πηγή αλήθειας: τη χρησιμοποιεί και η ανάθεση (assign_specialty)
        και η ζωντανή προεπισκόπηση της πρόσληψης. Γραμμή χωρίς ποσοστά για
        την περίοδο επιστρέφεται με `has_rate=False` (δεν σκάει) ώστε το UI
        να δείξει τι λείπει."""
        sp, carrier, choice, flags = self.resolve_insurance_choice(
            specialty_name, auxiliary_scheme, pron_choice
        )
        on = on or date.today()
        lines = []
        for c in config.resolve_coverages(sp.coverages, flags):
            fund, kpk = c["fund"], c["kpk"]
            ident = self.kpk_identity(fund, kpk)
            try:
                rates = self.kpk_rates_for(fund, kpk, on)
            except ValueError:
                rates = None
            lines.append(
                {
                    "role": c.get("role", ""),
                    "label": c.get("label", ""),
                    "fund": fund,
                    "carrier_label": config.CARRIERS.get(
                        config.fund_carrier(fund), fund
                    ),
                    "kpk": kpk,
                    "kpk_name": ident.name if ident else "",
                    "kad": c.get("kad", ""),
                    "specialty_code": c.get("specialty_code", ""),
                    "employee": rates.employee if rates else None,
                    "employer": rates.employer if rates else None,
                    "has_kpk": ident is not None,
                    "has_rate": rates is not None,
                    "coverage": c,
                }
            )
        return {
            "specialty": sp,
            "carrier": carrier,  # φορέας επικουρικής που τελικά ίσχυσε
            "pron_choice": choice,
            "lines": lines,
            "employee": sum(line["employee"] or 0.0 for line in lines),
            "employer": sum(line["employer"] or 0.0 for line in lines),
            "incomplete": any(not line["has_rate"] for line in lines),
        }

    def assign_specialty(
        self,
        employment_id,
        specialty_name,
        auxiliary_scheme=None,
        pron_choice=None,
        branch_no=0,
        labor_subsidy_excluded=None,
    ):
        """Αναθέτει σε απασχόληση μια δηλωμένη ειδικότητα της εταιρείας.

        `labor_subsidy_excluded`: None = κρατά ό,τι ίσχυε (η διόρθωση φόρμας
        πρόσληψης ξαναγράφει το προφίλ και δεν πρέπει να σβήνει τη δήλωση).

        Στην πρόσληψη επιλέγονται ΜΟΝΟ: ειδικότητα, ΦΟΡΕΑΣ ΕΠΙΚΟΥΡΙΚΗΣ
        (ΕΦΚΑ ή ΤΕΚΑ, από τους επιτρεπτούς της ειδικότητας) και, όπου
        προβλέπεται, η ΕΠΙΛΟΓΗ ΠΡΟΝΟΙΑΣ (`new`/`old` όταν είναι
        διαφοροποιημένη, `yes`/`no` όταν είναι ενιαία/προαιρετική). Οι
        καλύψεις επιλύονται από τα πακέτα της ειδικότητας και γράφονται στο
        event, ώστε μισθοδοσία/ΑΠΔ να μην εξαρτώνται από μεταγενέστερες
        αλλαγές της."""
        emp = self.proj.employments.get(employment_id)
        if emp is None:
            raise ValueError(f"Άγνωστη απασχόληση {employment_id}")
        # Τα ποσοστά ΚΠΚ είναι ΧΡΟΝΟΣΕΙΡΑ και το μητρώο ξεκινά από τότε που
        # η εταιρεία μπήκε σε ΑΥΤΟ το σύστημα: η επίλυση γίνεται στην ΑΝΑΛΗΨΗ
        # της μισθοδοσίας, όχι σε μια πρόσληψη ετών πριν (που δεν θα είχε
        # καταχωρημένα ποσοστά — και δεν την πλήρωσε αυτό το πρόγραμμα).
        on = emp.payroll_start or emp.hire_date
        result = self.specialty_lines(
            specialty_name, auxiliary_scheme, pron_choice, on=on
        )
        sp, carrier, choice = (
            result["specialty"],
            result["carrier"],
            result["pron_choice"],
        )
        resolved = tuple(line["coverage"] for line in result["lines"])
        mains = [c for c in resolved if config.fund_branch(c["fund"]) == "main"]
        if len(mains) != 1:
            raise ValueError(
                f"Η ειδικότητα «{sp.name}» πρέπει να επιλύει ακριβώς ένα κύριο "
                f"πακέτο (βρέθηκαν {len(mains)})"
            )
        welfare_scheme = "none" if choice == "no" else "yes"
        insurance_class = choice if choice in {"new", "old"} else "new"
        # Ο ΕΠΙΛΥΜΕΝΟΣ φορέας (όχι το όρισμα, που μπορεί να ήρθε κενό).
        self._validate_package_components(resolved, carrier, welfare_scheme, on)
        if labor_subsidy_excluded is None:
            prev = emp.insurance_profile
            labor_subsidy_excluded = bool(prev and prev.labor_subsidy_excluded)
        self.assign_insurance_profile(
            employment_id,
            branch_no=branch_no,
            specialty_name=sp.name,
            coverages=resolved,
            auxiliary_scheme=carrier,
            welfare_scheme=welfare_scheme,
            insurance_class=insurance_class,
            ergani_specialty_code=sp.ergani_specialty_code,
            ergani_specialty_description=sp.ergani_specialty_description,
            labor_subsidy_excluded=labor_subsidy_excluded,
        )

    def _validate_package_components(
        self, coverages, auxiliary_scheme, welfare_scheme, on
    ):
        """Δεν επιτρέπει διπλοχρέωση αναλυτικού κλάδου μεταξύ πακέτων.

        `auxiliary_scheme` είναι ο ΕΠΙΛΥΜΕΝΟΣ φορέας επικουρικής (efka/teka —
        η επικουρική δεν είναι προαιρετική στο μοντέλο), οπότε κάποιο από τα
        επιλεγμένα ΚΠΚ πρέπει να καλύπτει τον κλάδο `auxiliary`: είτε το
        σύνθετο 0101, είτε το ζεύγος 0103 + ΤΕΚΑ.

        ➜ Ο έλεγχος είναι ΔΥΝΑΤΟΣ ΜΟΝΟ όσο τα ΚΠΚ κουβαλούν ανάλυση κλάδων
        (προαιρετική — βλ. `define_kpk_rates`). Χωρίς καμία ανάλυση δεν
        υπάρχει τίποτα να συγκριθεί, οπότε παραλείπεται σιωπηλά αντί να
        απορρίπτει κάθε πρόσληψη."""
        owners = {}
        analysed = False
        for c in coverages:
            rates = self.kpk_rates_for(c["fund"], c["kpk"], on)
            components = tuple(rates.components)
            analysed = analysed or bool(components)
            for part in components:
                branch = part["branch"]
                if branch in owners:
                    raise ValueError(
                        f"Ο κλάδος «{branch}» χρεώνεται δύο φορές: "
                        f"{owners[branch]} και {c['kpk']}"
                    )
                owners[branch] = c["kpk"]
        if analysed:
            for required in ("main_pension", "healthcare"):
                if required not in owners:
                    raise ValueError(f"Λείπει ο υποχρεωτικός κλάδος «{required}»")
            if "auxiliary" not in owners:
                raise ValueError(
                    "Λείπει ο κλάδος επικουρικής από τα επιλεγμένα ΚΠΚ "
                    f"(φορέας: {config.CARRIERS.get(auxiliary_scheme, auxiliary_scheme)})"
                )
            if welfare_scheme != "none" and "welfare" not in owners:
                raise ValueError(
                    "Δεν υπάρχει προνοιακό ΚΠΚ για τον επιλεγμένο συνδυασμό "
                    f"welfare={welfare_scheme}"
                )
            if welfare_scheme == "none" and "welfare" in owners:
                raise ValueError("Επιλέχθηκε προνοιακό ΚΠΚ χωρίς υπαγωγή σε πρόνοια")

    def contribution_rates(self, prof, on) -> config.EfkaRates:
        """Συνολικά ποσοστά κρατήσεων (εργαζομένου/εργοδότη) μιας απασχόλησης
        από τα ΚΠΚ των καλύψεών της (μητρώο config.KPK_HISTORY).

        - Κάθε επιλυμένο πακέτο μετρά ακριβώς μία φορά.
        - Σύνθετο 0101 μετρά μόνο του· ΤΕΚΑ επιλύεται ως 0103 + 4169.
        """
        base = config.efka_rates_for(on)
        covs = profile_coverages(prof)
        main = next((c for c in covs if config.fund_branch(c["fund"]) == "main"), None)
        if main is None:
            raise ValueError("Λείπει η κύρια ασφαλιστική κάλυψη")
        mk = self.kpk_rates_for(main["fund"], main["kpk"], on)
        employee, employer = mk.employee, mk.employer
        for c in covs:
            if c is main:
                continue  # η κύρια μετρήθηκε ήδη
            k = self.kpk_rates_for(c["fund"], c["kpk"], on)
            employee += k.employee
            employer += k.employer
        return config.EfkaRates(base.valid_from, employee, employer, base.monthly_cap)

    def contribution_breakdown(self, prof, on) -> list[dict]:
        """Ανάλυση των ποσοστών κρατήσεων ΑΝΑ ΤΑΜΕΙΟ/ΚΠΚ (εργαζ./εργοδ.) —
        όπως τα αθροίζει η `contribution_rates`, αλλά σπασμένα για την
        απόδειξη. Κάθε επιλυμένο ΚΠΚ εμφανίζεται μία φορά (ίδια λογική με την ΑΠΔ,
        `build_apd_rows`). Το άθροισμα των `employee`/`employer` ισούται με
        το συνολικό ποσοστό της `contribution_rates`."""
        covs = profile_coverages(prof)
        main = next((c for c in covs if config.fund_branch(c["fund"]) == "main"), None)
        if main is None:
            return []
        # Μία γραμμή ανά επιλυμένο πακέτο (κύριο πρώτο), με το δικό του ΚΠΚ.
        ordered = [main] + [c for c in covs if c is not main]
        rows = []
        for c in ordered:
            fund = c["fund"]
            k = self.kpk_rates_for(fund, c["kpk"], on)
            label = c.get("label") or (
                config.FUNDS[fund].label if fund in config.FUNDS else fund
            )
            rows.append(
                {
                    "fund": fund,
                    "label": label,
                    "kpk": c["kpk"],
                    "employee": k.employee,
                    "employer": k.employer,
                    "components": list(k.components or ()),
                }
            )
        return rows

    def assign_insurance_profile(
        self,
        employment_id,
        branch_no=0,
        specialty_name="",
        coverages=(),
        auxiliary_scheme="none",
        welfare_scheme="none",
        insurance_class="new",
        ergani_specialty_code="",
        ergani_specialty_description="",
        labor_subsidy_excluded=False,
    ):
        """Ασφαλιστικό προφίλ ΑΠΑΣΧΟΛΗΣΗΣ (ΚΑΔ/ειδικότητα/ΚΠΚ/ΤΕΚΑ). Τα
        στοιχεία ταυτότητας (ΑΜΑ, πατρώνυμο, μητρώνυμο, γέννηση) αντλούνται
        από το φυσικό πρόσωπο — δεν διπλογράφονται εδώ. Από το UI προτίμησε
        την assign_specialty() (δηλωμένη ειδικότητα εταιρείας)."""
        emp = self.proj.employments.get(employment_id)
        if emp is None:
            raise ValueError(f"Άγνωστη απασχόληση {employment_id}")
        branch = self.get_branch(branch_no)
        branch_no = branch.code
        if not coverages:
            raise ValueError("Το ασφαλιστικό προφίλ απαιτεί τουλάχιστον μία κάλυψη")
        self.store.append(
            employment_id,
            InsuranceProfileAssigned(
                employee_id=employment_id,
                branch_no=branch_no,
                specialty_name=specialty_name,
                coverages=tuple(coverages),
                auxiliary_scheme=auxiliary_scheme,
                welfare_scheme=welfare_scheme,
                insurance_class=insurance_class,
                ergani_specialty_code=ergani_specialty_code,
                ergani_specialty_description=ergani_specialty_description,
                labor_subsidy_excluded=bool(labor_subsidy_excluded),
            ),
        )
