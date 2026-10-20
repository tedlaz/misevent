"""
Backup / restore ΟΛΟΥ του συστήματος — ΜΟΝΟ για admin.

Σε αντίθεση με το audit (event log ΜΙΑΣ εταιρείας, JSON), εδώ ταξιδεύει
ολόκληρη η βάση: όλες οι εταιρείες, οι χρήστες και οι κοινές παράμετροι,
ως .zip: `misevent.db` (events), `documents.db` (έγγραφα — blobs εκτός event
log, βλ. persistence/documents.py) και `secrets.db` + `secrets.key` (κωδικοί
ΕΡΓΑΝΗ ανά εταιρεία, κρυπτογραφημένοι — βλ. webapp/ergani_credentials.py).
⚠️ Άρα το αρχείο backup δίνει πρόσβαση στους κωδικούς ΕΡΓΑΝΗ.
Παλιό backup σκέτο `.db` γίνεται ακόμη δεκτό: επαναφέρει μόνο τα events.

Ίδιο δίχτυ ασφαλείας με το audit: αντίγραφο της τρέχουσας κατάστασης στον
δίσκο πριν την αντικατάσταση, και επιστροφή σε αυτήν αν το replay του
backup αποτύχει.
"""

import re
import zipfile
from datetime import UTC, datetime
from io import BytesIO

