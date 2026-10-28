"""Βοήθεια: κάθε άρθρο αποδίδεται, κάθε σελίδα του μενού έχει άρθρο, το ❓
της κεφαλίδας δείχνει στο σωστό, και η αναζήτηση αγνοεί τόνους."""

from pathlib import Path


def test_every_nav_page_has_help():
    from webapp.labels import NAV_GROUPS, NAV_TOP
    from webapp.views.help import help_for

    endpoints = [e for e, _i, _l in NAV_TOP if e != "help.index"]
    endpoints += [e for _k, _l, _i, items in NAV_GROUPS for e, *_ in items]
    missing = [e for e in endpoints if not help_for(e)]
    assert not missing, f"σελίδες χωρίς άρθρο Βοήθειας: {missing}"
    assert help_for("attendance.index") == "attendance"
    assert help_for("company.specialty_modal") == "specialties"
    assert help_for("company.index") == "company"


def test_every_topic_has_template():
    from webapp.views.help import TOPICS

    folder = Path(__file__).parent.parent / "webapp" / "templates" / "help"
    assert all((folder / f"{t['slug']}.html").exists() for t in TOPICS)


def test_help_pages_render_and_search(fresh_system):
    from _bootstrap import ui_client
    from markupsafe import escape

    from webapp import create_app
    from webapp.views.help import TOPICS

    client = ui_client(create_app())
    for t in TOPICS:
        r = client.get(f"/help/{t['slug']}")
        assert r.status_code == 200, t["slug"]
        assert str(escape(t["title"])) in r.get_data(as_text=True)
    assert client.get("/help/nope").status_code == 404

    # ❓ της κεφαλίδας → άρθρο της οθόνης
    assert 'href="/help/attendance"' in client.get("/attendance/").get_data(as_text=True)

    # αναζήτηση χωρίς τόνους/πεζά
    body = client.get("/help/?q=τεκα").get_data(as_text=True)
    assert "Ειδικότητες &amp; ασφάλιση" in body
    assert "Δεν βρέθηκε" in client.get("/help/?q=ξξξψψψ").get_data(as_text=True)


def test_help_book_pdf(fresh_system):
    """Το βιβλίο PDF: παράγεται από τα ίδια άρθρα, με περιεχόμενα και ένα
    κεφάλαιο (τουλάχιστον μία σελίδα) ανά θέμα."""
    import io

    from _bootstrap import ui_client
    from pypdf import PdfReader

    from webapp import create_app
    from webapp.views.help import TOPICS

    r = ui_client(create_app()).get("/help/book.pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    pdf = PdfReader(io.BytesIO(r.data))
    assert len(pdf.pages) > len(TOPICS)
    toc = pdf.pages[1].extract_text()
    assert "Περιεχόμενα" in toc and "Συχνές ερωτήσεις" in toc
