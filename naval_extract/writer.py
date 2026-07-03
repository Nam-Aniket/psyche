"""Write validated principle-atoms into the naval topic's rules/rule_links schema."""
import db
from naval_extract import schema


def write_atom(conn, atom: dict, domain: str = "naval"):
    """Writes a decision-rule atom to the `rules` table and returns its rule ID.

    Returns None (writes nothing) when the atom carries no decision_rule — that is
    a principle-note the caller routes to archival, not a rule. Raises ValueError
    when the atom is meant to be a rule but fails structural validation."""
    if not schema.is_rule(atom):
        return None

    errors = schema.validate(atom)
    if errors:
        raise ValueError("invalid atom: " + "; ".join(errors))

    rule_text = f'{atom["statement"]} — Decision rule: {atom["decision_rule"]}'
    return db.add_rule(
        conn, domain, rule_text,
        source=atom["source"],
        confidence=atom.get("confidence", "core"),
        map=atom["map"],
        source_date=atom["source_date"],
        source_tier=atom["source_tier"],
        principle_type=atom["principle_type"],
        current_stance=atom.get("current_stance"),
    )


def write_link(conn, rule_a: int, rule_b: int, link_type: str,
               as_of: str = None, why: str = None, source: str = None) -> int:
    """Records a tension/evolution/supports/depends link between two rules."""
    return db.add_rule_link(conn, rule_a, rule_b, link_type, as_of=as_of, why=why, source=source)
