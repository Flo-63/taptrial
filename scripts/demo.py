"""Demo-Veranstaltung „Demo-Zeitfahren Kottenforst“ anlegen (Details in app/demo.py).

uv run python scripts/demo.py --db data/zeitfahren.sqlite3 [--ersetzen]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.demo import main

if __name__ == "__main__":
    sys.exit(main())
