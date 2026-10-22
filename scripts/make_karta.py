"""
Δοκιμαστικό αρχείο «Κάρτας Εργασίας» ΕΡΓΑΝΗ (xlsx) για την ΤΡΕΧΟΥΣΑ εταιρεία.

Παράγει ό,τι θα έβγαζε το ΕΡΓΑΝΗ για μια περίοδο (στήλες ΑΑ Παραρτηματος ·
ΑΦΜ · Επώνυμο · Όνομα · Ημ/νία · Από · Έως), ώστε να δοκιμαστεί η εισαγωγή από τη
σελίδα «Παρουσίες» (`svc.import_karta_attendance` → χτυπήματα κάρτας, §5.11).

- Εργαζόμενοι: οι ΕΝΕΡΓΟΙ της πρώτης εταιρείας του μητρώου (πραγματικά ΑΦΜ).
- Ημέρες: όσες ορίζει το πρόγραμμά τους, εκτός αργιών και ΑΔΕΙΩΝ· ρεπό και
  τηλεεργασία παραλείπονται (η κάρτα καταγράφει μόνο φυσική παρουσία).
- Ώρες: του προγράμματος, με αποκλίσεις λίγων λεπτών και πού και πού
  παραμονή 35΄ (σταθερό seed → ίδιο αρχείο σε κάθε εκτέλεση).
- ΤΥΦΛΑ ΧΤΥΠΗΜΑΤΑ (`GAPS`): σε μία γραμμή λείπει η ΕΞΟΔΟΣ και σε μία η
  ΕΙΣΟΔΟΣ· η εισαγωγή τα διορθώνει από το πρόγραμμα. Τυπώνονται στο τέλος.
- Κέλυφος xlsx: το δείγμα `data/karta_2026-03-05.xlsx`.
- ΜΟΝΟ ανάγνωση της βάσης· αποτυγχάνει αν η περίοδος είναι κλεισμένη (§5.6).

Χρήση (από τη ρίζα του repo):

    uv run scripts/make_karta.py ΑΡΧΕΙΟ.xlsx ΑΠΟ ΕΩΣ

π.χ. `uv run scripts/make_karta.py karta_2026-10.xlsx 2026-10-01 2026-10-20`
"""

import random
import re
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from misevent.integrations import card_punches as cp  # noqa: E402
from misevent.integrations import karta_import as ki  # noqa: E402
from misevent.rules.calendar_gr import public_holidays  # noqa: E402
from webapp.system import registry  # noqa: E402

SAMPLE = ROOT / "data" / "karta_2026-03-05.xlsx"
COLUMNS = ki.COLUMNS
# Γραμμή δεδομένων (1 = πρώτη μετά την επικεφαλίδα) -> στήλη που μένει κενή
GAPS = {3: 6, 8: 5}  # 6 = Έως, 5 = Από


def build_rows(day_from: date, day_to: date) -> list[tuple]:
    rnd = random.Random(2026)
    t = registry.tenant(registry.auth.active_companies()[0].company_id)
    t.svc._check_open_days({day_from, day_to}, "δοκιμαστικό αρχείο καρτών")
    rows = [COLUMNS]
    d = day_from
    while d <= day_to:
        for e in t.proj.employments.values():
            if (not (e.hire_submitted and e.active_on(d))
                    or d in public_holidays(d.year)
                    or d in e.leave_days_in_month(d.year, d.month)):
                continue
            p = t.proj.persons[e.person_id]
            for seg in (e.day_schedule(d) or "").split("+"):
                if "-" not in seg or seg[0] in "ΤT":
                    continue  # ρεπό / τηλεεργασία
                a, b = (cp.parse_time(x) for x in seg.split("-"))
                a += rnd.randint(-7, 6)
                b += rnd.choice([0, 1, 3, -2, 5, 35])
                rows.append(("0", p.afm, p.last_name, p.first_name,
                             f"{d.day}/{d.month}/{d.year}",
                             cp.hhmm(a), cp.hhmm(b)))
        d += timedelta(days=1)
    for i, col in GAPS.items():
        if i < len(rows):
            rows[i] = rows[i][:col] + ("",) + rows[i][col + 1:]
    return rows


def write_xlsx(rows: list[tuple], out: str) -> None:
    """Το δείγμα με αντικατεστημένο μόνο το sheetData του φύλλου."""
    xml_rows = "".join(
        f'<row r="{i}">'
        + "".join(f'<c r="{c}{i}" t="str"><v>{escape(v)}</v></c>'
                  for c, v in zip("ABCDEFG", r))
        + "</row>"
        for i, r in enumerate(rows, start=1)
    )
    with zipfile.ZipFile(SAMPLE) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        sheet = src.read("xl/worksheets/sheet1.xml").decode()
        head, rest = sheet.split("<sheetData>", 1)
        tail = rest.split("</sheetData>", 1)[1]
        head = re.sub(r'<dimension ref="[^"]*"/>', f'<dimension ref="A1:G{len(rows)}"/>', head)
        for item in src.infolist():
            data = src.read(item)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = f"{head}<sheetData>{xml_rows}</sheetData>{tail}".encode()
            z.writestr(item, data)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    out, day_from, day_to = sys.argv[1], *map(date.fromisoformat, sys.argv[2:])
    rows = build_rows(day_from, day_to)
    write_xlsx(rows, out)
    print(f"{len(rows) - 1} γραμμές -> {out}")
    for i, col in GAPS.items():
        if i < len(rows):
            _b, afm, last, first, day, tin, tout = rows[i]
            print(f"  χωρίς {COLUMNS[col].lower()}: {last} {first} {day} {tin or tout}")
