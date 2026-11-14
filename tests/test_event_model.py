"""Το Event Model (docs/event_model/) δεν «σαπίζει»:
1. τα παραγόμενα (`model.js`, `docs/event_model.md`) είναι ενημερωμένα·
2. κάθε όνομα κώδικα στο `model.json` υπάρχει στις πηγές·
3. κάθε event του `events.py` βρίσκεται σε slice ή στη λίστα `unmodeled`."""

import builtins
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs" / "event_model"
MODEL = json.loads((DOCS / "model.json").read_text(encoding="utf-8"))
SRC = "\n".join(p.read_text(encoding="utf-8")
                for d in ("misevent", "webapp") for p in (ROOT / d).rglob("*.py"))
EVENTS = set(re.findall(r"^class (\w+)\(DomainEvent", SRC, re.M))
CLASSES = set(re.findall(r"^class (\w+)", SRC, re.M))
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _items(step, keys):
    for k in keys:
        for x in step.get(k, []):
            yield x[0] if isinstance(x, list) else x


def test_generated_files_are_fresh():
    spec = importlib.util.spec_from_file_location("em_build", DOCS / "build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    stale = [p.name for p, text in build.outputs().items()
             if not p.exists() or p.read_text(encoding="utf-8") != text]
    assert not stale, f"τρέξε `uv run python docs/event_model/build.py` ({stale})"


def test_names_exist():
    missing = set()
    for s in MODEL["slices"]:
        for st in s["steps"]:
            for name in _items(st, ("evtP", "evtE", "evtC")):
                if name not in EVENTS:
                    missing.add(name)
            names = [n for n in _items(st, ("cmd", "rm", "auto")) if IDENT.match(n)]
            names += re.findall(r"`([^`\s]+)`", " ".join(st["rules"]))
            for name in names:
                if "/" in name or name.isupper() or ":" in name:
                    continue  # διαδρομή, κωδικός υποχρέωσης, subject
                if name[:1].isupper():
                    ok = name in CLASSES or hasattr(builtins, name)
                else:
                    ok = re.search(rf"\b{re.escape(name)}\b", SRC) is not None
                if not ok:
                    missing.add(name)
    assert not missing, f"model.json: άγνωστα ονόματα {sorted(missing)}"


def test_every_event_is_modeled():
    seen = {n for s in MODEL["slices"] for st in s["steps"]
            for n in _items(st, ("evtP", "evtE", "evtC"))}
    seen |= {e for evs in MODEL["unmodeled"].values() for e in evs}
    assert not EVENTS - seen, f"events χωρίς θέση στο model.json: {sorted(EVENTS - seen)}"
    assert not seen - EVENTS, f"άγνωστα events στο model.json: {sorted(seen - EVENTS)}"
