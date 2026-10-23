"""
ΕΤΗΣΙΑ ΑΝΑΦΟΡΑ ΚΟΣΤΟΥΣ της Επισκόπησης (`queries.annual_cost_report`).

Το δίχτυ που κρατά την αναφορά ΕΙΛΙΚΡΙΝΗ — τα σιωπηλά λάθη μιας πρόβλεψης
είναι ακριβώς αυτά που κανείς δεν βλέπει:

  · ό,τι έχει ΥΠΟΛΟΓΙΣΤΕΙ πρέπει να συμφωνεί ΑΚΡΙΒΩΣ με το `monthly_totals`
    (ίδιος ορισμός κόστους εργοδότη — μικτά + εισφορές εργοδότη)· αν κάποιος
    αλλάξει τον έναν ορισμό, εδώ σκάει·
  · το «υπολογισμένο» ΔΕΝ επισημαίνεται ποτέ ως πρόβλεψη, και αντίστροφα·
  · οι ΑΠΟΖΗΜΙΩΣΕΙΣ ΑΠΟΧΩΡΗΣΗΣ δεν προβλέπονται ΠΟΤΕ (καμία απόλυση δεν
    είναι προγραμματισμένη) — μπαίνουν στον μήνα της αποχώρησης·
  · τα αθροίσματα κλείνουν από όποια πλευρά κι αν τα κοιτάξεις.

⚠️ Ζητά `fresh_system`: στήνει εταιρεία, χρήστες και νομοθετικές παραμέτρους.
"""


