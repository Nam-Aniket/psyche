"""Validation for Naval decision-engine principle-atoms.

An atom is a plain dict. It becomes a `rules` row only if it carries a non-empty
`decision_rule` (decision-rule-first). Atoms without one are principle-notes the
caller routes to archival, never to the rules table.
"""
import guidance

VALID_TIERS = {"canonical", "foundational", "evidence"}
VALID_TYPES = {"axiom", "derived"}

# Every rule-atom must carry these. `decision_rule` is included: a principle with
# no decision rule is a note, not a rule (checked separately via is_rule).
REQUIRED = (
    "statement", "decision_rule", "map", "source",
    "source_date", "source_tier", "principle_type",
)


def valid_maps():
    """The map keys declared in the naval domain pack."""
    pack = guidance.load_domain_pack("naval")
    return set(pack.get("maps", {}).keys())


def is_rule(atom: dict) -> bool:
    """True if the atom carries a decision rule (and so belongs in `rules`)."""
    return bool(str(atom.get("decision_rule", "")).strip())


def validate(atom: dict) -> list:
    """Returns a list of human-readable validation errors; empty means the atom
    is a structurally valid decision-rule atom."""
    errors = []
    for field in REQUIRED:
        if not str(atom.get(field, "")).strip():
            errors.append(f"missing required field: {field}")

    m = atom.get("map")
    if m and m not in valid_maps():
        errors.append(f"unknown map: {m} (expected one of {sorted(valid_maps())})")

    tier = atom.get("source_tier")
    if tier and tier not in VALID_TIERS:
        errors.append(f"invalid source_tier: {tier} (expected one of {sorted(VALID_TIERS)})")

    ptype = atom.get("principle_type")
    if ptype and ptype not in VALID_TYPES:
        errors.append(f"invalid principle_type: {ptype} (expected axiom|derived)")

    return errors
