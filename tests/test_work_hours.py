"""
Tests κατάταξης ωρών εργασίας (work_hours.py — κανόνες parousies.md):
νύχτα 25%, αργία 75%, πρόσθετη 12%, υπερεργασία 20%, υπερωρία 40%,
παράνομη υπερωρία 80%, όρια 3 ωρών/ημέρα και 150 ωρών/έτος.

Η αμοιβή ΔΕΝ είναι αθροιστική μεταξύ των αξόνων: ωρομίσθιο κλάσης x
(1 + νύχτα + αργία). Οι ώρες πέραν του ωραρίου πληρώνονται ΟΛΟΚΛΗΡΕΣ
(δεν τις καλύπτει ο μισθός)· οι εντός ωραρίου μόνο την προσαύξηση.

Εκτέλεση: python test_work_hours.py
"""

from datetime import date, timedelta
from types import SimpleNamespace

import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

from misevent.app.services import PayrollService
from misevent.domain import work_hours as wh
from misevent.rules import constraints
from misevent.rules.config import WorkHourRules, work_hour_rules_for

RULES = work_hour_rules_for(date(2026, 1, 1))

# Δευ 2/2/2026 - Παρ 6/2/2026 (καμία αργία, εκτός Κυριακής)
MON, TUE, WED, THU, FRI, SAT, SUN = (date(2026, 2, d) for d in range(2, 9))


def by_rate(buckets):
    out = {}
    for b in buckets:
        out[b.rate] = out.get(b.rate, 0) + b.minutes
    return {k: v / 60.0 for k, v in out.items()}


def supplement(buckets, rate=10.0):
    """Συνολικές επιπλέον αμοιβές των buckets με δεδομένο ωρομίσθιο."""
    fields = wh.sheet_fields_from_buckets(buckets)
    return wh.pay_supplement(rate, fields, RULES)["total"]


def test_normal_week_no_premiums():
    """Πλήρης απασχόληση, 5x8 εντός ημέρας: όλα normal, μηδέν προσαυξήσεις."""
    shifts = {d: ["09:00-17:00"] for d in (MON, TUE, WED, THU, FRI)}
    buckets = wh.classify_shifts(shifts, 40, 5)
    assert by_rate(buckets) == {"normal": 40.0}
    assert all(not b.night and not b.holiday for b in buckets)
    fields = wh.sheet_fields_from_buckets(buckets)
    assert fields["normal"] == 40.0
    assert sum(v for k, v in fields.items() if k != "normal") == 0.0
    assert supplement(buckets) == 0.0
    print("OK κανονική εβδομάδα: καμία προσαύξηση")


def test_night_hours():
    """Βάρδια 20:00-02:00: οι ώρες 22:00-02:00 είναι νύχτα (4 από 6)."""
    buckets = wh.classify_shifts({MON: ["20:00-02:00"]}, 40, 5)
    fields = wh.sheet_fields_from_buckets(buckets)
    assert fields["normal"] == 2.0 and fields["normal_n"] == 4.0
    # εντός ωραρίου: μόνο η προσαύξηση 25% -> 4ω x 25% x 10€
    assert supplement(buckets) == 10.0
    print("OK νύχτα 22:00-06:00 (βάρδια που περνά μεσάνυχτα)")


def test_sunday_is_holiday():
    """Κυριακή πριν τις 40ω της εβδομάδας: εντός ωραρίου, μόνο 75%."""
    buckets = wh.classify_shifts({SUN: ["10:00-14:00"]}, 40, 5)
    fields = wh.sheet_fields_from_buckets(buckets)
    assert fields["normal_h"] == 4.0
    assert supplement(buckets) == 30.0  # 4ω x 75% x 10€
    print("OK Κυριακή: 75%")


def test_public_holiday():
    """25η Μαρτίου (Τετάρτη το 2026): αργία."""
    d = date(2026, 3, 25)
    buckets = wh.classify_shifts({d: ["09:00-17:00"]}, 40, 5)
    assert all(b.holiday for b in buckets)
    print("OK επίσημη αργία αναγνωρίζεται")


