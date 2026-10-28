"""Το ΒΙΒΛΙΟ της Βοήθειας σε PDF: εξώφυλλο, περιεχόμενα, ένα μέρος ανά ομάδα
θεμάτων και ένα κεφάλαιο ανά άρθρο.

➜ ΔΕΝ έχει δικό του κείμενο: αποδίδει τα ΙΔΙΑ templates `help/<slug>.html`
  (`body_only`) και τα περνά στο `write_html` του fpdf2 — μία πηγή αλήθειας με
  τη Βοήθεια της εφαρμογής. Το HTML απλοποιείται πρώτα σε ό,τι ξέρει το fpdf2
  (κουμπιά → «έντονα», συμβουλές/προσοχή → χρωματιστή παράγραφος, χωρίς συνδέσμους/emoji —
  η NotoSans δεν έχει emoji).

Καλείται μέσα σε request (τα templates θέλουν `url_for`/context).
"""

import re
import tomllib
from datetime import date
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from flask import render_template
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

from .pdf_reports import (
    BORDER,
    INK,
    MUTED,
    NAVY,
    NAVY_SOFT,
    ROW_ALT,
    TEAL,
    WHITE,
    ReportPDF,
    dmy,
)

# emoji & σύμβολα εκτός NotoSans (θα έβγαιναν κενά κουτάκια)
_NO_GLYPH = re.compile(r"[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍⃣]")


# σύμβολα που η NotoSans δεν έχει → ισοδύναμο κείμενο
_SUBST = str.maketrans({"→": "›", "←": "‹", "≤": "έως", "◆": "", "＝": "", "■": "",
                        "↩": "", **{chr(0x2460 + i): f"{i + 1}." for i in range(9)}})


def _txt(s: str) -> str:
    return _NO_GLYPH.sub("", s).translate(_SUBST)


def _clean(html: str) -> str:
    """HTML άρθρου → το υποσύνολο που αποδίδει σωστά το fpdf2."""
    s = _NO_GLYPH.sub("", html).translate(_SUBST)
    s = re.sub(r"\{#.*?#\}", "", s, flags=re.S)
    s = re.sub(r'<span class="ui">\s*(.*?)\s*</span>', r"<b>«\1»</b>", s, flags=re.S)
    s = re.sub(r'<div class="hint">(.*?)</div>',
               r'<p><font color="#0D8076"><b>Συμβουλή:</b> \1</font></p>', s, flags=re.S)
    s = re.sub(r'<div class="warnbox">(.*?)</div>',
               r'<p><font color="#B06216"><b>Προσοχή:</b> \1</font></p>', s, flags=re.S)
    # σύνδεσμοι της εφαρμογής (άχρηστοι στο χαρτί) και πλάγια (χωρίς γραμματοσειρά)
    s = re.sub(r"</?(a|span|i|em|div)\b[^>]*>", "", s)
    s = re.sub(r"<(ol|ul|table|h3|p|tr|td|th|li)\b[^>]*>", r"<\1>", s)  # χωρίς class/attrs
    # Τα κελιά πινάκων του fpdf2 δέχονται μόνο σκέτο κείμενο
    s = re.sub(r"<(td|th)>(.*?)</\1>",
               lambda m: f"<{m[1]}>{re.sub(r'<[^>]+>', '', m[2])}</{m[1]}>", s, flags=re.S)
    # πίνακες: στοίχιση αριστερά (αλλιώς πλήρης), περιθώριο κελιού, απαλή κεφαλίδα
    s = s.replace("<table>", '<table cellpadding="1.6" width="100%">')
    s = re.sub(r"<(td|th)>", r'<\1 align="left">', s)
    s = s.replace("<th ", '<th bgcolor="#EEF3F9" ')
    # αέρας ανάμεσα στις γραμμές (το fpdf2 το δέχεται ως style σε p/ul/ol)
    s = re.sub(r"<(p|ul|ol)>", r'<\1 style="line-height:1.45">', s)
    return re.sub(r"[ \t]*\n\s*", " ", s).strip()


