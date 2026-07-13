"""Module d'exemple `sample_digest` (Phase 7 T6).

Trivial mais traversant TOUT le contrat : routes protégées, permissions rattachées
aux rôles, tâche périodique, capability requise (`MailCapability`), `AIGateway`,
audit, table en DB tenant. Sa PR ne modifie AUCUN fichier du cœur (preuve du §9,
garantie par `test_module_isolation`).
"""
