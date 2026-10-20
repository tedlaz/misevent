"""
Client για το Web API (REST) του ΠΣ ΕΡΓΑΝΗ ΙΙ.

Περιβάλλοντα:
- Δοκιμαστικό: https://trialv2eservices.yeka.gr/WebservicesAPI/Api
- Παραγωγικό:  https://eservices.yeka.gr/WebservicesAPI/Api

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
  E5_LIXI      Ψηφιακή Αναγγελία Λήξης: WebE5N (οικειοθελής αποχώρηση) ή
               WebE7N (λήξη ορισμένου χρόνου)· η καταγγελία ΔΕΝ υπάρχει στο API

ΠΡΟΣΟΧΗ: Με simulate=True (προεπιλογή) ΔΕΝ γίνεται καμία κλήση δικτύου·
ο client παράγει το πλήρες payload και «εικονικό» αριθμό πρωτοκόλλου,
ώστε η ροή της εφαρμογής να είναι πλήρης. Για πραγματική υποβολή:
ErganiClient(simulate=False, environment="trial"/"production",
username=..., password=...) — τα ακριβή JSON σχήματα ανά έντυπο πρέπει
να επαληθευτούν με τα επίσημα XSD/οδηγίες του ΠΣ ΕΡΓΑΝΗ πριν τη χρήση.
"""

import base64
import hashlib
import json
import uuid
from datetime import UTC, date, datetime