def test_cost_report(fresh_system):
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from webapp import create_app
    from webapp import queries as q
    from webapp.clock import today
    from webapp.system import proj

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200

    # ── Μία περίοδος μισθοδοσίας ΥΠΟΛΟΓΙΣΜΕΝΗ, οι υπόλοιπες πρόβλεψη ──────
    with app.test_request_context("/"):
        year = today().year
        period = q.next_payroll_period()
        assert period is not None, "αναμενόταν ανοικτή περίοδος μισθοδοσίας"
        y, m = period
        sheet = q.attendance_sheet(y, m)
    data = {"eid": [r["eid"] for r in sheet["rows"]]}
    for r in sheet["rows"]:
        for field in ("worked", "unpaid", "leave"):
            data[f"{field}_{r['eid']}"] = r[field]
        data[f"efka_{r['eid']}"] = r["efka_days"]
    assert c.post(f"/attendance/approve?y={y}&m={m}", data=data).status_code == 200
    assert c.post("/payroll/run", data={"y": y, "m": m}).status_code == 200

    with app.test_request_context("/"):
        rep = q.annual_cost_report(year)
        monthly = q.monthly_totals()

    assert [cell["m"] for cell in rep["months"]] == list(range(1, 13))

    # 1. Το ΥΠΟΛΟΓΙΣΜΕΝΟ κομμάτι ταυτίζεται με το `monthly_totals` του μήνα
    cell = rep["months"][m - 1]
    assert cell["parts"]["payroll"] == monthly[(y, m)]["cost"]
    assert cell["fore"]["payroll"] == 0, "υπολογισμένη μισθοδοσία δεν προβλέπεται"
    assert not cell["estimated"], f"ο μήνας {m} έχει υπολογιστεί, δεν είναι πρόβλεψη"

    # 2. Οι επόμενοι μήνες ΔΕΝ έχουν τρέξει -> είναι εξ ολοκλήρου πρόβλεψη
    nxt = rep["months"][m]
    assert nxt["estimated"] and nxt["fore"]["payroll"] == nxt["parts"]["payroll"] > 0
    assert rep["first_forecast"] == m + 1
    before = [x["parts"]["payroll"] for x in rep["months"]]

    # 3. Τα αθροίσματα κλείνουν από κάθε πλευρά
    assert rep["total"] == round(sum(x["total"] for x in rep["months"]), 2)
    assert rep["total"] == round(rep["actual"] + rep["forecast"], 2)
    assert rep["forecast"] == round(
        sum(x["forecast_total"] for x in rep["months"]), 2)
    for comp in rep["components"]:
        assert comp["total"] == round(
            sum(x["parts"][comp["key"]] for x in rep["months"]), 2)
    assert rep["total"] == round(sum(comp["total"] for comp in rep["components"]), 2)

    # 4. Καμία αποζημίωση αποχώρησης δεν είναι ΠΡΟΒΛΕΨΗ — ούτε πριν ούτε μετά
    severance = next(x for x in rep["components"] if x["key"] == "severance")
    assert severance["forecast"] == 0
    assert severance["total"] == 0, "καμία αποχώρηση δεν έχει καταγραφεί ακόμη"

    # ── Απόλυση: η αποζημίωση μπαίνει στον μήνα της αποχώρησης, ως ΓΕΓΟΝΟΣ ──
    # Η αποχώρηση επιτρέπεται ΜΟΝΟ μέσα στον τρέχοντα μήνα μισθοδοσίας — που
    # μετά το τρέξιμο του `m` είναι ο επόμενος (η αλυσίδα είναι κυλιόμενη).
    exit_m = m + 1
    eid = next(e.employment_id for e in proj.employments.values() if e.active)
    assert c.post(
        f"/employments/{eid}/terminate",
        data={"termination_date": f"{year}-{exit_m:02d}-28",
              "reason": "ΚΑΤΑΓΓΕΛΙΑ_ΣΥΜΒΑΣΗΣ"},
    ).status_code == 200
    paid = round(
        sum(ev.gross for ev in proj.severances if ev.employee_id == eid)
        + sum(ev.gross for ev in proj.leave_compensations if ev.employee_id == eid),
        2,
    )
    assert paid > 0, "η απόλυση δεν γέννησε αποζημίωση"

    with app.test_request_context("/"):
        rep = q.annual_cost_report(year)
    cell = rep["months"][exit_m - 1]
    assert cell["parts"]["severance"] == paid
    assert cell["fore"]["severance"] == 0, "η αποζημίωση ΔΕΝ είναι πρόβλεψη"
    # Η εκκαθάριση αποχώρησης ΔΕΝ κάνει τον μήνα «υπολογισμένο»: οι υπόλοιποι
    # εργαζόμενοι του ίδιου μήνα μένουν πρόβλεψη (διάκριση ΑΝΑ ΕΡΓΑΖΟΜΕΝΟ).
    assert cell["estimated"] and 0 < cell["fore"]["payroll"] < cell["parts"]["payroll"]
    # Ο απολυμένος δεν κοστίζει πια σε ΚΑΝΕΝΑΝ επόμενο μήνα — ο ΙΔΙΟΣ μήνας,
    # πριν και μετά την απόλυση
    after = [x["parts"]["payroll"] for x in rep["months"]]
    for i in range(exit_m, 12):  # index exit_m = μήνας exit_m + 1
        assert after[i] < before[i], f"ο μήνας {i + 1} δεν έφτηνε μετά την απόλυση"

    # ── Η σελίδα αποδίδεται με την αναφορά μέσα της ──────────────────────
    body = c.get("/").get_data(as_text=True)
    for needle in ("Μηνιαία εξέλιξη", "cost-bars", "cost-cum",
                   "Προβλεπόμενο σύνολο", "Αποζημιώσεις αποχώρησης"):
        assert needle in body, f"λείπει «{needle}» από την Επισκόπηση"

