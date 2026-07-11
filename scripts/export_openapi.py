"""Exporte le contrat OpenAPI de l'app FastAPI, sans lancer de serveur.

Usage : uv run python scripts/export_openapi.py [chemin/openapi.json]
"""

import json
import sys
from pathlib import Path

from app.main import create_app


def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("openapi.json")
    schema = create_app().openapi()
    output.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Contrat OpenAPI exporté vers {output}")


if __name__ == "__main__":
    main()
