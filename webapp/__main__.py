"""Εκκίνηση: uv run -m webapp  ->  http://localhost:8000"""

import webbrowser

from . import create_app

PORT = 8000


def main():
    app = create_app()
    # Ποιες εταιρείες φορτώθηκαν και πόσα events έχει η καθεμία — ώστε να
    # φαίνεται αμέσως αν μια βάση ΔΕΝ είναι αυτή που περίμενες (π.χ. db από
    # demo/tests, ή διαγραφή που απέτυχε σιωπηλά).
    from .system import registry
    print(f"Δεδομένα: {registry.root.resolve()}")
    for ref in registry.auth.active_companies():
        n = registry.tenant(ref.company_id).event_count
        print(f"  • {ref.name}  ({n} events{' — ΚΕΝΗ' if n == 0 else ''})")
    url = f"http://localhost:{PORT}"
    print(f"Web UI misevent -> {url}   (Ctrl+C για τερματισμό)")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - headless περιβάλλον
        pass
    # debug=False: το reloader θα ξαναέστηνε το event store σε 2ο process
    app.run(host="127.0.0.1", port=PORT, debug=False)


if __name__ == "__main__":
    main()
