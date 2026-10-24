"""Εκκίνηση: uv run -m webapp  ->  http://localhost:8000"""

import sys
import webbrowser

from werkzeug.serving import is_running_from_reloader, run_simple

from . import create_app

PORT = 8000


def main():
    reload = "--reload" in sys.argv
    url = f"http://localhost:{PORT}"
    if reload and not is_running_from_reloader():
        # Γονική διεργασία του reloader: μόνο ξαναξεκινά το παιδί σε κάθε
        # αλλαγή .py — ΔΕΝ στήνει event store (η app δεν καλείται ποτέ εδώ).
        webbrowser.open(url)
        run_simple("127.0.0.1", PORT, lambda environ, start: [], use_reloader=True)
        return
    app = create_app()
    # Ποιες εταιρείες φορτώθηκαν και πόσα events έχει η καθεμία — ώστε να
    # φαίνεται αμέσως αν μια βάση ΔΕΝ είναι αυτή που περίμενες (π.χ. db από
    # demo/tests, ή διαγραφή που απέτυχε σιωπηλά).
    from .system import registry
    print(f"Δεδομένα: {registry.root.resolve()}")
    for ref in registry.auth.active_companies():
        n = registry.tenant(ref.company_id).event_count
        print(f"  • {ref.name}  ({n} events{' — ΚΕΝΗ' if n == 0 else ''})")
    print(f"Web UI misevent -> {url}   (Ctrl+C για τερματισμό)")
    if not reload:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - headless περιβάλλον
            pass
    # --reload: debug=True -> reloader (.py) + auto-reload templates. Στο παιδί
    # τρέχει μόνο ΕΝΑ event store· η γονική διεργασία δεν το στήνει.
    app.run(host="127.0.0.1", port=PORT, debug=reload)


if __name__ == "__main__":
    main()
