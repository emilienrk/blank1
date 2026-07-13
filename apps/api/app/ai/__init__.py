"""Gateway IA (Phase 6) : interface interne unique — chat, streaming, embeddings.

Tout appel à un provider IA passe par `AIGateway` (invariant n°1 de la phase) :
aucun import de `litellm` ni d'un SDK provider hors de ce package. Le metering et
les politiques par tenant sont notre valeur ajoutée, dans du code typé.
"""
