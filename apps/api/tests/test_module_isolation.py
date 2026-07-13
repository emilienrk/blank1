"""Isolation cœur ↔ modules (Phase 7 D7) — LE gardien permanent de la promesse §9.

Deux garanties structurelles, vérifiées par analyse statique (AST) :
1. Le cœur (`app/` hors `app/modules/`) n'importe AUCUN module, sauf l'unique ligne
   d'enregistrement de `app/automation/registry.py` : ajouter un module ne modifie
   jamais le cœur (invariant de phase n°1).
2. Un module n'importe jamais un autre module (décision D8) : tout partage passe par
   le socle. `sample_digest` n'importe que des briques `app.*` autorisées + lui-même.
"""

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent / "app"
MODULES_ROOT = APP_ROOT / "modules"
# Seul point du cœur autorisé à référencer un module : la ligne du registre (D1).
REGISTRY_FILE = APP_ROOT / "automation" / "registry.py"


def _imported_modules(path: Path) -> set[str]:
    """Noms de modules importés par un fichier (`import a.b`, `from a.b import c`)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            names.add(node.module)
    return names


def _module_package(path: Path) -> str | None:
    """Nom du module métier auquel appartient un fichier sous `app/modules/…`."""
    try:
        relative = path.relative_to(MODULES_ROOT)
    except ValueError:
        return None
    return relative.parts[0] if relative.parts else None


def test_core_never_imports_a_module_except_the_registry() -> None:
    offenders: list[tuple[str, str]] = []
    for path in APP_ROOT.rglob("*.py"):
        if MODULES_ROOT in path.parents:
            continue  # les modules eux-mêmes sont couverts par l'autre test
        if path == REGISTRY_FILE:
            continue  # l'unique couture autorisée (D1)
        for name in _imported_modules(path):
            if name == "app.modules" or name.startswith("app.modules."):
                offenders.append((str(path.relative_to(APP_ROOT)), name))
    assert offenders == [], (
        f"Le cœur importe un module hors registre (interdit, invariant de phase n°1) : {offenders}"
    )


def test_registry_is_the_only_core_module_reference() -> None:
    # La couture existe bien là où on l'attend (le test ci-dessus n'est pas vide de sens).
    referenced = {
        name for name in _imported_modules(REGISTRY_FILE) if name.startswith("app.modules.")
    }
    assert referenced, "Le registre devrait importer au moins un manifeste de module."


def test_a_module_never_imports_another_module() -> None:
    offenders: list[tuple[str, str]] = []
    for path in MODULES_ROOT.rglob("*.py"):
        own = _module_package(path)
        if own is None:
            continue
        for name in _imported_modules(path):
            if not name.startswith("app.modules."):
                continue
            imported = name.split(".")[2] if len(name.split(".")) > 2 else None
            if imported is not None and imported != own:
                offenders.append((str(path.relative_to(MODULES_ROOT)), name))
    assert offenders == [], f"Un module en importe un autre (interdit, décision D8) : {offenders}"