class HelpBook(ReportPDF):
    def __init__(self):
        super().__init__(footer_note="misevent — Οδηγός χρήσης")
        self.set_margins(20, 18, 20)
        self.set_auto_page_break(True, margin=20)
        self.chapter = ""

    def header(self):
        if self.page_no() <= 1 or not self.chapter:
            return
        self.set_y(9)
        self.font(7.5, color=MUTED)
        self.cell(0, 4, self.chapter, align="R")
        self.set_draw_color(*BORDER)
        self.line(self.l_margin, 14, self.w - self.r_margin, 14)
        self.set_xy(self.l_margin, 18)

    def footer(self):
        if self.page_no() > 1:
            super().footer()

    def multi_cell(self, *args, **kwargs):
        # πάντα στην αρχή της επόμενης γραμμής (το fpdf2 αφήνει δεξιά)
        kwargs.setdefault("new_x", XPos.LMARGIN)
        kwargs.setdefault("new_y", YPos.NEXT)
        return super().multi_cell(*args, **kwargs)

    # -- σελίδες ------------------------------------------------------------
    def cover(self, ver: str):
        self.add_page()
        self.set_fill_color(*NAVY)
        self.rect(0, 0, self.w, self.h, style="F")
        self.set_fill_color(*NAVY_SOFT)
        self.rect(0, self.h * 0.62, self.w, self.h * 0.38, style="F")
        self.set_fill_color(*TEAL)
        self.rect(20, 78, 46, 2.2, style="F")
        self.set_xy(20, 52)
        self.font(13, bold=True, color=(160, 200, 196))
        self.cell(0, 8, "misevent", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_xy(20, 86)
        self.font(34, bold=True, color=WHITE)
        self.multi_cell(150, 15, "Οδηγός χρήσης")
        self.set_x(20)
        self.font(14, color=(205, 216, 232))
        self.multi_cell(160, 8, "Ελληνική μισθοδοσία βήμα-βήμα: από την πρόσληψη "
                        "ως την ΑΠΔ, τον ΦΜΥ και τα Δώρα")
        self.set_xy(20, self.h * 0.62 + 18)
        self.font(10.5, color=(205, 216, 232))
        self.multi_cell(160, 6.5, "Για λογιστές και χρήστες της εφαρμογής.\n"
                        "Το ίδιο περιεχόμενο υπάρχει και μέσα στην εφαρμογή, "
                        "στο μενού «Βοήθεια».")
        self.set_xy(20, self.h - 30)
        self.font(9, color=(160, 176, 200))
        self.cell(0, 5, f"Έκδοση {ver}  ·  {dmy(date.today())}")

    def part(self, n: int, title: str, topics: list):
        self.chapter = title
        if self.get_y() > self.t_margin + 1:  # η σελίδα μετά τα περιεχόμενα είναι ήδη κενή
            self.add_page()
        self.start_section(f"Μέρος {n} — {title}", level=0)
        self.set_y(70)
        self.font(11, bold=True, color=TEAL)
        self.cell(0, 6, f"ΜΕΡΟΣ {n}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.font(26, bold=True, color=NAVY)
        self.multi_cell(0, 12, title)
        self.set_fill_color(*TEAL)
        self.rect(self.l_margin, self.get_y() + 3, 30, 1.6, style="F")
        self.ln(14)
        for t in topics:
            self.font(11.5, bold=True, color=INK)
            self.cell(0, 6.5, t["title"], new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.font(9.5, color=MUTED)
            self.multi_cell(0, 5, t["summary"])
            self.ln(3)

    def article(self, topic: dict, html: str):
        self.add_page()
        self.start_section(topic["title"], level=1)
        self.font(20, bold=True, color=NAVY)
        self.multi_cell(0, 10, topic["title"])
        self.font(10.5, color=MUTED)
        self.multi_cell(0, 6, topic["summary"])
        self.set_fill_color(*TEAL)
        self.rect(self.l_margin, self.get_y() + 2, 22, 1.2, style="F")
        self.ln(8)
        self.font(10.5, color=INK)
        # οι πίνακες γεμίζουν με το τρέχον χρώμα — όχι το teal της γραμμής πάνω
        self.set_fill_color(*ROW_ALT)
        self.write_html(
            html,
            font_family="noto",
            li_prefix_color=TEAL,
            tag_styles={
                "h3": FontFace(family="noto", emphasis="BOLD", color=NAVY, size_pt=13),
                "b": FontFace(family="noto", emphasis="BOLD"),
            },
            table_line_separators=True,
        )


def _toc(pdf: HelpBook, outline):
    pdf.chapter = "Περιεχόμενα"
    pdf.set_xy(pdf.l_margin, pdf.t_margin)
    pdf.font(22, bold=True, color=NAVY)
    pdf.cell(0, 12, "Περιεχόμενα", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)
    # μόνο μέρη & κεφάλαια — τα <h3> των άρθρων μένουν στους σελιδοδείκτες
    for s in (s for s in outline if s.level <= 1):
        top = s.level == 0
        pdf.font(11.5 if top else 10.5, bold=top, color=NAVY if top else INK)
        if top:
            pdf.ln(3)
        indent = 0 if top else 6
        pdf.set_x(pdf.l_margin + indent)
        w = pdf.w - pdf.l_margin - pdf.r_margin - indent
        pdf.cell(w - 12, 7, s.name, link=pdf.add_link(page=s.page_number))
        pdf.cell(12, 7, str(s.page_number), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def help_book_pdf() -> bytes:
    from .views.help import TOPICS

    try:
        ver = version("misevent")
    except PackageNotFoundError:
        try:  # εκτέλεση από τον φάκελο του κώδικα (χωρίς εγκατεστημένο πακέτο)
            pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
            ver = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
        except (OSError, KeyError, tomllib.TOMLDecodeError):
            ver = "—"
    pdf = HelpBook()
    pdf.set_title("misevent — Οδηγός χρήσης")
    pdf.set_author("misevent")
    pdf.cover(ver)
    pdf.add_page()
    pdf.insert_toc_placeholder(_toc, pages=1)  # ανοίγει μόνο του την επόμενη σελίδα
    groups: dict[str, list] = {}
    for t in TOPICS:
        groups.setdefault(t["group"], []).append(t)
    for n, (g, topics) in enumerate(groups.items(), 1):
        plain = [{**t, "title": _txt(t["title"]), "summary": _txt(t["summary"])}
                 for t in topics]
        pdf.part(n, g, plain)
        for t, p in zip(topics, plain, strict=True):
            html = render_template(f"help/{t['slug']}.html", topic=t, body_only=True)
            pdf.article(p, _clean(html))
    return bytes(pdf.output())
