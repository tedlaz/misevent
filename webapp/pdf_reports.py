"""Εκτυπώσεις PDF (fpdf2) — μισθοδοτική κατάσταση & αποδείξεις αποδοχών.

Καθαρά READ-ONLY πάνω στα projections/queries (κανένα event). Το fpdf2
επιτρέπεται μόνο εδώ, στο webapp — ο πυρήνας μένει stdlib-only.

Σχεδιαστικό σύστημα: γραμματοσειρές NotoSans (fonts/), κοινή παλέτα,
διανυσματικά εικονίδια (σχεδιάζονται με primitives — καμία εικόνα/emoji,
ώστε η εκτύπωση να είναι ευκρινής σε κάθε ανάλυση).
"""

import io
import math
import zipfile
from datetime import date
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from misevent.domain import payroll_engine as pe
from misevent.rules import calendar_gr, config

from . import clock, labels
from . import queries as q
from .filters import hours_gr
from .system import proj, svc


def today() -> date:
    """Η ημερομηνία αναφοράς του UI — εκτός request context (π.χ. tests),
    η πραγματική σημερινή."""
    try:
        return clock.today()
    except RuntimeError:
        return date.today()

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"

# ------------------------------------------------------------------ Παλέτα
NAVY = (23, 42, 69)          # κύριο σκούρο (κεφαλίδες)
NAVY_SOFT = (38, 63, 97)     # δεύτερος τόνος πάνω στο navy
TEAL = (13, 128, 118)        # accent (καθαρά, θετικά highlights)
TEAL_DARK = (9, 96, 89)
GREEN = (22, 122, 79)        # αποδοχές
ROSE = (183, 41, 66)         # κρατήσεις / περικοπές
AMBER = (176, 98, 22)        # ασθένεια / προειδοποιήσεις
INK = (33, 43, 60)           # κυρίως κείμενο
MUTED = (108, 121, 140)      # δευτερεύον κείμενο
FAINT = (243, 246, 250)      # απαλό γέμισμα καρτών
ROW_ALT = (248, 250, 253)    # ζέβρα πίνακα
BORDER = (222, 229, 238)
WHITE = (255, 255, 255)

MONTHS = labels.MONTHS_GEN  # ΜΙΑ πηγή αλήθειας με το UI (labels.py)

CLASS_COLORS = {  # χρωματική ταυτότητα ανά κλάση ωρομισθίου
    "normal": TEAL,
    "extra": (63, 106, 172),
    "overwork": (176, 98, 22),
    "overtime": (154, 74, 30),
    "illegal": ROSE,
}


_UP_PLAIN = str.maketrans("ΆΈΉΊΌΎΏ", "ΑΕΗΙΟΥΩ")


def up(s: str) -> str:
    """Κεφαλαία ΧΩΡΙΣ τόνους (τα ελληνικά κεφαλαία γράφονται άτονα)."""
    return s.upper().translate(_UP_PLAIN)


def eur(x: float) -> str:
    """1234.5 -> «1.234,50 €» (ελληνική μορφή)."""
    s = f"{x:,.2f}".replace(",", "\0").replace(".", ",").replace("\0", ".")
    return f"{s} €"


def num(x: float) -> str:
    s = f"{x:,.2f}".replace(",", "\0").replace(".", ",").replace("\0", ".")
    return s.rstrip("0").rstrip(",") if "," in s else s


