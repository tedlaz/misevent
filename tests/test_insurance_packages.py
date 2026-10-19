"""Σύνθετα ΚΠΚ και δηλωτική επιλογή πακέτων ανά ασφαλιστικό καθεστώς."""

from datetime import date

import _bootstrap  # noqa: F401
import pytest

from misevent.app.services import PayrollService
from misevent.domain.projections import Projections, profile_coverages
from misevent.persistence.database import Database

WEEK = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}


def service():
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)
    svc.confirm_default_parameters()
    svc.register_company(ame="1234567890", afm="999888771", legal_name="ΔΟΚΙΜΗ ΑΕ")
    svc.define_branch(0)
    # Τα ΚΠΚ έρχονται από την ΑΡΧΙΚΟΠΟΙΗΣΗ (data/init_parameters.json) που
    # έκανε η `confirm_default_parameters` παραπάνω — μία πηγή αλήθειας.
    assert {"0101", "0103"} <= set(svc.known_kpks("efka_main"))
    assert "4169" in svc.known_kpks("teka")
    with open("data/eidikotites.txt", "rb") as f:
        result = svc.import_specialties(f.read())
    assert result["errors"] == []
    assert "ΠΩΛΗΤΕΣ" in proj.specialties
    return store, proj, svc


def hire(svc, i, auxiliary, pron_choice="no"):
    people = (
        ("111222336", "05059012343", date(1990, 5, 5)),
        ("222333440", "03039012343", date(1990, 3, 3)),
        ("444555667", "04049112347", date(1991, 4, 4)),
    )
    afm, amka, born = people[i]
    pid = svc.register_person(
        last_name=f"ΔΟΚΙΜΗ{i}", first_name="ΝΙΚΟΣ", father_name="Γ",
        mother_name="Μ", birth_date=born, afm=afm, amka=amka,
        ama=f"12345678{i}",
    )
    eid = svc.start_employment(
        pid, date(2026, 1, 1), pay_type="monthly", monthly_gross=1500,
        weekly_hours=40, weekly_pattern=WEEK,
    )
    svc.assign_specialty(
        eid, "ΠΩΛΗΤΕΣ", auxiliary_scheme=auxiliary, pron_choice=pron_choice,
    )
    svc.submit_hire_e3(eid)
    svc.submit_hire_wto(eid)
    return eid


def test_0101_and_0103_plus_4169_are_equivalent():
    _store, proj, svc = service()
    efka_id = hire(svc, 0, "efka")
    teka_id = hire(svc, 1, "teka")

    efka = profile_coverages(proj.employments[efka_id].insurance_profile)
    teka = profile_coverages(proj.employments[teka_id].insurance_profile)
    assert [(c["fund"], c["kpk"]) for c in efka] == [("efka_main", "0101")]
    assert [(c["fund"], c["kpk"]) for c in teka] == [
        ("efka_main", "0103"), ("teka", "4169")
    ]

    er = svc.contribution_rates(proj.employments[efka_id].insurance_profile,
                                date(2026, 1, 31))
    tr = svc.contribution_rates(proj.employments[teka_id].insurance_profile,
                                date(2026, 1, 31))
    assert (er.employee, er.employer) == pytest.approx((0.1337, 0.2179))
    assert (tr.employee, tr.employer) == pytest.approx((0.1337, 0.2179))

    svc.approve_attendance(2026, 1)
    svc.run_monthly_payroll(2026, 1)
    apd = svc.build_apd_rows(2026, 1)
    efka_rows = [row for _info, row in apd["efka"] if row.earnings_type == "001"]
    teka_rows = [row for _info, row in apd["teka"] if row.earnings_type == "001"]
    assert sorted(r.kpk for r in efka_rows) == ["0101", "0103"]
    assert [r.kpk for r in teka_rows] == ["4169"]
    assert next(r for r in efka_rows if r.kpk == "0103").special_case == "09"


