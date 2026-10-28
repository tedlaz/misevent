"""Χάρτης κανόνων: ο χάρτης ελέγχων παράγεται από τον κώδικα (AST) και η
σελίδα αποδίδεται με τον γράφο της ροής."""


def test_validation_map_finds_callers():
    from webapp.views.rules_map import validation_map

    v = validation_map()
    actions = {a["name"]: a["checks"] for g in v["groups"] for a in g["actions"]}
    # Η πρόσληψη επικυρώνεται στην υπηρεσία ΚΑΙ στην οθόνη (§6α-Β)
    assert "check_schedule" in actions["start_employment"]
    assert "check_schedule" in actions["create"]
    # εμφωλευμένοι έλεγχοι: ο έλεγχος κωδικών εργοδότη καλεί τον ΑΦΜ
    assert "check_afm" in v["checks"]["check_employer_codes"]["nested"]
    # κανένας έλεγχος χωρίς καλούντα (αλλιώς είναι νεκρός κώδικας)
    assert v["unused"] == []


def test_calc_flows_use_parameter_values():
    """Οι τιμές των διαγραμμάτων έρχονται από τις παραμέτρους, όχι από κείμενο."""
    from datetime import date

    from misevent.rules import config
    from webapp.views.rules_map import calc_flows

    on = date(2026, 3, 1)
    flows = {f["key"]: f for f in calc_flows(on)}
    assert set(flows) == {"termination", "sickness", "premiums",
                          "minimum", "gifts", "leave_allowance", "fmy"}

    def texts(seq):
        for n in seq:
            yield n["t"] + " " + n["d"]
            for label, sub in n["br"]:
                yield label
                yield from texts(sub)

    sick = " ".join(texts(flows["sickness"]["seq"]))
    rules = config.SICK_LEAVE_HISTORY[-1]
    assert f"{rules.limit_under_year} ημέρες" in sick and str(rules.limit_over_year) in sick
    prem = " ".join(texts(flows["premiums"]["seq"]))
    assert "Νύχτα +25%" in prem and "Παράνομη υπερωρία +80%" in prem
    term = " ".join(texts(flows["termination"]["seq"]))
    assert "16+ ετ. → 12 μ." in term and "Καταγγελία (απόλυση)" in term
    al = config.ALLOWANCE_HISTORY[-1]
    minimum = " ".join(texts(flows["minimum"]["seq"]))
    assert f"έως {al.monthly_max_triennia}" in minimum and "31/12/2023" in minimum
    assert "× 28" in " ".join(texts(flows["leave_allowance"]["seq"]))
    assert "Κλίμακα «" in " ".join(texts(flows["fmy"]["seq"]))


def test_rules_page_renders(fresh_system):
    from _bootstrap import ui_client

    from webapp import create_app

    client = ui_client(create_app())
    body = client.get("/rules/").get_data(as_text=True)
    assert "<svg" in body and "Έλεγχοι εγκυρότητας" in body
    assert "start_employment" in body
    assert "Αποχώρηση / απόλυση" in body and "fc-branches" in body
    flow = client.get("/rules/flow?y=2026&m=3").get_data(as_text=True)
    assert 'id="rules-flow"' in flow and "gs-" in flow
