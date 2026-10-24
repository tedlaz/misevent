"""Λωρίδα χρόνου του header: έτη χρήσης και κατάσταση ανά μήνα."""

from datetime import date

import _bootstrap  # noqa: F401

from misevent.domain.projections import Obligation
from webapp.queries import build_time_strip


def _ob(y, m, due, fulfilled=False):
    return Obligation("x", "MONTHLY_PAYROLL", due, "", y, m, fulfilled)


def test_years_from_first_period_to_ref_and_month_states():
    ref = date(2026, 3, 15)
    s = build_time_strip(ref, [
        _ob(2024, 11, date(2024, 12, 5), fulfilled=True),
        _ob(2026, 2, date(2026, 3, 5)),          # εκπρόθεσμη
        _ob(2026, 3, date(2026, 4, 5)),          # ανοικτή
        _ob(2026, 0, date(2026, 1, 1)),          # ετήσια: όχι σε μήνα
    ])
    assert [y["y"] for y in s["years"]] == [2024, 2025, 2026, 2027]
    assert s["open_y"] == 2026
    y24, y25, y26, y27 = s["years"]
    assert y24["state"] == "done" and y25["state"] == "" and y26["state"] == "late"
    assert y27["state"] == ""
    st = {mo["m"]: mo for mo in y26["months"]}
    assert st[2]["state"] == "late" and st[3]["state"] == "open" and st[1]["state"] == ""
    assert st[3]["now"] and st[4]["future"] and not st[2]["future"]


def test_empty_system_shows_previous_current_and_next_year():
    s = build_time_strip(date(2026, 10, 20), [])
    assert [y["y"] for y in s["years"]] == [2025, 2026, 2027]
    assert all(mo["future"] for mo in s["years"][2]["months"])


def test_pages_render_the_strip_in_the_header(fresh_system):
    from _bootstrap import ui_client

    from webapp import create_app

    c = ui_client(create_app())
    assert _bootstrap.seed(c).status_code == 200  # δοκιμαστικά δεδομένα: υπάρχουν υποχρεώσεις
    for url in ("/", "/payroll/"):
        html = c.get(url, follow_redirects=True).get_data(as_text=True)
        head = html.split("</header>")[0]
        assert 'class="tbar"' in head, url


def test_htmx_command_returns_the_strip_oob_with_open_year_kept(fresh_system):
    """Άμεση ενημέρωση: εντολή από σελίδα με λωρίδα -> λωρίδα OOB, με τις
    ΝΕΕΣ καταστάσεις και το έτος που είχε ανοίξει ο χρήστης."""
    import re

    from _bootstrap import ui_client

    from webapp import create_app
    from webapp.system import proj

    c = ui_client(create_app())
    assert _bootstrap.seed(c).status_code == 200
    ob = next(o for o in sorted(proj.obligations.values(), key=lambda o: o.due_date)
              if o.obligation_type == "ATTENDANCE_APPROVAL" and not o.fulfilled)
    ym = (ob.period_year, ob.period_month)

    def month_open(html):  # ανοικτές υποχρεώσεις του μήνα στη λωρίδα
        y = html.split('id="tb-yr-%d"' % ym[0])[1]
        cell = re.findall(r'class="tb-emp">[^<]*</small><i>([^<]*)', y)[ym[1] - 1]
        return int(cell or 0)

    before = month_open(c.get("/").get_data(as_text=True))
    hx = {"HX-Request": "true", "X-Time-Bar": str(ym[0])}
    html = c.post(f"/obligations/{ob.obligation_id}/run", headers=hx).get_data(as_text=True)
    assert proj.obligations[ob.obligation_id].fulfilled
    assert 'hx-swap-oob="true"' in html
    assert re.search(r'id="tb-yr-%d"\s+hidden\s+checked' % ym[0], html)
    assert month_open(html) == before - 1

    # Χωρίς λωρίδα στη σελίδα (κανένα header): τίποτα επιπλέον
    other = next(o for o in proj.obligations.values() if not o.fulfilled)
    html = c.post(f"/obligations/{other.obligation_id}/run",
                  headers={"HX-Request": "true"}).get_data(as_text=True)
    assert 'id="tbar"' not in html


def test_month_page_renders_everything_about_the_month(fresh_system):
    """Κλικ σε μήνα της λωρίδας -> οθόνη μήνα με παρουσίες, μισθοδοσία,
    υποχρεώσεις· κενός/μελλοντικός μήνας δεν σκάει, άκυρος = 404."""
    from _bootstrap import ui_client

    from webapp import create_app
    from webapp.system import proj

    c = ui_client(create_app())
    assert _bootstrap.seed(c).status_code == 200
    y, m = min((o.period_year, o.period_month) for o in proj.obligations.values()
               if o.period_month)

    html = c.get("/").get_data(as_text=True)
    assert f'href="/month/{y}/{m}"' in html  # η λωρίδα οδηγεί στην οθόνη μήνα

    r = c.get(f"/month/{y}/{m}")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    for part in ("mo-hero", "mo-kpis", "mo-att-row", "mo-tl", "Μισθοδοσία"):
        assert part in page, part
    assert "tb-mo" in page and " sel" in page  # ο μήνας τονίζεται στη λωρίδα

    assert c.get(f"/month/{y + 5}/1").status_code == 200
    assert c.get(f"/month/{y}/13").status_code == 404


def test_month_employee_count_and_badges():
    """Πλήθος εργαζομένων ανά μήνα + σήματα: Δώρο (στον μήνα της προθεσμίας,
    ετήσια υποχρέωση), Επίδομα Αδείας (μήνας περιόδου), αποζημίωση."""
    from types import SimpleNamespace as E

    emps = [
        E(hire_submitted=True, hire_date=date(2026, 2, 10), termination_date=None),
        E(hire_submitted=True, hire_date=date(2025, 1, 1), termination_date=date(2026, 3, 1)),
        E(hire_submitted=False, hire_date=date(2025, 1, 1), termination_date=None),  # χωρίς Ε3
    ]
    obs = [
        Obligation("g", "CHRISTMAS_GIFT", date(2026, 12, 21), "", 2026, 0, True),
        Obligation("a1", "LEAVE_ALLOWANCE", date(2026, 7, 31), "e1", 2026, 7),
        Obligation("a2", "LEAVE_ALLOWANCE", date(2026, 7, 31), "e2", 2026, 7, True),
    ]
    s = build_time_strip(date(2026, 10, 20), obs, emps, [(date(2026, 3, 1), "severance")])
    mo = {m["m"]: m for m in s["years"][1]["months"]}  # 2026
    assert [mo[k]["emps"] for k in (1, 2, 3, 4)] == [1, 2, 2, 1]
    assert [(b["icon"], b["done"]) for b in mo[12]["badges"]] == [("🎄", True)]
    (al,) = mo[7]["badges"]
    assert al["label"] == "Επίδομα Αδείας" and al["n"] == 2 and not al["done"]
    assert mo[3]["badges"][0]["kind"] == "severance"
    assert mo[12]["state"] == ""  # τα ετήσια Δώρα δεν αλλάζουν την κατάσταση μήνα