def test_part_time_extra_work():
    """Μερική απασχόληση 20ω: ώρες 20-30 = πρόσθετη εργασία 12%."""
    shifts = {d: ["09:00-15:00"] for d in (MON, TUE, WED, THU, FRI)}  # 5x6=30
    buckets = wh.classify_shifts(shifts, 20, 5)
    assert by_rate(buckets) == {"normal": 20.0, "extra": 10.0}
    # Οι πρόσθετες ώρες δεν καλύπτονται από τον μισθό: πληρώνονται
    # ΟΛΟΚΛΗΡΕΣ με ωρομίσθιο x 1.12 -> 10ω x 11.20€ = 112€
    assert supplement(buckets) == 112.0
    print("OK πρόσθετη εργασία μερικής απασχόλησης (ολόκληρο ωρομίσθιο x1.12)")


def test_overwork_40_to_45():
    """Πλήρης 5x9 = 45ω: η 9η ώρα ΚΑΘΕ ημέρας είναι ημερήσια υπερεργασία
    και ΔΕΝ ξαναμετρά στον εβδομαδιαίο μετρητή — 40 normal + 5 υπερεργασία
    (= ακριβώς οι ώρες 40->45), καμία υπερωρία."""
    shifts = {d: ["08:00-17:00"] for d in (MON, TUE, WED, THU, FRI)}
    buckets = wh.classify_shifts(shifts, 40, 5)
    assert by_rate(buckets) == {"normal": 40.0, "overwork": 5.0}
    print("OK υπερεργασία 40->45 χωρίς διπλομέτρηση")


def test_long_day_not_counted_twice():
    """parousies.md: 10ωρη ημέρα σε εβδομάδα 42ω -> 9η υπερεργασία, 10η
    υπερωρία· οι υπόλοιπες ημέρες normal (2 ώρες υπέρβασης, όχι 4)."""
    shifts = {d: ["09:00-17:00"] for d in (MON, TUE, THU, FRI)}
    shifts[WED] = ["09:00-19:00"]
    buckets = wh.classify_shifts(shifts, 40, 5)
    assert by_rate(buckets) == {"normal": 40.0, "overwork": 1.0, "overtime": 1.0}


def test_weekly_overtime_after_daily_excess():
    """5x10 = 50ω: 5 υπερεργασία (9η ώρα) + 5 υπερωρία (10η) = οι ώρες
    40->45 και 45->50 της εβδομάδας."""
    shifts = {d: ["08:00-18:00"] for d in (MON, TUE, WED, THU, FRI)}
    buckets = wh.classify_shifts(shifts, 40, 5)
    assert by_rate(buckets) == {"normal": 40.0, "overwork": 5.0, "overtime": 5.0}


def test_daily_overwork_ninth_hour():
    """Πενθήμερο, ημέρα 9ω εντός εβδομαδιαίου ορίου: η 9η ώρα = υπερεργασία
    (ημερήσια ζώνη 8ω->νόμιμο 9ω), οι υπόλοιπες 8 normal."""
    # Τρίτη μόνο (εβδομάδα < 40ω): 8 normal + 1 υπερεργασία
    buckets = wh.classify_shifts({TUE: ["08:00-17:00"]}, 40, 5)
    assert by_rate(buckets) == {"normal": 8.0, "overwork": 1.0}
    # Εξαήμερο: ημερήσιο νόμιμο 8ω -> η 9η ώρα πάει κατευθείαν υπερωρία
    buckets6 = wh.classify_shifts({TUE: ["08:00-17:00"]}, 48, 6)
    assert by_rate(buckets6) == {"normal": 8.0, "overtime": 1.0}
    print("OK ημερήσια υπερεργασία 9ης ώρας (πενθήμερο) / υπερωρία (εξαήμερο)")


