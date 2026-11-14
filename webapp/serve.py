"""Online (SaaS) εκκίνηση: waitress, χωρίς browser — τρέχει μέσα στο Docker.

    uv run --extra server -m webapp.serve      (desktop/τοπικά: `uv run -m webapp`)

⚠️ ΜΙΑ διεργασία με threads: SQLite + projections στη μνήμη + `Registry.lock`.
"""

import logging
import os

from waitress import serve

from . import create_app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = create_app()
    from .system import registry

    print(f"Δεδομένα: {registry.root.resolve()}")
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)),
          threads=int(os.environ.get("THREADS", 8)))


if __name__ == "__main__":
    main()
