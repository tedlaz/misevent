"""
Client για το Web API (REST) του ΠΣ ΕΡΓΑΝΗ ΙΙ.

Περιβάλλοντα:
- Εργάνη ΙΙ δοκιμαστικό: https://trialv2eservices.yeka.gr)
- Παραγωγικό:            https://eservices.yeka.gr/WebservicesAPI/api

Ροή (κατά την τεκμηρίωση «Ergani Web API για εργοδότες»):
  POST /Authentication          {UserName, Password, UserType: "01"}
                                -> accessToken (JWT) + refreshToken
  POST /RefreshAuthentication   ανανέωση token
  POST /Documents/{doc}         υποβολή εντύπου (JSON/XML κατά τα XSD
                                του ΠΣ ΕΡΓΑΝΗ)
  POST /Logout

Έντυπα που υποστηρίζει ο client:
  WebE3N       Αναγγελία Πρόσληψης,
               με τα υποχρεωτικά πεδία Οργάνωσης Χρόνου Εργασίας
  WTO          Ψηφιακή Οργάνωση Χρόνου Εργασίας — Σταθερό Εβδομαδιαίο
  E4_METAVOLI  Δήλωση Μεταβολής Στοιχείων Εργασιακής Σχέσης
               (αποδοχές / ωράριο / μερική απασχόληση)
  E5_LIXI      Ψηφιακή Αναγγελία Λήξης (οικειοθελής αποχώρηση /
               καταγγελία / λήξη σύμβασης)

ΠΡΟΣΟΧΗ: Με simulate=True (προεπιλογή) ΔΕΝ γίνεται καμία κλήση δικτύου·
ο client παράγει το πλήρες payload και «εικονικό» αριθμό πρωτοκόλλου,
ώστε η ροή της εφαρμογής να είναι πλήρης. Για πραγματική υποβολή:
ErganiClient(simulate=False, environment="trial"/"production",
username=..., password=...) — τα ακριβή JSON σχήματα ανά έντυπο πρέπει
να επαληθευτούν με τα επίσημα XSD/οδηγίες του ΠΣ ΕΡΓΑΝΗ πριν τη χρήση.
"""

import json
import uuid
from datetime import UTC, date, datetime

BASE_URLS = {
    "trial": "https://trialv2eservices.yeka.gr/WebservicesAPI/api",
    "production": "https://eservicesPROSOXI.yeka.gr/WebservicesAPI/api",
}

DOC_LABELS = {
    "WebE3N": "Αναγγελία Πρόσληψης",
    "WTO": "Ψηφιακή Οργάνωση Χρόνου Εργασίας (Σταθερό Εβδομαδιαίο)",
    "E4_METAVOLI": "Δήλωση Μεταβολής Στοιχείων Εργασιακής Σχέσης",
    "E5_LIXI": "Ψηφιακή Αναγγελία Λήξης Εργασίας",
}


class ErganiError(RuntimeError):
    pass