def test_daily_overtime():
    """Ημέρα 12ω (πενθήμερο): 8 normal + 1 υπερεργασία (9η) + 3 υπερωρία."""
    buckets = wh.classify_shifts({MON: ["08:00-20:00"]}, 40, 5)
    assert by_rate(buckets) == {"normal": 8.0, "overwork": 1.0, "overtime": 3.0}
    print("OK ημερήσια υπερωρία πάνω από 9 ώρες (με 9η ώρα υπερεργασία)")


def test_illegal_overtime_daily_cap():
    """Ημέρα 14ω: 8 normal + 1 υπερεργασία + 3 νόμιμη + 2 ΠΑΡΑΝΟΜΗ (όριο 3ω)."""
    buckets = wh.classify_shifts({MON: ["06:00-20:00"]}, 40, 5)
    assert by_rate(buckets) == {
        "normal": 8.0, "overwork": 1.0, "overtime": 3.0, "illegal": 2.0}
    # Ολόκληρο ωρομίσθιο: 1x10x1.20 + 3x10x1.40 + 2x10x1.80 = 12+42+36 = 90€
    assert supplement(buckets) == 90.0
    print("OK παράνομη υπερωρία πάνω από το ημερήσιο όριο")


def test_yearly_overtime_cap():
    """Το ετήσιο όριο υπερωρίας: πέραν αυτού όλα παράνομα."""
    rules = WorkHourRules(valid_from=date(2024, 1, 1), overtime_year_cap=2)
    buckets = wh.classify_shifts({MON: ["06:00-20:00"]}, 40, 5, rules=rules)
    assert by_rate(buckets) == {
        "normal": 8.0, "overwork": 1.0, "overtime": 2.0, "illegal": 3.0}
    print("OK ετήσιο όριο υπερωρίας")


def test_weekly_overtime_45_plus():
    """4x9 + Παρ 11ω = 47ω. Οι 9ες ώρες = ημερήσια υπερεργασία (5), οι
    10η-11η της Παρ = υπερωρία (>9ω ημερήσιο)· οι 8 πρώτες ώρες κάθε ημέρας
    normal — υπέρβαση 7ω = ακριβώς 40->47."""
    shifts = {d: ["08:00-17:00"] for d in (MON, TUE, WED, THU)}
    shifts[FRI] = ["08:00-19:00"]
    buckets = wh.classify_shifts(shifts, 40, 5)
    assert by_rate(buckets) == {"normal": 40.0, "overwork": 5.0, "overtime": 2.0}
    print("OK εβδομαδιαία κλιμάκωση: normal -> υπερεργασία -> υπερωρία")


def test_sixth_day_is_rest_day_work():
    """Σεπτ. 2026, πενθήμερο 40ω. Εβδ. 1: Δευ–Πεμ 8ω + Παρ 15:00-00:00 (9η
    ώρα = ημερήσια υπερεργασία), Σάββατο 19-23 = 6η ημέρα. Εβδ. 2: Δευ–Παρ,
    Κυριακή 19-23 = 6η ημέρα ΚΑΙ αργία. Οι ώρες 6ης ημέρας ΔΕΝ είναι
    υπερεργασία (ΚΑΕΔ 182Β/182Γ, νομολογία) — `restday`, ολόκληρη ώρα +40%."""
    d = date(2026, 8, 31)  # Δευτέρα
    days = [d + timedelta(days=i) for i in range(14)]
    shifts = {x: ["09:00-17:00"] for x in days[:4] + days[7:12]}
    shifts[days[4]] = ["15:00-00:00"]
    shifts[days[5]] = ["19:00-23:00"]  # Σάββατο
    shifts[days[13]] = ["19:00-23:00"]  # Κυριακή
    buckets = wh.classify_shifts(shifts, 40, 5)
    assert by_rate(buckets) == {"normal": 80.0, "overwork": 1.0, "restday": 8.0}
    assert {b.day for b in buckets if b.rate == "restday"} == {days[5], days[13]}
    assert all(b.holiday for b in buckets if b.day == days[13])
    # ωρομίσθιο 10: Σάββατο 3ω ημέρα 14 + 1ω νύχτα 17,5· Κυριακή 3ω αργία
    # 10×1,4×1,75 = 24,5 + 1ω νύχτα-αργία 10×1,4×2 = 28 — συν η 9η ώρα Παρ
    # (νύχτα υπερεργασία 10×1,2×1,25 = 15) και η 22-23 Παρ (νύχτα εντός 2,5)
    assert supplement(buckets) == round(3 * 14 + 17.5 + 3 * 24.5 + 28 + 15 + 2.5, 2)


