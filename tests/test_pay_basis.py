"""
ΣΥΓΚΡΙΣΗ ΠΡΟΓΡΑΜΜΑΤΟΣ ↔ ΚΑΡΤΑΣ & ΤΡΟΠΟΣ ΥΠΟΛΟΓΙΣΜΟΥ ΜΗΝΑ (`MonthPayBasisChosen`).

- η σύγκριση ταξινομεί τις ημέρες όπως η `pay_source` (§5.11)·
- «πρόγραμμα»/«κάρτα» υπερισχύει του κανόνα του συστήματος στην κατάταξη ωρών·
- χωρίς απολογιστική επιλογή ο μήνας βγαίνει από την απολογιστική πρόταση.

Εκτέλεση: uv run pytest tests/test_pay_basis.py
"""

from datetime import date

from test_card_tolerance import BIG, NONE, SMALL, _month

from misevent.integrations.card_punches import day_matches, round_total


def test_flex_and_rounding():
    # ευέλικτη προσέλευση 120΄: 2 ώρες αργότερα και 2 ώρες αργότερα έξοδος = ΟΚ
    assert day_matches("09:00-17:00", "11:00-19:00", 120)
    assert not day_matches("09:00-17:00", "11:01-19:01", 120)
    # στρογγυλοποίηση στη μισή ώρα της συνολικής απασχόλησης
    assert [round_total(int(h * 60), 30) / 60 for h in (8.2, 8.5, 7.9, 8.25)] == [8, 8.5, 8, 8.5]
    assert not day_matches("09:00-17:00", "09:00-17:12", 0)
    assert day_matches("09:00-17:00", "09:00-17:12", 0, 0, 30)      # 8,2ω -> 8
    assert day_matches("09:00-17:00", "10:30-18:24", 120, 0, 30)    # 7,9ω -> 8
    assert not day_matches("09:00-17:00", "09:00-17:20", 0, 0, 30)  # 8,33ω -> 8,5
    assert not day_matches("09:00-17:00", "08:30-16:30", 0, 0, 30)  # πριν την έναρξη


def _over(svc, eid):
    h = svc.hours_analysis(2026, 2)[eid]
    return sum(v for k, v in h.items() if k.startswith(("overwork", "overtime", "extra")))


def test_comparison_and_basis(fresh_system):
    svc, proj, eid = _month(10)  # απολογιστικό, ανοχή 10΄
    c = svc.card_comparison(proj.employments[eid], 2026, 2)
    state = {r["day"]: r["state"] for r in c["days"]}
    assert state[BIG] == "diff" and state[SMALL] == "ok" and state[NONE] == "nopunch"
    assert c["diff_days"] == 1 and c["retro_needed"] and c["auto_card"]
    # η SMALL (09:03-17:07) τακτοποιείται από την ανοχή: 8ω → 8ω04΄
    small = next(r for r in c["days"] if r["day"] == SMALL)
    assert c["settled_days"] == 1 and small["rule"] == "ανοχή"
    assert (small["plan_min"], small["card_exact"]) == (480, 484)
    assert c["card_min"] - c["plan_min"] == 120 + 4  # BIG +2ω, SMALL +4΄

    assert _over(svc, eid) == 2.0  # αυτόματα (απολογιστικό) = κάρτα
    # οι προσαυξήσεις του modal = ίδια κατάταξη με το φύλλο: BIG (+2ω) + Καθαρά Δευτέρα
    p = svc.month_premiums(proj.employments[eid], 2026, 2)
    assert [r["day"] for r in p["days"]] == [BIG, date(2026, 2, 23)]
    assert [ln["hours"] for ln in p["days"][0]["lines"]] == [1.0, 1.0] and p["premium"] > 0
    svc.choose_month_pay_basis(eid, 2026, 2, "schedule", retro=False)
    assert _over(svc, eid) == 0
    assert eid not in svc.retro_schedule_proposal(2026, 2)
    svc.choose_month_pay_basis(eid, 2026, 2, "card", retro=True)
    assert _over(svc, eid) == 2.0
    assert set(svc.retro_schedule_proposal(2026, 2)[eid]) == {BIG}


def test_card_overrides_pre_policy(fresh_system):
    svc, _proj, eid = _month(10, system="pre")
    svc.set_misc_settings(pre_major_by_card=False)
    assert _over(svc, eid) == 0
    svc.choose_month_pay_basis(eid, 2026, 2, "card")
    assert _over(svc, eid) == 2.0
    try:
        svc.choose_month_pay_basis(eid, 2026, 2, "κάτι")
        raise AssertionError("πέρασε άγνωστος τρόπος")
    except ValueError:
        pass