def test_old_and_new_tapit_select_their_configured_welfare_package():
    _store, proj, svc = service()
    eid = hire(svc, 2, "efka", pron_choice="old")
    covs = profile_coverages(proj.employments[eid].insurance_profile)
    assert [(c["fund"], c["kpk"]) for c in covs] == [
        ("efka_main", "0101"), ("efka_welfare", "0026")
    ]
    rates = svc.contribution_rates(proj.employments[eid].insurance_profile,
                                   date(2026, 1, 31))
    assert (rates.employee, rates.employer) == pytest.approx((0.1737, 0.2579))

    svc.approve_attendance(2026, 1)
    svc.run_monthly_payroll(2026, 1)
    efka_rows = [
        row for _info, row in svc.build_apd_rows(2026, 1)["efka"]
        if row.earnings_type == "001"
    ]
    main = next(row for row in efka_rows if row.kpk == "0101")
    welfare = next(row for row in efka_rows if row.kpk == "0026")
    assert main.special_case == "10"
    assert welfare.special_case == "00"

    # Η παραμετροποίηση περιέχει πλέον χωριστό ΚΠΚ νέου ασφαλισμένου.
    svc.assign_specialty(
        eid, "ΠΩΛΗΤΕΣ", auxiliary_scheme="efka", pron_choice="new",
    )
    new_profile = proj.employments[eid].insurance_profile
    assert [(c["fund"], c["kpk"]) for c in profile_coverages(new_profile)] == [
        ("efka_main", "0101"), ("efka_welfare", "0024")
    ]
    new_rates = svc.contribution_rates(new_profile, date(2026, 1, 31))
    assert (new_rates.employee, new_rates.employer) == pytest.approx((0.1737, 0.2179))


def test_component_totals_are_preserved():
    _store, _proj, svc = service()
    k = svc.kpk_rates_for("efka_main", "0101", date(2026, 1, 31))
    assert [c["branch"] for c in k.components] == [
        "main_pension", "healthcare", "auxiliary", "dypa"
    ]
    assert sum(c["employee"] for c in k.components) == pytest.approx(k.employee)
    assert sum(c["employer"] for c in k.components) == pytest.approx(k.employer)



def test_inconsistent_component_analysis_is_rejected():
    _store, _proj, svc = service()
    svc.define_kpk("efka", "0998", "ΔΟΚΙΜΗ", "main")
    with pytest.raises(ValueError, match="δεν συμφωνούν"):
        svc.define_kpk_rates(
            "efka_main", "0998", date(2026, 1, 1), 0.10, 0.20,
            "main_pension=6,67/13,33",
        )


def test_rates_without_identity_are_rejected():
    """Χωρίς ΤΑΥΤΟΤΗΤΑ (ονομασία/βαθμίδα) δεν μπαίνουν ποσοστά — αλλιώς το
    ΚΠΚ δεν θα μπορούσε να μπει σε κανέναν ρόλο πακέτου."""
    _store, _proj, svc = service()
    with pytest.raises(ValueError, match="δηλώστε πρώτα την ταυτότητά του"):
        svc.define_kpk_rates(
            "efka_main", "0997", date(2026, 1, 1), 0.10, 0.20,
            "main_pension=10/20",
        )


def test_kpk_tier_must_match_the_package_role():
    """Ο ρόλος δέχεται ΜΟΝΟ ΚΠΚ της βαθμίδας του (ο κανόνας δεν επιβάλλεται
    από τη βάση — ζει στο command layer)."""
    _store, _proj, svc = service()
    with pytest.raises(ValueError, match="βαθμίδας"):
        svc.define_specialty("ΔΟΚΙΜΗ", {
            # 0103 είναι ΚΥΡΙΑ — ο MAIN_EPIK απαιτεί ΚΥΡΙΑ+ΕΠΙΚΟΥΡΙΚΗ
            "MAIN_EPIK": {"kad": "5242", "specialty_code": "532030",
                          "kpk": "0103"},
        })


def test_teka_carrier_allows_only_the_auxiliary_tier():
    """Business rule του μοντέλου: ΤΕΚΑ = μόνο επικουρική."""
    _store, _proj, svc = service()
    with pytest.raises(ValueError, match="δεν επιτρέπει βαθμίδα"):
        svc.define_kpk("teka", "4170", "ΛΑΘΟΣ", "main")