def test_hourly_cost(fresh_system):
    """ΤΟ ΚΟΣΤΟΣ ΤΗΣ ΕΡΓΑΤΟΩΡΑΣ — ο παρονομαστής είναι το ευαίσθητο σημείο.

    Δύο πράγματα κλειδώνονται εδώ: (α) ο ΑΡΙΘΜΗΤΗΣ είναι ακριβώς το κόστος
    της ετήσιας αναφοράς, ξανακομμένο ανά εργαζόμενο — τα δύο νούμερα δεν
    επιτρέπεται να αποκλίνουν· (β) οι ΗΜΕΡΕΣ ΑΔΕΙΑΣ ΔΕΝ ΕΙΝΑΙ ΩΡΕΣ ΕΡΓΑΣΙΑΣ:
    μια άδεια αφήνει το κόστος ίδιο και ρίχνει τις ώρες, άρα ΑΝΕΒΑΖΕΙ το
    κόστος της εργατοώρας. Αν κάποιος αρχίσει να μετρά και τις άδειες, η
    επιβάρυνση θα «βελτιωνόταν» σιωπηλά.
    """
    from datetime import date

    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from misevent.domain.events import LeaveType
    from webapp import create_app
    from webapp import queries as q
    from webapp.clock import today
    from webapp.system import proj, svc

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200

    with app.test_request_context("/"):
        year = today().year
        rep = q.annual_cost_report(year)
    hourly = rep["hourly"]
    assert hourly["rows"], "αναμένονταν εργαζόμενοι με ώρες εργασίας"

    # 1. Ο αριθμητής είναι ΤΟ ΙΔΙΟ ποσό με την ετήσια αναφορά
    assert hourly["total"]["cost"] == rep["total"]
    assert hourly["total"]["hours"] == round(
        sum(r["hours"] for r in hourly["rows"]), 2)
    for r in hourly["rows"]:
        assert r["rate"] == round(r["cost"] / r["hours"], 4)
        # Η επιβάρυνση είναι ο λόγος κόστους/ώρα προς ωρομίσθιο σύμβασης
        assert r["ratio"] == round(r["rate"] / r["nominal"], 2)
        assert r["rate"] > r["nominal"], (
            "το κόστος της ώρας δεν μπορεί να είναι μικρότερο του ωρομισθίου: "
            "λείπουν εισφορές εργοδότη/Δώρα από τον αριθμητή"
        )

    # 2. ΑΔΕΙΑ: ΟΛΟΚΛΗΡΟ το δικαίωμα είναι εκτός ωρών εργασίας — και όσο δεν
    #    έχει ληφθεί, αφαιρείται στο ΤΕΛΟΣ του έτους. Άρα η καταχώρηση άδειας
    #    μέσα στο δικαίωμα ΜΕΤΑΚΙΝΕΙ ώρες, δεν τις αφαιρεί δεύτερη φορά.
    eid = hourly["rows"][0]["eid"]
    before = hourly["rows"][0]
    emp = proj.employments[eid]
    daily_hours = (emp.weekly_hours or 40.0) / (emp.weekly_system or 5)
    with app.test_request_context("/"):
        unused = q._unused_leave_hours(emp, year)
    assert unused > 0, "αναμενόταν υπόλοιπο αδείας προς λήψη"

    # Πέντε συνεχόμενες εργάσιμες σε ΜΕΛΛΟΝΤΙΚΟ μήνα (χωρίς εγκεκριμένο
    # συγκεντρωτικό -> οι ώρες εκτιμώνται από το πρόγραμμα)
    start = date(year, 12, 7)  # Δευτέρα-Παρασκευή, χωρίς αργία
    svc.record_leave(eid, LeaveType.ANNUAL, start, date(year, 12, 11))
    planned = sum(
        1 for d in emp.scheduled_days_in_month(year, 12)
        if start <= d <= date(year, 12, 11)
    )
    assert planned > 0, "η άδεια δεν έπεσε σε προγραμματισμένες ημέρες"

    with app.test_request_context("/"):
        after = next(r for r in q.annual_cost_report(year)["hourly"]["rows"]
                     if r["eid"] == eid)
        assert q._unused_leave_hours(emp, year) == round(
            unused - planned * daily_hours, 2), "το υπόλοιπο δεν μειώθηκε"
    assert after["hours"] == before["hours"], (
        "η ΚΑΤΑΧΩΡΗΣΗ άδειας μέσα στο δικαίωμα δεν αλλάζει τις ετήσιες ώρες "
        "— ήταν ήδη αφαιρεμένη ως υπόλοιπο (αλλιώς μετριέται δύο φορές)"
    )
    assert after["rate"] == before["rate"]

    # 3. ΑΡΓΙΕΣ: ούτε αυτές είναι ώρες εργασίας. Ο Οκτώβριος 2026 έχει την
    #    28η (Τετάρτη) — μήνας ΧΩΡΙΣ εγκεκριμένο συγκεντρωτικό, άρα η εκτίμηση
    #    πρέπει να μετρήσει τις προγραμματισμένες ημέρες ΜΕΙΟΝ την αργία.
    from misevent.rules.calendar_gr import public_holidays

    scheduled = emp.scheduled_days_in_month(year, 10)
    off = [d for d in scheduled if d in public_holidays(year)]
    assert off == [date(year, 10, 28)], "αναμενόταν η 28η Οκτωβρίου στο πρόγραμμα"
    hours_oct, certain = q._worked_hours(emp, year, 10)
    assert not certain, "ο Οκτώβριος δεν έχει εγκεκριμένο συγκεντρωτικό"
    assert hours_oct == round((len(scheduled) - 1) * daily_hours, 2), (
        "η αργία μετρήθηκε ως ώρα εργασίας"
    )


