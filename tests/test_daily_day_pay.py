"""Αμοιβή ημέρας ημερομισθίου = ημερομίσθιο × 6/40 × ώρες ημέρας."""

from datetime import date, timedelta

import _bootstrap  # noqa: F401

import misevent.domain.payroll_engine as pe


def test_daily_day_pay():
    w = 41.09
    assert pe.daily_day_pay(w, 40, 6) == round(w, 4)          # εξαήμερο 6:40
    assert pe.daily_day_pay(w, 40, 5) == round(w * 1.2, 4)    # πενθήμερο 8ωρο
    assert pe.daily_day_pay(w, 20, 5) == round(w * 0.6, 4)    # 4ωρο
    # 20ω = μισές αποδοχές της πλήρους, όπως κι αν μοιράζονται στην εβδομάδα
    assert abs(pe.daily_day_pay(w, 20, 5) * 5 * 2 - pe.daily_day_pay(w, 40, 5) * 5) < 1e-9


def test_daily_gift_ratio():
    assert pe.daily_gift_ratio(40) == 1.0
    assert pe.daily_gift_ratio(20) == 0.5
    assert pe.daily_gift_ratio(48) == 1.0


def _weekdays(first, last):
    d, out = first, set()
    while d <= last:
        if d.weekday() < 5:
            out.add(d)
        d += timedelta(1)
    return out


def test_daily_insurance_days():
    """+1 ανά εβδομάδα με ≥3 ημέρες (ΚΑ ΙΚΑ άρθρο 18, Εγκ. 44/1975, 114/1981)."""
    ins = pe.daily_insurance_days
    jan = _weekdays(date(2025, 12, 29), date(2026, 1, 31))  # + Δευ–Τετ Δεκεμβρίου
    assert ins(jan, 2026, 1, 40, 5) == 22 + 5   # 5 Σάββατα -> 27
    feb = _weekdays(date(2026, 2, 1), date(2026, 2, 28))
    assert ins(feb, 2026, 2, 40, 5) == 20 + 4   # 24
    mar = _weekdays(date(2026, 3, 1), date(2026, 3, 31))
    assert ins(mar, 2026, 3, 40, 5) == 22 + 4   # 26
    # εβδομάδα με 2 ημέρες: καμία προσαύξηση
    two = {date(2026, 3, 2), date(2026, 3, 3)}
    assert ins(two, 2026, 3, 40, 5) == 2
    assert ins(two | {date(2026, 3, 4)}, 2026, 3, 40, 5) == 4
    # μερική ή εξαήμερο: οι πραγματικές ημέρες
    assert ins(mar, 2026, 3, 20, 5) == 22
    assert ins(mar, 2026, 3, 40, 6) == 22
