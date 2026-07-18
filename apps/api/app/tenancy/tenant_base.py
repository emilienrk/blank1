"""Mixin `TenantScoped` + garde-fous de session — l'invariant racine n°1 en single-DB.

Toute table métier hérite de `(Base, TenantScoped)` : colonne `tenant_id` (FK
indexée vers `tenants.id`, CASCADE) et adhésion aux garde-fous installés ici sur
la classe `Session` (donc TOUTE session du process) :

- `do_orm_execute` : tout SELECT/UPDATE/DELETE touchant un mapper `TenantScoped`
  exige un contexte tenant (`TenantContextError` sinon) et reçoit un
  `with_loader_criteria(tenant_id == contexte)` — aliases inclus, propagé aux
  lazy loads. Impossible de lire les données d'un autre tenant par construction.
- Une requête qui référence une TABLE scopée sans son entité ORM (ex.
  `select(func.count()).select_from(Model)`) est REFUSÉE : `with_loader_criteria`
  ne peut pas s'y appliquer — écrire `func.count(Model.id)` à la place.
- `before_flush` : les nouveaux objets scopés sont estampillés du tenant courant ;
  un `tenant_id` incohérent avec le contexte (insert, update ou delete) est refusé.

Hors périmètre : le SQL textuel (`text(...)`) contourne l'ORM — réservé aux tests
et aux migrations, greppable en revue.
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import ForeignKey, Table, event
from sqlalchemy.orm import (
    Mapped,
    ORMExecuteState,
    Session,
    UOWTransaction,
    mapped_column,
    with_loader_criteria,
)
from sqlalchemy.sql.elements import ClauseElement
from sqlalchemy.sql.util import find_tables

from app.tenancy.context import TenantContextError, current_tenant


class TenantScoped:
    """Colonne `tenant_id` + adhésion aux garde-fous de session (invariant n°1)."""

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )


def _touches_scoped_mapper(state: ORMExecuteState) -> bool:
    return any(issubclass(mapper.class_, TenantScoped) for mapper in state.all_mappers)


def _scoped_tables() -> set[Table]:
    """Tables des mapper `TenantScoped` (calcul à la volée : registre petit et stable)."""
    from app.core.db import Base

    return {
        mapper.local_table
        for mapper in Base.registry.mappers
        if issubclass(mapper.class_, TenantScoped) and isinstance(mapper.local_table, Table)
    }


def _refuse_unscoped_table_access(state: ORMExecuteState) -> None:
    """Refuse une requête où une table scopée apparaît sans son entité ORM :
    `with_loader_criteria` ne peut pas la filtrer (ex. `select(func.count())
    .select_from(Model)`) — la laisser passer serait une fuite cross-tenant."""
    statement = state.statement
    if not isinstance(statement, ClauseElement):  # pragma: no cover — jamais en pratique
        return
    found = find_tables(statement, check_columns=True, include_joins=True, include_selects=True)
    table_names = {t.name for t in found if isinstance(t, Table)}
    leaked = table_names & {t.name for t in _scoped_tables()}
    if leaked:
        msg = (
            f"Requête sur table(s) scopée(s) {sorted(leaked)} sans entité ORM : le filtre "
            "tenant automatique ne peut pas s'appliquer. Référencez la classe mappée "
            "(ex. func.count(Model.id) plutôt que select_from(Model))."
        )
        raise TenantContextError(msg)


def _guard_orm_execute(state: ORMExecuteState) -> None:
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.is_column_load or state.is_relationship_load:
        # Chargements internes : les critères posés sur la requête d'origine se propagent.
        return
    if not _touches_scoped_mapper(state):
        _refuse_unscoped_table_access(state)
        return
    # La lambda ne capture qu'une VALEUR simple (uuid) : SQLAlchemy la lie en
    # paramètre et met l'expression en cache — jamais l'objet contexte entier.
    tenant_id = current_tenant().tenant_id  # lève TenantContextError sans contexte
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantScoped,
            lambda cls: cls.tenant_id == tenant_id,
            include_aliases=True,
        )
    )


def _guard_flush(session: Session, _ctx: UOWTransaction, _instances: Sequence[Any] | None) -> None:
    new_scoped = [obj for obj in session.new if isinstance(obj, TenantScoped)]
    touched_scoped = [
        obj for obj in [*session.dirty, *session.deleted] if isinstance(obj, TenantScoped)
    ]
    if not new_scoped and not touched_scoped:
        return
    ctx = current_tenant()  # lève TenantContextError sans contexte — invariant n°1
    for obj in new_scoped:
        tenant_id: uuid.UUID | None = getattr(obj, "tenant_id", None)
        if tenant_id is None:
            obj.tenant_id = ctx.tenant_id
        elif tenant_id != ctx.tenant_id:
            msg = f"tenant_id {tenant_id} ≠ contexte courant {ctx.tenant_id} (insert refusé)"
            raise TenantContextError(msg)
    for obj in touched_scoped:
        if obj.tenant_id != ctx.tenant_id:
            msg = f"tenant_id {obj.tenant_id} ≠ contexte courant {ctx.tenant_id} (écriture refusée)"
            raise TenantContextError(msg)


_guards_installed = False


def install_tenant_guards() -> None:
    """Enregistre les listeners sur la classe Session (idempotent, fait à l'import)."""
    global _guards_installed
    if _guards_installed:
        return
    event.listen(Session, "do_orm_execute", _guard_orm_execute)
    event.listen(Session, "before_flush", _guard_flush)
    _guards_installed = True


# À l'import : tout process qui mappe un modèle TenantScoped importe ce module,
# les garde-fous sont donc toujours en place avant la première requête.
install_tenant_guards()
