"""Entry point for `psyche web`.

Reads DATABASE_PATH from the environment (set by cli.py via resolve_db_path),
starts uvicorn, and opens a browser tab pointing at the local server.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def main(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Start the Psyche web API server with uvicorn.

    Args:
        host:   Bind address (default 127.0.0.1).
        port:   TCP port (default 8000).
        reload: Enable uvicorn auto-reload for development (default False).
    """
    import uvicorn

    # The web app is browser-configured, so never block startup on the
    # interactive CLI setup wizard. With no .env, LLMClient defaults to the
    # local/offline provider (ONNX embeddings) — search, graph, ingest and
    # memory all work with zero configuration; chat can be enabled in the UI.
    os.environ.setdefault("PSYCHE_NONINTERACTIVE", "1")

    # Open the browser after a brief delay so the server has time to bind.
    # We do this in a daemon thread so it doesn't block uvicorn startup.
    import threading
    import time

    def _open_browser():
        time.sleep(1.2)
        import webbrowser

        webbrowser.open(f"http://{host}:{port}")

    threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(
        "web.app:app",
        host=host,
        port=port,
        reload=reload,
    )