def test_rest_day_over_eight_hours_is_overtime():
    """6η ημέρα 10ω: οι 8 πρώτες `restday`, οι 2 επόμενες υπερωρία."""
    shifts = {d: ["09:00-17:00"] for d in (MON, TUE, WED, THU, FRI)}
    shifts[SAT] = ["08:00-18:00"]
    assert by_rate(wh.classify_shifts(shifts, 40, 5)) == {
        "normal": 40.0, "restday": 8.0, "overtime": 2.0}


def test_night_and_holiday_stack():
    """Κυριακή νύχτα: 25% + 75% = 100% προσαύξηση (εντός ωραρίου)."""
    buckets = wh.classify_shifts({SUN: ["22:00-02:00"]}, 40, 5)
    fields = wh.sheet_fields_from_buckets(buckets)
    # 4ω: 2ω Κυριακή-νύχτα, 2ω Δευτέρα-νύχτα (μετά τα μεσάνυχτα)
    assert fields["normal_nh"] == 2.0 and fields["normal_n"] == 2.0
    assert supplement(buckets) == 25.0  # 2x10x1.00 + 2x10x0.25
    print("OK σώρευση νύχτας + αργίας (και αλλαγή ημέρας στα μεσάνυχτα)")


def test_multiplicative_class_daytype():
    """Υπερεργασία νύχτα = ωρομίσθιο x 1.20 x 1.25 = 1.50 — ΟΧΙ αθροιστικά
    (1+0.20+0.25=1.45). Η λίστα ωρομισθίων του parousies.md."""
    assert wh.field_multiplier("overwork_n", RULES) == 1.5
    assert wh.field_multiplier("overtime_nh", RULES) == 1.4 * 2.0
    assert wh.field_multiplier("illegal_h", RULES) == 1.8 * 1.75
    assert wh.field_multiplier("extra", RULES) == 1.12
    assert wh.field_multiplier("normal", RULES) == 0.0  # καλύπτεται από μισθό
    assert wh.field_multiplier("normal_nh", RULES) == 1.0  # μόνο 25+75%
    print("OK πολλαπλασιαστική σχέση κλάσης x ημέρας-τύπου")


def test_hourly_rate():
    """Κανονικό ωρομίσθιο: μισθός/25 x 6/40 (πλήρης), ημερομίσθιο x 6/40."""
    assert wh.hourly_rate("monthly", 1000.0, 0, 40) == 6.0
    assert wh.hourly_rate("daily", 0, 40.0, 40) == 6.0
    # μερική 20ω: ο (μικρότερος) μισθός αντιστοιχεί σε 20 ώρες/εβδομάδα
    assert wh.hourly_rate("monthly", 500.0, 0, 20) == 6.0
    print("OK κανονικό ωρομίσθιο")


def test_schedule_daily_maximum():
    """Το πρόγραμμα δέχεται έως 13 ώρες εργασίας, ποτέ περισσότερο."""
    assert constraints.day_schedule_minutes("08:00-21:00") == 13 * 60
    try:
        constraints.parse_day_schedule("08:00-21:15")
    except ValueError as exc:
        assert "13 ώρες" in str(exc)
    else:
        raise AssertionError("Έγινε δεκτό ημερήσιο ωράριο πάνω από 13 ώρες")
    print("OK μέγιστη ημερήσια εργασία 13 ωρών")


