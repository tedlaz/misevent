"""Παράγει από το `model.json` (ΜΟΝΗ πηγή) το `model.js` για τη σελίδα
`index.html` και το `docs/event_model.md` για το GitHub.

    uv run python docs/event_model/build.py

Το `tests/test_event_model.py` αποτυγχάνει αν τα παραγόμενα δεν είναι
ενημερωμένα ή αν ένα όνομα δεν υπάρχει πια στον κώδικα."""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
LANES = {"ui": "⬜ Οθόνη", "cmd": "🟦 Εντολή", "evt": "🟧 Events",
         "rm": "🟩 Read model", "auto": "⚙️ Αυτοματισμός", "ext": "🟪 Εξωτερικό"}


def _item(x, code):
    main, note = (x if isinstance(x, list) else [x, ""])
    main = f"`{main}`" if code and main.replace("_", "").isalnum() and main.isascii() else main
    return f"{main} ({note})" if note else main


def _cell(step, keys, code=True):
    items = [_item(x, code) for k in keys for x in step.get(k, [])]
    return "<br>".join(items) or "—"


def render_md(model) -> str:
    out = [
        "# Event Model του misevent",
        "",
        "<!-- ΠΑΡΑΓΕΤΑΙ από docs/event_model/model.json με "
        "`uv run python docs/event_model/build.py` — μην το επεξεργάζεσαι εδώ. -->",
        "",
        "Η ροή πληροφορίας του συστήματος σε **slices**: ποια οθόνη ξεκινά κάθε "
        "βήμα, ποια **εντολή** (μέθοδος service) γράφει ποιο **event**, ποιο "
        "**read model** το διαβάζει και ποιος **αυτοματισμός** αντιδρά. Οι νομικοί "
        "κανόνες ζουν στο `docs/payroll_rules.md` και στο ARCH_REF §7.",
        "",
        "**Οπτικοί πίνακες:** άνοιξε το `docs/event_model/index.html` στον browser.",
        "",
        "## Slices",
        "",
    ]
    out += [f"{i}. [{s['title']}](#{i}-{s['id']})" for i, s in enumerate(model["slices"], 1)]
    for i, s in enumerate(model["slices"], 1):
        out += ["", "---", "", f'<a id="{i}-{s["id"]}"></a>', f"## {i}. {s['title']}", "",
                s["summary"], "",
                "| # | " + " | ".join(LANES.values()) + " |",
                "|---" * (len(LANES) + 1) + "|"]
        for n, st in enumerate(s["steps"], 1):
            loop = " ↻" if st.get("loop") else ""
            row = [f"**{n}. {st['t']}**{loop}<br>{_cell(st, ['ui'], code=False)}",
                   _cell(st, ["cmd"]), _cell(st, ["evtP", "evtE", "evtC"]),
                   _cell(st, ["rm"]), _cell(st, ["auto"]), _cell(st, ["ext"], code=False)]
            out.append("| " + " | ".join(row) + " |")
        out += ["", "### Κανόνες", ""]
        for n, st in enumerate(s["steps"], 1):
            out.append(f"- **{n}. {st['t']}**")
            out += [f"  - {r}" for r in st["rules"]]
        for d in s.get("diagrams", []):
            out += ["", f"### {d['title']}", "", "```mermaid", *d["code"], "```"]
    out += ["", "---", "", "## Events εκτός slices (επόμενα)", ""]
    out += [f"- **{g}:** " + ", ".join(f"`{e}`" for e in evs)
            for g, evs in model["unmodeled"].items()]
    return "\n".join(out) + "\n"


def render_js(model) -> str:
    return ("// ΠΑΡΑΓΕΤΑΙ από model.json (build.py) — μην το επεξεργάζεσαι εδώ.\n"
            "window.EVENT_MODEL = " + json.dumps(model, ensure_ascii=False, indent=1) + ";\n")


def outputs() -> dict[Path, str]:
    model = json.loads((HERE / "model.json").read_text(encoding="utf-8"))
    return {HERE / "model.js": render_js(model),
            HERE.parent / "event_model.md": render_md(model)}


if __name__ == "__main__":
    for path, text in outputs().items():
        path.write_text(text, encoding="utf-8", newline="\n")
        print("έγραψα", path)