def test_monthly_hours_column(fresh_system):
    """Η ΣΤΗΛΗ «ΩΡΕΣ» της μηνιαίας εξέλιξης — ίδιος παρονομαστής με το κόστος
    εργατοώρας, κομμένος ανά μήνα.

    Δύο πράγματα κλειδώνονται: (α) το άθροισμα των μηνών ΤΑΥΤΙΖΕΤΑΙ με τις
    ετήσιες ώρες του κόστους εργατοώρας — αν κάποιος αλλάξει τη μία διαδρομή,
    εδώ σκάει· (β) στην ΠΡΟΒΛΕΨΗ οι υπόλοιπες ημέρες αδείας ΑΦΑΙΡΟΥΝΤΑΙ, και
    μάλιστα μόνο από τον μήνα τους.
    """
    from datetime import date

    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from misevent.domain.events import LeaveType
    from webapp import create_app
    from webapp import queries as q
    from webapp.clock import today
    from webapp.system import proj, svc

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200

    with app.test_request_context("/"):
        year = today().year
        rep = q.annual_cost_report(year)

    assert all("hours" in cell for cell in rep["months"])
    assert rep["hours"] == round(sum(cell["hours"] for cell in rep["months"]), 2)
    # Η ΤΑΥΤΟΤΗΤΑ των τεσσάρων στηλών, σε κάθε μήνα ΚΑΙ στο σύνολο:
    # Π.ΩΡΕΣ − ΑΡΓΙΕΣ − ΑΔΕΙΕΣ = ΩΡΕΣ. Αν κάποιος μετρήσει τις π.ώρες από
    # άλλη πηγή, ο πίνακας θα σταματήσει να αθροίζει — και εδώ σκάει.
    for cell in rep["months"] + [rep]:
        assert cell["hours"] == round(
            cell["sched_hours"] - cell["holiday_hours"] - cell["leave_hours"], 2
        ), "οι στήλες των ωρών δεν αθροίζουν"
    assert sum(c["holiday_hours"] for c in rep["months"]) > 0, (
        "καμία αργία σε ολόκληρο το έτος — η στήλη δεν ελέγχεται"
    )
    # Το υπόλοιπο αδείας αφαιρείται ΜΕΤΑ το σύνολο — δεν χρεώνεται σε μήνα
    assert rep["work_hours"] == round(
        rep["hours"] - rep["unused_leave_hours"], 2)
    assert rep["work_hours"] == rep["hourly"]["total"]["hours"], (
        "οι ώρες εργασίας διαφωνούν με τον παρονομαστή της εργατοώρας"
    )
    assert rep["hours"] == rep["hourly"]["total"]["month_hours"]
    before = [cell["hours"] for cell in rep["months"]]

    # ΠΡΟΒΛΕΨΗ: άδεια σε μελλοντικό μήνα -> λιγότερες ώρες ΜΟΝΟ εκεί, και
    # ισόποσα μικρότερο υπόλοιπο — οι ΩΡΕΣ ΕΡΓΑΣΙΑΣ του έτους δεν αλλάζουν.
    eid = rep["hourly"]["rows"][0]["eid"]
    emp = proj.employments[eid]
    daily_hours = (emp.weekly_hours or 40.0) / (emp.weekly_system or 5)
    work_before = rep["work_hours"]
    start, end = date(year, 12, 7), date(year, 12, 11)  # Δευτέρα-Παρασκευή
    svc.record_leave(eid, LeaveType.ANNUAL, start, end)
    planned = sum(1 for d in emp.scheduled_days_in_month(year, 12)
                  if start <= d <= end)
    assert planned > 0, "η άδεια δεν έπεσε σε προγραμματισμένες ημέρες"

    with app.test_request_context("/"):
        rep = q.annual_cost_report(year)
    after = [cell["hours"] for cell in rep["months"]]
    assert after[11] == round(before[11] - planned * daily_hours, 2), (
        "η πρόβλεψη δεν αφαίρεσε τις ώρες των ημερών αδείας"
    )
    assert after[:11] == before[:11], "η άδεια χρεώθηκε σε λάθος μήνα"
    assert rep["work_hours"] == work_before, (
        "άδεια μέσα στο δικαίωμα ΜΕΤΑΚΙΝΕΙ ώρες, δεν τις αφαιρεί δεύτερη φορά"
    )
    assert rep["months"][11]["hours_estimated"], "ο Δεκέμβριος είναι πρόβλεψη"

    # Η άδεια δεν εξαφανίζεται: φεύγει από τις ΩΡΕΣ και εμφανίζεται στις ΑΔΕΙΕΣ
    assert rep["months"][11]["leave_hours"] == round(planned * daily_hours, 2), (
        "οι ώρες της άδειας δεν πέρασαν στη στήλη «Άδειες»"
    )

    # Οι στήλες φτάνουν ΚΑΙ στη σελίδα
    body = c.get("/").get_data(as_text=True)
    for label in ("Π.ώρες", "Αργίες", "Άδειες", "Ώρες"):
        assert f'>{label}</th>' in body, f"λείπει η στήλη «{label}»"


