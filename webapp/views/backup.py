"""
Backup / restore ΟΛΟΥ του συστήματος — ΜΟΝΟ για admin.

Σε αντίθεση με το audit (event log ΜΙΑΣ εταιρείας, JSON), εδώ ταξιδεύει
ολόκληρη η βάση: όλες οι εταιρείες, οι χρήστες και οι κοινές παράμετροι,
ως αρχείο SQLite. Ίδιο δίχτυ ασφαλείας με το audit: αντίγραφο της τρέχουσας
βάσης στον δίσκο πριν την αντικατάσταση, και επιστροφή σε αυτήν αν το replay
του backup αποτύχει.
"""

from datetime import UTC, datetime
from io import BytesIO

from flask import (
    Blueprint,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from ..system import lock, registry
from ..tenancy import data_dir

bp = Blueprint("backup", __name__, url_prefix="/backup")

MAX_BACKUP_BYTES = 500 * 1024 * 1024
SAFETY_BACKUP_DIR = data_dir() / "backups" / "system"


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")


@bp.get("/")
def index():
    return render_template("backup.html", error=request.args.get("error") or "")


@bp.get("/download")
def download():
    with lock:
        data = registry.db.snapshot()
    return send_file(
        BytesIO(data),
        mimetype="application/vnd.sqlite3",
        as_attachment=True,
        download_name=f"misevent-backup-{_stamp()}.db",
    )


def _restore_upload(first_run: bool = False):
    """Έλεγχος, αντίγραφο ασφαλείας, αντικατάσταση, replay. Πετά ValueError
    με μήνυμα για τον χρήστη."""
    upload = request.files.get("backup")
    if upload is None or not upload.filename:
        raise ValueError("Επίλεξε αρχείο backup")
    data = upload.stream.read(MAX_BACKUP_BYTES + 1)
    if len(data) > MAX_BACKUP_BYTES:
        raise ValueError("Το backup ξεπερνά τα 500 MB")

    with lock:
        if first_run and not registry.is_fresh():
            raise ValueError("Το σύστημα έχει ήδη δεδομένα· restore μόνο από διαχειριστή")
        current = registry.db.snapshot()
        SAFETY_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        safety = SAFETY_BACKUP_DIR / f"before-restore-{_stamp()}.db"
        safety.write_bytes(current)
        registry.db.restore_snapshot(data)
        try:
            registry.reload()
        except Exception:
            # Τα events μπήκαν αλλά δεν ξαναπαίζονται: πίσω στην προηγούμενη.
            registry.db.restore_snapshot(current)
            registry.reload()
            raise ValueError(
                "Το backup δεν φορτώθηκε· επανήλθε η προηγούμενη κατάσταση"
            ) from None


def _to_login():
    """Μετά από restore οι χρήστες είναι αυτοί του backup: νέα σύνδεση."""
    session.clear()
    return redirect(url_for("auth.login", restored=1))


@bp.post("/restore")
def restore():
    try:
        _restore_upload()
    except ValueError as exc:
        return redirect(url_for("backup.index", error=str(exc)))
    return _to_login()


@bp.post("/first-run")
def first_run():
    """Restore από τη σελίδα σύνδεσης, ΜΟΝΟ σε πρώτη εκκίνηση (`is_fresh`)."""
    try:
        _restore_upload(first_run=True)
    except ValueError as exc:
        return redirect(url_for("auth.login", restore_error=str(exc)))
    return _to_login()