class ErganiClient:
    def __init__(
        self,
        username: str = "",
        password: str = "",
        environment: str = "trial",
        simulate: bool = True,
        timeout: int = 30,
    ):
        if environment not in BASE_URLS:
            raise ValueError("environment: trial ή production")
        self.username = username
        self.password = password
        self.environment = environment
        self.simulate = simulate
        self.timeout = timeout
        self.base = BASE_URLS[environment]
        self._token: str | None = None

    # ------------------------------------------------------------ HTTP
    def _post(self, path: str, payload: dict, auth: bool = True) -> dict:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                **(
                    {"Authorization": f"Bearer {self._token}"}
                    if auth and self._token
                    else {}
                ),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise ErganiError(
                f"ΕΡΓΑΝΗ {path}: HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except Exception as exc:
            raise ErganiError(f"ΕΡΓΑΝΗ {path}: {exc}") from exc

    def authenticate(self):
        if self.simulate:
            self._token = "SIMULATED"
            return
        res = self._post(
            "/Authentication",
            {"UserName": self.username, "Password": self.password, "UserType": "01"},
            auth=False,
        )
        self._token = res.get("accessToken")
        if not self._token:
            raise ErganiError("Αποτυχία authentication στο ΕΡΓΑΝΗ")

    # ------------------------------------------------------ Υποβολή
    def submit(self, doc_type: str, payload: dict) -> dict:
        """Υποβάλλει έντυπο. Επιστρέφει {status, reference, payload}."""
        if doc_type not in DOC_LABELS:
            raise ValueError(f"Άγνωστο έντυπο {doc_type}")
        body = json.dumps(payload, ensure_ascii=False, default=str)
        if self.simulate:
            ref = (
                f"ΨΣ-{doc_type}-{datetime.now(UTC):%Y%m%d%H%M%S}-"
                f"{uuid.uuid4().hex[:6].upper()}"
            )
            return {"status": "simulated", "reference": ref, "payload": body}
        if self._token is None:
            self.authenticate()
        res = self._post(f"/Documents/{doc_type}", payload)
        return {
            "status": "submitted",
            "reference": str(res.get("protocol") or res.get("id") or ""),
            "payload": body,
        }


# ------------------------------------------------- Payload builders
def _d(x: date) -> str:
    return x.isoformat()


def _d_gr(x: date | None) -> str:
    return x.strftime("%d/%m/%Y") if x else ""


def _decimal_gr(value: float, digits: int) -> str:
    return f"{float(value or 0):.{digits}f}".replace(".", ",")


def _first_day_hours(emp, schedule: dict | None) -> tuple[str, str]:
    """Επιστρέφει την πρώτη βάρδια της ημέρας πρόσληψης ως HH:MM."""
    if not emp.hire_date:
        return "09:00", "17:00"
    day = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")[emp.hire_date.weekday()]
    shifts = (schedule or {}).get(day, "")
    if isinstance(shifts, (list, tuple)):
        shifts = shifts[0] if shifts else ""
    shift = str(shifts or "")
    if "-" not in shift:
        return "09:00", "17:00"
    start, end = shift.split("-", 1)
    return start.strip()[:5], end.strip()[:5]


def _hourly_pay(emp) -> float:
    """Συμβατικό ωρομίσθιο για το πεδίο f_hour_apodoxes."""
    if emp.pay_type == "daily":
        return float(emp.daily_wage or 0) * 6 / float(emp.weekly_hours or 40)
    # Μηνιαίος μισθός / 25 ημέρες × 6 ημέρες / εβδομαδιαίες ώρες.
    return float(emp.monthly_gross or 0) * 6 / (25 * float(emp.weekly_hours or 40))


def employment_status(weekly_hours: float, schedule: dict | None) -> str:
    """Καθεστώς απασχόλησης WebE3N από ώρες και ημέρες του template.

    0 = πλήρης, 1 = μερική, 2 = εκ περιτροπής. Η πλήρης απασχόληση
    υπερισχύει του πλήθους ημερών όταν οι εβδομαδιαίες ώρες είναι >= 40.
    """
    if float(weekly_hours or 0) >= 40:
        return "0"

    day_codes = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")

    def has_work(value) -> bool:
        if isinstance(value, (list, tuple)):
            return any(str(shift or "").strip() for shift in value)
        return bool(str(value or "").strip())

    work_days = sum(has_work((schedule or {}).get(day)) for day in day_codes)
    return "2" if work_days < 5 else "1"


def build_web_e3n(
    company,
    emp,
    profile,
    schedule: dict | None,
    first_day_start: str | None = None,
    first_day_end: str | None = None,
    branch=None,
) -> dict:
    """Payload αναγγελίας πρόσληψης κατά τη δομή ``WebE3N.json``.

    Τα στοιχεία που δεν τηρούνται ακόμη στο μητρώο αποδίδονται με την κενή
    τιμή που επιτρέπει η προδιαγραφή. Το payload παραμένει πλήρες, ώστε η
    προεπισκόπηση να δείχνει ακριβώς τι πρόκειται να σταλεί.
    """
    inferred_start, inferred_end = _first_day_hours(emp, schedule)
    first_day_start = first_day_start or inferred_start
    first_day_end = first_day_end or inferred_end
    gross = emp.daily_wage if emp.pay_type == "daily" else emp.monthly_gross
    auxiliary_scheme = str(
        getattr(profile, "auxiliary_scheme", "") if profile else ""
    ).lower()
    # Το WebE3N απαιτεί τουλάχιστον μία επιλογή. Η κύρια ασφάλιση και η
    # απουσία χωριστού επικουρικού φορέα αντιστοιχούν στον e-ΕΦΚΑ (001),
    # ενώ η ρητή υπαγωγή στο ΤΕΚΑ αντιστοιχεί στο 002.
    auxiliary_code = "002" if auxiliary_scheme == "teka" else "001"
    auxiliary = [{"f_kod_epikourikis": auxiliary_code}]

    form = {
        "f_aa_pararthmatos": str(getattr(profile, "branch_no", 0) if profile else 0),
        "f_rel_protocol": "",
        "f_rel_date": "",
        "f_ypiresia_sepe": str(
            getattr(branch, "sepe_code", "") or getattr(company, "sepe_code", "") or ""
        ),
        "f_ypiresia_oaed": str(
            getattr(branch, "oaed_code", "") or getattr(company, "dypa_code", "") or ""
        ),
        "f_kad_pararthmatos": str(
            getattr(branch, "kad_code", "") or getattr(profile, "kad", "") or ""
        ),
        "f_kallikratis_pararthmatos": str(
            getattr(branch, "kallikratis_code", "")
            or getattr(company, "kallikratis_code", "")
            or ""
        ),
        "f_eponymo": emp.last_name,
        "f_onoma": emp.first_name,
        "f_onoma_patros": emp.father_name,
        "f_onoma_mitros": emp.mother_name,
        "f_birthdate": _d_gr(emp.birth_date),
        "f_sex": str(getattr(emp, "sex", "") or ""),
        "f_yphkoothta": str(getattr(emp, "nationality_code", "") or ""),
        "f_typos_taytothtas": str(getattr(emp, "identity_type", "") or ""),
        "f_ar_taytothtas": str(getattr(emp, "identity_number", "") or ""),
        "f_ekdousa_arxh": str(getattr(emp, "identity_issuer", "") or ""),
        "f_date_ekdosis": _d_gr(getattr(emp, "identity_issue_date", None)),
        "f_date_ekdosis_lixi": _d_gr(getattr(emp, "identity_expiry_date", None)),
        "f_res_permit_inst": getattr(emp, "res_permit_inst", "") or None,
        "f_res_permit_inst_type": getattr(emp, "res_permit_inst_type", "") or "",
        "f_res_permit_inst_ar": getattr(emp, "res_permit_inst_number", "") or "",
        "f_res_permit_inst_lixi": _d_gr(
            getattr(emp, "res_permit_inst_expiry_date", None)
        ),
        "f_res_permit_ap": getattr(emp, "res_permit_ap", "") or None,
        "f_res_permit_ap_type": getattr(emp, "res_permit_ap_type", "") or "",
        "f_res_permit_ap_ar": getattr(emp, "res_permit_ap_number", "") or "",
        "f_res_permit_ap_lixi": _d_gr(getattr(emp, "res_permit_ap_expiry_date", None)),
        "f_res_permit_visa": getattr(emp, "res_permit_visa", "") or None,
        "f_res_permit_visa_ar": getattr(emp, "res_permit_visa_number", "") or "",
        "f_res_permit_visa_from": _d_gr(getattr(emp, "res_permit_visa_from", None)),
        "f_res_permit_visa_to": _d_gr(getattr(emp, "res_permit_visa_to", None)),
        "f_marital_status": str(getattr(emp, "marital_status", "0") or "0"),
        "f_arithmos_teknon": str(emp.children),
        "f_afm": emp.afm,
        "f_doy": str(getattr(emp, "doy", "") or ""),
        "f_amika": emp.ama,
        "f_amka": emp.amka,
        "f_code_anergias": "",
        "f_ar_vivliou_anilikou": "",
        "f_epipedo_morfosis": str(getattr(emp, "education_level", "") or ""),
        "f_proslipsidate": _d_gr(emp.hire_date),
        "f_proslipsitime": first_day_start,
        "f_apoxwrisitime": first_day_end,
        "f_week_hours": _decimal_gr(emp.weekly_hours, 1),
        "f_eidikothta": str(
            getattr(profile, "ergani_specialty_code", "")
            or getattr(profile, "specialty_code", "")
            or ""
        ),
        "f_eidikothta_anal": str(
            getattr(profile, "ergani_specialty_description", "")
            or getattr(profile, "specialty_name", "")
            or ""
        ),
        # ΣΥΝΟΛΙΚΗ προϋπηρεσία: τριετίες + παγωμένος χρόνος 2012-2023 (το
        # ΕΡΓΑΝΗ θέλει τον χρόνο εργασίας, όχι μόνο ό,τι μετρά στις τριετίες)
        "f_proipiresia": str(
            int(
                (emp.service_years or 0)
                + (getattr(emp, "frozen_service_years", 0) or 0)
            )
        ),
        "f_apodoxes": _decimal_gr(gross, 2),
        "f_hour_apodoxes": _decimal_gr(_hourly_pay(emp), 2),
        "f_sxeshapasxolisis": str(getattr(emp, "employment_relationship", "0") or "0"),
        "f_orismenou_apo": _d_gr(getattr(emp, "fixed_term_from", None)),
        "f_orismenou_ews": _d_gr(getattr(emp, "fixed_term_to", None)),
        "f_kathestosapasxolisis": employment_status(emp.weekly_hours, schedule),
        "f_xaraktirismos": "1" if emp.pay_type == "monthly" else "0",
        "f_special_case": None,
        "f_responsible_position": None,
        "f_working_time_digital_organization": "1",
        "f_full_employment_hours": "40,0",
        "f_week_days": str(emp.weekly_system),
        "f_euelikto_wrario_minutes": str(
            getattr(emp, "flexible_arrival_minutes", "0") or "0"
        ),
        "f_working_card": str(getattr(emp, "working_card", "0") or "0"),
        "f_dialeimma_minutes": str(getattr(emp, "break_minutes", "0") or "0"),
        "f_dialeimma_entos_wrariou": str(
            getattr(emp, "break_within_schedule", "0") or "0"
        ),
        "f_topothetisioaed": str(getattr(emp, "oaed_placement", "0") or "0"),
        "f_programaoaed": str(getattr(emp, "oaed_program", "") or ""),
        "f_replaceprograma": (str(getattr(emp, "oaed_replacement", "") or "") or None),
        "f_replaceprograma_afm": str(getattr(emp, "oaed_replaced_afm", "") or ""),
        "f_replaceprograma_amka": str(getattr(emp, "oaed_replaced_amka", "") or ""),
        "f_trial_period": str(getattr(emp, "trial_period", "0") or "0"),
        "f_trial_date_to": _d_gr(getattr(emp, "trial_date_to", None)),
        "f_basics_acceptance": "1",
        "f_file": "",
        "f_file_symbash": "",
        "f_comments": "",
        "f_foreign_file": "",
        "f_young_file": "",
        "f_xronos_katavolis_apodoxon": "ΜΗΝΙΑΙΩΣ",
        "f_ipoxreotiki_katartisi": "0",
        "f_efarmoste_sillogiki_simbasi": "0",
        "f_efarmoste_sillogiki_simbasi_comments": "",
        # Οι κωδικοί WebE3N είναι ανεξάρτητοι από τα ΚΠΚ της ΑΠΔ.
        "f_kyria_asfalisi": "001",
        "EpikourikiSelections": {"EpikourikiSelectionsE3N": auxiliary},
        # Η σειρά είναι δεσμευτική κατά τη μετατροπή JSON -> XML του WebE3N:
        # κύρια ασφάλιση, επικουρικές επιλογές και μετά πρόσθετες παροχές.
        "f_prosthetes_asfalistikes": "",
        "f_mh_provlepsimo_programma": (
            "1" if getattr(emp, "schedule_mode", "fixed") == "variable" else "0"
        ),
        "f_paraggelia_hmeres_hours": "",
        "f_paraggelia_min_notification": "",
        "f_paraggelia_notes": "",
        "f_topos_ergasias": "0",
        "f_topos_ergasias_comment": "",
    }
    return {
        "AnaggeliesE3N": {
            "AnaggeliaE3N": [form],
        }
    }


def build_wto(
    company, emp, schedule: dict, effective_from: date, end_date: date | None = None
) -> dict:
    """Ψηφιακή Οργάνωση Χρόνου Εργασίας — Σταθερό Εβδομαδιαίο.

    `end_date` κενό = δεν λήγει· διαφορετικά το πρόγραμμα ισχύει έως εκείνη
    την ημερομηνία (μετά δεν υπάρχει πρόγραμμα)."""
    return {
        "Entypo": "WTO",
        "Ergodotis": {"AME": company.ame, "AFM": company.afm},
        "Ergazomenos": {
            "AFM": emp.afm,
            "Eponymo": emp.last_name,
            "Onoma": emp.first_name,
        },
        "IsxysApo": _d(effective_from),
        "IsxysEws": _d(end_date) if end_date else "",
        "Typos": "ΣΤΑΘΕΡΟ_ΕΒΔΟΜΑΔΙΑΙΟ",
        "Programma": schedule,  # {"MON": "09:00-17:00", ...}
    }


def build_wto_variable(company, emp, days: dict, week_start: date) -> dict:
    """Ψηφιακή Οργάνωση Χρόνου Εργασίας — ΜΕΤΑΒΛΗΤΟ (ημερήσιο) για μία εβδομάδα.

    `days`: {"2026-07-14": "09:00-17:00", ...} — οι 7 ημέρες της εβδομάδας
    (κενό ωράριο = ρεπό). Υποβάλλεται με ισχύ την πρώτη ημέρα της εβδομάδας."""
    return {
        "Entypo": "WTO",
        "Ergodotis": {"AME": company.ame, "AFM": company.afm},
        "Ergazomenos": {
            "AFM": emp.afm,
            "Eponymo": emp.last_name,
            "Onoma": emp.first_name,
        },
        "IsxysApo": _d(week_start),
        "Typos": "ΜΕΤΑΒΛΗΤΟ_ΕΒΔΟΜΑΔΙΑΙΟ",
        "Programma": dict(days),  # {"2026-07-14": "09:00-17:00", ...}
    }


def build_metavoli(company, emp, effective_from: date) -> dict:
    """Δήλωση Μεταβολής Στοιχείων Εργασιακής Σχέσης (αποδοχές/ωράριο)."""
    return {
        "Entypo": "E4_METAVOLI",
        "Ergodotis": {"AME": company.ame, "AFM": company.afm},
        "Ergazomenos": {
            "AFM": emp.afm,
            "Eponymo": emp.last_name,
            "Onoma": emp.first_name,
        },
        "IsxysApo": _d(effective_from),
        "NeaStoixeia": {
            "TyposApasxolisis": ("ΜΕΡΙΚΗ" if emp.weekly_hours < 40 else "ΠΛΗΡΗΣ"),
            "EvdomadiaiesOres": emp.weekly_hours,
            "TyposAmoivis": ("ΗΜΕΡΟΜΙΣΘΙΟ" if emp.pay_type == "daily" else "ΜΙΣΘΟΣ"),
            "MiktesApodoxes": (
                emp.daily_wage if emp.pay_type == "daily" else emp.monthly_gross
            ),
        },
    }


def build_lixi(company, emp, termination_date: date, reason: str) -> dict:
    """Ψηφιακή Αναγγελία Λήξης (οικειοθελής / καταγγελία / λήξη)."""
    return {
        "Entypo": "E5_LIXI",
        "Ergodotis": {"AME": company.ame, "AFM": company.afm},
        "Ergazomenos": {
            "AFM": emp.afm,
            "Eponymo": emp.last_name,
            "Onoma": emp.first_name,
            "AMKA": emp.amka,
        },
        "ImLixis": _d(termination_date),
        "Aitia": reason or "ΟΙΚΕΙΟΘΕΛΗΣ_ΑΠΟΧΩΡΗΣΗ",
    }