def test_take_home_per_hour(fresh_system):
    """Η ΣΤΗΛΗ «ΕΡΓ/ΩΡΑ» — τα καθαρά του εργαζόμενου ανά δουλεμένη ώρα.

    Ίδιος παρονομαστής με το κόστος/ώρα, ΑΛΛΟΣ αριθμητής: ό,τι μένει στην
    τσέπη μετά εισφορές και ΦΜΥ. Δύο δίχτυα: (α) τα καθαρά είναι πάντα
    ΜΙΚΡΟΤΕΡΑ του κόστους εργοδότη — αν κάποιος περάσει μικτά ή κόστος στη
    θέση των καθαρών, εδώ σκάει· (β) το πηλίκο κλείνει ακριβώς με τις ώρες.
    """
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from webapp import create_app
    from webapp import queries as q
    from webapp.clock import today

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200

    with app.test_request_context("/"):
        year = today().year
        hourly = q.annual_cost_report(year)["hourly"]

    tot = hourly["total"]
    assert tot["net"] == round(sum(r["net"] for r in hourly["rows"]), 2)
    assert tot["take"] == round(tot["net"] / tot["hours"], 4)
    for r in hourly["rows"]:
        assert r["take"] == round(r["net"] / r["hours"], 4)
        # Η ταυτότητα που διαβάζει ο χρήστης στον πίνακα: Εργ/Ωρα × ώρες
        # εργασίας = πληρωτέο (μέχρι τη στρογγυλοποίηση της ίδιας της στήλης)
        assert abs(r["take"] * r["hours"] - r["net"]) < 0.10
        assert 0 < r["net"] < r["cost"], (
            "τα καθαρά του εργαζόμενου δεν μπορεί να φτάνουν το κόστος "
            "εργοδότη — λείπουν εισφορές/ΦΜΥ από τη διαφορά"
        )
        assert r["take"] < r["rate"], "η Εργ/Ωρα ξεπέρασε το κόστος της ώρας"

    body = c.get("/").get_data(as_text=True)
    for needle in ("<th class=\"num\">Πληρωτέο</th>",
                   "<th class=\"num\">Εργ/Ωρα</th>"):
        assert needle in body, f"λείπει η στήλη «{needle}»"


