import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from web.deps import get_state

router = APIRouter()


class GraphBuildRequest(BaseModel):
    clusters: int = 6


@router.get("/graph/nodes")
def graph_nodes(request: Request):
    """Returns all concepts (nodes) in the knowledge graph."""
    from db import get_connection, get_all_concepts
    st = get_state(request)
    conn = get_connection(st.db_path)
    try:
        nodes = get_all_concepts(conn)
    finally:
        conn.close()
    return nodes


@router.get("/graph/edges")
def graph_edges(request: Request):
    """Returns all concept links (edges) in the knowledge graph."""
    from db import get_connection, get_concept_links
    st = get_state(request)
    conn = get_connection(st.db_path)
    try:
        edges = get_concept_links(conn)
    finally:
        conn.close()
    return edges


@router.post("/graph/build")
def graph_build(request: Request, body: GraphBuildRequest = GraphBuildRequest()):
    """Builds the concept graph synchronously.

    Guards:
    - Returns 400 when the database has no chunks (avoids sys.exit inside
      build_concept_graph).
    - Catches SystemExit from build_concept_graph and returns 500 instead of
      killing the server process.

    `clusters` is read from the JSON body (a plain param would be a query param).
    """
    from db import get_connection
    from build_graph import build_concept_graph

    clusters = body.clusters
    st = get_state(request)

    # Pre-flight: reject immediately if DB is empty to avoid sys.exit(1) inside
    # build_concept_graph.
    conn = get_connection(st.db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM chunks")
        count = cur.fetchone()[0]
    finally:
        conn.close()

    if count == 0:
        raise HTTPException(
            status_code=400,
            detail="Database is empty. Ingest some documents first.",
        )

    try:
        build_concept_graph(st.db_path, clusters)
    except SystemExit as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Graph build failed: {exc}",
        )

    return {"status": "ok", "clusters": clusters}
