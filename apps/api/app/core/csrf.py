"""Protection CSRF (décision D7 Phase 2) : SameSite=Lax + contrôle d'Origin.

Le cookie de session est SameSite=Lax ; ce middleware ferme le reste : sur
toute méthode mutante, si le navigateur envoie un header Origin, son hôte doit
être celui de la requête ou un frère sous le domaine du cookie de session
(SPA sur sous-domaine tenant → API sur le même hôte). Les clients sans Origin
(curl, tests, serveurs) ne sont pas concernés — le CSRF est une attaque
navigateur, et les navigateurs envoient toujours Origin en cross-site.
"""

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _bare_host(value: str) -> str:
    return value.split(":", 1)[0].strip().lower()


def origin_allowed(origin: str, request_host: str) -> bool:
    origin_host = _bare_host(urlsplit(origin).netloc)
    if not origin_host:
        return False
    if origin_host == _bare_host(request_host):
        return True
    # Sous le domaine du cookie de session, les sous-domaines sont de confiance
    # (décision D2 : même app servie sur chaque sous-domaine tenant).
    cookie_domain = get_settings().session_cookie_domain.lstrip(".").lower()
    return bool(cookie_domain) and (
        origin_host == cookie_domain or origin_host.endswith("." + cookie_domain)
    )


class CsrfOriginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in _MUTATING_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and not origin_allowed(origin, request.headers.get("host", "")):
                return JSONResponse(status_code=403, content={"detail": "Origin non autorisée"})
        return await call_next(request)