def test_schedule_split_gap_and_daily_rest():
    """Σπαστό >=3ω και ανάπαυση διαδοχικών ημερών >=11ω, μαζί με Κυρ->Δευ."""
    constraints.parse_day_schedule("08:00-10:00+13:00-15:00")
    try:
        constraints.parse_day_schedule("08:00-10:00+12:45-15:00")
    except ValueError as exc:
        assert "τουλάχιστον 3 ώρες" in str(exc)
    else:
        raise AssertionError("Έγινε δεκτό σπαστό ωράριο με κενό κάτω από 3 ώρες")

    constraints.check_daily_rest("22:00-06:00", "17:00-21:00")
    try:
        constraints.check_daily_rest("22:00-06:00", "16:45-21:00")
    except ValueError as exc:
        assert "11 ώρες" in str(exc)
    else:
        raise AssertionError("Έγινε δεκτή ημερήσια ανάπαυση κάτω από 11 ώρες")

    constraints.check_schedule(
        {"SUN": "22:00-06:00", "MON": "17:00-21:00"}, weekly_hours=None
    )
    print("OK κενό σπαστού 3ω και ημερήσια ανάπαυση 11ω")


def test_schedule_hours_must_equal_contract():
    """Το template και η πλήρης εβδομάδα ισούνται με τις συμβατικές ώρες."""
    full = {d: "09:00-17:00" for d in ("MON", "TUE", "WED", "THU", "FRI")}
    constraints.check_schedule(full, weekly_hours=40)  # ακριβώς 40
    for pattern in (
        dict(full, MON="09:00-19:00"),  # 42 > 40
        dict(full, FRI="09:00-13:00"),  # 36 < 40
    ):
        try:
            constraints.check_schedule(pattern, 40)
        except ValueError as exc:
            assert "πρέπει να είναι ίσο" in str(exc)
        else:
            raise AssertionError("Έγινε δεκτό πρόγραμμα διαφορετικό από τη σύμβαση")

    print("OK ώρες συμβατικού template: αυστηρή ισότητα με τη σύμβαση")

    constraints.check_week_hours_minimum(40, 40, "εβδ.")
    constraints.check_week_hours_minimum(42, 40, "εβδ.")
    try:
        constraints.check_week_hours_minimum(39, 40, "εβδ.")
    except ValueError as exc:
        assert "λιγότερες" in str(exc)
    else:
        raise AssertionError("Έγινε δεκτό ημερήσιο εβδομαδιαίο κάτω από τη σύμβαση")


def test_daily_override_checks_adjacent_rest():
    """Η ημερήσια μεταβολή ελέγχεται απέναντι στο πρόγραμμα της προηγουμένης."""
    previous = date(2026, 2, 2)
    selected = date(2026, 2, 3)

    class FakeEmployment:
        hire_date = None
        termination_date = None
        daily_schedule = {}

        def active_on(self, _day):
            return True

        def day_schedule(self, day):
            return "22:00-02:00" if day == previous else ""

    appended = []
    projection = SimpleNamespace(
        employments={"E1": FakeEmployment()}, company=None,
        obligations={},  # καμία κλεισμένη μισθοδοσία (έλεγχος κλειδώματος)
    )
    service = PayrollService(
        SimpleNamespace(append=lambda *args: appended.append(args)), projection
    )
    try:
        service.set_daily_schedule("E1", {selected: "12:45-20:45"})
    except ValueError as exc:
        assert "11 ώρες" in str(exc)
    else:
        raise AssertionError("Η ημερήσια μεταβολή παρέκαμψε την 11ωρη ανάπαυση")
    assert not appended

    service.set_daily_schedule("E1", {selected: "13:00-21:00"})
    assert len(appended) == 1
    print("OK ημερήσια μεταβολή με έλεγχο γειτονικών ημερών")
