import os
import sys
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


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
    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    # --- health check (no auth, no state required) ---
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # --- router registrations (alphabetical; one include_router line per router) ---
    from web import routes_graph, routes_search, routes_sources

    app.include_router(routes_graph.router)
    app.include_router(routes_search.router)
    app.include_router(routes_sources.router)

    return app


# Module-level app instance for uvicorn: uvicorn web.app:app
app = create_app()
