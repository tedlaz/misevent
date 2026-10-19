"""
ΠΥΡΗΝΑΣ ΜΙΣΘΟΔΟΣΙΑΣ — stdlib only, καμία εξάρτηση από το `webapp/`.

Οι υποφάκελοι ακολουθούν τον ΓΡΑΦΟ ΕΞΑΡΤΗΣΕΩΝ (κατεύθυνση προς τα κάτω):

    rules/         ΔΕΔΟΜΕΝΑ & ΚΑΝΟΝΕΣ — config, ημερολόγιο, constraints,
                   μητρώο τύπων υποχρεώσεων. Δεν εξαρτώνται από τίποτα άλλο.
    domain/        ΓΕΓΟΝΟΤΑ & ΚΑΘΑΡΟΙ ΥΠΟΛΟΓΙΣΜΟΙ — events, projections,
                   scheduler, payroll_engine, work_hours.
    persistence/   ΑΠΟΘΗΚΕΥΣΗ — event store (append-only SQLite).
    integrations/  ΑΡΧΕΙΑ & ΦΟΡΕΙΣ — ΑΠΔ, ΦΜΥ, ΕΡΓΑΝΗ, κάρτα εργασίας.
    app/           ΕΝΤΟΛΕΣ — services (command layer), auth.

Imports ΠΑΝΤΑ απόλυτα: `from misevent.rules import config` (βλ. ARCH_REF.md).
"""