def test_period_totals_are_per_year(fresh_system):
    """«ΣΥΝΟΛΑ ΑΝΑ ΠΕΡΙΟΔΟ»: ΜΟΝΟ το επιλεγμένο έτος.

    Ο πίνακας διαβάζεται ως ετήσιος (έχει και γραμμή «Σύνολο <έτος>»). Όσο
    έδειχνε ΟΛΕΣ τις χρονιές μαζί, το άθροισμά του δεν συμφωνούσε με την
    ετήσια αναφορά της Επισκόπησης — και η διαφορά ήταν αόρατη.
    """
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from webapp import create_app
    from webapp import queries as q

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200

    with app.test_request_context("/"):
        y, m = q.next_payroll_period()
        sheet = q.attendance_sheet(y, m)
    data = {"eid": [r["eid"] for r in sheet["rows"]]}
    for r in sheet["rows"]:
        for field in ("worked", "unpaid", "leave"):
            data[f"{field}_{r['eid']}"] = r[field]
        data[f"efka_{r['eid']}"] = r["efka_days"]
    assert c.post(f"/attendance/approve?y={y}&m={m}", data=data).status_code == 200
    assert c.post("/payroll/run", data={"y": y, "m": m}).status_code == 200

    with app.test_request_context("/"):
        this_year = q.monthly_totals(y)
        assert this_year, "αναμενόταν υπολογισμένη περίοδος στο τρέχον έτος"
        assert all(py == y for py, _pm in this_year)
        assert this_year == {k: v for k, v in q.monthly_totals().items()
                             if k[0] == y}
        assert q.monthly_totals(y + 1) == {}, "άλλο έτος δεν έχει περιόδους"

    body = c.get(f"/payroll/?gy={y}").get_data(as_text=True)
    assert f"{m}/{y}" in body and f"Σύνολο {y}" in body
    other = c.get(f"/payroll/?gy={y + 1}").get_data(as_text=True)
    assert f"{m}/{y}" not in other, (
        "περίοδος άλλου έτους διέρρευσε στα «Σύνολα ανά περίοδο»"
    )


def test_ledger_has_every_kind(fresh_system):
    """ΚΑΘΟΛΙΚΟ ΚΑΤΑΒΟΛΩΝ: κάθε είδος έχει τη δική του γραμμή.

    Ο πίνακας «Σύνολα ανά περίοδο» δεν είναι πια μόνο η τακτική μισθοδοσία:
    Δώρα, Επίδομα Αδείας και αποζημιώσεις μπαίνουν ως ξεχωριστές γραμμές με
    τον τύπο τους. Το δίχτυ: ό,τι υπολογίστηκε ΠΡΕΠΕΙ να εμφανίζεται — μια
    καταβολή που λείπει από τον πίνακα λείπει και από κάθε συμφωνία.
    """
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from webapp import create_app
    from webapp import queries as q
    from webapp.system import proj

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200

    with app.test_request_context("/"):
        y, m = q.next_payroll_period()
        sheet = q.attendance_sheet(y, m)
    data = {"eid": [r["eid"] for r in sheet["rows"]]}
    for r in sheet["rows"]:
        for field in ("worked", "unpaid", "leave"):
            data[f"{field}_{r['eid']}"] = r[field]
        data[f"efka_{r['eid']}"] = r["efka_days"]
    assert c.post(f"/attendance/approve?y={y}&m={m}", data=data).status_code == 200
    assert c.post("/payroll/run", data={"y": y, "m": m}).status_code == 200
    assert c.post("/payroll/gift/christmas", data={"gy": y}).status_code == 200

    with app.test_request_context("/"):
        ledger = q.payroll_ledger(y)
        totals = q.ledger_totals(ledger)

    kinds = {r["kind"] for r in ledger}
    assert {"payroll", "christmas"} <= kinds, "λείπει είδος από το καθολικό"
    gift_net = round(sum(g.net for g in proj.gifts
                         if g.gift_type == "christmas" and g.year == y), 2)
    assert next(r["net"] for r in ledger if r["kind"] == "christmas") == gift_net
    # Το σύνολο του πίνακα = ΟΛΑ τα καθαρά που υπολογίστηκαν μέσα στο έτος
    calculated = round(
        sum(p.net for p in proj.payrolls if p.period_year == y)
        + sum(g.net for g in proj.gifts if g.year == y)
        + sum(a.net for a in proj.allowances if a.year == y)
        + sum(ev.net for ev in list(proj.severances) + list(proj.leave_compensations)
              if ev.termination_date.year == y),
        2,
    )
    assert totals["net"] == calculated
    # Οι δύο στήλες είναι ΔΙΑΦΟΡΕΤΙΚΑ μεγέθη και δεν επιτρέπεται να μπερδευτούν:
    # η κράτηση του ΕΡΓΑΖΟΜΕΝΟΥ κλείνει τα καθαρά, το κόστος βλέπει εργοδότη
    for r in ledger:
        assert abs(r["gross"] - r["efka_employee"] - r["fmy"] - r["net"]) < 0.02, (
            "μικτά − εισφορές εργαζομένου − ΦΜΥ δεν δίνουν τα καθαρά"
        )
        assert r["cost"] >= r["gross"], (
            "το κόστος εργοδότη δεν γίνεται να είναι μικρότερο των μικτών"
        )
    payroll_cost = round(sum(p.gross + p.efka_employer for p in proj.payrolls
                             if p.period_year == y), 2)
    assert next(r["cost"] for r in ledger if r["kind"] == "payroll"
                and r["month"] == m) == payroll_cost

    # Νεότερος μήνας πρώτος, και μέσα στον μήνα πρώτη η τακτική μισθοδοσία
    assert [r["month"] for r in ledger] == sorted(
        (r["month"] for r in ledger), reverse=True)

    body = c.get(f"/payroll/?gy={y}").get_data(as_text=True)
    for needle in ("<th>Τύπος</th>", "Περιόδου", "Δώρο Χριστ.",
                   "Εισφορές<br>Εργαζομένου", "Κόστος"):
        assert needle in body, f"λείπει «{needle}» από τα «Σύνολα ανά περίοδο»"


