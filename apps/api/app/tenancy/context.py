"""Contexte tenant courant (contextvars) — fondation de l'invariant racine n°1 :
« jamais de requête métier sans contexte tenant résolu »."""

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass


class TenantContextError(RuntimeError):
    """Levée quand du code métier s'exécute sans contexte tenant résolu."""


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: uuid.UUID
    slug: str
    # Rôle du membership de l'utilisateur courant (Phase 2) — None hors HTTP
    # (CLI, tâches Celery : pas d'utilisateur).
    role: str | None = None


_current_tenant: ContextVar[TenantContext | None] = ContextVar("current_tenant", default=None)


def current_tenant() -> TenantContext:
    ctx = _current_tenant.get()
    if ctx is None:
        msg = (
            "Aucun contexte tenant résolu — toute requête métier exige un tenant "
            "(dépendance resolve_tenant ou tenant_context())."
        )
        raise TenantContextError(msg)
    return ctx


def current_tenant_or_none() -> TenantContext | None:
    return _current_tenant.get()


def push_tenant(ctx: TenantContext) -> Token[TenantContext | None]:
    return _current_tenant.set(ctx)


def pop_tenant(token: Token[TenantContext | None]) -> None:
    _current_tenant.reset(token)


@contextmanager
def tenant_context(ctx: TenantContext) -> Generator[TenantContext]:
    """Pose le contexte tenant pour la durée du bloc (CLI, tâches Celery, tests)."""
    token = push_tenant(ctx)
    try:
        yield ctx
    finally:
        pop_tenant(token)
