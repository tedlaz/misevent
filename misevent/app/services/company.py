"""
Εταιρεία, παραρτήματα και ΠΡΟΣΩΠΑ (φυσικά πρόσωπα — §2)

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

import json
import uuid
from dataclasses import fields as dc_fields
from datetime import date

import misevent.integrations.ergani_parameters as ergani_parameters
import misevent.rules.config as config
import misevent.rules.constraints as constraints
from misevent.domain.events import (
    CompanyBranchDefined,
    CompanyRegistered,
    PersonDetailsCorrected,
    PersonRegistered,
)
from misevent.domain.projections import PersonState
from misevent.integrations.apd_files import EmployerInfo

from ._base import _decode_import, _parse_import_date

# ΤΑ ΠΕΔΙΑ ΤΟΥ ΑΡΧΕΙΟΥ ΠΡΟΣΩΠΩΝ — ΠΑΡΑΓΩΓΑ, ΟΧΙ ΧΕΙΡΟΓΡΑΦΗ ΛΙΣΤΑ.
# Πηγή: το `constraints.PERSON_FIELDS` (ό,τι δέχεται η `correct_person`) συν
# την **ημερομηνία γέννησης**, που λείπει από εκεί επίτηδες: είναι μέρος του
# κλειδιού ταυτότητας και ΔΕΝ διορθώνεται — αλλά ΠΡΕΠΕΙ να ταξιδεύει, αλλιώς
# το πρόσωπο δεν μπορεί να καταχωρηθεί σε άλλο σύστημα.
# ➜ Νέο πεδίο ταυτότητας στο μητρώο = μπαίνει ΜΟΝΟ ΤΟΥ στην εξαγωγή.
PERSON_EXPORT_FIELDS = tuple(
    name
    for field_name in constraints.PERSON_FIELDS
    for name in ((field_name, "birth_date") if field_name == "mother_name"
                 else (field_name,))
)

# Ποια από αυτά είναι ημερομηνίες — από την ΙΔΙΑ την κλάση του projection,
# ώστε ένα νέο πεδίο-ημερομηνία να μη γραφτεί ποτέ ως σκέτο string.
PERSON_DATE_FIELDS = frozenset(
    f.name
    for f in dc_fields(PersonState)
    if f.type in (date | None, "date | None") and f.name in PERSON_EXPORT_FIELDS
)


class CompanyMixin:
    # ---------------------------------------------------------- Εταιρεία
    def register_company(
        self,
        ame,
        afm,
        legal_name,
        first_name="",
        father_name="",
        local_office_code="0000000000",
        local_office_name="",
        activity="",
    ):
        """Καταχωρεί τα στοιχεία του εργοδότη ως event (persisted)."""
        constraints.check_employer_name(legal_name, first_name, father_name)
        constraints.check_employer_codes(ame, afm, "00000", local_office_code)
        self.store.append(
            "COMPANY",
            CompanyRegistered(
                ame,
                afm,
                legal_name,
                first_name,
                father_name,
                local_office_code,
                local_office_name,
                activity,
            ),
        )

    def employer_info(self) -> EmployerInfo:
        c = self.proj.company
        if c is None:
            raise ValueError(
                "Δεν έχουν καταχωρηθεί στοιχεία εταιρείας — "
                "καλέστε πρώτα register_company()"
            )
        branch = self.get_branch(0)
        return EmployerInfo(
            c.ame,
            c.afm,
            c.legal_name,
            c.first_name,
            c.father_name,
            branch.street,
            branch.street_no,
            branch.postal_code,
            branch.city,
            c.local_office_code,
            c.local_office_name,
        )

    # ---------------------------------------------------------- Πρόσωπο
    def register_person(
        self,
        last_name,
        first_name,
        father_name,
        mother_name,
        birth_date,
        afm,
        amka,
        ama="",
        children=0,
        email="",
        sex="",
        nationality_code="",
        identity_type="",
        identity_number="",
        identity_issuer="",
        identity_issue_date=None,
        identity_expiry_date=None,
        marital_status="0",
        res_permit_inst="",
        res_permit_inst_type="",
        res_permit_inst_number="",
        res_permit_inst_expiry_date=None,
        res_permit_ap="",
        res_permit_ap_type="",
        res_permit_ap_number="",
        res_permit_ap_expiry_date=None,
        res_permit_visa="",
        res_permit_visa_number="",
        res_permit_visa_from=None,
        res_permit_visa_to=None,
        education_level="",
    ):
        """Καταχωρεί ΦΥΣΙΚΟ ΠΡΟΣΩΠΟ (σταθερή ταυτότητα). Ελέγχει
        μοναδικότητα ΑΦΜ / ΑΜΚΑ / (Επώνυμο+Όνομα+Πατρώνυμο+ημ. γέννησης)
        (constraints.py) πριν γραφτεί το event. Επιστρέφει το νέο person_id."""
        ln = constraints.norm_name(last_name)
        fn = constraints.norm_name(first_name)
        fa = constraints.norm_name(father_name)
        mo = constraints.norm_name(mother_name)
        afm_n = constraints.norm_digits(afm)
        amka_n = constraints.norm_digits(amka)
        ama_n = constraints.norm_digits(ama)
        email_n = constraints.norm_email(email)
        sex_n = str(sex or "").strip()
        nationality_n = str(nationality_code or "").strip()
        identity_type_n = str(identity_type or "").strip().upper()
        identity_number_n = str(identity_number or "").strip().upper()
        identity_issuer_n = str(identity_issuer or "").strip().upper()

        # Η γέννηση είναι ΜΕΡΟΣ του κλειδιού ταυτότητας -> υποχρεωτική
        if birth_date is None:
            raise ValueError("Απαιτείται ημερομηνία γέννησης")
        constraints.check_person_identity(ln, fn, fa, mo, afm_n, amka_n, ama_n)
        constraints.check_web_e3n_identity(
            sex_n,
            nationality_n,
            identity_type_n,
            identity_number_n,
            identity_issuer_n,
            identity_issue_date,
            identity_expiry_date,
        )

        constraints.check_web_e3n_person_details(
            marital_status,
            nationality_n,
            res_permit_inst,
            res_permit_inst_type,
            res_permit_inst_number,
            res_permit_inst_expiry_date,
            res_permit_ap,
            res_permit_ap_type,
            res_permit_ap_number,
            res_permit_ap_expiry_date,
            res_permit_visa,
            res_permit_visa_number,
            res_permit_visa_from,
            res_permit_visa_to,
            education_level,
        )
        constraints.check_person_unique(
            self.proj.persons.values(),
            afm_n,
            amka_n,
            constraints.identity_key(ln, fn, fa, birth_date),
        )

        person_id = "P" + uuid.uuid4().hex[:8]
        self.store.append(
            person_id,
            PersonRegistered(
                person_id=person_id,
                last_name=ln,
                first_name=fn,
                father_name=fa,
                mother_name=mo,
                birth_date=birth_date,
                afm=afm_n,
                amka=amka_n,
                ama=ama_n,
                children=int(children),
                email=email_n,
                sex=sex_n,
                nationality_code=nationality_n,
                identity_type=identity_type_n,
                identity_number=identity_number_n,
                identity_issuer=identity_issuer_n,
                identity_issue_date=identity_issue_date,
                identity_expiry_date=identity_expiry_date,
                marital_status=str(marital_status),
                res_permit_inst=str(res_permit_inst or ""),
                res_permit_inst_type=str(res_permit_inst_type or "").strip(),
                res_permit_inst_number=str(res_permit_inst_number or "").strip(),
                res_permit_inst_expiry_date=res_permit_inst_expiry_date,
                res_permit_ap=str(res_permit_ap or ""),
                res_permit_ap_type=str(res_permit_ap_type or "").strip(),
                res_permit_ap_number=str(res_permit_ap_number or "").strip(),
                res_permit_ap_expiry_date=res_permit_ap_expiry_date,
                res_permit_visa=str(res_permit_visa or ""),
                res_permit_visa_number=str(res_permit_visa_number or "").strip(),
                res_permit_visa_from=res_permit_visa_from,
                res_permit_visa_to=res_permit_visa_to,
                education_level=str(education_level or "").strip(),
            ),
        )
        return person_id

    def define_branch(
        self,
        code,
        street="",
        street_no="",
        postal_code="00000",
        city="",
        sepe_code="",
        oaed_code="",
        kad_code="",
        kallikratis_code="",
    ):
        """Δηλώνει/ενημερώνει φυσικό παράρτημα της εταιρείας."""
        if self.proj.company is None:
            raise ValueError("Καταχωρήστε πρώτα τα στοιχεία της εταιρείας")
        try:
            code = int(code)
        except (TypeError, ValueError) as exc:
            raise ValueError("Ο κωδικός παραρτήματος πρέπει να είναι ακέραιος") from exc
        if code < 0:
            raise ValueError("Ο κωδικός παραρτήματος δεν μπορεί να είναι αρνητικός")
        postal_code = str(postal_code or "00000").strip()
        if len(postal_code) != 5 or not postal_code.isdigit():
            raise ValueError("Ο Τ.Κ. παραρτήματος πρέπει να είναι 5ψήφιος")
        for kind, value, label in (
            ("sepe", sepe_code, "Υπηρεσία ΣΕΠΕ"),
            ("oaed", oaed_code, "Υπηρεσία ΟΑΕΔ"),
            ("kad", kad_code, "ΚΑΔ παραρτήματος"),
            ("koinotites", kallikratis_code, "Καλλικράτης"),
        ):
            if value and not ergani_parameters.contains(kind, value):
                raise ValueError(f"Άγνωστη τιμή για {label}: {value}")
        self.store.append(
            f"BRANCH-{code}",
            CompanyBranchDefined(
                code=code,
                street=str(street or "").strip().upper(),
                street_no=str(street_no or "").strip(),
                postal_code=postal_code,
                city=str(city or "").strip().upper(),
                sepe_code=str(sepe_code or "").strip(),
                oaed_code=str(oaed_code or "").strip(),
                kad_code=str(kad_code or "").strip(),
                kallikratis_code=str(kallikratis_code or "").strip(),
            ),
        )

    def get_branch(self, code=0):
        try:
            code = int(code)
        except (TypeError, ValueError) as exc:
            raise ValueError("Μη έγκυρος κωδικός παραρτήματος") from exc
        branch = self.proj.branches.get(code)
        if branch is None:
            raise ValueError(f"Δεν υπάρχει παράρτημα με κωδικό {code}")
        return branch

    # ------------------------- Εξαγωγή/εισαγωγή ΠΡΟΣΩΠΩΝ (σελίδα Εργαζόμενοι)
    # ΕΝΑ αρχείο με την ΤΑΥΤΟΤΗΤΑ των φυσικών προσώπων — ΟΧΙ απασχολήσεις:
    # η πρόσληψη είναι ξεχωριστή πράξη με δικούς της όρους (§2), και η
    # «κίνηση» (παρουσίες, άδειες, μισθοδοσίες) ζει στο event log, που έχει
    # δικό του backup/restore. Ίδιο μοτίβο με τα άλλα αρχεία εξαγωγής: η
    # ΕΞΑΓΩΓΗ διαβάζει μόνο projections, η ΕΙΣΑΓΩΓΗ περνά από τα ΚΑΝΟΝΙΚΑ
    # commands (`register_person`/`correct_person`), άρα ισχύουν όλοι οι
    # έλεγχοι μοναδικότητας και γράφονται κανονικά events.
    PERSONS_EXPORT_SCHEMA = "misevent.persons"
    PERSONS_EXPORT_VERSION = 1

    def export_persons(self) -> dict:
        """Τα πρόσωπα της εταιρείας ως JSON-serializable dict.

        Τα ΚΕΝΑ πεδία παραλείπονται (οι άδειες διαμονής αφορούν ελάχιστους):
        το αρχείο μένει αναγνώσιμο και η εισαγωγή τα γεμίζει με τις ίδιες
        προεπιλογές που έχει και η φόρμα."""
        persons = []
        for p in sorted(self.proj.persons.values(), key=lambda x: x.full_name):
            row = {}
            for name in PERSON_EXPORT_FIELDS:
                value = getattr(p, name, None)
                if value in (None, ""):
                    continue
                row[name] = value.isoformat() if isinstance(value, date) else value
            persons.append(row)
        return {
            "schema": self.PERSONS_EXPORT_SCHEMA,
            "version": self.PERSONS_EXPORT_VERSION,
            "exported_at": date.today().isoformat(),
            "persons": persons,
        }

    def export_persons_bytes(self) -> bytes:
        """Το αρχείο εξαγωγής (UTF-8, αναγνώσιμα ελληνικά)."""
        return json.dumps(
            self.export_persons(), ensure_ascii=False, indent=2
        ).encode("utf-8")

    def import_persons(self, data) -> dict:
        """Εισάγει αρχείο προσώπων.

        ΚΛΕΙΔΙ ΤΟ ΑΦΜ: πρόσωπο που υπάρχει ήδη **ενημερώνεται**
        (`correct_person`), αλλιώς **καταχωρείται** (`register_person`).
        Κανένα πρόσωπο δεν διαγράφεται και καμία απασχόληση δεν θίγεται.

        ⚠️ Η ΗΜ. ΓΕΝΝΗΣΗΣ ΔΕΝ ΔΙΟΡΘΩΝΕΤΑΙ (κλειδί ταυτότητας): αν το αρχείο
        δίνει άλλη για το ίδιο ΑΦΜ, η γραμμή επιστρέφεται ως ΣΦΑΛΜΑ αντί να
        περάσει μισή. Οι λάθος γραμμές δεν χαλούν τις υπόλοιπες."""
        text = _decode_import(data)
        try:
            doc = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Μη έγκυρο αρχείο JSON: {exc}") from None
        if not isinstance(doc, dict):
            raise ValueError("Μη έγκυρο αρχείο εξαγωγής (αναμένεται αντικείμενο JSON)")
        schema = doc.get("schema")
        if schema and schema != self.PERSONS_EXPORT_SCHEMA:
            raise ValueError(f"Άγνωστος τύπος αρχείου «{schema}»")
        rows = doc.get("persons")
        if not isinstance(rows, list):
            raise ValueError("Το αρχείο δεν έχει λίστα «persons»")

        counts = {"created": 0, "updated": 0}
        errors: list[str] = []
        for row in rows:
            who = " ".join(
                str(row.get(k, "")) for k in ("last_name", "first_name")
            ).strip() or "?"
            try:
                if not isinstance(row, dict):
                    raise ValueError("η γραμμή δεν είναι αντικείμενο JSON")
                values = {}
                for name in PERSON_EXPORT_FIELDS:
                    if name not in row:
                        continue
                    v = row[name]
                    if name in PERSON_DATE_FIELDS and v not in (None, ""):
                        v = _parse_import_date(str(v))
                    values[name] = v
                existing = self.proj.person_by_afm(values.get("afm", ""))
                if existing is None:
                    self.register_person(**values)
                    counts["created"] += 1
                    continue
                born = values.pop("birth_date", None)
                if born and existing.birth_date and born != existing.birth_date:
                    raise ValueError(
                        f"ίδιο ΑΦΜ με ΑΛΛΗ ημ. γέννησης "
                        f"({config.gr_date(existing.birth_date)} στο σύστημα, "
                        f"{config.gr_date(born)} στο αρχείο) — "
                        "η ημ. γέννησης είναι κλειδί ταυτότητας και δεν διορθώνεται"
                    )
                self.correct_person(existing.person_id, **values)
                counts["updated"] += 1
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                errors.append(f"{who}: {exc}")
        return {"counts": counts, "errors": errors}

    def correct_person(self, person_id, **fields):
        """CRUD update στοιχείων ταυτότητας προσώπου (επώνυμο, όνομα,
        πατρώνυμο, μητρώνυμο, ΑΦΜ, ΑΜΚΑ, ΑΜΑ, τέκνα) με επανέλεγχο
        μοναδικότητας."""
        p = self.proj.persons.get(person_id)
        if p is None:
            raise ValueError(f"Άγνωστο πρόσωπο {person_id}")

        clean = {}
        clearable = {
            "res_permit_inst",
            "res_permit_inst_type",
            "res_permit_inst_number",
            "res_permit_inst_expiry_date",
            "res_permit_ap",
            "res_permit_ap_type",
            "res_permit_ap_number",
            "res_permit_ap_expiry_date",
            "res_permit_visa",
            "res_permit_visa_number",
            "res_permit_visa_from",
            "res_permit_visa_to",
        }
        for k, v in fields.items():
            if k not in constraints.PERSON_FIELDS:
                continue
            if v in (None, "") and k not in clearable:
                continue
            if k in ("last_name", "first_name", "father_name", "mother_name"):
                clean[k] = constraints.norm_name(v)
            elif k in ("afm", "amka", "ama"):
                clean[k] = constraints.norm_digits(v)
            elif k == "children":
                clean[k] = int(v)
            elif k == "email":
                clean[k] = constraints.norm_email(v)
            elif k in ("sex", "nationality_code"):
                clean[k] = str(v).strip()
            elif k in ("identity_type", "identity_number", "identity_issuer"):
                clean[k] = str(v).strip().upper()
            elif k in ("identity_issue_date", "identity_expiry_date"):
                clean[k] = v
            elif k in (
                "marital_status",
                "education_level",
                "res_permit_inst",
                "res_permit_inst_type",
                "res_permit_inst_number",
                "res_permit_ap",
                "res_permit_ap_type",
                "res_permit_ap_number",
                "res_permit_visa",
                "res_permit_visa_number",
            ):
                clean[k] = str(v or "").strip()
            elif k in (
                "res_permit_inst_expiry_date",
                "res_permit_ap_expiry_date",
                "res_permit_visa_from",
                "res_permit_visa_to",
            ):
                clean[k] = v

        if not clean:
            raise ValueError("Δεν δόθηκαν πεδία προς ενημέρωση")

        ln = clean.get("last_name", p.last_name)
        fn = clean.get("first_name", p.first_name)
        fa = clean.get("father_name", p.father_name)
        mo = clean.get("mother_name", p.mother_name)
        # Μορφή & υποχρεωτικά πεδία στην ΤΕΛΙΚΗ ταυτότητα: μια μερική διόρθωση
        # δεν επιτρέπεται να αφήσει άκυρο ΑΦΜ/ΑΜΚΑ/ΑΜΑ ή κενό υποχρεωτικό πεδίο.
        constraints.check_person_identity(
            ln,
            fn,
            fa,
            mo,
            clean.get("afm", p.afm),
            clean.get("amka", p.amka),
            clean.get("ama", p.ama),
        )
        constraints.check_web_e3n_identity(
            clean.get("sex", p.sex),
            clean.get("nationality_code", p.nationality_code),
            clean.get("identity_type", p.identity_type),
            clean.get("identity_number", p.identity_number),
            clean.get("identity_issuer", p.identity_issuer),
            clean.get("identity_issue_date", p.identity_issue_date),
            clean.get("identity_expiry_date", p.identity_expiry_date),
        )
        constraints.check_web_e3n_person_details(
            clean.get("marital_status", p.marital_status),
            clean.get("nationality_code", p.nationality_code),
            clean.get("res_permit_inst", p.res_permit_inst),
            clean.get("res_permit_inst_type", p.res_permit_inst_type),
            clean.get("res_permit_inst_number", p.res_permit_inst_number),
            clean.get("res_permit_inst_expiry_date", p.res_permit_inst_expiry_date),
            clean.get("res_permit_ap", p.res_permit_ap),
            clean.get("res_permit_ap_type", p.res_permit_ap_type),
            clean.get("res_permit_ap_number", p.res_permit_ap_number),
            clean.get("res_permit_ap_expiry_date", p.res_permit_ap_expiry_date),
            clean.get("res_permit_visa", p.res_permit_visa),
            clean.get("res_permit_visa_number", p.res_permit_visa_number),
            clean.get("res_permit_visa_from", p.res_permit_visa_from),
            clean.get("res_permit_visa_to", p.res_permit_visa_to),
            clean.get("education_level", p.education_level),
        )
        # Το κλειδί ταυτότητας κρατά την (αμετάβλητη εδώ) ημ. γέννησης
        constraints.check_person_unique(
            self.proj.persons.values(),
            clean.get("afm", p.afm),
            clean.get("amka", p.amka),
            constraints.identity_key(ln, fn, fa, p.birth_date),
            exclude_person_id=person_id,
        )
        self.store.append(person_id, PersonDetailsCorrected(person_id, clean))