def test_report_measures(fresh_system):
    """ΤΡΕΙΣ ΟΠΤΙΚΕΣ του ίδιου ποσού: κόστος / μικτά / καθαρά.

    Η επιλογή αλλάζει ΜΟΝΟ ποιο μέγεθος διαβάζεται — όχι ποια γεγονότα
    μετρώνται. Τα δίχτυα: (α) η ιεραρχία κόστος ≥ μικτά ≥ καθαρά ισχύει σε
    ΚΑΘΕ κελί (αν κάποιος μπερδέψει σειρά ή πηγή, σκάει)· (β) η οπτική του
    κόστους μένει ΑΚΡΙΒΩΣ ό,τι έδειχνε η αναφορά πριν αποκτήσει οπτικές·
    (γ) τα καθαρά συμφωνούν με το «πληρωτέο» του κόστους εργατοώρας.
    """
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from webapp import create_app
    from webapp import queries as q
    from webapp.clock import today

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200

    with app.test_request_context("/"):
        year = today().year
        rep = q.annual_cost_report(year)

    assert [ms["key"] for ms in rep["measures"]] == ["cost", "gross", "net"]

    # (α) ιεραρχία σε κάθε κελί, και τα σύνολα κλείνουν ανά οπτική
    for ms in rep["measures"]:
        key = ms["key"]
        assert ms["total"] == round(ms["actual"] + ms["forecast"], 2)
        assert ms["total"] == round(
            sum(m["by"][key]["total"] for m in rep["months"]), 2)
        assert ms["total"] == round(
            sum(comp["total"] for comp in ms["components"]), 2)
        assert rep["months"][-1]["by"][key]["cumulative"] == ms["total"]
    for m in rep["months"]:
        for comp in rep["components"]:
            k = comp["key"]
            cost = m["by"]["cost"]["parts"][k]
            gross = m["by"]["gross"]["parts"][k]
            net = m["by"]["net"]["parts"][k]
            assert cost >= gross >= net, (
                f"{m['long']}/{k}: χάλασε η ιεραρχία κόστος ≥ μικτά ≥ καθαρά"
            )

    # (β) η οπτική του κόστους ΕΙΝΑΙ η αναφορά (ίδια αντικείμενα, όχι αντίγραφα)
    assert rep["by"]["cost"]["total"] == rep["total"]
    assert rep["months"][0]["parts"] is rep["months"][0]["by"]["cost"]["parts"]
    assert rep["components"] == rep["by"]["cost"]["components"]

    # (γ) καθαρά = το πληρωτέο του πίνακα εργατοώρας (ίδια πηγή)
    assert abs(rep["by"]["net"]["total"] - rep["hourly"]["total"]["net"]) < 0.05

    # Η οθόνη: επιλογέας + κάθε ποσό με τις τρεις τιμές του
    body = c.get("/").get_data(as_text=True)
    for needle in ('data-measure="cost"', 'data-measure="gross"',
                   'data-measure="net"', "Συνολικό κόστος", "Μικτά", "Καθαρά"):
        assert needle in body, f"λείπει «{needle}» από τη Μηνιαία εξέλιξη"
    assert body.count('data-gross="') == body.count('data-net="') > 12, (
        "τα ποσά του πίνακα δεν κουβαλούν όλες τις οπτικές"
    )