from flask import (
    Blueprint,
    abort,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from ..system import lock, registry
from ..tenancy import (
    DB_NAME,
    DOCUMENTS_DB_NAME,
    SECRETS_DB_NAME,
    SECRETS_KEY_NAME,
    data_dir,
)

bp = Blueprint("backup", __name__, url_prefix="/backup")

MAX_BACKUP_BYTES = 500 * 1024 * 1024
# Τα αυτόματα αντίγραφα πριν από restore: `system/*.db` (εδώ) και
# `<company_id>/*.json` (audit log μιας εταιρείας).
BACKUPS_DIR = data_dir() / "backups"
SAFETY_BACKUP_DIR = BACKUPS_DIR / "system"
_SAFETY_NAME = re.compile(r"before-restore-[\w.-]+\.(zip|db|json)")
_SCOPE = re.compile(r"[\w-]+")


def _bundle() -> bytes:
    """Backup ΟΛΟΥ του συστήματος: .zip με τη βάση events, τη βάση εγγράφων και
    τους κωδικούς ΕΡΓΑΝΗ (βάση + κλειδί τους — ταξιδεύουν μαζί)."""
    secrets_db, secrets_key = registry.secrets.snapshot()
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(DB_NAME, registry.db.snapshot())
        z.writestr(DOCUMENTS_DB_NAME, registry.documents.snapshot())
        z.writestr(SECRETS_DB_NAME, secrets_db)
        z.writestr(SECRETS_KEY_NAME, secrets_key)
    return out.getvalue()


def _unbundle(data: bytes) -> dict[str, bytes]:
    """{όνομα αρχείου: bytes} από .zip — ή από παλιό σκέτο .db (μόνο events).
    Ό,τι λείπει από ένα παλιότερο backup δεν αγγίζεται στο restore."""
    if not data.startswith(b"PK"):
        return {DB_NAME: data}
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            parts = {n: z.read(n) for n in z.namelist()}
    except zipfile.BadZipFile:
        raise ValueError("Το αρχείο δεν είναι backup του misevent") from None
    if DB_NAME not in parts:
        raise ValueError("Το αρχείο δεν είναι backup του misevent")
    return parts


def _apply(parts: dict[str, bytes]):
    registry.db.restore_snapshot(parts[DB_NAME])
    if DOCUMENTS_DB_NAME in parts:
        registry.documents.restore_snapshot(parts[DOCUMENTS_DB_NAME])
    if SECRETS_DB_NAME in parts and SECRETS_KEY_NAME in parts:
        registry.secrets.restore_snapshot(parts[SECRETS_DB_NAME], parts[SECRETS_KEY_NAME])


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")


def _safety_files() -> list[dict]:
    """Τα αυτόματα αντίγραφα όλων των scopes, νεότερα πρώτα."""
    rows = []
    for path in BACKUPS_DIR.glob("*/before-restore-*"):
        if not _SAFETY_NAME.fullmatch(path.name):
            continue
        scope = path.parent.name
        stat = path.stat()
        rows.append({
            "scope": scope,
            "name": path.name,
            "system": scope == "system",
            "label": ("Όλο το σύστημα" if scope == "system"
                      else registry.display_name(scope) or scope),
            "size": stat.st_size,
            "when": datetime.fromtimestamp(stat.st_mtime),
        })
    return sorted(rows, key=lambda r: r["when"], reverse=True)


def _safety_path(scope: str, name: str):
    """Τα ονόματα έρχονται από το URL: μόνο αρχεία αντιγράφων, μέσα στο
    `BACKUPS_DIR` — καμία διαδρομή εκτός (path traversal)."""
    if not (_SCOPE.fullmatch(scope) and _SAFETY_NAME.fullmatch(name)):
        abort(404)
    path = BACKUPS_DIR / scope / name
    if not path.resolve().is_relative_to(BACKUPS_DIR.resolve()) or not path.is_file():
        abort(404)
    return path


@bp.get("/")
def index():
    return render_template(
        "backup.html",
        error=request.args.get("error") or "",
        safety_files=_safety_files(),
    )


@bp.get("/download")
def download():
    with lock:
        data = _bundle()
    return send_file(
        BytesIO(data),
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"misevent-backup-{_stamp()}.zip",
    )


def _restore_upload(first_run: bool = False):
    upload = request.files.get("backup")
    if upload is None or not upload.filename:
        raise ValueError("Επίλεξε αρχείο backup")
    data = upload.stream.read(MAX_BACKUP_BYTES + 1)
    if len(data) > MAX_BACKUP_BYTES:
        raise ValueError("Το backup ξεπερνά τα 500 MB")
    _restore_bytes(data, first_run)


def _restore_bytes(data: bytes, first_run: bool = False):
    """Αντίγραφο ασφαλείας, αντικατάσταση, replay. Πετά ValueError με μήνυμα
    για τον χρήστη."""
    with lock:
        if first_run and not registry.is_fresh():
            raise ValueError("Το σύστημα έχει ήδη δεδομένα· restore μόνο από διαχειριστή")
        parts = _unbundle(data)
        current = _bundle()
        SAFETY_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        # Μικροδευτερόλεπτα: δύο restores στο ίδιο δευτερόλεπτο δεν
        # επικαλύπτουν το ένα το αντίγραφο του άλλου.
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%fZ")
        safety = SAFETY_BACKUP_DIR / f"before-restore-{stamp}.zip"
        safety.write_bytes(current)
        registry.db.restore_snapshot(parts[DB_NAME])  # άκυρη βάση: τίποτα δεν άλλαξε
        try:
            _apply(parts)
            registry.reload()
        except Exception:
            # Δεν φορτώθηκε: πίσω στην προηγούμενη κατάσταση (ΟΛΑ τα αρχεία).
            _apply(_unbundle(current))
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


# ------------------------------------------------------- αυτόματα αντίγραφα
@bp.get("/safety/<scope>/<name>")
def safety_download(scope, name):
    return send_file(_safety_path(scope, name), as_attachment=True)


@bp.post("/safety/<scope>/<name>/delete")
def safety_delete(scope, name):
    _safety_path(scope, name).unlink()
    return redirect(url_for("backup.index"))


@bp.post("/safety/<scope>/<name>/restore")
def safety_restore(scope, name):
    """Μόνο τα `.zip`/`.db` όλου του συστήματος· τα `.json` μιας εταιρείας
    επαναφέρονται από το audit log της."""
    path = _safety_path(scope, name)
    if scope != "system" or path.suffix not in (".zip", ".db"):
        abort(404)
    try:
        _restore_bytes(path.read_bytes())
    except ValueError as exc:
        return redirect(url_for("backup.index", error=str(exc)))
    return _to_login()