def dmy(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


# ------------------------------------------------------------ Βάση εγγράφου
class ReportPDF(FPDF):
    """Κοινή βάση: NotoSans, footer με σελιδαρίθμηση & ημερομηνία έκδοσης."""

    def __init__(self, orientation="P", footer_note=""):
        super().__init__(orientation=orientation, unit="mm", format="A4")
        self.footer_note = footer_note
        self.add_font("noto", "", FONT_DIR / "NotoSans-Regular.ttf")
        self.add_font("noto", "B", FONT_DIR / "NotoSans-Bold.ttf")
        self.set_auto_page_break(True, margin=16)
        self.set_margins(12, 12, 12)
        self.alias_nb_pages("{nb}")

    # -- συντομογραφίες στυλ ----------------------------------------------
    def font(self, size, bold=False, color=INK):
        self.set_font("noto", "B" if bold else "", size)
        self.set_text_color(*color)

    def footer(self):
        self.set_y(-11)
        self.set_draw_color(*BORDER)
        self.set_line_width(0.2)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.set_y(-9)
        self.font(6.8, color=MUTED)
        self.cell(0, 4, self.footer_note, align="L")
        self.cell(0, 4, f"Σελίδα {self.page_no()}/{{nb}}", align="R")

    # -- διανυσματικά εικονίδια -------------------------------------------
    def badge(self, x, y, d, color, icon):
        """Χρωματιστός κύκλος διαμέτρου d με λευκό εικονίδιο μέσα."""
        self.set_fill_color(*color)
        self.ellipse(x, y, d, d, style="F")
        self.icon(icon, x + d * 0.2, y + d * 0.2, d * 0.6, WHITE, bg=color)

    def icon(self, name, x, y, s, color=INK, bg=WHITE):
        """Μικρό γεωμετρικό εικονίδιο σε κουτί s x s στο (x, y)."""
        self.set_draw_color(*color)
        self.set_fill_color(*color)
        lw = max(s * 0.11, 0.25)
        self.set_line_width(lw)
        if name == "person":
            self.ellipse(x + s * 0.28, y, s * 0.44, s * 0.44, style="D")
            self.arc(x + s * 0.06, y + s * 0.52, s * 0.88,
                     180, 360, b=s * 0.9, style="D")
        elif name == "people":
            self.ellipse(x + s * 0.08, y + s * 0.05, s * 0.34, s * 0.34, "D")
            self.ellipse(x + s * 0.52, y + s * 0.05, s * 0.34, s * 0.34, "D")
            self.arc(x - s * 0.04, y + s * 0.5, s * 0.6,
                     180, 360, b=s * 0.72, style="D")
            self.arc(x + s * 0.42, y + s * 0.5, s * 0.6,
                     180, 360, b=s * 0.72, style="D")
        elif name == "building":
            self.rect(x + s * 0.08, y, s * 0.84, s, style="D")
            for ry in (0.2, 0.45, 0.7):
                self.line(x + s * 0.26, y + s * ry, x + s * 0.44, y + s * ry)
                self.line(x + s * 0.58, y + s * ry, x + s * 0.76, y + s * ry)
        elif name == "calendar":
            self.rect(x, y + s * 0.12, s, s * 0.85, style="D")
            self.line(x, y + s * 0.36, x + s, y + s * 0.36)
            self.line(x + s * 0.28, y, x + s * 0.28, y + s * 0.22)
            self.line(x + s * 0.72, y, x + s * 0.72, y + s * 0.22)
        elif name == "euro":
            f = self.font_size_pt
            self.set_font("noto", "B", s * 3.6)
            self.set_text_color(*color)
            self.text(x + s * 0.08, y + s * 0.92, "€")
            self.set_font_size(f)
        elif name == "sigma":
            f = self.font_size_pt
            self.set_font("noto", "B", s * 3.4)
            self.set_text_color(*color)
            self.text(x + s * 0.12, y + s * 0.9, "Σ")
            self.set_font_size(f)
        elif name == "plus":
            self.line(x + s / 2, y + s * 0.1, x + s / 2, y + s * 0.9)
            self.line(x + s * 0.1, y + s / 2, x + s * 0.9, y + s / 2)
        elif name == "minus":
            self.line(x + s * 0.1, y + s / 2, x + s * 0.9, y + s / 2)
        elif name == "health":
            self.set_line_width(lw * 1.9)
            self.line(x + s / 2, y + s * 0.08, x + s / 2, y + s * 0.92)
            self.line(x + s * 0.08, y + s / 2, x + s * 0.92, y + s / 2)
        elif name == "clock":
            self.ellipse(x, y, s, s, style="D")
            self.line(x + s / 2, y + s / 2, x + s / 2, y + s * 0.18)
            self.line(x + s / 2, y + s / 2, x + s * 0.74, y + s * 0.6)
        elif name == "moon":
            self.ellipse(x, y, s, s, style="F")
            self.set_fill_color(*bg)
            self.ellipse(x + s * 0.3, y - s * 0.12, s * 0.85, s * 0.85, style="F")
        elif name == "doc":
            self.rect(x + s * 0.12, y, s * 0.76, s, style="D")
            for ry in (0.3, 0.5, 0.7):
                self.line(x + s * 0.28, y + s * ry, x + s * 0.72, y + s * ry)
        elif name == "bank":
            self.line(x, y + s * 0.34, x + s / 2, y)
            self.line(x + s / 2, y, x + s, y + s * 0.34)
            self.line(x, y + s * 0.34, x + s, y + s * 0.34)
            for rx in (0.14, 0.5, 0.86):
                self.line(x + s * rx, y + s * 0.42, x + s * rx, y + s * 0.82)
            self.line(x, y + s * 0.92, x + s, y + s * 0.92)
        elif name == "check":
            self.line(x + s * 0.12, y + s * 0.55, x + s * 0.4, y + s * 0.85)
            self.line(x + s * 0.4, y + s * 0.85, x + s * 0.9, y + s * 0.15)

    # -- κεφαλίδα εγγράφου -------------------------------------------------
    def title_band(self, title, subtitle, period_label, icon="doc"):
        """Κεφαλίδα τίτλου με στοιχεία εταιρείας & «καρτελάκι» περιόδου.

        Ανοιχτόχρωμη (περιγράμματα αντί για γεμίσματα) — φιλική προς
        εκτύπωση σε χαρτί: τα μεγάλα σκούρα φόντα τρώνε μελάνι."""
        c = proj.company
        w = self.w
        # εικονίδιο εγγράφου σε «τσιπ» με περίγραμμα
        self.set_draw_color(*NAVY)
        self.set_line_width(0.5)
        self.rect(self.l_margin, 5.4, 15, 15, round_corners=True,
                  corner_radius=2.6, style="D")
        self.icon(icon, self.l_margin + 3.6, 8.2, 7.8, NAVY, bg=WHITE)
        self.set_xy(self.l_margin + 19, 6.2)
        self.font(14, bold=True, color=NAVY)
        self.cell(0, 7, title)
        self.set_xy(self.l_margin + 19, 13.4)
        self.font(8.2, color=MUTED)
        who = c.legal_name if c else "—"
        ids = f"ΑΦΜ {c.afm} · ΑΜΕ {c.ame}" if c else ""
        self.cell(0, 4.4, f"{who}   {ids}")
        self.set_xy(self.l_margin + 19, 18.2)
        self.cell(0, 4.4, subtitle)
        # καρτελάκι περιόδου δεξιά (περίγραμμα, όχι γέμισμα)
        self.font(10.4, bold=True, color=NAVY)
        pw = self.get_string_width(period_label) + 10
        self.set_draw_color(*NAVY)
        self.set_line_width(0.5)
        self.rect(w - self.r_margin - pw, 7.6, pw, 10.6, round_corners=True,
                  corner_radius=2.2, style="D")
        self.set_xy(w - self.r_margin - pw, 7.6)
        self.cell(pw, 10.6, period_label, align="C")
        # διπλή γραμμή βάσης της κεφαλίδας
        self.set_draw_color(*NAVY)
        self.set_line_width(0.7)
        self.line(self.l_margin, 24.8, w - self.r_margin, 24.8)
        self.set_draw_color(*BORDER)
        self.set_line_width(0.3)
        self.line(self.l_margin, 26.2, w - self.r_margin, 26.2)
        self.set_y(31)

    def section(self, title, color, icon, note=""):
        """Επικεφαλίδα ενότητας: badge + τίτλος + λεπτή γραμμή."""
        y = self.get_y() + 1.5
        self.badge(self.l_margin, y, 6.4, color, icon)
        self.set_xy(self.l_margin + 8.6, y)
        self.font(10, bold=True, color=color)
        self.cell(self.get_string_width(title) + 2, 6.4, title)
        if note:
            self.font(7.2, color=MUTED)
            self.cell(0, 6.4, note)
        self.set_draw_color(*BORDER)
        self.set_line_width(0.25)
        self.line(self.l_margin, y + 7.8, self.w - self.r_margin, y + 7.8)
        self.set_y(y + 9.6)


# ------------------------------------------------- Μισθοδοτική κατάσταση
STMT_COLS = [  # (τίτλος, πλάτος mm, στοίχιση, χρώμα ποσών ή None)
    ("Α/Α", 8, "C", None),
    ("Εργαζόμενος", 47, "L", None),
    ("Ειδικότητα", 30, "L", None),
    ("Ημ.\nΕΦΚΑ", 11, "C", None),
    ("Βασικές\nαποδοχές", 21, "R", None),
    ("Προσαυ-\nξήσεις", 19, "R", GREEN),
    ("Ασθένεια\n(περικοπή)", 19, "R", AMBER),
    ("ΜΙΚΤΑ", 21, "R", None),
    ("Εισφ.\nΕργαζ.", 19, "R", ROSE),
    ("ΦΜΥ", 17, "R", ROSE),
    ("ΚΑΘΑΡΑ", 21, "R", TEAL),
    ("Εισφ.\nΕργοδ.", 19, "R", MUTED),
    ("Κόστος\nεργοδότη", 21, "R", None),
]


def payroll_statement_pdf(year: int, month: int) -> bytes:
    """Αναλυτική μισθοδοτική κατάσταση περιόδου (A4 οριζόντιο)."""
    rows = q.payroll_analysis(year, month)
    if not rows:
        raise ValueError(f"Δεν έχει υπολογιστεί μισθοδοσία {month}/{year}")

    calc = q.payroll_event_date(year, month)
    calc_note = f" · υπολογισμός {dmy(calc)}" if calc else ""
    pdf = ReportPDF(
        orientation="L",
        footer_note=f"Μισθοδοτική κατάσταση {month}/{year}{calc_note} — "
                    f"εκδόθηκε {dmy(today())}",
    )
    pdf.add_page()
    pdf.title_band(
        "Αναλυτική Μισθοδοτική Κατάσταση",
        f"Τακτικές αποδοχές · {len(rows)} "
        f"εργαζόμενο{'ς' if len(rows) == 1 else 'ι'}{calc_note}",
        f"{MONTHS[month]} {year}",
        icon="doc",
    )

    x0 = pdf.l_margin
    widths = [c[1] for c in STMT_COLS]

    def head():
        pdf.set_fill_color(*FAINT)
        pdf.font(6.9, bold=True, color=NAVY)
        y = pdf.get_y()
        pdf.rect(x0, y, sum(widths), 9, style="F")
        x = x0
        for (title, w, align, _c) in STMT_COLS:
            pdf.set_xy(x, y)
            pdf.multi_cell(w, 4.4 if "\n" in title else 9, title, align="C",
                           new_x=XPos.RIGHT, new_y=YPos.TOP)
            x += w
        pdf.set_draw_color(*NAVY)
        pdf.set_line_width(0.4)
        pdf.line(x0, y, x0 + sum(widths), y)
        pdf.line(x0, y + 9, x0 + sum(widths), y + 9)
        pdf.set_y(y + 9)

    head()
    total = {k: 0.0 for k in ("base", "prem", "sick", "gross", "efka",
                              "fmy", "net", "er", "cost",
                              # σύνολα ΑΝΑ ΦΟΡΕΑ (εργαζ. + εργοδ.) για τις
                              # κάρτες σύνοψης — βλ. queries.contribution_split
                              "c_efka", "c_teka", "c_other")}
    ref = date(year, month, 1)
    for i, r in enumerate(rows):
        a = r["a"]
        emp = proj.employments.get(r["eid"])
        spec = ""
        if emp is not None and emp.insurance_profile is not None:
            spec = getattr(emp.insurance_profile, "specialty_name", "") or ""
        cost = round(a["gross"] + a["efka_employer"], 2)
        vals = [
            str(i + 1),
            None,  # όνομα+ΑΦΜ: ειδική σχεδίαση
            spec or "—",
            str(a.get("efka_days", "")),
            eur(a.get("base_gross", 0.0)),
            eur(a.get("premium_total", 0.0)),
            ("−" + eur(a["sick_deduction"])) if a.get("sick_deduction") else "—",
            eur(a["gross"]),
            eur(a["efka_employee"]),
            eur(a["fmy"]),
            eur(a["net"]),
            eur(a["efka_employer"]),
            eur(cost),
        ]
        total["base"] += a.get("base_gross", 0.0)
        total["prem"] += a.get("premium_total", 0.0)
        total["sick"] += a.get("sick_deduction", 0.0)
        total["gross"] += a["gross"]
        total["efka"] += a["efka_employee"]
        total["fmy"] += a["fmy"]
        total["net"] += a["net"]
        total["er"] += a["efka_employer"]
        total["cost"] += cost
        # Κατανομή των εισφορών ΑΝΑ ΦΟΡΕΑ (ΕΦΚΑ / ΤΕΚΑ / λοιπά ταμεία) —
        # εργαζομένου + εργοδότη, όπως καταβάλλονται στον κάθε φορέα.
        split = q.contribution_split(
            emp, round(a["gross"] - a.get("premium_exempt", 0.0), 2),
            a["efka_employee"], a["efka_employer"], ref
        )
        for carrier in ("efka", "teka", "other"):
            total["c_" + carrier] += (
                split[carrier]["employee"] + split[carrier]["employer"]
            )

        h = 10.4
        if pdf.get_y() + h > pdf.page_break_trigger:
            pdf.add_page()
            head()
        y = pdf.get_y()
        if i % 2:
            pdf.set_fill_color(*ROW_ALT)
            pdf.rect(x0, y, sum(widths), h, style="F")
        x = x0
        for (title, w, align, color), v in zip(STMT_COLS, vals):
            if v is None:  # Εργαζόμενος: όνομα bold + ΑΦΜ/τύπος από κάτω
                pdf.set_xy(x + 1.5, y + 0.8)
                pdf.font(7.6, bold=True)
                pdf.cell(w - 3, 4.2, r["name"])
                pdf.set_xy(x + 1.5, y + 5.2)
                pdf.font(6.2, color=MUTED)
                kind = ("ημερομίσθιο " + eur(a.get("daily_wage") or 0)
                        if r["pay_type"] == "daily"
                        else "μισθός " + eur(a.get("monthly_gross") or 0))
                pdf.cell(w - 3, 3.6, f"ΑΦΜ {emp.afm if emp else ''} · {kind}")
            else:
                pdf.set_xy(x, y)
                pdf.font(7.2, bold=(title in ("ΜΙΚΤΑ", "ΚΑΘΑΡΑ", "Κόστος\nεργοδότη")),
                         color=color or INK)
                pad = 1.5 if align != "C" else 0
                while len(v) > 3 and pdf.get_string_width(v) > w - pad - 1:
                    v = v[:-2].rstrip() + "…"
                pdf.cell(w - pad, h, v, align=align)
            x += w
        pdf.set_draw_color(*BORDER)
        pdf.set_line_width(0.15)
        pdf.line(x0, y + h, x0 + sum(widths), y + h)
        pdf.set_y(y + h)

    # -- γραμμή συνόλων ----------------------------------------------------
    y = pdf.get_y()
    pdf.set_fill_color(*FAINT)
    pdf.rect(x0, y, sum(widths), 9.4, style="F")
    pdf.set_draw_color(*NAVY)
    pdf.set_line_width(0.5)
    pdf.line(x0, y, x0 + sum(widths), y)
    tvals = ["", "ΣΥΝΟΛΑ ΠΕΡΙΟΔΟΥ", "", "",
             eur(total["base"]), eur(total["prem"]),
             ("−" + eur(total["sick"])) if total["sick"] else "—",
             eur(total["gross"]), eur(total["efka"]), eur(total["fmy"]),
             eur(total["net"]), eur(total["er"]), eur(total["cost"])]
    x = x0
    for (title, w, align, color), v in zip(STMT_COLS, tvals):
        pdf.set_xy(x, y)
        pdf.font(7.4, bold=True, color=color or NAVY)
        pdf.cell(w - (1.5 if align != "C" else 0), 9.4, v, align=align)
        x += w
    pdf.set_y(y + 13)

    # -- κάρτες σύνοψης ----------------------------------------------------
    # Τι ΟΦΕΙΛΕΤΑΙ πού: εισφορές ανά φορέα (εργαζ.+εργοδ.), φόρος, καθαρά.
    cards = [
        ("bank", NAVY, "Σύνολο ΕΦΚΑ", total["c_efka"]),
        ("bank", NAVY_SOFT, "Σύνολο ΤΕΚΑ", total["c_teka"]),
        ("minus", ROSE, "Σύνολο ΦΜΥ", total["fmy"]),
        ("check", TEAL, "Καθαρό πληρωτέο", total["net"]),
    ]
    cw = (sum(widths) - 3 * 4) / 4
    x, y = x0, pdf.get_y()
    if y + 16 > pdf.page_break_trigger:
        pdf.add_page()
        y = pdf.get_y()
    for icon, color, label, val in cards:
        pdf.set_fill_color(*FAINT)
        pdf.rect(x, y, cw, 15, round_corners=True, corner_radius=2.4, style="F")
        pdf.set_fill_color(*color)
        pdf.rect(x, y, 1.6, 15, round_corners=True, corner_radius=0.8, style="F")
        pdf.badge(x + 4, y + 4.2, 6.6, color, icon)
        pdf.set_xy(x + 13, y + 2.6)
        pdf.font(6.6, color=MUTED)
        pdf.cell(cw - 14, 4, up(label))
        pdf.set_xy(x + 13, y + 6.8)
        pdf.font(11, bold=True, color=color)
        pdf.cell(cw - 14, 6, eur(round(val, 2)))
        x += cw + 4
    pdf.set_y(y + 19)
    pdf.font(6.6, color=MUTED)
    note = ("Σύνολα ΕΦΚΑ/ΤΕΚΑ: εισφορές εργαζομένου + εργοδότη, ανά φορέα "
            "· μικτές αποδοχές " + eur(round(total["gross"], 2))
            + " · κόστος εργοδότη " + eur(round(total["cost"], 2)) + ". ")
    if round(total["c_other"], 2):
        note += ("Λοιπά ταμεία εκτός ΑΠΔ: "
                 + eur(round(total["c_other"], 2)) + ". ")
    pdf.cell(0, 4, note
             + "Οι κρατήσεις υπολογίζονται από τα ΚΠΚ των καλύψεων κάθε "
               "απασχόλησης · ΦΜΥ με αναγωγή σε ετήσιο εισόδημα ×14.")
    return bytes(pdf.output())


# ---------------------------------------------------- Αποδείξεις αποδοχών
def _line(pdf, label, note, amount, color=INK, bold=False, sign=""):
    """Γραμμή ποσού: ετικέτα αριστερά (+ επεξήγηση από κάτω), ποσό δεξιά."""
    y = pdf.get_y()
    h = 5.2 if not note else 8.6
    if y + h > pdf.page_break_trigger:
        pdf.add_page()
        y = pdf.get_y()
    pdf.set_xy(pdf.l_margin + 2, y)
    pdf.font(8.4, bold=bold)
    pdf.cell(pdf.epw - 40, 5.2, label)
    pdf.set_xy(pdf.w - pdf.r_margin - 38, y)
    pdf.font(8.8, bold=True, color=color)
    pdf.cell(36, 5.2, f"{sign}{eur(amount)}", align="R")
    if note:
        pdf.set_xy(pdf.l_margin + 2, y + 4.6)
        pdf.font(6.8, color=MUTED)
        pdf.cell(pdf.epw - 40, 3.6, note)
    pdf.set_y(y + h)


def _subtotal(pdf, label, amount, color):
    y = pdf.get_y() + 0.6
    pdf.set_fill_color(*FAINT)
    pdf.rect(pdf.l_margin, y, pdf.epw, 6.6, round_corners=True,
             corner_radius=1.6, style="F")
    pdf.set_xy(pdf.l_margin + 2, y)
    pdf.font(8.4, bold=True, color=color)
    pdf.cell(pdf.epw - 40, 6.6, label)
    pdf.set_xy(pdf.w - pdf.r_margin - 38, y)
    txt = ("− " + eur(-amount)) if amount < 0 else eur(amount)
    pdf.cell(36, 6.6, txt, align="R")
    pdf.set_y(y + 8)


def _net_band(pdf: ReportPDF, note: str, net: float):
    """Λωρίδα «ΚΑΘΑΡΑ ΠΛΗΡΩΤΕΑ» με την πράξη από κάτω — ανοιχτόχρωμη
    (απαλό γέμισμα + περίγραμμα, όχι συμπαγές χρώμα: οικονομία μελανιού)."""
    y = pdf.get_y() + 1
    if y + 17 > pdf.page_break_trigger:
        pdf.add_page()
        y = pdf.get_y()
    pdf.set_fill_color(*FAINT)
    pdf.rect(pdf.l_margin, y, pdf.epw, 15, round_corners=True,
             corner_radius=2.6, style="F")
    pdf.set_draw_color(*TEAL)
    pdf.set_line_width(0.5)
    pdf.rect(pdf.l_margin, y, pdf.epw, 15, round_corners=True,
             corner_radius=2.6, style="D")
    pdf.set_fill_color(*TEAL)
    pdf.rect(pdf.l_margin, y, 2, 15, round_corners=True,
             corner_radius=1, style="F")
    pdf.badge(pdf.l_margin + 5, y + 4, 7, TEAL, "check")
    pdf.set_xy(pdf.l_margin + 15, y + 2.4)
    pdf.font(10.5, bold=True, color=TEAL_DARK)
    pdf.cell(80, 6, "ΚΑΘΑΡΑ ΠΛΗΡΩΤΕΑ")
    pdf.set_xy(pdf.l_margin + 15, y + 8.6)
    pdf.font(6.8, color=MUTED)
    pdf.cell(110, 4, note)
    pdf.set_xy(pdf.w - pdf.r_margin - 62, y)
    pdf.font(16, bold=True, color=TEAL_DARK)
    pdf.cell(60, 15, eur(net), align="R")
    pdf.set_y(y + 18.4)


def _signatures(pdf: ReportPDF):
    """Διακεκομμένες γραμμές υπογραφών εργοδότη/εργαζομένου."""
    y = pdf.get_y() + 4
    if y + 18 > pdf.page_break_trigger:
        pdf.add_page()
        y = pdf.get_y() + 4
    pdf.set_draw_color(*MUTED)
    pdf.set_line_width(0.2)
    pdf.set_dash_pattern(dash=1, gap=1)
    half = pdf.epw / 2
    pdf.line(pdf.l_margin + 8, y + 10, pdf.l_margin + half - 16, y + 10)
    pdf.line(pdf.l_margin + half + 16, y + 10, pdf.w - pdf.r_margin - 8, y + 10)
    pdf.set_dash_pattern()
    pdf.font(7, color=MUTED)
    pdf.set_xy(pdf.l_margin, y + 11)
    pdf.cell(half, 4, "Ο Εργοδότης", align="C")
    pdf.cell(half, 4, "Ο/Η Εργαζόμενος/η", align="C")


def _hours(h: float) -> str:
    return f"{num(h)} ώρ{'α' if h == 1 else 'ες'}"


def _variant_label(row) -> str:
    parts = []
    if row["night"]:
        parts.append(f"νύχτα +{row['night_pct']}%")
    if row["holiday"]:
        parts.append(f"Κυριακή/αργία +{row['holiday_pct']}%")
    return " · ".join(parts) if parts else "ημέρα, εργάσιμη"


def _wage_breakdown(emp, a: dict, on_date: date):
    """Σύνθεση του μισθού/ημερομισθίου: βασικός κατώτατος + τριετίες + γάμος
    + λοιπές + «έξτρα» (ό,τι υπερβαίνει το προσαυξημένο κατώτατο όριο). Ίδιοι
    κανόνες με το `services.minimum_floor`/`config.seniority_factor` (οι
    τριετίες ωριμάζουν στον χρόνο). Επιστρέφει {rows: [(ετικέτα, ποσό)],
    total, floor} ή None αν δεν υπάρχει τίποτα πέρα από τον βασικό μισθό."""
    if emp is None:
        return None
    daily = a["pay_type"] == "daily"
    wage = (a.get("daily_wage") if daily else a.get("monthly_gross")) or 0.0
    if wage <= 0:
        return None
    mw = config.minimum_wage_for(on_date)
    rules = config.allowance_rules_for(on_date)
    tsy = emp.total_service_years(on_date)
    if daily:
        base_min = round(mw.daily, 2)
        tri_max, tri_pct = rules.daily_max_triennia, rules.daily_triennium_pct
        rows = [("Βασικό ημερομίσθιο", base_min)]
    else:
        ratio = min(emp.weekly_hours or 40.0, 40.0) / 40.0
        base_min = round(mw.monthly * ratio, 2)
        tri_max, tri_pct = rules.monthly_max_triennia, rules.monthly_triennium_pct
        rows = [("Βασικός μισθός", base_min)]
    tri = config.triennia_count(tsy, tri_max)
    if tri:
        rows.append((f"{tri} τριετίες (+{tri * tri_pct:.0%})",
                     round(base_min * tri * tri_pct, 2)))
    if emp.married and rules.marriage_pct:
        rows.append((f"Επίδομα γάμου (+{rules.marriage_pct:.0%})",
                     round(base_min * rules.marriage_pct, 2)))
    if emp.extra_allowances_pct:
        rows.append((f"Λοιπές προσαυξήσεις (+{num(emp.extra_allowances_pct)}%)",
                     round(base_min * emp.extra_allowances_pct / 100.0, 2)))
    floor = round(sum(amt for _l, amt in rows), 2)
    extra = round(wage - floor, 2)
    if extra > 0.005:
        rows.append(("Έξτρα (πάνω από το κατώτατο όριο)", extra))
    if len(rows) == 1:  # μόνο βασικός, χωρίς προσαυξήσεις/έξτρα — δεν αναλύεται
        return None
    return {"rows": rows, "total": round(wage, 2), "floor": floor}


def _wage_composition(pdf: ReportPDF, wb: dict | None):
    """Πληροφοριακή ανάλυση της σύνθεσης του μισθού, κάτω από τις βασικές
    αποδοχές. ΔΕΝ προστίθεται στα μικτά — εξηγεί πώς προκύπτει ο μισθός
    (βασικός + προσαυξήσεις + έξτρα)."""
    if not wb:
        return
    pdf.set_xy(pdf.l_margin + 4, pdf.get_y() + 0.4)
    pdf.font(6.8, color=MUTED)
    pdf.cell(0, 3.8, "Σύνθεση μισθού (πληροφοριακά — ήδη μέσα στις βασικές "
                     "αποδοχές):")
    pdf.set_y(pdf.get_y() + 4.4)

    def row(label, amount, sign="", bold=False, color=MUTED):
        y = pdf.get_y()
        if y + 4.4 > pdf.page_break_trigger:
            pdf.add_page()
            y = pdf.get_y()
        pdf.set_xy(pdf.l_margin + 8, y)
        pdf.font(7.4, bold=bold, color=color)
        pdf.cell(pdf.epw - 46, 4.2, f"{sign}{label}")
        pdf.set_xy(pdf.w - pdf.r_margin - 38, y)
        pdf.cell(36, 4.2, eur(amount), align="R")
        pdf.set_y(y + 4.6)

    for i, (label, amount) in enumerate(wb["rows"]):
        row(label, amount, sign="" if i == 0 else "+ ")
    row("Σύνολο μισθού", wb["total"], sign="= ", bold=True, color=INK)


def _efka_deduction_lines(pdf: ReportPDF, a: dict):
    """Γραμμές ασφαλιστικών κρατήσεων εργαζομένου ανά ταμείο/ΚΠΚ. Η επιδότηση
    (102/103) αφορά την ΚΥΡΙΑ κάλυψη (1η γραμμή): αυτή δείχνει την ΠΛΗΡΗ
    εισφορά και η επιδότηση ακολουθεί ως ξεχωριστή γραμμή «+»."""
    sub = a.get("subsidy_employee") or 0.0
    for i, d in enumerate(a["efka_detail"]):
        kpk = f" · ΚΠΚ {d['kpk']}" if d.get("kpk") else ""
        _line(pdf, f"Εισφορές — {d['label']}{kpk}",
              f"{num(round(d['employee'] * 100, 2))}% επί των ασφαλιστέων "
              "αποδοχών (μικτά έως το πλαφόν ΕΦΚΑ)",
              round(d["amount"] + (sub if i == 0 else 0), 2),
              color=ROSE, sign="− ")
    if sub:
        _line(pdf, labels.SUBSIDY_LABELS[a.get("subsidy_code") or "102"],
              "μέρος της εισφοράς που ΔΕΝ παρακρατείται — καλύπτεται από "
              "την επιδότηση", sub, color=GREEN, sign="+ ")


def _employer_cost_lines(pdf: ReportPDF, a: dict, note: str):
    """«Στοιχεία κόστους εργοδότη»: πλήρεις εισφορές, μείον την επιδότηση
    εργοδοτικής εισφοράς (107), = κόστος."""
    sub = a.get("subsidy_employer") or 0.0
    _line(pdf, "Ασφαλιστικές εισφορές εργοδότη", note,
          round(a["efka_employer"] + sub, 2), color=MUTED, sign="+ ")
    if sub:
        _line(pdf, labels.SUBSIDY_LABELS["107"],
              "μέρος της εργοδοτικής εισφοράς που δεν καταβάλλεται",
              sub, color=GREEN, sign="− ")
    _subtotal(pdf, "Συνολικό κόστος εργοδότη (μικτά + εισφορές)",
              round(a["gross"] + a["efka_employer"], 2), NAVY)


def _slip_page(pdf: ReportPDF, r: dict, year: int, month: int,
               calc_note: str = ""):
    """Μία σελίδα: πλήρης απόδειξη αποδοχών ενός εργαζόμενου."""
    a = r["a"]
    emp = proj.employments.get(r["eid"])
    pdf.add_page()
    pdf.title_band(
        "Απόδειξη Αποδοχών",
        f"Ανάλυση υπολογισμού τακτικής μισθοδοσίας{calc_note}",
        f"{MONTHS[month]} {year}",
        icon="euro",
    )

    # -- κάρτα εργαζόμενου -------------------------------------------------
    y = pdf.get_y()
    pdf.set_fill_color(*FAINT)
    pdf.rect(pdf.l_margin, y, pdf.epw, 21, round_corners=True,
             corner_radius=2.6, style="F")
    pdf.badge(pdf.l_margin + 4, y + 5.6, 10, TEAL, "person")
    pdf.set_xy(pdf.l_margin + 18, y + 3)
    pdf.font(12, bold=True, color=NAVY)
    pdf.cell(0, 6.4, r["name"])
    spec = ""
    if emp is not None and emp.insurance_profile is not None:
        spec = getattr(emp.insurance_profile, "specialty_name", "") or ""
    pdf.set_xy(pdf.l_margin + 18, y + 9.6)
    pdf.font(7.4, color=MUTED)
    idbits = []
    if emp is not None:
        idbits += [f"ΑΦΜ {emp.afm}", f"ΑΜΚΑ {emp.amka}"]
        if emp.hire_date:
            idbits.append(f"πρόσληψη {dmy(emp.hire_date)}")
    pdf.cell(0, 4, " · ".join(idbits))
    pdf.set_xy(pdf.l_margin + 18, y + 14)
    if r["pay_type"] == "daily":
        contract = f"Ημερομίσθιος · ημερομίσθιο {eur(a.get('daily_wage') or 0)}"
    else:
        wk = emp.weekly_hours if emp is not None else 40
        kind = "πλήρης" if (wk or 40) >= 40 else "μερική"
        contract = (f"Μισθωτός ({kind} απασχόληση {num(wk)} ώρες/εβδ.) · "
                    f"μισθός {eur(a.get('monthly_gross') or 0)}")
    if spec:
        contract += f" · {spec}"
    pdf.font(7.4, color=INK)
    pdf.cell(0, 4, contract)
    # μίνι-στατιστικά δεξιά: ημέρες ΕΦΚΑ & ωρομίσθιο
    sx = pdf.w - pdf.r_margin - 52
    for label, val, icon in (
        ("ΗΜΕΡΕΣ ΕΦΚΑ", str(a.get("efka_days", "—")), "calendar"),
        ("ΩΡΟΜΙΣΘΙΟ", eur(a.get("hourly_rate") or 0), "clock"),
    ):
        pdf.set_fill_color(*WHITE)
        pdf.rect(sx, y + 3.4, 24, 14.2, round_corners=True,
                 corner_radius=2, style="F")
        pdf.icon(icon, sx + 2.6, y + 5.6, 3.4, MUTED)
        pdf.set_xy(sx + 6.6, y + 4.6)
        pdf.font(5.6, color=MUTED)
        pdf.cell(17, 3.4, label)
        pdf.set_xy(sx + 2.6, y + 9.4)
        pdf.font(8.6, bold=True, color=NAVY)
        pdf.cell(20, 5.4, val)
        sx += 26
    pdf.set_y(y + 25)

    # -- ΑΠΟΔΟΧΕΣ ----------------------------------------------------------
    pdf.section("Αποδοχές", GREEN, "plus")
    if r["pay_type"] == "daily":
        base_note = (f"ημερομίσθιο {eur(a.get('daily_wage') or 0)} × "
                     f"{a.get('efka_days', 0)} ημέρες εργασίας")
    else:
        wk = emp.weekly_hours if emp is not None else 40.0
        bdays = a.get("base_days") or pe.monthly_insurance_days(wk or 40.0)
        base_note = (f"μισθός {eur(a.get('monthly_gross') or 0)} × "
                     f"{a.get('efka_days', 0)}/{bdays} ημέρες ΕΦΚΑ")
        if (wk or 40) < 40:
            base_note += (f" — μερική: {bdays} ημέρες εργασίας του μήνα "
                          "κατά το πρόγραμμα")
        if a.get("unpaid_days"):
            base_note += f" · {a['unpaid_days']} ημέρες άνευ αποδοχών"
    _line(pdf, "Βασικές αποδοχές περιόδου", base_note,
          a.get("base_gross", 0.0), bold=True)
    _wage_composition(pdf, _wage_breakdown(emp, a, date(year, month, 1)))

    for g in a.get("premium_detail") or []:
        color = CLASS_COLORS.get(g["cls"], INK)
        if g["covered"]:
            title = f"{g['class_label']} — προσαύξηση νύχτας/αργίας"
            tnote = ("ώρες εντός ωραρίου: καλύπτονται από τον μισθό — "
                     "καταβάλλεται μόνο η προσαύξηση")
        else:
            title = (f"{g['class_label']} (+{g['class_pct']}%)")
            tnote = (f"ωρομίσθιο κλάσης {eur(g['class_rate'])} = "
                     f"{eur(g['hourly'])} × {num(1 + g['class_pct'] / 100)} — "
                     "πληρώνεται ολόκληρη, πέραν του ωραρίου")
        y = pdf.get_y()
        if y + 9 > pdf.page_break_trigger:
            pdf.add_page()
            y = pdf.get_y()
        pdf.set_xy(pdf.l_margin + 2, y)
        pdf.font(8.2, bold=True, color=color)
        pdf.cell(pdf.epw - 40, 4.8, title)
        pdf.set_xy(pdf.l_margin + 2, y + 4.4)
        pdf.font(6.8, color=MUTED)
        pdf.cell(pdf.epw - 40, 3.6, tnote)
        pdf.set_y(y + 8.6)
        for row in g["rows"]:
            if g["covered"]:
                note = (f"    {num(row['hours'])} ω × {eur(g['hourly'])} × "
                        f"{num(row['mult'] * 100)}% — μόνο η προσαύξηση "
                        "(οι ώρες περιέχονται στον μισθό)")
            else:
                note = (f"    {num(row['hours'])} ω × {eur(g['hourly'])} × "
                        f"{num(row['mult'])} (συνολικός συντελεστής "
                        "κλάσης × νύχτας/αργίας)")
            _line(
                pdf,
                f"    {_hours(row['hours'])} — {_variant_label(row)}",
                note, row["amount"], color=color, sign="+ ",
            )
    if a.get("premium_total"):
        _subtotal(pdf, "Σύνολο πρόσθετων αμοιβών ωρών",
                  a["premium_total"], GREEN)

    # -- ΑΣΘΕΝΕΙΑ ----------------------------------------------------------
    if a.get("sick_deduction"):
        pdf.section("Ασθένεια — περικοπή μικτών", AMBER, "health",
                    note="το τριήμερο βαρύνει τον εργοδότη κατά το ήμισυ · "
                         "το επίδομα ΕΦΚΑ δεν καταβάλλεται δεύτερη φορά")
        half = a.get("sick_half_days") or 0
        if half:
            _line(pdf, f"Τριήμερο εργοδότη — {half} ημέρες με μισό ημερομίσθιο",
                  "οι 3 πρώτες ημέρες ασθενείας του κυλιόμενου έτους",
                  0.0 if not a.get("sick_deduction") else
                  round(a["sick_deduction"] - (a.get("sick_benefit") or 0), 2),
                  color=AMBER, sign="− ")
        if a.get("sick_benefit"):
            _line(pdf, "Επίδομα ασθενείας ΕΦΚΑ (καταβλήθηκε από τον ΕΦΚΑ)",
                  "αφαιρείται από τις αποδοχές — δεν το πληρώνει ο εργοδότης",
                  a["sick_benefit"], color=AMBER, sign="− ")
        _subtotal(pdf, "Συνολική περικοπή ασθενείας",
                  -a["sick_deduction"], AMBER)

    # -- ΜΙΚΤΑ -------------------------------------------------------------
    _subtotal(pdf, "ΜΙΚΤΕΣ ΑΠΟΔΟΧΕΣ ΠΕΡΙΟΔΟΥ", a["gross"], NAVY)

    # -- ΚΡΑΤΗΣΕΙΣ ---------------------------------------------------------
    pdf.section("Κρατήσεις εργαζομένου", ROSE, "minus")
    _efka_deduction_lines(pdf, a)
    _line(pdf, "Φόρος μισθωτών υπηρεσιών (ΦΜΥ)",
          f"επί φορολογητέου {eur(a['taxable'])} — αναγωγή σε ετήσιο ×14, "
          "κλίμακα & μείωση τέκνων, διά 14",
          a["fmy"], color=ROSE, sign="− ")
    _subtotal(pdf, "Σύνολο κρατήσεων",
              round(a["efka_employee"] + a["fmy"], 2), ROSE)

    # -- ΚΑΘΑΡΑ ------------------------------------------------------------
    _net_band(pdf, f"μικτά {eur(a['gross'])} − εισφορές "
                   f"{eur(a['efka_employee'])} − ΦΜΥ {eur(a['fmy'])}",
              a["net"])

    # -- ΕΡΓΟΔΟΤΗΣ (πληροφοριακά) — εμφανίζεται κατ' επιλογή («Διάφορα») ---
    if proj.misc_settings.get("show_employer_cost", True):
        pdf.section("Στοιχεία κόστους εργοδότη", NAVY_SOFT, "bank",
                    note="πληροφοριακά — δεν αφορούν τον εργαζόμενο")
        _employer_cost_lines(
            pdf, a, "αποδίδονται στον ΕΦΚΑ μαζί με τις εισφορές εργαζομένου")

    # -- υπογραφές ---------------------------------------------------------
    _signatures(pdf)


def payroll_slips_pdf(year: int, month: int, eid: str | None = None) -> bytes:
    """Αναλυτικές αποδείξεις αποδοχών — μία σελίδα ανά εργαζόμενο.

    `eid`: μόνο ο συγκεκριμένος εργαζόμενος (αλλιώς όλοι της περιόδου)."""
    rows = q.payroll_analysis(year, month)
    if eid:
        rows = [r for r in rows if r["eid"] == eid]
    if not rows:
        raise ValueError(f"Δεν έχει υπολογιστεί μισθοδοσία {month}/{year}")
    calc = q.payroll_event_date(year, month)
    calc_note = f" · υπολογισμός {dmy(calc)}" if calc else ""
    pdf = ReportPDF(
        orientation="P",
        footer_note=f"Απόδειξη αποδοχών {month}/{year}{calc_note} — "
                    f"εκδόθηκε {dmy(today())}",
    )
    for r in rows:
        _slip_page(pdf, r, year, month, calc_note)
    return bytes(pdf.output())


# --------------------------------------- Δώρα εορτών & Επίδομα Αδείας
GIFT_TITLES = {
    "easter": "Δώρο Πάσχα",
    "christmas": "Δώρο Χριστουγέννων",
    "allowance": "Επίδομα Αδείας",
}


def _coeff_pct(coeff: float) -> str:
    """0.04166 -> «4,166» (3 δεκαδικά, χωρίς περιττά μηδενικά)."""
    return f"{coeff * 100:.3f}".rstrip("0").rstrip(".").replace(".", ",")


def _gift_period_label(kind: str, year: int, month: int | None) -> str:
    if kind == "allowance" and month:
        return f"{MONTHS[month]} {year}"
    return f"Έτος {year}"


def _gift_base_note(kind: str, a: dict) -> str:
    """Επεξήγηση της βάσης υπολογισμού — ίδιοι κανόνες με το payroll_engine."""
    daily = a["pay_type"] == "daily"
    wage = eur(a.get("wage") or 0)
    if kind == "allowance":
        months = a.get("months")
        per = "2 ημερομίσθια" if daily else f"2 ημερομίσθια ({wage}/25)"
        cap = "έως 13 ημερομίσθια" if daily else "έως μισό μισθό"
        m_txt = f" × {months} μήνες υπηρεσίας στο έτος" if months else ""
        return f"{per} ανά μήνα{m_txt} — {cap}"
    start, end = a["period"]
    days, full = a.get("days"), a["full_days"]
    span = f"{dmy(start)} – {dmy(end)}"
    if days is not None and days >= full:
        whole = ("25 ημερομίσθια" if daily else "1 μισθός") \
            if kind == "christmas" else \
            ("15 ημερομίσθια" if daily else "μισός μισθός")
        return f"πλήρης απασχόληση {span} → {whole}" + _gift_wage_note(kind, a)
    rule = {
        ("christmas", False): f"2/25 του μισθού {wage} ανά 19 ημερολογιακές ημέρες",
        ("christmas", True): f"1 ημερομίσθιο {wage} ανά 8 ημερολογιακές ημέρες (έως 25)",
        ("easter", False): f"1/15 του μισού μισθού {wage} ανά 8 ημερολογιακές ημέρες",
        ("easter", True): f"1 ημερομίσθιο {wage} ανά 8 ημερολογιακές ημέρες (έως 15)",
    }[(kind, daily)]
    d_txt = f"{days} ημέρες απασχόλησης στην περίοδο {span} · " if days is not None else ""
    return d_txt + rule + _gift_wage_note(kind, a)


def _gift_wage_note(kind: str, a: dict) -> str:
    """Το Δώρο Πάσχα αποτιμάται στον μισθό που ΙΣΧΥΕ 15 ημέρες πριν την
    καταβολή (Μ. Τετάρτη) — γράφεται ρητά στην απόδειξη."""
    if kind != "easter" or not a.get("wage_date"):
        return ""
    return (f" · μισθός ισχύος {dmy(a['wage_date'])} "
            f"(15 ημέρες πριν την καταβολή {dmy(a['pay_date'])})")


def gift_statement_pdf(kind: str, year: int, month: int | None = None) -> bytes:
    """Κατάσταση Δώρου εορτών / Επιδόματος Αδείας (A4 οριζόντιο)."""
    rows = q.gift_analysis(kind, year, month)
    label = GIFT_TITLES[kind]
    per = _gift_period_label(kind, year, month)
    if not rows:
        raise ValueError(f"Δεν έχει υπολογιστεί {label} ({per})")

    calc = q.gift_event_date(kind, year, month)
    calc_note = f" · υπολογισμός {dmy(calc)}" if calc else ""
    pdf = ReportPDF(
        orientation="L",
        footer_note=f"Κατάσταση — {label} {year}{calc_note} — "
                    f"εκδόθηκε {dmy(today())}",
    )
    pdf.add_page()
    pdf.title_band(
        f"Κατάσταση — {label}",
        f"{len(rows)} εργαζόμενο{'ς' if len(rows) == 1 else 'ι'} · "
        f"μπαίνει στην ΑΠΔ της επόμενης μισθολογικής περιόδου{calc_note}",
        per,
        icon="doc",
    )

    cols = [  # (τίτλος, πλάτος mm, στοίχιση, χρώμα ποσών ή None)
        ("Α/Α", 8, "C", None),
        ("Εργαζόμενος", 52, "L", None),
        ("Ειδικότητα", 31, "L", None),
        ("Μήνες\nυπηρ." if kind == "allowance" else "Ημέρες\nπεριόδου",
         14, "C", None),
        ("Βάση\nυπολογ.", 22, "R", None),
        ("Προσαύξ.\nαδείας", 18, "R", GREEN),
        ("ΜΙΚΤΑ", 22, "R", None),
        ("ΕΦΚΑ\nεργαζ.", 20, "R", ROSE),
        ("ΦΜΥ", 18, "R", ROSE),
        ("ΚΑΘΑΡΑ", 22, "R", TEAL),
        ("ΕΦΚΑ\nεργοδ.", 20, "R", MUTED),
        ("Κόστος\nεργοδότη", 22, "R", None),
    ]
    x0 = pdf.l_margin
    widths = [c[1] for c in cols]

    def head():
        pdf.set_fill_color(*FAINT)
        pdf.font(6.9, bold=True, color=NAVY)
        y = pdf.get_y()
        pdf.rect(x0, y, sum(widths), 9, style="F")
        x = x0
        for (title, w, align, _c) in cols:
            pdf.set_xy(x, y)
            pdf.multi_cell(w, 4.4 if "\n" in title else 9, title, align="C",
                           new_x=XPos.RIGHT, new_y=YPos.TOP)
            x += w
        pdf.set_draw_color(*NAVY)
        pdf.set_line_width(0.4)
        pdf.line(x0, y, x0 + sum(widths), y)
        pdf.line(x0, y + 9, x0 + sum(widths), y + 9)
        pdf.set_y(y + 9)

    head()
    total = {k: 0.0 for k in ("base", "prem", "gross", "efka",
                              "fmy", "net", "er", "cost")}
    for i, r in enumerate(rows):
        a = r["a"]
        emp = proj.employments.get(r["eid"])
        spec = ""
        if emp is not None and emp.insurance_profile is not None:
            spec = getattr(emp.insurance_profile, "specialty_name", "") or ""
        prem = round(a["gross"] - a["base"], 2)
        cost = round(a["gross"] + a["efka_employer"], 2)
        basis = (str(a.get("months", "—")) if kind == "allowance"
                 else str(a.get("days", "—")))
        vals = [str(i + 1), None, spec or "—", basis,
                eur(a["base"]), eur(prem) if prem else "—",
                eur(a["gross"]), eur(a["efka_employee"]), eur(a["fmy"]),
                eur(a["net"]), eur(a["efka_employer"]), eur(cost)]
        total["base"] += a["base"]
        total["prem"] += prem
        total["gross"] += a["gross"]
        total["efka"] += a["efka_employee"]
        total["fmy"] += a["fmy"]
        total["net"] += a["net"]
        total["er"] += a["efka_employer"]
        total["cost"] += cost

        h = 10.4
        if pdf.get_y() + h > pdf.page_break_trigger:
            pdf.add_page()
            head()
        y = pdf.get_y()
        if i % 2:
            pdf.set_fill_color(*ROW_ALT)
            pdf.rect(x0, y, sum(widths), h, style="F")
        x = x0
        for (title, w, align, color), v in zip(cols, vals):
            if v is None:  # όνομα bold + ΑΦΜ/αποδοχές από κάτω
                pdf.set_xy(x + 1.5, y + 0.8)
                pdf.font(7.6, bold=True)
                pdf.cell(w - 3, 4.2, r["name"])
                pdf.set_xy(x + 1.5, y + 5.2)
                pdf.font(6.2, color=MUTED)
                kind_txt = ("ημερομίσθιο " if r["pay_type"] == "daily"
                            else "μισθός ") + eur(a.get("wage") or 0)
                pdf.cell(w - 3, 3.6, f"ΑΦΜ {emp.afm if emp else ''} · {kind_txt}")
            else:
                pdf.set_xy(x, y)
                pdf.font(7.2, bold=(title in ("ΜΙΚΤΑ", "ΚΑΘΑΡΑ",
                                              "Κόστος\nεργοδότη")),
                         color=color or INK)
                pad = 1.5 if align != "C" else 0
                pdf.cell(w - pad, h, v, align=align)
            x += w
        pdf.set_draw_color(*BORDER)
        pdf.set_line_width(0.15)
        pdf.line(x0, y + h, x0 + sum(widths), y + h)
        pdf.set_y(y + h)

    # -- γραμμή συνόλων ----------------------------------------------------
    y = pdf.get_y()
    pdf.set_fill_color(*FAINT)
    pdf.rect(x0, y, sum(widths), 9.4, style="F")
    pdf.set_draw_color(*NAVY)
    pdf.set_line_width(0.5)
    pdf.line(x0, y, x0 + sum(widths), y)
    tvals = ["", "ΣΥΝΟΛΑ", "", "",
             eur(total["base"]), eur(total["prem"]) if total["prem"] else "—",
             eur(total["gross"]), eur(total["efka"]), eur(total["fmy"]),
             eur(total["net"]), eur(total["er"]), eur(total["cost"])]
    x = x0
    for (title, w, align, color), v in zip(cols, tvals):
        pdf.set_xy(x, y)
        pdf.font(7.4, bold=True, color=color or NAVY)
        pdf.cell(w - (1.5 if align != "C" else 0), 9.4, v, align=align)
        x += w
    pdf.set_y(y + 13)

    # -- κάρτες σύνοψης ----------------------------------------------------
    cards = [
        ("sigma", NAVY, "Μικτές αποδοχές", total["gross"]),
        ("minus", ROSE, "Κρατήσεις εργαζομένων", total["efka"] + total["fmy"]),
        ("check", TEAL, "Καθαρά πληρωτέα", total["net"]),
        ("bank", NAVY_SOFT, "Κόστος εργοδότη", total["gross"] + total["er"]),
    ]
    cw = (sum(widths) - 3 * 4) / 4
    x, y = x0, pdf.get_y()
    if y + 16 > pdf.page_break_trigger:
        pdf.add_page()
        y = pdf.get_y()
    for icon, color, lab, val in cards:
        pdf.set_fill_color(*FAINT)
        pdf.rect(x, y, cw, 15, round_corners=True, corner_radius=2.4, style="F")
        pdf.set_fill_color(*color)
        pdf.rect(x, y, 1.6, 15, round_corners=True, corner_radius=0.8, style="F")
        pdf.badge(x + 3.4, y + 4.2, 6.6, color, icon)
        pdf.set_xy(x + 11.6, y + 2.6)
        pdf.font(6.2, color=MUTED)
        pdf.cell(cw - 13, 4, up(lab))
        pdf.set_xy(x + 11.6, y + 6.8)
        pdf.font(10, bold=True, color=color)
        pdf.cell(cw - 13, 6, eur(round(val, 2)))
        x += cw + 4
    pdf.set_y(y + 19)
    pdf.font(6.6, color=MUTED)
    note = ("Οι αποδοχές μπαίνουν στο αρχείο ΑΠΔ της επόμενης μισθολογικής "
            "περιόδου, μαζί με τις τακτικές.")
    if kind in ("easter", "christmas"):
        note += (" Στα μικτά περιλαμβάνεται η προσαύξηση συντελεστή αδείας "
                 f"{_coeff_pct(config.GIFT_LEAVE_COEFFICIENT)}%.")
    pdf.cell(0, 4, note)
    return bytes(pdf.output())


def _gift_slip_page(pdf: ReportPDF, r: dict, kind: str, year: int,
                    month: int | None, calc_note: str = ""):
    """Μία σελίδα: απόδειξη Δώρου/Επιδόματος Αδείας ενός εργαζόμενου."""
    a = r["a"]
    emp = proj.employments.get(r["eid"])
    label = GIFT_TITLES[kind]
    pdf.add_page()
    pdf.title_band(
        f"Απόδειξη — {label}",
        f"Ανάλυση υπολογισμού αποδοχών{calc_note}",
        _gift_period_label(kind, year, month),
        icon="euro",
    )

    # -- κάρτα εργαζόμενου -------------------------------------------------
    y = pdf.get_y()
    pdf.set_fill_color(*FAINT)
    pdf.rect(pdf.l_margin, y, pdf.epw, 21, round_corners=True,
             corner_radius=2.6, style="F")
    pdf.badge(pdf.l_margin + 4, y + 5.6, 10, TEAL, "person")
    pdf.set_xy(pdf.l_margin + 18, y + 3)
    pdf.font(12, bold=True, color=NAVY)
    pdf.cell(0, 6.4, r["name"])
    spec = ""
    if emp is not None and emp.insurance_profile is not None:
        spec = getattr(emp.insurance_profile, "specialty_name", "") or ""
    pdf.set_xy(pdf.l_margin + 18, y + 9.6)
    pdf.font(7.4, color=MUTED)
    idbits = []
    if emp is not None:
        idbits += [f"ΑΦΜ {emp.afm}", f"ΑΜΚΑ {emp.amka}"]
        if emp.hire_date:
            idbits.append(f"πρόσληψη {dmy(emp.hire_date)}")
    pdf.cell(0, 4, " · ".join(idbits))
    pdf.set_xy(pdf.l_margin + 18, y + 14)
    contract = ("Ημερομίσθιος · ημερομίσθιο " if r["pay_type"] == "daily"
                else "Μισθωτός · μισθός ") + eur(a.get("wage") or 0)
    if spec:
        contract += f" · {spec}"
    pdf.font(7.4, color=INK)
    pdf.cell(0, 4, contract)
    pdf.set_y(y + 25)

    # -- ΑΠΟΔΟΧΕΣ ----------------------------------------------------------
    pdf.section("Αποδοχές", GREEN, "plus")
    _line(pdf, f"Βάση υπολογισμού — {label}", _gift_base_note(kind, a),
          a["base"], bold=True)
    if a.get("leave_coeff"):
        _line(pdf, f"Προσαύξηση συντελεστή αδείας "
                   f"{_coeff_pct(a['leave_coeff'])}%",
              "επί της βάσης — ενσωματώνεται στα μικτά του Δώρου",
              round(a["gross"] - a["base"], 2), color=GREEN, sign="+ ")
    _subtotal(pdf, "ΜΙΚΤΕΣ ΑΠΟΔΟΧΕΣ", a["gross"], NAVY)

    # -- ΚΡΑΤΗΣΕΙΣ ---------------------------------------------------------
    pdf.section("Κρατήσεις εργαζομένου", ROSE, "minus")
    _efka_deduction_lines(pdf, a)
    _line(pdf, "Φόρος μισθωτών υπηρεσιών (ΦΜΥ)",
          f"επί φορολογητέου {eur(a['taxable'])} — μέσος μηνιαίος "
          "συντελεστής (φορολογείται μαζί με τις λοιπές αποδοχές)",
          a["fmy"], color=ROSE, sign="− ")
    _subtotal(pdf, "Σύνολο κρατήσεων",
              round(a["efka_employee"] + a["fmy"], 2), ROSE)

    # -- ΚΑΘΑΡΑ ------------------------------------------------------------
    _net_band(pdf, f"μικτά {eur(a['gross'])} − εισφορές "
                   f"{eur(a['efka_employee'])} − ΦΜΥ {eur(a['fmy'])}",
              a["net"])

    # -- ΕΡΓΟΔΟΤΗΣ (πληροφοριακά) — εμφανίζεται κατ' επιλογή («Διάφορα») ---
    if proj.misc_settings.get("show_employer_cost", True):
        pdf.section("Στοιχεία κόστους εργοδότη", NAVY_SOFT, "bank",
                    note="πληροφοριακά — δεν αφορούν τον εργαζόμενο")
        _employer_cost_lines(
            pdf, a, "αποδίδονται στον ΕΦΚΑ με την ΑΠΔ της επόμενης περιόδου")

    _signatures(pdf)


def gift_slips_pdf(kind: str, year: int, month: int | None = None,
                   eid: str | None = None) -> bytes:
    """Αποδείξεις Δώρου/Επιδόματος Αδείας — μία σελίδα ανά εργαζόμενο."""
    rows = q.gift_analysis(kind, year, month)
    if eid:
        rows = [r for r in rows if r["eid"] == eid]
    label = GIFT_TITLES[kind]
    per = _gift_period_label(kind, year, month)
    if not rows:
        raise ValueError(f"Δεν έχει υπολογιστεί {label} ({per})")
    calc = q.gift_event_date(kind, year, month)
    calc_note = f" · υπολογισμός {dmy(calc)}" if calc else ""
    pdf = ReportPDF(
        orientation="P",
        footer_note=f"Απόδειξη — {label} {year}{calc_note} — "
                    f"εκδόθηκε {dmy(today())}",
    )
    for r in rows:
        _gift_slip_page(pdf, r, kind, year, month, calc_note)
    return bytes(pdf.output())


# -------------------------- Εκκαθαρίσεις αποχώρησης (αποδείξεις)
def _settlement_emp_card(pdf: ReportPDF, emp, extra: str = ""):
    """Κάρτα εργαζόμενου για τις αποδείξεις εκκαθάρισης αποχώρησης
    (μη ληφθείσα άδεια / αποζημίωση απόλυσης)."""
    y = pdf.get_y()
    pdf.set_fill_color(*FAINT)
    pdf.rect(pdf.l_margin, y, pdf.epw, 21, round_corners=True,
             corner_radius=2.6, style="F")
    pdf.badge(pdf.l_margin + 4, y + 5.6, 10, TEAL, "person")
    pdf.set_xy(pdf.l_margin + 18, y + 3)
    pdf.font(12, bold=True, color=NAVY)
    pdf.cell(0, 6.4, emp.full_name)
    pdf.set_xy(pdf.l_margin + 18, y + 9.6)
    pdf.font(7.4, color=MUTED)
    idbits = [f"ΑΦΜ {emp.afm}", f"ΑΜΚΑ {emp.amka}"]
    if emp.hire_date:
        idbits.append(f"πρόσληψη {dmy(emp.hire_date)}")
    if emp.termination_date:
        idbits.append(f"λήξη {dmy(emp.termination_date)}")
    pdf.cell(0, 4, " · ".join(idbits))
    pdf.set_xy(pdf.l_margin + 18, y + 14)
    daily = emp.pay_type == "daily"
    wage = emp.daily_wage if daily else emp.monthly_gross
    contract = ("Ημερομίσθιος · ημερομίσθιο " if daily
                else "Μισθωτός · μισθός ") + eur(wage or 0)
    if extra:
        contract += f" · {extra}"
    pdf.font(7.4, color=INK)
    pdf.cell(0, 4, contract)
    pdf.set_y(y + 25)


def _settlement_deductions(pdf: ReportPDF, tax_label: str, tax_note: str,
                           fmy: float, net: float, gross: float):
    """Κοινό «τέλος» απόδειξης εκκαθάρισης: κρατήσεις (ΜΟΝΟ ΦΜΥ) + καθαρά."""
    pdf.section("Κρατήσεις", ROSE, "minus",
                note="καμία ασφαλιστική κράτηση")
    _line(pdf, tax_label, tax_note, fmy, color=ROSE, sign="− ")
    _subtotal(pdf, "Σύνολο κρατήσεων", fmy, ROSE)
    _net_band(pdf, f"μικτά {eur(gross)} − φόρος {eur(fmy)}", net)
    _signatures(pdf)


def _leave_comp_slip_page(pdf: ReportPDF, ev, emp):
    """Μία σελίδα: απόδειξη αποζημίωσης μη ληφθείσας αδείας. Κοινή για το
    αυτόνομο PDF και για το συγκεντρωτικό της αποχώρησης."""
    pdf.add_page()
    pdf.title_band(
        "Απόδειξη — Αποζημίωση μη ληφθείσας Αδείας",
        "Εκκαθάριση αποχώρησης · μόνο φορολογική κράτηση (ΦΜΥ)",
        dmy(ev.termination_date),
        icon="euro",
    )
    _settlement_emp_card(pdf, emp)

    pdf.section("Αποδοχές", GREEN, "plus")
    daily_pay = round(ev.gross / ev.comp_days, 2) if ev.comp_days else 0.0
    factor = "×1 (εξαήμερο)" if emp.scheduled_days_per_week() >= 6 \
        else "×1,2 (αναγωγή πενθημέρου σε εξαήμερο)"
    _line(pdf, "Αποζημίωση μη ληφθείσας κανονικής αδείας",
          f"{num(ev.leave_days)} ημέρες υπολοίπου {factor} = "
          f"{num(ev.comp_days)} ημέρες × ημερήσια αμοιβή {eur(daily_pay)}",
          ev.gross, bold=True)
    _subtotal(pdf, "ΜΙΚΤΕΣ ΑΠΟΔΟΧΕΣ", ev.gross, NAVY)

    _settlement_deductions(
        pdf, "Φόρος μισθωτών υπηρεσιών (ΦΜΥ)",
        f"επί των μικτών {eur(ev.gross)} — αναγωγή σε ετήσιο εισόδημα",
        ev.fmy, ev.net, ev.gross)


def leave_compensation_slip_pdf(employment_id: str) -> bytes:
    """Απόδειξη αποζημίωσης μη ληφθείσας κανονικής αδείας (μία σελίδα)."""
    ev = svc.leave_compensation(employment_id)
    emp = proj.employments.get(employment_id)
    if ev is None or emp is None:
        raise ValueError("Δεν έχει υπολογιστεί αποζημίωση μη ληφθείσας αδείας")
    pdf = ReportPDF(
        orientation="P",
        footer_note=f"Απόδειξη — Αποζημίωση μη ληφθείσας Αδείας "
                    f"{dmy(ev.termination_date)} — εκδόθηκε {dmy(today())}",
    )
    _leave_comp_slip_page(pdf, ev, emp)
    return bytes(pdf.output())


def _severance_slip_page(pdf: ReportPDF, ev, emp):
    """Μία σελίδα: απόδειξη αποζημίωσης απόλυσης (κοινή, βλ. παραπάνω)."""
    pdf.add_page()
    pdf.title_band(
        "Απόδειξη — Αποζημίωση Απόλυσης",
        "Καταγγελία σύμβασης · αυτοτελής φορολόγηση, καμία ασφαλιστική κράτηση",
        dmy(ev.termination_date),
        icon="euro",
    )
    _settlement_emp_card(pdf, emp,
                         extra=f"υπηρεσία στον εργοδότη {num(ev.service_years)} έτη")

    pdf.section("Αποδοχές", GREEN, "plus")
    notice = "με προειδοποίηση" if ev.with_notice else "χωρίς προειδοποίηση"
    _line(pdf, "Αποζημίωση απόλυσης",
          f"{num(ev.months)} μηνιαίες αποδοχές ({notice}) × "
          f"{eur(ev.monthly_salary)} × 7/6 (αναγωγή Δώρων/επιδόματος)",
          ev.gross, bold=True)
    _subtotal(pdf, "ΜΙΚΤΕΣ ΑΠΟΔΟΧΕΣ", ev.gross, NAVY)

    _settlement_deductions(
        pdf, "Αυτοτελής φόρος αποζημίωσης",
        "κλιμακωτός συντελεστής — αφορολόγητο έως 60.000 €",
        ev.fmy, ev.net, ev.gross)


def severance_slip_pdf(employment_id: str) -> bytes:
    """Απόδειξη αποζημίωσης απόλυσης (μία σελίδα)."""
    ev = svc.severance(employment_id)
    emp = proj.employments.get(employment_id)
    if ev is None or emp is None:
        raise ValueError("Δεν έχει υπολογιστεί αποζημίωση απόλυσης")
    pdf = ReportPDF(
        orientation="P",
        footer_note=f"Απόδειξη — Αποζημίωση Απόλυσης "
                    f"{dmy(ev.termination_date)} — εκδόθηκε {dmy(today())}",
    )
    _severance_slip_page(pdf, ev, emp)
    return bytes(pdf.output())


# Αποζημιώσεις αποχώρησης ανά είδος: (τίτλος, κατάληξη αρχείου ZIP)
SETTLEMENT_TITLES = {
    "leave": ("Αποζημίωση μη ληφθείσας Αδείας", "AA"),
    "severance": ("Αποζημίωση Απόλυσης", "AP"),
}


def _settlement_month(kind: str, year: int, month: int) -> list:
    """Οι αποζημιώσεις ενός είδους με λήξη μέσα στον μήνα (γραμμή του
    καθολικού «Σύνολα ανά περίοδο»), παλαιότερη πρώτη."""
    events = proj.leave_compensations if kind == "leave" else proj.severances
    evs = [e for e in events if (e.termination_date.year,
                                 e.termination_date.month) == (year, month)]
    if not evs:
        raise ValueError(f"Καμία {SETTLEMENT_TITLES[kind][0]} για {month}/{year}")
    return sorted(evs, key=lambda e: e.termination_date)


def settlement_slips_pdf(kind: str, year: int, month: int) -> bytes:
    """ΟΛΕΣ οι αποδείξεις αποζημίωσης ενός είδους (`leave`/`severance`) του
    μήνα — μία σελίδα ανά εργαζόμενο."""
    page = _leave_comp_slip_page if kind == "leave" else _severance_slip_page
    evs = _settlement_month(kind, year, month)
    pdf = ReportPDF(
        orientation="P",
        footer_note=f"Αποδείξεις — {SETTLEMENT_TITLES[kind][0]} "
                    f"{month}/{year} — εκδόθηκε {dmy(today())}",
    )
    for ev in evs:
        page(pdf, ev, proj.employments.get(ev.employee_id))
    return bytes(pdf.output())


def settlement_statement_pdf(kind: str, year: int, month: int) -> bytes:
    """Κατάσταση αποζημιώσεων ενός είδους του μήνα — μία γραμμή ανά
    εργαζόμενο με τον κανόνα υπολογισμού, και σύνολο."""
    _settlement_month(kind, year, month)  # ValueError αν δεν υπάρχει καμία
    rows = q.settlement_rows(kind, year, month)
    tot = q.settlement_totals(rows)
    label = SETTLEMENT_TITLES[kind][0]
    pdf = ReportPDF(
        orientation="P",
        footer_note=f"Κατάσταση — {label} {month}/{year} — "
                    f"εκδόθηκε {dmy(today())}",
    )
    pdf.add_page()
    pdf.title_band(
        f"Κατάσταση — {label}",
        f"{len(rows)} εργαζόμενο{'ς' if len(rows) == 1 else 'ι'} · "
        "καμία ασφαλιστική κράτηση",
        f"{month:02d}/{year}",
        icon="doc",
    )
    _exit_summary_row(pdf, ["Εργαζόμενος", "Μικτά", "Εισφορές", "ΦΜΥ",
                            "ΚΑΘΑΡΑ"], bold=True, color=NAVY, fill=FAINT)
    for r in rows:
        _exit_summary_row(
            pdf,
            [f"{r['name']} — λήξη {dmy(r['date'])}", eur(r["gross"]),
             eur(0), eur(r["fmy"]), eur(r["net"])],
            note=r["note"],
        )
    _exit_summary_row(
        pdf, ["ΣΥΝΟΛΟ", eur(tot["gross"]), eur(0), eur(tot["fmy"]),
              eur(tot["net"])], bold=True, color=NAVY, fill=FAINT)
    return bytes(pdf.output())


# ------------------- ΣΥΓΚΕΝΤΡΩΤΙΚΟ ΕΚΚΑΘΑΡΙΣΗΣ ΑΠΟΧΩΡΗΣΗΣ (ένα PDF)
# ΣΤΗΛΕΣ της συγκεντρωτικής πρώτης σελίδας (τίτλος, πλάτος mm, στοίχιση)
EXIT_COLS = [
    ("Υπολογισμός", 78, "L"),
    ("Μικτά", 24, "R"),
    ("Εισφορές", 22, "R"),
    ("ΦΜΥ", 22, "R"),
    ("ΚΑΘΑΡΑ", 26, "R"),
]


def _exit_summary_row(pdf: ReportPDF, cells, bold=False, color=INK,
                      note="", fill=None):
    """Μία γραμμή του συγκεντρωτικού πίνακα εκκαθάρισης."""
    y = pdf.get_y()
    h = 6.4 if not note else 9.4
    if y + h > pdf.page_break_trigger:
        pdf.add_page()
        y = pdf.get_y()
    if fill:
        pdf.set_fill_color(*fill)
        pdf.rect(pdf.l_margin, y, pdf.epw, h, round_corners=True,
                 corner_radius=1.6, style="F")
    x = pdf.l_margin + 2
    for (text, (_t, w, align)) in zip(cells, EXIT_COLS):
        pdf.set_xy(x, y + 0.6)
        pdf.font(8.4, bold=bold, color=color if (bold or align == "R") else INK)
        pdf.cell(w - 2, 5.2, text, align=align)
        x += w
    if note:
        pdf.set_xy(pdf.l_margin + 2, y + 5.4)
        pdf.font(6.8, color=MUTED)
        pdf.cell(pdf.epw - 40, 3.6, note)
    pdf.set_y(y + h)


def exit_settlement_pdf(employment_id: str) -> bytes:
    """ΟΛΕΣ οι αποδείξεις της εκκαθάρισης αποχώρησης σε ΕΝΑ αρχείο:

    - **σελίδα 1**: συγκεντρωτικό με μία γραμμή ανά υπολογισμό, σύνολο
      **καταβλητέο στην ημέρα της αποχώρησης** και ΧΩΡΙΣΤΑ το Δώρο εορτών,
      που καταβάλλεται στη δική του ημερομηνία·
    - **επόμενες σελίδες**: μία αναλυτική απόδειξη ανά υπολογισμό, με
      υπογραφές — **εκτός του Δώρου** (καταβάλλεται αργότερα, με τη δική του
      απόδειξη στον τακτικό κύκλο).
    """
    emp = proj.employments.get(employment_id)
    if emp is None or emp.termination_date is None:
        raise ValueError("Η απασχόληση δεν έχει λήξει")
    s = q.exit_settlement(employment_id)
    if not s:
        raise ValueError("Δεν υπάρχουν ποσά εκκαθάρισης αποχώρησης")
    term = emp.termination_date

    pdf = ReportPDF(
        orientation="P",
        footer_note=f"Εκκαθάριση αποχώρησης {emp.full_name} "
                    f"{dmy(term)} — εκδόθηκε {dmy(today())}",
    )

    # ---------------------------------------------- σελίδα 1: συγκεντρωτικό
    pdf.add_page()
    pdf.title_band(
        "Εκκαθάριση Αποχώρησης — Συγκεντρωτικό",
        "Ανάλυση ανά υπολογισμό · καταβλητέο στην ημέρα της αποχώρησης",
        dmy(term),
        icon="euro",
    )
    _settlement_emp_card(pdf, emp)

    pdf.section("Καταβλητέα στην αποχώρηση", GREEN, "plus",
                note=f"εξοφλούνται {dmy(term)}")
    _exit_summary_row(pdf, [c[0] for c in EXIT_COLS], bold=True, color=NAVY,
                      fill=FAINT)
    for it in s["now_lines"]:
        _exit_summary_row(
            pdf,
            [it["label"], eur(it["gross"]), eur(it["efka"]), eur(it["fmy"]),
             eur(it["net"])],
            note=it["note"],
        )
    if s["payroll_missing"]:
        # Οι αποδοχές του μήνα ΔΕΝ σβήνονται σιωπηλά όταν λείπουν
        _exit_summary_row(
            pdf,
            [f"Αποδοχές {s['payroll_period']} — εκκρεμούν", "—", "—", "—", "—"],
            color=AMBER,
            note=f"{s['payroll_missing']} · θα καταβληθούν με τον τακτικό "
                 f"κύκλο μισθοδοσίας",
        )
    _exit_summary_row(
        pdf,
        ["ΣΥΝΟΛΟ", eur(s["now_gross"]), eur(s["now_efka"]), eur(s["now_fmy"]),
         eur(s["now_net"])],
        bold=True, color=NAVY, fill=FAINT,
    )
    _net_band(pdf, f"σύνολο καταβλητέο στην αποχώρηση {dmy(term)}", s["now_net"])

    # ---- Δώρο εορτών: υπολογίζεται τώρα, ΚΑΤΑΒΑΛΛΕΤΑΙ στην ημερομηνία του
    if s["later_lines"]:
        pdf.section("Δώρο εορτών — χωριστή καταβολή", AMBER, "clock",
                    note="δεν καταβάλλεται στην αποχώρηση")
        for it in s["later_lines"]:
            _line(pdf, it["label"],
                  f"μικτά {eur(it['gross'])} · εισφορές {eur(it['efka'])} · "
                  f"ΦΜΥ {eur(it['fmy'])} — καταβάλλεται "
                  f"{dmy(it['pay_date'])}",
                  it["net"], color=AMBER, bold=True)
        _subtotal(pdf, "Σύνολο Δώρου (καθαρά, σε επόμενη ημερομηνία)",
                  s["later_net"], AMBER)

    # ------------------------------------- επόμενες σελίδες: οι αποδείξεις
    for it in s["now_lines"]:
        kind = it["kind"]
        if kind == "payroll":
            rows = [r for r in q.payroll_analysis(term.year, term.month)
                    if r["eid"] == employment_id]
            for r in rows:
                _slip_page(pdf, r, term.year, term.month,
                           calc_note=" · εκκαθάριση αποχώρησης")
        elif kind == "allowance":
            month = emp.allowance_period_month(term.year)
            rows = [r for r in q.gift_analysis("allowance", term.year, month)
                    if r["eid"] == employment_id]
            for r in rows:
                _gift_slip_page(pdf, r, "allowance", term.year, month,
                                calc_note=" · εκκαθάριση αποχώρησης")
        elif kind == "leave_comp":
            _leave_comp_slip_page(pdf, svc.leave_compensation(employment_id), emp)
        elif kind == "severance":
            _severance_slip_page(pdf, svc.severance(employment_id), emp)
    return bytes(pdf.output())


# ---------------------------- ΕΠΙΣΚΟΠΗΣΗ: ετήσια αναφορά κόστους (2 κάρτες)
# Οι δύο κάρτες της επισκόπησης — «Μηνιαία εξέλιξη» και «Πραγματικό κόστος
# εργατοώρας» — σε χαρτί. ΙΔΙΑ ΠΗΓΗ με την οθόνη (`q.annual_cost_report`):
# ίδιοι αριθμοί, ίδιες στήλες, ίδια χρώματα. Τα γραφήματα ξανασχεδιάζονται
# ΔΙΑΝΥΣΜΑΤΙΚΑ εδώ (κανένα raster από τον καμβά του browser) — το PDF
# παράγεται στον server, δεν χρειάζεται ανοιχτή σελίδα.

COST_ACCENT = "#2A78D6"  # 1η θέση της παλέτας (queries.COST_COMPONENTS)


def _rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _tint(rgb, f: float = 0.58) -> tuple:
    """Ίδια απόχρωση, πιο ανοιχτή. Έτσι ξεχωρίζει η ΠΡΟΒΛΕΨΗ από το
    υπολογισμένο ποσό — όπως στην οθόνη, χωρίς δεύτερο χρώμα."""
    return tuple(round(c + (255 - c) * f) for c in rgb)


def _nice_top(v: float) -> float:
    """Στρογγυλή κορυφή άξονα ≥ v, ώστε το πλέγμα να πέφτει σε ήσυχους
    αριθμούς (10k, 25k, 50k…) αντί για την ωμή μέγιστη τιμή."""
    if v <= 0:
        return 0.0
    base = 10 ** math.floor(math.log10(v))
    for f in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if v <= f * base:
            return f * base
    return 10 * base


def _axis_money(v: float) -> str:
    return (num(round(v / 1000, 1)) + "k€") if v >= 1000 else (num(round(v)) + "€")


def _grid(pdf: ReportPDF, x0, y0, w, h, top):
    """Πλέγμα 5 γραμμών με ετικέτες ποσού αριστερά. Επιστρέφει (x, πλάτος,
    ύψος) της περιοχής σχεδίασης."""
    lab_w, ah = 14.0, h - 5.4
    ax, aw = x0 + lab_w, w - lab_w
    for i in range(5):
        gy = y0 + ah - ah * i / 4
        pdf.set_draw_color(*BORDER)
        pdf.set_line_width(0.15)
        pdf.line(ax, gy, ax + aw, gy)
        pdf.font(5.6, color=MUTED)
        pdf.set_xy(x0, gy - 2)
        pdf.cell(lab_w - 1.6, 4, _axis_money(top * i / 4), align="R")
    return ax, aw, ah


def _month_axis(pdf: ReportPDF, ax, aw, ybase, months):
    slot = aw / 12
    for i, m in enumerate(months):
        pdf.font(5.6, color=MUTED)
        pdf.set_xy(ax + slot * i, ybase + 0.5)
        pdf.cell(slot, 4, m["label"], align="C")
    pdf.set_draw_color(*MUTED)
    pdf.set_line_width(0.25)
    pdf.line(ax, ybase, ax + aw, ybase)


def _cost_bars(pdf: ReportPDF, x0, y0, w, h, months, comps, ms: str):
    """Στοιβαγμένες στήλες: 12 μήνες × 4 συνιστώσες. Ανοιχτός τόνος = ποσό
    που περιέχει πρόβλεψη — η πληροφορία δεν κρέμεται μόνο από το χρώμα, την
    ξαναλέει η στήλη «πρόβλεψη» του πίνακα."""
    cells = [m["by"][ms] for m in months]
    top = _nice_top(max([c["total"] for c in cells] + [0.0]))
    if not top:
        return
    ax, aw, ah = _grid(pdf, x0, y0, w, h, top)
    slot = aw / 12
    bw = min(slot * 0.6, 8.0)
    for i, c in enumerate(cells):
        cx, yb = ax + slot * i + (slot - bw) / 2, y0 + ah
        for k, color in comps:
            v = c["parts"][k]
            if not v:
                continue
            bh = ah * v / top
            rgb = _rgb(color)
            pdf.set_fill_color(*(_tint(rgb) if c["fore"][k] else rgb))
            pdf.rect(cx, yb - bh, bw, bh, style="F")
            yb -= bh
    _month_axis(pdf, ax, aw, y0 + ah, months)


def _cum_line(pdf: ReportPDF, x0, y0, w, h, months, ms: str, first_forecast):
    """Σωρευτική πορεία μέσα στο έτος. Από τον πρώτο μήνα πρόβλεψης και μετά
    η γραμμή γίνεται διακεκομμένη — το σχήμα λέει «από εδώ εκτιμάμε»."""
    vals = [m["by"][ms]["cumulative"] for m in months]
    top = _nice_top(max(vals + [0.0]))
    if not top:
        return
    ax, aw, ah = _grid(pdf, x0, y0, w, h, top)
    slot = aw / 12
    pts = [(ax + slot * (i + 0.5), y0 + ah - ah * v / top)
           for i, v in enumerate(vals)]
    accent = _rgb(COST_ACCENT)
    fi = (first_forecast - 1) if first_forecast else len(pts)
    pdf.set_draw_color(*accent)
    pdf.set_line_width(0.6)
    for i in range(len(pts) - 1):
        if i + 1 >= fi:  # το τμήμα μπαίνει στην περιοχή της πρόβλεψης
            pdf.set_dash_pattern(dash=1.3, gap=1.3)
        pdf.line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
        pdf.set_dash_pattern()
    for i in (fi - 1, len(pts) - 1):
        if 0 <= i < len(pts) and vals[i]:
            pdf.set_fill_color(*accent)
            pdf.ellipse(pts[i][0] - 0.8, pts[i][1] - 0.8, 1.6, 1.6, style="F")
    if vals[-1]:
        pdf.font(6.4, bold=True, color=NAVY)
        pdf.set_xy(ax + aw - 32, pts[-1][1] - 5.4)
        pdf.cell(32, 4, eur(vals[-1]), align="R")
    _month_axis(pdf, ax, aw, y0 + ah, months)


def _kpi_cards(pdf: ReportPDF, cards, width):
    """Οι αριθμοί-κλειδιά σε κάρτες (ίδιο μοτίβο με τη μισθοδοτική
    κατάσταση): εικονίδιο, ετικέτα, ποσό, μία γραμμή επεξήγησης."""
    cw = (width - 4 * (len(cards) - 1)) / len(cards)
    x, y = pdf.l_margin, pdf.get_y()
    for icon, color, label, val, note in cards:
        pdf.set_fill_color(*FAINT)
        pdf.rect(x, y, cw, 18, round_corners=True, corner_radius=2.4, style="F")
        pdf.set_fill_color(*color)
        pdf.rect(x, y, 1.6, 18, round_corners=True, corner_radius=0.8, style="F")
        pdf.badge(x + 4, y + 4.6, 6.6, color, icon)
        pdf.set_xy(x + 13, y + 2.2)
        pdf.font(6.4, color=MUTED)
        pdf.cell(cw - 14, 4, up(label))
        pdf.set_xy(x + 13, y + 6.2)
        pdf.font(11, bold=True, color=color)
        pdf.cell(cw - 14, 6, val)
        pdf.set_xy(x + 13, y + 12.6)
        pdf.font(5.9, color=MUTED)
        while note and pdf.get_string_width(note) > cw - 15:
            note = note[:-2].rstrip() + "…"
        pdf.cell(cw - 14, 4, note)
        x += cw + 4
    pdf.set_y(y + 22)


class Table:
    """Μικρός ζωγράφος πίνακα: κεφαλίδα που ξαναμπαίνει σε κάθε σελίδα και
    γραμμές με ζέβρα. Οι στήλες είναι (τίτλος, πλάτος mm, στοίχιση)."""

    def __init__(self, pdf: ReportPDF, cols):
        self.pdf, self.cols = pdf, cols
        self.x0 = pdf.l_margin
        self.w = sum(c[1] for c in cols)

    def head(self):
        pdf, y = self.pdf, self.pdf.get_y()
        pdf.set_fill_color(*FAINT)
        pdf.rect(self.x0, y, self.w, 8.6, style="F")
        x = self.x0
        for (title, w, _a) in self.cols:
            pdf.set_xy(x, y)
            pdf.font(6.6, bold=True, color=NAVY)
            pdf.multi_cell(w, 4.3 if "\n" in title else 8.6, title, align="C",
                           new_x=XPos.RIGHT, new_y=YPos.TOP)
            x += w
        pdf.set_draw_color(*NAVY)
        pdf.set_line_width(0.4)
        pdf.line(self.x0, y, self.x0 + self.w, y)
        pdf.line(self.x0, y + 8.6, self.x0 + self.w, y + 8.6)
        pdf.set_y(y + 8.6)

    def row(self, vals, h=6.6, bold=False, fill=None, rule=False, color=INK):
        pdf = self.pdf
        y = pdf.get_y()
        if y + h > pdf.page_break_trigger:
            pdf.add_page()
            self.head()
            y = pdf.get_y()
        if fill:
            pdf.set_fill_color(*fill)
            pdf.rect(self.x0, y, self.w, h, style="F")
        if rule:
            pdf.set_draw_color(*NAVY)
            pdf.set_line_width(0.4)
            pdf.line(self.x0, y, self.x0 + self.w, y)
        x = self.x0
        for (title, w, align), v in zip(self.cols, vals):
            pdf.set_xy(x, y)
            pdf.font(6.8, bold=bold, color=color)
            pad = 1.8 if align != "C" else 0
            v = str(v)
            while len(v) > 3 and pdf.get_string_width(v) > w - pad - 1:
                v = v[:-2].rstrip() + "…"
            pdf.cell(w - pad, h, v, align=align)
            x += w
        pdf.set_draw_color(*BORDER)
        pdf.set_line_width(0.12)
        pdf.line(self.x0, y + h, self.x0 + self.w, y + h)
        pdf.set_y(y + h)


# Οι σταθερές στήλες της «Μηνιαίας εξέλιξης» — ανάμεσά τους μπαίνουν οι 4
# συνιστώσες κόστους (`q.COST_COMPONENTS`), όσες κι αν είναι.
COST_HEAD_COLS = [("Περίοδος", 24, "L"), ("Π.ώρες", 17, "R"),
                  ("Αργίες", 15, "R"), ("Άδειες", 15, "R"), ("Ώρες", 17, "R")]
COST_TAIL_COLS = [("Σύνολο", 30, "R"), ("Σωρευτικά", 28, "R"), ("", 15, "C")]


def _measure(report: dict, measure: str) -> dict:
    """Η οπτική της οθόνης: κόστος / μικτά / καθαρά (q.COST_MEASURES)."""
    for ms in report["measures"]:
        if ms["key"] == measure:
            return ms
    raise ValueError(f"Άγνωστη οπτική «{measure}»")


def annual_cost_pdf(year: int, measure: str = "cost") -> bytes:
    """ΜΗΝΙΑΙΑ ΕΞΕΛΙΞΗ — η ετήσια αναφορά κόστους της επισκόπησης, σε A4
    οριζόντιο: οι τρεις αριθμοί-κλειδιά, τα δύο γραφήματα, ο πίνακας των 12
    μηνών με τα σύνολα και τις ώρες."""
    report = q.annual_cost_report(year)
    ms = _measure(report, measure)
    if not ms["total"]:
        raise ValueError(f"Δεν υπάρχει κόστος μισθοδοσίας για το {year}")
    comps = [(c["key"], c["color"]) for c in ms["components"]]

    pdf = ReportPDF(
        orientation="L",
        footer_note=f"Μηνιαία εξέλιξη {year} · {ms['label'].lower()} "
                    f"({ms['note']}) — εκδόθηκε {dmy(today())}",
    )
    pdf.add_page()
    pdf.title_band(
        "Μηνιαία Εξέλιξη Κόστους",
        f"{ms['label']} ανά περίοδο — {ms['note']} · με πρόβλεψη έως το τέλος "
        "του έτους",
        str(year),
        icon="euro",
    )
    width = pdf.w - pdf.l_margin - pdf.r_margin
    first = ms["first_forecast"]

    _kpi_cards(pdf, [
        ("check", TEAL, "Υπολογισμένο", eur(ms["actual"]),
         "ό,τι έχει ήδη υπολογιστεί (events μισθοδοσίας, Δώρων, επιδόματος)"),
        ("clock", AMBER, "Πρόβλεψη υπολοίπου", eur(ms["forecast"]),
         f"{MONTHS[first]} – Δεκεμβρίου {year}: εκτίμηση στις συμβάσεις που "
         f"ισχύουν" if first else f"τίποτε δεν εκκρεμεί για το {year}"),
        ("sigma", NAVY, f"Προβλεπόμενο σύνολο {year}", eur(ms["total"]),
         "υπολογισμένο + πρόβλεψη"),
    ], width)

    # -- λεζάντα: τα 4 χρώματα ΜΕ το ετήσιο σύνολό τους δίπλα -------------
    y, x = pdf.get_y(), pdf.l_margin
    for c in ms["components"]:
        pdf.set_fill_color(*_rgb(c["color"]))
        pdf.rect(x, y + 1.2, 3.2, 3.2, round_corners=True,
                 corner_radius=0.8, style="F")
        pdf.set_xy(x + 4.6, y)
        pdf.font(6.6, color=INK)
        label = c["label"] + "  "
        pdf.cell(pdf.get_string_width(label), 5.4, label)
        pdf.font(6.6, bold=True, color=NAVY)
        amount = eur(c["total"])
        pdf.cell(pdf.get_string_width(amount), 5.4, amount)
        x += 4.6 + pdf.get_string_width(label) + pdf.get_string_width(amount) + 9
    pdf.set_fill_color(*_tint(_rgb(COST_ACCENT)))
    pdf.rect(x, y + 1.2, 3.2, 3.2, round_corners=True, corner_radius=0.8, style="F")
    pdf.set_xy(x + 4.6, y)
    pdf.font(6.6, color=MUTED)
    pdf.cell(70, 5.4, "ανοιχτόχρωμη στήλη = πρόβλεψη")
    pdf.set_y(y + 7.4)

    # -- τα δύο γραφήματα, δίπλα-δίπλα ------------------------------------
    y, gw = pdf.get_y(), (width - 10) / 2
    pdf.font(7, bold=True, color=NAVY)
    pdf.set_xy(pdf.l_margin, y)
    pdf.cell(gw, 4.4, "Σύνθεση ανά μήνα")
    pdf.set_xy(pdf.l_margin + gw + 10, y)
    pdf.cell(gw, 4.4, "Σωρευτικά μέσα στο έτος")
    _cost_bars(pdf, pdf.l_margin, y + 5.4, gw, 42, report["months"], comps, measure)
    _cum_line(pdf, pdf.l_margin + gw + 10, y + 5.4, gw, 42, report["months"],
              measure, first)
    pdf.set_y(y + 52)

    # -- ο πίνακας των 12 μηνών (η προσβάσιμη εκδοχή των γραφημάτων) ------
    # Ο πίνακας ΔΕΝ σπάει στη μέση: αν δεν χωράει ολόκληρος κάτω από τα
    # γραφήματα, πάει σε δική του σελίδα (12 μήνες + σύνολα + ώρες ≈ 115mm).
    t = Table(pdf, COST_HEAD_COLS
              + [(c["label"], 28, "R") for c in ms["components"]]
              + COST_TAIL_COLS)
    if pdf.get_y() + 116 > pdf.page_break_trigger:
        pdf.add_page()
    t.head()
    for i, m in enumerate(report["months"]):
        b = m["by"][measure]
        t.row([m["long"],
               hours_gr(m["sched_hours"]) if m["sched_hours"] else "—",
               hours_gr(m["holiday_hours"]) if m["holiday_hours"] else "—",
               hours_gr(m["leave_hours"]) if m["leave_hours"] else "—",
               hours_gr(m["hours"]) if m["hours"] else "—"]
              + [eur(b["parts"][c["key"]]) if b["parts"][c["key"]] else "—"
                 for c in ms["components"]]
              + [eur(b["total"]) if b["total"] else "—",
                 eur(b["cumulative"]) if b["cumulative"] else "—",
                 "πρόβλεψη" if b["estimated"] else ""],
              fill=ROW_ALT if i % 2 else None)
    t.row([f"Σύνολο {year}", hours_gr(report["sched_hours"]),
           hours_gr(report["holiday_hours"]), hours_gr(report["leave_hours"]),
           hours_gr(report["hours"])]
          + [eur(c["total"]) for c in ms["components"]]
          + [eur(ms["total"]), "", ""],
          h=7.6, bold=True, fill=FAINT, rule=True, color=NAVY)
    # Το ΥΠΟΛΟΙΠΟ ΑΔΕΙΑΣ δεν ανήκει σε κανέναν μήνα — αφαιρείται ΜΕΤΑ το
    # σύνολο, ρητά, ώστε να φαίνεται πώς προκύπτει ο παρονομαστής του
    # κόστους εργατοώρας (η αναφορά από κάτω).
    if report["unused_leave_hours"]:
        pad = [""] * (len(ms["components"]) + 3)
        t.row(["μείον υπόλοιπο κανονικής αδείας", "", "", "",
               "−" + hours_gr(report["unused_leave_hours"])] + pad, color=MUTED)
        t.row([f"Ώρες εργασίας {year}", "", "", "",
               hours_gr(report["work_hours"])] + pad,
              h=7.6, bold=True, fill=FAINT, color=NAVY)

    # -- η σημείωση που συνοδεύει την κάρτα στην οθόνη --------------------
    pdf.set_y(pdf.get_y() + 3)
    if pdf.get_y() + 22 > pdf.page_break_trigger:
        pdf.add_page()
    pdf.font(6.4, color=MUTED)
    pdf.multi_cell(
        t.w, 3.4,
        "Π.ΩΡΕΣ είναι όσες προβλέπει το πρόγραμμα, μαζί με τις αργίες και τις "
        "άδειες· ΩΡΕΣ είναι οι πραγματικά δουλεμένες (π.ώρες − αργίες − "
        "άδειες): πληρώνονται και οι τρεις στήλες, εργατοώρα όμως παράγει "
        "μόνο η τελευταία. Το υπόλοιπο της κανονικής αδείας δεν ανήκει σε "
        "κανέναν μήνα, γι' αυτό αφαιρείται μετά το σύνολο: εξαντλείται μέσα "
        "στο ημερολογιακό έτος, οπότε θα ληφθεί ή θα αποζημιωθεί ούτως ή "
        "άλλως.\n"
        "Η ΠΡΟΒΛΕΨΗ είναι εκτίμηση, όχι υπολογισμός: παίρνει τη σύμβαση που "
        "ισχύει και υποθέτει πλήρη κανονικό μήνα — χωρίς άδειες άνευ "
        "αποδοχών, ασθένεια ή προσαυξήσεις ωρών, που γεννιούνται από "
        "παρουσίες οι οποίες δεν έχουν συμβεί. Το Επίδομα Αδείας όσων δεν "
        "έχουν πάρει ακόμη άδεια χρεώνεται στον Δεκέμβριο. Οι αποζημιώσεις "
        "αποχώρησης δεν προβλέπονται ποτέ — καμία απόλυση δεν είναι "
        "προγραμματισμένη. Το έτος ακολουθεί την ημερομηνία αναφοράς του UI.")
    return bytes(pdf.output())


# ------------------------------------ ΠΡΑΓΜΑΤΙΚΟ ΚΟΣΤΟΣ ΕΡΓΑΤΟΩΡΑΣ (PDF)
HOURLY_COLS = [
    ("Εργαζόμενος", 40, "L"),
    ("Ώρες\nμηνών", 20, "R"),
    ("Υπόλοιπο\nαδείας", 21, "R"),
    ("Ώρες\nεργασίας", 21, "R"),
    ("Συνολικό\nκόστος", 25, "R"),
    ("Κόστος\n/ ώρα", 20, "R"),
    ("Πληρωτέο", 22, "R"),
    ("Εργ/Ωρα", 16, "R"),
]
HOURLY_NOMINAL = "#9EC5F4"  # ωρομίσθιο σύμβασης — ο ίδιος ήχος, πιο ήσυχος
HOURLY_REAL = COST_ACCENT   # πραγματικό κόστος/ώρα


def _hour_bars(pdf: ReportPDF, x0, y0, w, rows):
    """Δύο σκαλιά της ΙΔΙΑΣ απόχρωσης ανά εργαζόμενο: ανοιχτό = ωρομίσθιο
    σύμβασης, σκούρο = πραγματικό κόστος/ώρα. Το κενό ανάμεσά τους ΕΙΝΑΙ η
    επιβάρυνση — γι' αυτό μπαίνουν το ένα πάνω από το άλλο, όχι χωριστά."""
    top = _nice_top(max([max(r["nominal"], r["rate"] or 0) for r in rows]))
    if not top:
        return 0.0
    name_w, lab_w = 42.0, 30.0
    ax = x0 + name_w
    aw = w - name_w - lab_w
    rh = 8.0
    h = rh * len(rows)
    for i in range(5):  # κατακόρυφο πλέγμα με ετικέτες ποσού από κάτω
        gx = ax + aw * i / 4
        pdf.set_draw_color(*BORDER)
        pdf.set_line_width(0.15)
        pdf.line(gx, y0, gx, y0 + h)
        pdf.font(5.6, color=MUTED)
        pdf.set_xy(gx - 8, y0 + h + 0.6)
        pdf.cell(16, 4, _axis_money(top * i / 4), align="C")
    for i, r in enumerate(rows):
        y = y0 + rh * i
        pdf.font(6.6, color=INK)
        pdf.set_xy(x0, y + 1.4)
        name = r["name"]
        while name and pdf.get_string_width(name) > name_w - 3:
            name = name[:-2].rstrip() + "…"
        pdf.cell(name_w - 2, 5.2, name)
        for j, (val, color) in enumerate(((r["nominal"], HOURLY_NOMINAL),
                                          (r["rate"] or 0, HOURLY_REAL))):
            pdf.set_fill_color(*_rgb(color))
            pdf.rect(ax, y + 1.4 + j * 2.9, max(aw * val / top, 0.2), 2.4,
                     style="F")
        if r["rate"]:
            pdf.font(6.4, bold=True, color=INK)
            pdf.set_xy(ax + aw + 1.5, y + 1.4)
            ratio = ("  ×" + num(r["ratio"])) if r["ratio"] else ""
            pdf.cell(lab_w - 2, 5.2, eur(r["rate"]) + ratio)
    pdf.set_draw_color(*MUTED)
    pdf.set_line_width(0.25)
    pdf.line(ax, y0 + h, ax + aw, y0 + h)
    return h + 6


def hourly_cost_pdf(year: int) -> bytes:
    """ΠΡΑΓΜΑΤΙΚΟ ΚΟΣΤΟΣ ΕΡΓΑΤΟΩΡΑΣ — όλο το ετήσιο κόστος εργοδότη διά τις
    ώρες που ΔΟΥΛΕΥΤΗΚΑΝ, ανά εργαζόμενο, δίπλα στο ωρομίσθιο της σύμβασης
    (A4 κατακόρυφο)."""
    report = q.annual_cost_report(year)
    rows = report["hourly"]["rows"]
    if not rows:
        raise ValueError(f"Δεν υπάρχουν ώρες εργασίας για το {year}")
    ht = report["hourly"]["total"]

    pdf = ReportPDF(
        footer_note=f"Πραγματικό κόστος εργατοώρας {year} — "
                    f"εκδόθηκε {dmy(today())}",
    )
    pdf.add_page()
    pdf.title_band(
        "Πραγματικό Κόστος Εργατοώρας",
        "Όλο το κόστος εργοδότη διά τις ώρες που δουλεύτηκαν — άδειες, "
        "υπόλοιπο αδείας και αργίες δεν μετρούν στις ώρες",
        str(year),
        icon="clock",
    )
    width = pdf.w - pdf.l_margin - pdf.r_margin

    _kpi_cards(pdf, [
        ("euro", NAVY, "Κόστος / ώρα", eur(ht["rate"] or 0) + " /ώρα",
         f"έναντι {eur(ht['nominal'])} ωρομίσθιο σύμβασης"),
        ("clock", TEAL, "Ώρες εργασίας", hours_gr(ht["hours"]) + " ώρες",
         (f"{hours_gr(ht['month_hours'])} μείον "
          f"{hours_gr(ht['unused_leave'])} υπόλοιπο αδείας")
         if ht["unused_leave"] else
         f"{hours_gr(ht['certain_hours'])} από αυτές εγκεκριμένες"),
        ("plus", AMBER, "Επιβάρυνση",
         ("×" + num(ht["ratio"])) if ht["ratio"] else "—",
         "πόσο κοστίζει η ώρα πάνω από το ωρομίσθιο"),
    ], width)

    # -- λεζάντα + οριζόντιες μπάρες ανά εργαζόμενο ------------------------
    y = pdf.get_y()
    x = pdf.l_margin
    for color, label in ((HOURLY_NOMINAL, "Ωρομίσθιο σύμβασης"),
                         (HOURLY_REAL, "Πραγματικό κόστος/ώρα")):
        pdf.set_fill_color(*_rgb(color))
        pdf.rect(x, y + 1.2, 3.2, 3.2, round_corners=True,
                 corner_radius=0.8, style="F")
        pdf.set_xy(x + 4.6, y)
        pdf.font(6.6, color=INK)
        pdf.cell(pdf.get_string_width(label) + 1, 5.4, label)
        x += 4.6 + pdf.get_string_width(label) + 10
    pdf.set_y(y + 7)

    # Το γράφημα χωράει σε μία σελίδα ή πάει στην επόμενη· με πολλούς
    # εργαζόμενους παραλείπεται — τα ίδια νούμερα τα λέει ο πίνακας.
    need = 8.0 * len(rows) + 8
    if need < pdf.h - 40:
        if pdf.get_y() + need > pdf.page_break_trigger:
            pdf.add_page()
        pdf.set_y(pdf.get_y() + _hour_bars(pdf, pdf.l_margin, pdf.get_y(),
                                           width, rows))

    t = Table(pdf, HOURLY_COLS)
    if pdf.get_y() + 30 > pdf.page_break_trigger:
        pdf.add_page()
    t.head()
    for i, r in enumerate(rows):
        t.row([r["name"], hours_gr(r["month_hours"]),
               ("−" + hours_gr(r["unused_leave"])) if r["unused_leave"] else "—",
               hours_gr(r["hours"]), eur(r["cost"]),
               eur(r["rate"]) if r["rate"] else "—", eur(r["net"]),
               eur(r["take"]) if r["take"] else "—"],
              fill=ROW_ALT if i % 2 else None)
    t.row(["Σύνολο εταιρείας", hours_gr(ht["month_hours"]),
           ("−" + hours_gr(ht["unused_leave"])) if ht["unused_leave"] else "—",
           hours_gr(ht["hours"]), eur(ht["cost"]),
           eur(ht["rate"]) if ht["rate"] else "—", eur(ht["net"]),
           eur(ht["take"]) if ht["take"] else "—"],
          h=7.6, bold=True, fill=FAINT, rule=True, color=NAVY)

    pdf.set_y(pdf.get_y() + 3)
    if pdf.get_y() + 26 > pdf.page_break_trigger:
        pdf.add_page()
    pdf.font(6.4, color=MUTED)
    pdf.multi_cell(
        t.w, 3.4,
        "Η ΕΠΙΒΑΡΥΝΣΗ είναι όσο κοστίζει η ώρα εργασίας πάνω από το "
        "ωρομίσθιο: εργοδοτικές εισφορές, Δώρα εορτών, Επίδομα Αδείας, "
        "αποζημιώσεις — και οι πληρωμένες ημέρες που δεν δουλεύτηκαν "
        "(κανονική άδεια, ασθένεια, αργίες), αφού το κόστος τους "
        "επιμερίζεται στις ώρες που όντως δουλεύτηκαν. Ώρες: από το "
        "εγκεκριμένο συγκεντρωτικό παρουσιών όπου υπάρχει (κανονικές + "
        "πρόσθετη + υπερεργασία + υπερωρία)· για τους υπόλοιπους μήνες "
        "εκτιμώνται από το πρόγραμμα εργασίας, μείον άδειες και αργίες. "
        "Εργασία ΣΕ αργία ή Κυριακή μετράει κανονικά — είναι ώρα που "
        f"δουλεύτηκε ({hours_gr(ht['certain_hours'])} από "
        f"{hours_gr(ht['month_hours'])} ώρες μηνών είναι εγκεκριμένες). Από "
        "αυτές αφαιρείται στο τέλος το υπόλοιπο της κανονικής αδείας — "
        "ημέρες που δεν έχουν προγραμματιστεί ακόμη, αλλά θα ληφθούν ή θα "
        "αποζημιωθούν μέσα στο έτος.\n"
        "Το ΠΛΗΡΩΤΕΟ είναι τα καθαρά που εισπράττει ο εργαζόμενος μέσα στο "
        "έτος (μισθοδοσία, Δώρα, Επίδομα Αδείας, αποζημιώσεις — μετά "
        "εισφορές και ΦΜΥ) και η ΕΡΓ/ΩΡΑ είναι το ίδιο ποσό διά τις ΙΔΙΕΣ "
        "δουλεμένες ώρες. Η απόσταση από το «κόστος/ώρα» είναι όσα δεν "
        "φτάνουν ποτέ στην τσέπη: εισφορές εργοδότη, εισφορές εργαζόμενου, "
        "ΦΜΥ.")
    return bytes(pdf.output())


# ------------------------------------- ZIP αποδείξεων (ανά εργαζόμενο)
# Φάκελος αποθήκευσης — σχετικός με το cwd (ρίζα project, ίδιο με το
# payroll.db). Λειτουργική ρύθμιση, ΟΧΙ domain γεγονός: δεν αποθηκεύεται
# στο event store.
EXPORT_DIR = Path("apodeijeis")

# Κατάληξη ονόματος αρχείου ανά τύπο Δώρου/Επιδόματος (τακτική = καμία).
GIFT_SUFFIX = {"easter": "ΔΠ", "christmas": "ΔΧ", "allowance": "EA"}


# ------------------------------------------------- Βεβαιώσεις εργαζομένου
def _cert_parties(pdf: ReportPDF, person):
    """Κάρτες «Εργοδότης» / «Εργαζόμενος» στην κορυφή μιας βεβαίωσης."""
    c = proj.company
    if c is None:
        raise ValueError("Δεν έχουν καταχωρηθεί στοιχεία εταιρείας")
    b = proj.branches.get(0)
    addr = ", ".join(x for x in (
        f"{b.street} {b.street_no}".strip() if b else "",
        f"{b.postal_code} {b.city}".strip() if b else "") if x)
    cards = [
        ("building", "ΕΡΓΟΔΟΤΗΣ", c.legal_name,
         " · ".join(x for x in (f"ΑΦΜ {c.afm}", f"ΑΜΕ {c.ame}", addr) if x)),
        ("person", "ΕΡΓΑΖΟΜΕΝΟΣ/Η", person.full_name,
         " · ".join(x for x in (
             f"πατρώνυμο {person.father_name}" if person.father_name else "",
             f"ΑΦΜ {person.afm}", f"ΑΜΚΑ {person.amka}") if x)),
    ]
    for icon, cap, name, line in cards:
        y = pdf.get_y()
        pdf.set_fill_color(*FAINT)
        pdf.rect(pdf.l_margin, y, pdf.epw, 19, round_corners=True,
                 corner_radius=2.6, style="F")
        pdf.badge(pdf.l_margin + 4, y + 4.6, 10, TEAL, icon)
        pdf.set_xy(pdf.l_margin + 18, y + 2.4)
        pdf.font(6.6, bold=True, color=MUTED)
        pdf.cell(0, 4, cap)
        pdf.set_xy(pdf.l_margin + 18, y + 6.4)
        pdf.font(11, bold=True, color=NAVY)
        pdf.cell(0, 6, name)
        pdf.set_xy(pdf.l_margin + 18, y + 12.6)
        pdf.font(7.4, color=INK)
        pdf.cell(0, 4, line)
        pdf.set_y(y + 22)


def _cert_text(pdf: ReportPDF, text: str):
    pdf.font(9.6, color=INK)
    pdf.multi_cell(0, 5.6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)


def _cert_signature(pdf: ReportPDF):
    """Τόπος/ημερομηνία και υπογραφή ΜΟΝΟ του εργοδότη (η βεβαίωση είναι
    δήλωση του εργοδότη)."""
    b = proj.branches.get(0)
    place = f"{b.city}, " if b and b.city else ""
    y = max(pdf.get_y() + 8, 225)
    if y + 30 > pdf.page_break_trigger:
        pdf.add_page()
        y = pdf.get_y() + 8
    x = pdf.l_margin + pdf.epw / 2
    pdf.set_xy(x, y)
    pdf.font(9, color=INK)
    pdf.cell(pdf.epw / 2, 5, f"{place}{dmy(today())}", align="C")
    pdf.set_xy(x, y + 6)
    pdf.font(8, bold=True, color=NAVY)
    pdf.cell(pdf.epw / 2, 5, "Για τον εργοδότη", align="C")
    pdf.set_draw_color(*MUTED)
    pdf.set_line_width(0.2)
    pdf.set_dash_pattern(dash=1, gap=1)
    pdf.line(x + 12, y + 26, pdf.w - pdf.r_margin - 12, y + 26)
    pdf.set_dash_pattern()
    pdf.set_xy(x, y + 27)
    pdf.font(7, color=MUTED)
    pdf.cell(pdf.epw / 2, 4, "(σφραγίδα & υπογραφή)", align="C")


def tax_certificate_pdf(person_id: str, year: int) -> bytes:
    """Ετήσια βεβαίωση αποδοχών για φορολογική χρήση (μία σελίδα A4)."""
    c = q.tax_certificate(person_id, year, today())
    person, t = c["person"], c["totals"]
    pdf = ReportPDF(footer_note=f"Βεβαίωση αποδοχών {year} — {person.full_name}"
                                f" — εκδόθηκε {dmy(today())}")
    pdf.add_page()
    pdf.title_band("Βεβαίωση Αποδοχών", "για φορολογική χρήση",
                   f"Έτος {year}", icon="euro")
    _cert_parties(pdf, person)

    pdf.section("Περίοδος εργασίας", NAVY, "calendar",
                note=f"σύνολο {c['days']} ημερολογιακές ημέρες")
    _cert_text(pdf, "\n".join(f"από {dmy(a)} έως {dmy(b)}"
                              for a, b in c["periods"]) or "—")

    pdf.section("Αποδοχές & κρατήσεις", GREEN, "sigma")
    tbl = Table(pdf, [("Είδος", 62, "L"), ("Ακαθάριστες", 32, "R"),
                      ("Κρατήσεις\n(πριν φόρους)", 32, "R"),
                      ("Παρακρατηθείς\nφόρος", 32, "R"),
                      ("Καθαρό\nπληρωτέο", pdf.epw - 158, "R")])
    tbl.head()
    for i, k in enumerate(c["kinds"]):
        tbl.row([k["label"], eur(k["gross"]), eur(k["efka_employee"]),
                 eur(k["fmy"]), eur(k["net"])],
                fill=ROW_ALT if i % 2 else None)
    tbl.row(["ΣΥΝΟΛΟ", eur(t["gross"]), eur(t["efka_employee"]),
             eur(t["fmy"]), eur(t["net"])], h=7.6, bold=True, rule=True,
            color=NAVY)
    pdf.ln(4)
    _cert_text(pdf, (
        f"Βεβαιώνεται ότι ο/η {person.full_name} έλαβε κατά το έτος {year} "
        f"ακαθάριστες αποδοχές {eur(t['gross'])}, από τις οποίες "
        f"παρακρατήθηκαν ασφαλιστικές εισφορές {eur(t['efka_employee'])} και "
        f"φόρος μισθωτών υπηρεσιών {eur(t['fmy'])}, με καθαρό πληρωτέο ποσό "
        f"{eur(t['net'])}. Η βεβαίωση χορηγείται για φορολογική χρήση."))
    _cert_signature(pdf)
    return bytes(pdf.output())


def employment_certificate_pdf(person_id: str) -> bytes:
    """Βεβαίωση εργασίας / προϋπηρεσίας (μία σελίδα A4)."""
    c = q.employment_certificate(person_id)
    person = c["person"]
    pdf = ReportPDF(footer_note=f"Βεβαίωση εργασίας — {person.full_name}"
                                f" — εκδόθηκε {dmy(today())}")
    pdf.add_page()
    pdf.title_band("Βεβαίωση Εργασίας", "προϋπηρεσίας / απασχόλησης",
                   dmy(today()), icon="doc")
    _cert_parties(pdf, person)

    lines = []
    for p in c["periods"]:
        span = (f"από {dmy(p['date_from'])} έως {dmy(p['date_to'])}"
                if p["date_to"] else f"από {dmy(p['date_from'])} έως σήμερα")
        kind = (f"μερική απασχόληση ({num(p['weekly_hours'])} ώρες/εβδομάδα)"
                if p["part_time"] else "πλήρη απασχόληση")
        rel = "ορισμένου" if p["fixed_term"] else "αορίστου"
        role = f" ως {p['specialty']}" if p["specialty"] else ""
        lines.append(f"•  {span}{role}, με {kind}, σύμβαση {rel} χρόνου.")
    verb = "εργάζεται" if c["active"] else "εργάστηκε"
    _cert_text(pdf, f"Βεβαιώνεται ότι ο/η {person.full_name} {verb} στην "
                    f"επιχείρησή μας με σχέση εξαρτημένης εργασίας:")
    _cert_text(pdf, "\n".join(lines))
    _cert_text(pdf, "Η βεβαίωση χορηγείται ύστερα από αίτηση του/της "
                    "ενδιαφερόμενου/ης για κάθε νόμιμη χρήση.")
    _cert_signature(pdf)
    return bytes(pdf.output())


def _company_afm() -> str:
    """ΑΦΜ εταιρείας για το όνομα του ZIP."""
    if proj.company is None:
        raise ValueError("Δεν έχει καταχωρηθεί εταιρεία")
    return proj.company.afm


def _emp_afm(eid: str) -> str:
    """ΑΦΜ εργαζόμενου για το όνομα του PDF."""
    return proj.employments[eid].afm


def _gift_file_month(kind: str, year: int, month: int | None) -> int:
    """Ο μήνας που μπαίνει στο όνομα αρχείου Δώρου/Επιδόματος: το Επίδομα
    Αδείας έχει πραγματικό μήνα· το Δώρο Χριστουγέννων → Δεκέμβριος (12),
    το Δώρο Πάσχα → ο μήνας της ΚΑΤΑΒΟΛΗΣ του (Μ. Τετάρτη — ίδια πηγή με το
    `svc.gift_fmy_period`, ώστε αρχείο και δήλωση ΦΜΥ να λένε το ίδιο)."""
    if kind == "allowance":
        return month or 0
    if kind == "christmas":
        return 12
    return calendar_gr.holy_wednesday(year).month


def save_export(filename: str, data: bytes) -> Path:
    """Αποθηκεύει το ZIP στον φάκελο apodeijeis (τον δημιουργεί αν λείπει)
    και επιστρέφει τη διαδρομή."""
    EXPORT_DIR.mkdir(exist_ok=True)
    path = EXPORT_DIR / filename
    path.write_bytes(data)
    return path


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    """Πακετάρει (όνομα, bytes) σε ZIP στη μνήμη (UTF-8 ονόματα)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


def payroll_slips_zip(year: int, month: int) -> tuple[str, bytes]:
    """ZIP με μία απόδειξη αποδοχών ανά εργαζόμενο (αφμ-έτος-μήνας.pdf).
    Επιστρέφει (όνομα_zip, bytes). Όνομα zip: αφμΕταιρίας-έτος-μήνας.zip."""
    rows = q.payroll_analysis(year, month)
    if not rows:
        raise ValueError(f"Δεν έχει υπολογιστεί μισθοδοσία {month}/{year}")
    entries = [
        (f"{_emp_afm(r['eid'])}-{year}-{month:02d}.pdf",
         payroll_slips_pdf(year, month, r["eid"]))
        for r in rows
    ]
    name = f"{_company_afm()}-{year}-{month:02d}.zip"
    return name, _zip(entries)


def gift_slips_zip(kind: str, year: int,
                   month: int | None = None) -> tuple[str, bytes]:
    """ZIP με μία απόδειξη Δώρου/Επιδόματος ανά εργαζόμενο. Ονόματα PDF:
    αφμ-έτος-μήνας-{ΔΠ|ΔΧ|EA}.pdf· zip: αφμΕταιρίας-έτος-μήνας-{…}.zip."""
    rows = q.gift_analysis(kind, year, month)
    label = GIFT_TITLES[kind]
    if not rows:
        per = _gift_period_label(kind, year, month)
        raise ValueError(f"Δεν έχει υπολογιστεί {label} ({per})")
    suffix = GIFT_SUFFIX[kind]
    fm = _gift_file_month(kind, year, month)
    entries = [
        (f"{_emp_afm(r['eid'])}-{year}-{fm:02d}-{suffix}.pdf",
         gift_slips_pdf(kind, year, month, r["eid"]))
        for r in rows
    ]
    name = f"{_company_afm()}-{year}-{fm:02d}-{suffix}.zip"
    return name, _zip(entries)


def settlement_slips_zip(kind: str, year: int,
                         month: int) -> tuple[str, bytes]:
    """ZIP με μία απόδειξη αποζημίωσης ανά εργαζόμενο του μήνα. Ονόματα PDF:
    αφμ-έτος-μήνας-{AA|AP}.pdf· zip: αφμΕταιρίας-έτος-μήνας-{AA|AP}.zip."""
    build = (leave_compensation_slip_pdf if kind == "leave"
             else severance_slip_pdf)
    suffix = SETTLEMENT_TITLES[kind][1]
    entries = [
        (f"{_emp_afm(ev.employee_id)}-{year}-{month:02d}-{suffix}.pdf",
         build(ev.employee_id))
        for ev in _settlement_month(kind, year, month)
    ]
    name = f"{_company_afm()}-{year}-{month:02d}-{suffix}.zip"
    return name, _zip(entries)