def test_unused_leave_is_deducted_after_the_total(fresh_system):
    """ΤΟ ΥΠΟΛΟΙΠΟ ΑΔΕΙΑΣ ΔΕΝ ΕΙΝΑΙ ΩΡΕΣ ΕΡΓΑΣΙΑΣ — ΚΑΙ ΔΕΝ ΕΙΝΑΙ ΚΑΝΕΝΟΣ
    ΜΗΝΑ.

    Η κανονική άδεια εξαντλείται μέσα στο ημερολογιακό έτος: όσες ημέρες δεν
    έχουν καταχωρηθεί, θα ληφθούν (ή θα αποζημιωθούν). Πληρώνονται και στις
    δύο περιπτώσεις — αν μείνουν στον παρονομαστή, η εργατοώρα βγαίνει
    φθηνότερη απ' ό,τι είναι.

    ΠΟΤΕ δεν ξέρουμε, οπότε η αφαίρεση γίνεται ΜΕΤΑ ΤΟ ΣΥΝΟΛΟ, ρητά: αν
    φορτωνόταν στον Δεκέμβριο, ο μήνας θα έδειχνε τρύπα που δεν είναι δική
    του."""
    import _bootstrap  # noqa: F401  # ρίζα project στο sys.path + cwd

    from webapp import create_app
    from webapp import queries as q
    from webapp.clock import today
    from webapp.system import proj

    app = create_app()
    c = _bootstrap.ui_client(app)
    assert _bootstrap.seed(c).status_code == 200

    with app.test_request_context("/"):
        year = today().year
        rep = q.annual_cost_report(year)
        # Οι μήνες ΜΕΝΟΥΝ ΑΝΕΠΑΦΟΙ: μόνο πρόγραμμα, άδειες, αργίες
        raw = unused = 0.0
        for e in proj.employments.values():
            if e.hire_date is None:
                continue
            raw += sum(q._worked_hours(e, year, m)[0] for m in range(1, 13))
            unused += q._unused_leave_hours(e, year)
        assert unused > 0, "αναμενόταν υπόλοιπο αδείας στο seed"
        assert rep["hours"] == round(raw, 2), "το υπόλοιπο χρεώθηκε σε μήνα"
        assert rep["unused_leave_hours"] == round(unused, 2)
        assert rep["work_hours"] == round(raw - unused, 2)
        assert rep["hourly"]["total"]["hours"] == rep["work_hours"], (
            "οι δύο πίνακες πρέπει να μοιράζονται τον ΙΔΙΟ παρονομαστή"
        )

        # Ο παρονομαστής της εργατοώρας είναι ο ΚΑΘΑΡΟΣ, ανά εργαζόμενο
        for r in rep["hourly"]["rows"]:
            assert r["hours"] == round(r["month_hours"] - r["unused_leave"], 2)
            assert r["rate"] == round(r["cost"] / r["hours"], 4)
            assert r["unused_leave"] > 0, "κανένας δεν έχει εξαντλήσει την άδεια"

    # Η αφαίρεση φαίνεται ΡΗΤΑ στη σελίδα, μετά το σύνολο
    body = c.get("/").get_data(as_text=True)
    for needle in ("μείον υπόλοιπο κανονικής αδείας", f"Ώρες εργασίας {year}"):
        assert needle in body, f"λείπει «{needle}» από τη Μηνιαία εξέλιξη"
