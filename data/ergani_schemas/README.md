# Σχήματα υποβολής ΕΡΓΑΝΗ ΙΙ (Web API)

Ό,τι επιστρέφει το ίδιο το ΕΡΓΑΝΗ στο `GET /Documents/{τύπος}` (με token
σύνδεσης): `json` = δείγμα δομής, `propertiesInfo` = περιορισμοί ανά πεδίο.
Λήψη: 20/10/2026, δοκιμαστικό `trialv2eservices.yeka.gr/WebservicesAPI/Api`.

| Αρχείο | Έντυπο | Χρήση στην εφαρμογή |
|---|---|---|
| `WebE3N.json` | Αναγγελία πρόσληψης | `ergani.build_web_e3n` |
| `WTOWeek.json` | Οργάνωση χρόνου εργασίας — σταθερό εβδομαδιαίο | `ergani.build_wto` |
| `WTODaily.json` | Οργάνωση χρόνου εργασίας — ημερήσιο (μεταβλητό) | `ergani.build_wto_variable` |
| `WebE5N.json` | Οικειοθελής αποχώρηση (με δήλωση εργαζομένου, PDF) | `ergani.build_web_e5n` |
| `WebE7N.json` | Λύση σύμβασης ορισμένου χρόνου | `ergani.build_web_e7n` |
| `WorkTimeType.json` | Κωδικοί `f_type` (υπηρεσία EX_BASE_03, Parameter=WorkTimeType) | `ergani._wto_analytics` |

Έλεγχος συμμόρφωσης: `tests/test_ergani_schemas.py`. Για ανανέωση: νέο GET
και αντικατάσταση του αρχείου — το test δείχνει αμέσως τι άλλαξε.

Η **καταγγελία σύμβασης αορίστου** δεν βρέθηκε ως έντυπο του API (δοκιμάστηκαν
~100 ονόματα με GET)· η εφαρμογή δεν στέλνει τίποτα και ζητά υποβολή στο portal.