def test_specialty_offers_only_its_declared_carriers():
    _store, _proj, svc = service()
    opts = svc.specialty_options("ΤΑΜΙΕΣ")
    assert opts["carriers"] == ["efka", "teka"]
    assert opts["pron_choices"] == []  # χωρίς πρόνοια -> καμία ερώτηση
    sales = svc.specialty_options("ΠΩΛΗΤΕΣ")
    assert sales["pron"]["differentiated"] and not sales["pron"]["mandatory"]
    assert sales["pron_choices"] == ["new", "old", "no"]


def test_preview_lines_match_what_is_assigned():
    """Η προεπισκόπηση της πρόσληψης και η ανάθεση βγαίνουν από την ΙΔΙΑ
    συνάρτηση — δεν μπορούν να διαφωνήσουν."""
    _store, proj, svc = service()
    preview = svc.specialty_lines("ΠΩΛΗΤΕΣ", "teka", "old", on=date(2026, 1, 1))
    assert [(line["fund"], line["kpk"]) for line in preview["lines"]] == [
        ("efka_main", "0103"), ("teka", "4169"), ("efka_welfare", "0026")
    ]
    assert preview["employee"] == pytest.approx(0.1737)
    # ΚΑΔ/ειδικότητα επικουρικής/πρόνοιας ΑΠΟ ΤΟ ΚΠΚ (μία πηγή αλήθειας)
    teka_line = preview["lines"][1]
    assert (teka_line["kad"], teka_line["specialty_code"]) == ("0083", "000860")

    eid = hire(svc, 1, "teka", pron_choice="old")
    covs = profile_coverages(proj.employments[eid].insurance_profile)
    assert [(c["fund"], c["kpk"]) for c in covs] == [
        (line["fund"], line["kpk"]) for line in preview["lines"]
    ]


def test_kpk_csv_import_still_parses():
    """Ο μαζικός εισαγωγέας CSV (`svc.import_kpk_rates`) δεν έχει κουμπί στο
    UI — μένει ως API. ΔΕΝ έχει πια αρχείο-δείγμα στο repo (τα ΚΠΚ ζουν ΜΟΝΟ
    στο `data/init_parameters.json`), οπότε το δείγμα ζει εδώ: αλλιώς ο
    parser θα σάπιζε χωρίς κανέναν να το μάθει."""
    store = Database().store("test")
    proj = Projections()
    store.subscribe(proj.handle)
    svc = PayrollService(store, proj)

    csv = (
        "φορέας;ΚΠΚ;ονομασία;βαθμίδα;ισχύς από;εργαζόμενος %;εργοδότης %;ανάλυση\n"
        "ΕΦΚΑ;0103;ΙΚΑ ΜΙΚΤΑ (κύρια);main;2025-01-01;10,37;18,79;"
        "main_pension=6,67/13,33|healthcare=2,55/4,55|dypa=1,15/0,91\n"
        "ΤΕΚΑ;4169;ΤΕΚΑ Επικουρική;auxiliary;2022-06-01;3;3;auxiliary=3/3\n"
    ).encode("utf-8")
    res = svc.import_kpk_rates(csv)
    assert res["errors"] == [], res["errors"]
    assert res["imported"] == 2
    assert svc.known_kpks("efka_main") == ["0103"]
    rates = svc.kpk_rates_for("teka", "4169", date(2026, 1, 1))
    assert (rates.employee, rates.employer) == (0.03, 0.03)
    # Λάθος γραμμή: αναφέρεται, δεν χάνεται σιωπηλά. ⚠️ Μπαίνει ΔΕΥΤΕΡΗ:
    # η 1η γραμμή με άγνωστο φορέα θεωρείται επικεφαλίδα και προσπερνιέται.
    bad = svc.import_kpk_rates(
        "ΕΦΚΑ;0103;ΙΚΑ;main;2025-01-01;10,37;18,79;\n"
        "ΑΓΝΩΣΤΟΣ;9999;Χ;main;2025-01-01;1;1;\n".encode("utf-8")
    )
    assert bad["imported"] == 1
    assert any("ΑΓΝΩΣΤΟΣ" in e for e in bad["errors"]), bad["errors"]
