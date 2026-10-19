"""
Καταβολές (μισθοί, Δώρα, εισφορές, ΦΜΥ)

Μέρος του `PayrollService` (βλ. `misevent/app/services/__init__.py`).
⚠️ Τα mixins ΔΕΝ είναι αυτοτελή: καλούν μεταξύ τους μεθόδους μέσω `self`.
Η ΔΙΕΠΑΦΗ είναι η ίδια η κλάση, όχι το αρχείο — μη «καθαρίσεις» με imports
μεταξύ mixins, θα φτιάξεις κύκλους.
"""

from misevent.domain.events import PaymentMade


class PaymentsMixin:
    def pay_salaries(self, year: int, month: int):
        for p in self.proj.payrolls:
            if p.period_year == year and p.period_month == month:
                self.store.append(
                    p.employee_id,
                    PaymentMade("salary", year, month, p.net, p.employee_id),
                )

    def pay_gifts(self, gift_type: str, year: int):
        """Καταβολή Δώρου (easter|christmas) στους δικαιούχους — καθαρά ποσά."""
        rows = [
            g for g in self.proj.gifts if g.gift_type == gift_type and g.year == year
        ]
        if not rows:
            raise ValueError(
                f"Δεν έχει υπολογιστεί Δώρο {gift_type} {year} — τίποτα προς πληρωμή"
            )
        for g in rows:
            self.store.append(
                g.employee_id,
                PaymentMade(f"{gift_type}_gift", year, 0, g.net, g.employee_id),
            )
        return sum(g.net for g in rows)

    def pay_leave_allowance(self, year: int, month: int):
        """Καταβολή Επιδόματος Αδείας στους δικαιούχους της περιόδου."""
        eligible = {e.employment_id for e in self.allowance_beneficiaries(year, month)}
        rows = [
            a
            for a in self.proj.allowances
            if a.year == year and a.employee_id in eligible
        ]
        if not rows:
            raise ValueError(
                f"Δεν έχει υπολογιστεί Επίδομα Αδείας {month}/{year} — "
                "τίποτα προς πληρωμή"
            )
        for a in rows:
            self.store.append(
                a.employee_id,
                PaymentMade("leave_allowance", year, month, a.net, a.employee_id),
            )
        return sum(a.net for a in rows)

    def pay_efka(self, year: int, month: int, amount: float):
        self.store.append("COMPANY", PaymentMade("efka", year, month, amount, "EFKA"))

    def pay_teka(self, year: int, month: int, amount: float):
        self.store.append("COMPANY", PaymentMade("teka", year, month, amount, "TEKA"))

    def pay_fmy(self, year: int, month: int, amount: float):
        self.store.append("COMPANY", PaymentMade("fmy", year, month, amount, "AADE"))