# Το περιβάλλον (και τα στοιχεία σύνδεσης) ορίζεται ΑΝΑ ΕΤΑΙΡΕΙΑ από
# διαχειριστή — βλ. webapp/ergani_credentials.py. Προεπιλογή: simulate.
BASE_URLS = {
    "trial": "https://trialv2eservices.yeka.gr/WebservicesAPI/Api",
    "production": "https://eservices.yeka.gr/WebservicesAPI/Api",
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
    def submit(self, doc_type: str, payload: dict, endpoint: str = "") -> dict:
        """Υποβάλλει έντυπο. Επιστρέφει {status, reference, payload}.
        `endpoint`: ο τύπος του API (`/Documents/{endpoint}`), όταν διαφέρει από
        το εσωτερικό `doc_type` (π.χ. «WTO» -> WTOWeek / WTODaily)."""
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
        res = self._post(f"/Documents/{endpoint or doc_type}", payload)
        return {
            "status": "submitted",
            "reference": str(res.get("protocol") or res.get("id") or ""),
            "payload": body,
        }


# ------------------------------------------------- Payload builders
# Πεδία WebE3N με αρχείο PDF σε base64.
FILE_FIELDS = ("f_file", "f_file_symbash", "f_foreign_file", "f_young_file")


def _b64(content: bytes | None) -> str:
    return base64.b64encode(content).decode("ascii") if content else ""


def redact_files(payload: dict) -> dict:
    """Αντίγραφο του payload με τα base64 PDF -> «PDF 340 KB · sha256 ab12…».
    Για την καταγραφή της υποβολής (event) και την προεπισκόπηση: το έγγραφο
    ζει ήδη στη βάση εγγράφων — ούτε δεύτερο αντίγραφο στο log, ούτε MB στην οθόνη."""
    if isinstance(payload, list):
        return [redact_files(x) for x in payload]
    if not isinstance(payload, dict):
        return payload
    out = {}
    for key, value in payload.items():
        if key in FILE_FIELDS and value:
            raw = base64.b64decode(value)
            value = (f"PDF {max(1, round(len(raw) / 1024))} KB · "
                     f"sha256 {hashlib.sha256(raw).hexdigest()[:12]}")
        out[key] = redact_files(value)
    return out


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


def _identity_block(company, emp, profile, branch) -> dict:
    """Παράρτημα + στοιχεία προσώπου: ΚΟΙΝΟ πρώτο τμήμα των WebE3N / WebE5N /
    WebE7N (ίδια πεδία, ίδια σειρά — η σειρά δεσμεύει τη μετατροπή σε XML)."""
    return {
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
    }


def build_web_e3n(
    company,
    emp,
    profile,
    schedule: dict | None,
    first_day_start: str | None = None,
    first_day_end: str | None = None,
    branch=None,
    files: dict | None = None,
) -> dict:
    """Payload αναγγελίας πρόσληψης κατά τη δομή ``WebE3N.json``.

    ``files``: {πεδίο WebE3N (`foreign_file`/`young_file`): bytes PDF} — μπαίνουν
    ως base64 στα αντίστοιχα `f_*` (βλ. ``FILE_FIELDS``).

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
        **_identity_block(company, emp, profile, branch),
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
        "f_foreign_file": _b64((files or {}).get("foreign_file")),
        "f_young_file": _b64((files or {}).get("young_file")),
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


# Ψηφιακή Οργάνωση Χρόνου Εργασίας — σχήματα από το ίδιο το ΕΡΓΑΝΗ
# (GET /Documents/WTOWeek και /Documents/WTODaily· αντίγραφα στο data/).
# Οι τύποι αναλυτικής εγγραφής (`f_type`) είναι η λίστα «WorkTimeType» της
# υπηρεσίας EX_BASE_03: ΕΡΓ εργασία, ΤΗΛ τηλεργασία, ΑΝ ανάπαυση/ρεπό.
WTO_DAY = {"SUN": "0", "MON": "1", "TUE": "2", "WED": "3", "THU": "4",
           "FRI": "5", "SAT": "6"}


def _hm(minutes: int) -> str:
    """Λεπτά -> ΩΩ:ΛΛ (μετά τα μεσάνυχτα: modulo 24ω, π.χ. 22:00-06:00)."""
    m = minutes % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def _wto_analytics(day_text) -> dict:
    """Τα τμήματα ΜΙΑΣ ημέρας ως αναλυτικές εγγραφές· κενό = ανάπαυση/ρεπό."""
    from misevent.rules.constraints import parse_day_schedule

    segs = parse_day_schedule(day_text)
    rows = [{"f_type": "ΤΗΛ" if seg["telework"] else "ΕΡΓ",
             "f_from": _hm(seg["start"]), "f_to": _hm(seg["end"])} for seg in segs]
    return {"ErgazomenosWTOAnalytics": rows or [{"f_type": "ΑΝ", "f_from": "", "f_to": ""}]}


def _wto(emp, from_date: date, to_date: date | None, rows: list) -> dict:
    """Κοινό περίβλημα WTOWeek/WTODaily: ΜΙΑ δήλωση, ένας εργαζόμενος."""
    branch = getattr(getattr(emp, "insurance_profile", None), "branch_no", 0) or 0
    person = {"f_afm": emp.afm, "f_eponymo": emp.last_name, "f_onoma": emp.first_name}
    return {"WTOS": {"WTO": [{
        "f_aa_pararthmatos": str(branch),
        "f_rel_protocol": "",
        "f_rel_date": "",
        "f_comments": "",
        "f_from_date": _d_gr(from_date),
        "f_to_date": _d_gr(to_date),
        "Ergazomenoi": {"ErgazomenoiWTO": [{**person, **row} for row in rows]},
    }]}}


def build_wto(
    company, emp, schedule: dict, effective_from: date, end_date: date | None = None
) -> dict:
    """WTOWeek — Σταθερό Εβδομαδιαίο: μία εγγραφή ανά ημέρα της εβδομάδας
    (`f_day`, 0 = Κυριακή), ΚΑΙ τα ρεπό ρητά. `end_date` κενό = δεν λήγει."""
    return _wto(emp, effective_from, end_date, [
        {"f_day": WTO_DAY[day], "ErgazomenosAnalytics": _wto_analytics(schedule.get(day))}
        for day in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
    ])


def build_wto_variable(company, emp, days: dict, week_start: date) -> dict:
    """WTODaily — Μεταβλητό (ημερήσιο): `days` = {"2026-07-14": "09:00-17:00", ...}
    (κενό ωράριο = ρεπό). Περίοδος = από την πρώτη έως την τελευταία ημέρα."""
    dates = sorted(date.fromisoformat(d) for d in days)
    return _wto(emp, dates[0] if dates else week_start, dates[-1] if dates else week_start, [
        {"f_date": _d_gr(d), "ErgazomenosAnalytics": _wto_analytics(days[d.isoformat()])}
        for d in dates
    ])


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


# Ψηφιακή Αναγγελία Λήξης — σχήματα από το ίδιο το ΕΡΓΑΝΗ (data/ergani_schemas/).
# Η ΚΑΤΑΓΓΕΛΙΑ σύμβασης αορίστου ΔΕΝ βρέθηκε ως έντυπο του API (μόνο portal).
def _exit_common(emp, schedule) -> dict:
    gross = emp.daily_wage if emp.pay_type == "daily" else emp.monthly_gross
    return {
        "xaraktirismos": "1" if emp.pay_type == "monthly" else "0",
        "kathestos": employment_status(emp.weekly_hours, schedule),
        "apodoxes": _decimal_gr(gross, 2),
    }


def _specialty(profile) -> str:
    return str(getattr(profile, "ergani_specialty_code", "")
               or getattr(profile, "specialty_code", "") or "")


def build_web_e5n(company, emp, profile, termination_date: date, branch=None,
                  resignation_pdf: bytes | None = None) -> dict:
    """WebE5N — Οικειοθελής αποχώρηση. `resignation_pdf`: η δήλωση με
    υπογραφή του εργαζομένου (ΥΠΟΧΡΕΩΤΙΚΗ κατά τον οδηγό ΕΡΓΑΝΗ ΙΙ)."""
    c = _exit_common(emp, getattr(emp, "schedule", None))
    form = {
        **_identity_block(company, emp, profile, branch),
        "f_xaraktirismos": c["xaraktirismos"],
        "f_sxeshapasxolisis": str(getattr(emp, "employment_relationship", "0") or "0"),
        "f_orismenou_apo": _d_gr(getattr(emp, "fixed_term_from", None)),
        "f_orismenou_ews": _d_gr(getattr(emp, "fixed_term_to", None)),
        "f_kathestosapasxolisis": c["kathestos"],
        "f_eidikothta": _specialty(profile),
        "f_proslipsidate": _d_gr(emp.hire_date),
        "f_apoxwrisidate": _d_gr(termination_date),
        "f_apodoxes": c["apodoxes"],
        "f_comments": "",
        "f_file": _b64(resignation_pdf),
        "f_foreign_file": "",
        "f_young_file": "",
    }
    return {"AnaggeliesE5N": {"AnaggeliaE5N": [form]}}


def build_web_e7n(company, emp, profile, termination_date: date, branch=None) -> dict:
    """WebE7N — Λύση σύμβασης ορισμένου χρόνου λόγω λήξης του συμφωνημένου
    χρόνου (`f_logosperatosis` 0). Χωρίς συνημμένο (οδηγός ΕΡΓΑΝΗ ΙΙ)."""
    c = _exit_common(emp, getattr(emp, "schedule", None))
    form = {
        **_identity_block(company, emp, profile, branch),
        "f_xaraktirismos": c["xaraktirismos"],
        "f_sxeshapasxolisis": "1",
        "f_kathestosapasxolisis": c["kathestos"],
        "f_oros": "0",
        "f_eidikothta": _specialty(profile),
        "f_apodoxes": c["apodoxes"],
        "f_proslipsidate": _d_gr(emp.hire_date),
        "f_lixisymbashdate": _d_gr(getattr(emp, "fixed_term_to", None) or termination_date),
        "f_apolysisdate": _d_gr(termination_date),
        "f_comments": "",
        "f_logosperatosis": "0",
        "f_logosperatosiscomments": "",
        "f_foreign_file": "",
        "f_young_file": "",
    }
    return {"AnaggeliesE7N": {"AnaggeliaE7N": [form]}}
