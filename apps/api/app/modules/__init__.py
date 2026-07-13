"""Modules métier (Phase 7) — un package par module sous `app/modules/<name>/`.

Un module ne consomme QUE les briques socle (capabilities Phase 5, `AIGateway`
Phase 6, `record_audit_event` Phase 4, `get_tenant_session` Phase 1) et JAMAIS un
autre module (décision D8, vérifié par `test_module_isolation`). Le cœur n'importe
jamais `app/modules/*`, sauf `app/automation/registry.py` (la ligne d'enregistrement).

Voir `app/modules/CLAUDE.md` pour le guide « écrire un module ».
"""
