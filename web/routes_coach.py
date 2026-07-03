import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from web.deps import get_state_for

router = APIRouter()


class BriefRequest(BaseModel):
    goal_text: str
    domain: str | None = None
    topic: str | None = None


@router.get("/coach/state")
def coach_state(request: Request, topic: str = None):
    """Active goals (each with its experiments) + personal rules for the topic."""
    from db import get_connection, get_goals, get_experiments, get_rules
    st = get_state_for(request, topic)
    conn = get_connection(st.db_path)
    try:
        goals = get_goals(conn)
        for g in goals:
            g["experiments"] = get_experiments(conn, goal_id=g["id"])
        rules = get_rules(conn)
    finally:
        conn.close()
    return {"goals": goals, "rules": rules}


@router.post("/coach/brief")
def coach_brief(body: BriefRequest, request: Request):
    """Generate a guidance brief grounded in the topic's library. Degrades to a
    retrieval-only brief (cited principles, no synthesized actions) when no chat
    model is configured — never errors on that account."""
    if not body.goal_text.strip():
        raise HTTPException(status_code=400, detail="goal_text must not be empty")
    from guidance import generate_guidance_brief
    st = get_state_for(request, body.topic)
    domain = body.domain
    if not domain:
        try:
            from guidance import detect_domain
            domain = detect_domain(body.goal_text)
        except Exception:
            domain = "general"
    return generate_guidance_brief(body.goal_text, domain, st.db_path, st.llm)
