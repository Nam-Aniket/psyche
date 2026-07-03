import logging
import os
import sys
from contextlib import asynccontextmanager
from urllib.parse import urlparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

_LOG = logging.getLogger("psyche.web")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Only call build_state if psyche has not already been injected (e.g. by tests).
    if not getattr(app.state, "psyche", None):
        from web.state import build_state

        app.state.psyche = build_state(os.getenv("DATABASE_PATH"))
    yield


def create_app() -> FastAPI:
    """FastAPI application factory.

    Endpoint authors: add exactly ONE include_router() call per router module,
    kept in alphabetical order below the router imports.
    """
    app = FastAPI(title="Psyche Web API", version="0.1.0", lifespan=lifespan)

    # --- top-level exception handler (catch-all → 500) ---
    # Log the real exception server-side; return a generic body so internal paths
    # and error text never leak to the client. HTTPException detail is unaffected.
    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        _LOG.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    # --- CSRF guard: block cross-origin state-changing requests ---
    # A loopback-bound tool exposes destructive routes (DELETE /sources, POST
    # /ingest, POST /connect). Browsers attach an Origin header on cross-origin
    # writes; reject any whose origin host is not loopback. Same-origin app calls
    # and non-browser clients (no Origin) pass through.
    @app.middleware("http")
    async def _csrf_guard(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin:
                host = urlparse(origin).hostname
                if host not in _LOOPBACK_HOSTS:
                    return JSONResponse(status_code=403, content={"detail": "Cross-origin request blocked."})
        return await call_next(request)

    # --- always revalidate the frontend assets (ETag keeps unchanged fetches as 304) ---
    @app.middleware("http")
    async def _revalidate_static(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # --- health check (no auth, no state required) ---
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # --- router registrations (alphabetical; one include_router line per router) ---
    from web import routes_coach, routes_graph, routes_memory, routes_search, routes_sources, routes_system

    app.include_router(routes_coach.router)
    app.include_router(routes_graph.router)
    app.include_router(routes_memory.router)
    app.include_router(routes_search.router)
    app.include_router(routes_sources.router)
    app.include_router(routes_system.router)

    # --- static frontend (registered after API routers so they take priority) ---
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    _static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(_static_dir):

        @app.get("/", include_in_schema=False)
        async def _index():
            return FileResponse(os.path.join(_static_dir, "index.html"))

        app.mount(
            "/static", StaticFiles(directory=_static_dir), name="static"
        )

    return app


# Module-level app instance for uvicorn: uvicorn web.app:app
app = create_app()
