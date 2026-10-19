"""
Εξαρτήσεις μεταξύ υποχρεώσεων (dependency graph).

Ο ΓΡΑΦΟΣ ΔΕΝ ΖΕΙ ΕΔΩ: δηλώνεται στο `obligation_types.py` (πεδίο `requires`).
Εδώ μένει μόνο η *ανάγνωσή* του πάνω στις πραγματικές υποχρεώσεις:

  - ποια προαπαιτούμενα υπάρχουν στο σύστημα,
  - ποια είναι ανεκπλήρωτα (άρα η υποχρέωση είναι ΜΠΛΟΚΑΡΙΣΜΕΝΗ).

Ένα προαπαιτούμενο ισχύει μόνο εφόσον η αντίστοιχη υποχρέωση ΥΠΑΡΧΕΙ (π.χ. ο
πρώτος μήνας λειτουργίας δεν έχει προηγούμενη μισθοδοσία, οπότε δεν
μπλοκάρεται) — ΕΚΤΟΣ αν ο τύπος είναι `strict`: εκεί το προαπαιτούμενο
μετράει ακόμη κι αν δεν έχει προγραμματιστεί ακόμη (βλ. Δώρα).
"""

from dataclasses import dataclass

import misevent.rules.obligation_types as ot


def prerequisite_keys(ob):
    """Κλειδιά (type, period_year, period_month) που πρέπει να έχουν κλείσει."""
    return ot.prerequisite_keys(
        ob.obligation_type, ob.period_year, ob.period_month
    )


def build_index(obligations):
    """dict[(type, py, pm)] -> Obligation, από proj.obligations.values()."""
    return {(o.obligation_type, o.period_year, o.period_month): o for o in obligations}


@dataclass(frozen=True)
class PendingObligation:
    """Προαπαιτούμενο που δεν έχει καν προγραμματιστεί ακόμη (π.χ. η
    μισθοδοσία Νοεμβρίου, όταν τρέχουμε ακόμη τον Μάρτιο). Μετράει ως
    ΑΝΕΚΠΛΗΡΩΤΟ, αλλιώς τα Δώρα θα εμφανίζονταν εκτός αλυσίδας."""

    obligation_type: str
    period_year: int
    period_month: int
    obligation_id: str = ""
    fulfilled: bool = False


def prerequisites(ob, index):
    """Τα προαπαιτούμενα της υποχρέωσης που «μετράνε» (βλ. `strict`).

    Τα CONDITIONAL προαπαιτούμενα (π.χ. WAGE_ADJUSTMENT — υπάρχει μόνο σε
    περίοδο αλλαγής ΓΣΣΕ) ΔΕΝ γίνονται ποτέ phantom «pending»: μετράνε μόνο
    όταν όντως έχουν προγραμματιστεί, ακόμη κι αν ο τύπος είναι strict."""
    strict = ob.obligation_type in ot.STRICT_TYPES
    out = []
    for k in prerequisite_keys(ob):
        if k in index:
            out.append(index[k])
        elif strict and k[0] not in ot.CONDITIONAL_TYPES:
            out.append(PendingObligation(*k))
    return out


def unmet_prerequisites(ob, index):
    """Τα ανεκπλήρωτα προαπαιτούμενα (κενή λίστα = ελεύθερη)."""
    return [p for p in prerequisites(ob, index) if not p.fulfilled]
