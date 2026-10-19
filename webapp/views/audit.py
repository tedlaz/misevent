"""
Audit log: το πλήρες event log — η ΜΟΝΑΔΙΚΗ πηγή αλήθειας.

Το log είναι append-only στις κανονικές λειτουργίες. Από αυτή τη διαχειριστική
οθόνη επιτρέπεται ρητό rollback: διαγράφονται όλα τα events μετά το επιλεγμένο
και ξαναχτίζεται ολόκληρη η κατάσταση με replay.
"""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from flask import Blueprint, redirect, render_template, request, send_file, url_for

from ..queries import emp_name
from ..system import current_tenant, lock, proj, rebuild_all, store
from ..tenancy import data_dir

bp = Blueprint("audit", __name__, url_prefix="/audit")

PAGE = 200
MAX_BACKUP_BYTES = 50 * 1024 * 1024
SAFETY_BACKUP_DIR = data_dir() / "backups"


def _describe(agg: str) -> str:
    """Φιλικό όνομα για το aggregate id."""
    if agg == "COMPANY":
        return "ΕΤΑΙΡΕΙΑ"
    if agg in proj.employments:
        return emp_name(agg)
    if agg in proj.persons:
        return proj.persons[agg].full_name
    return agg


@bp.get("/")
def index():
    etype = request.args.get("type") or ""
    rows = []
    log = store.load_log()
    for rec in log:
        agg, ev = rec["aggregate_id"], rec["event"]
        name = rec["event_type"]
        if etype and name != etype:
            continue
        rows.append(
            {
                "seq": rec["seq"],
                "event": name,
                "aggregate": agg,
                "who": _describe(agg),
                "occurred_at": datetime.fromisoformat(rec["occurred_at"]),
                "payload": {
                    k: v for k, v in asdict(ev).items() if v not in (None, "", 0, {})
                },
            }
        )
    total = len(rows)
    rows = rows[::-1][:PAGE]  # νεότερα πρώτα
    types = sorted({rec["event_type"] for rec in log})
    return render_template(
        "audit.html",
        rows=rows,
        total=total,
        types=types,
        etype=etype,
        page=PAGE,
        rolled=request.args.get("rolled", type=int),
        removed=request.args.get("removed", type=int),
        restored=request.args.get("restored", type=int),
        safety=request.args.get("safety") or "",
    )


@bp.post("/rollback")
def rollback():
    """Επαναφορά όλης της εφαρμογής στην κατάσταση του επιλεγμένου event."""
    raw = (request.form.get("seq") or "").strip()
    try:
        seq = int(raw)
    except ValueError:
        return redirect(url_for("audit.index", error="Επίλεξε ένα event"))

    try:
        with lock:
            removed = store.rollback_to(seq)
            rebuild_all()
    except ValueError as exc:
        return redirect(url_for("audit.index", error=str(exc)))

    return redirect(url_for("audit.index", rolled=seq, removed=removed))


@bp.get("/backup")
def backup():
    """Κατέβασμα αυτούσιου, checksummed αντιγράφου του event log."""
    with lock:
        payload = store.backup_bytes()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
    return send_file(
        BytesIO(payload),
        mimetype="application/json",
        as_attachment=True,
        download_name=f"misevent-events-{stamp}.json",
    )


def _write_safety_backup(payload: bytes) -> Path:
    """Ατομική εγγραφή server-side backup πριν από κάθε αντικατάσταση.

    ΑΝΑ ΕΤΑΙΡΕΙΑ: το log που αντικαθίσταται είναι μιας συγκεκριμένης
    εταιρείας, οπότε και το δίχτυ ασφαλείας πρέπει να ξεχωρίζει ποιας."""
    folder = SAFETY_BACKUP_DIR / current_tenant().company_id
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%fZ")
    path = folder / f"before-restore-{stamp}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)
    return path


@bp.post("/restore")
def restore():
    """Έλεγχος, safety backup, ατομική αντικατάσταση και πλήρες replay."""
    upload = request.files.get("backup")
    if upload is None or not upload.filename:
        return redirect(url_for("audit.index", error="Επίλεξε αρχείο backup"))
    raw = upload.stream.read(MAX_BACKUP_BYTES + 1)
    if len(raw) > MAX_BACKUP_BYTES:
        return redirect(url_for("audit.index", error="Το backup ξεπερνά τα 50 MB"))
    try:
        document = json.loads(raw.decode("utf-8-sig"))
        store.validate_backup(document)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return redirect(url_for("audit.index", error=f"Αποτυχία ελέγχου backup: {exc}"))

    with lock:
        current = store.export_backup()
        current_bytes = (
            json.dumps(current, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        try:
            safety_path = _write_safety_backup(current_bytes)
            restored = store.restore_backup(document)
            rebuild_all()
        except Exception:
            # Αν κάτι αποτύχει μετά την αντικατάσταση, επιστρέφουμε αμέσως
            # στο in-memory safety copy και ξαναχτίζουμε το συνεπές state.
            store.restore_backup(current)
            rebuild_all()
            return redirect(
                url_for("audit.index", error="Το restore απέτυχε· επανήλθε το safety backup")
            )

    return redirect(
        url_for(
            "audit.index",
            restored=restored,
            safety=safety_path.name,
        )
    )
