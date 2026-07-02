"""Load hand-authored atom YAML files into the naval topic's rules table.

Two-phase and fail-closed: every rule-atom across every file is validated first;
only if all pass are any written. A single invalid atom aborts the whole load
with nothing written, so the brain never ends up half-populated with garbage.
"""
import glob
import os

import yaml

import db
from naval_extract import schema, writer


def load_atoms_file(path):
    """Returns (map, source_tier, atoms) with file-level map/source_tier injected
    into each atom that doesn't set its own."""
    doc = yaml.safe_load(open(path, encoding="utf-8")) or {}
    fmap = doc.get("map")
    ftier = doc.get("source_tier")
    atoms = []
    for a in (doc.get("atoms") or []):
        atoms.append({**a, "map": a.get("map", fmap), "source_tier": a.get("source_tier", ftier)})
    return fmap, ftier, atoms


def load_atoms_dir(conn, atoms_dir, domain="naval"):
    """Validates and writes every atom under atoms_dir/*.yaml.

    Returns {written: [(id, rule_id)], notes: [id], by_map: {map: count}}.
    Raises ValueError (before writing anything) if any rule-atom is invalid.
    Atoms without a decision_rule are treated as notes and skipped, not written.
    """
    # Phase 1 — load + validate everything. No DB writes yet.
    to_write = []
    notes = []
    for path in sorted(glob.glob(os.path.join(atoms_dir, "*.yaml"))):
        _, _, atoms = load_atoms_file(path)
        for a in atoms:
            if not schema.is_rule(a):
                notes.append(a.get("id"))
                continue
            errs = schema.validate(a)
            if errs:
                raise ValueError(f"{a.get('id', '?')} in {os.path.basename(path)}: {errs}")
            to_write.append(a)

    # Phase 2 — write the validated set.
    written = []
    by_map = {}
    for a in to_write:
        rid = writer.write_atom(conn, a, domain=domain)
        written.append((a["id"], rid))
        by_map[a["map"]] = by_map.get(a["map"], 0) + 1

    return {"written": written, "notes": notes, "by_map": by_map}


def resolve_rule_id(conn, statement, domain="naval"):
    """Rule ID for an atom, found by exact statement prefix on rule_text
    (writer stores rule_text as '<statement> — Decision rule: <rule>')."""
    row = conn.execute(
        "SELECT id FROM rules WHERE domain = ? AND substr(rule_text, 1, ?) = ?",
        (domain, len(statement), statement)).fetchone()
    return row[0] if row else None


# atom field -> rule_links.link_type. `supports` points at the principle the
# atom grounds; `tension_with` is symmetric (deduped in both directions).
LINK_FIELDS = (("supports", "supports"), ("tension_with", "tension"))


def link_atoms_dirs(conn, dirs, domain="naval"):
    """Materializes supports/tension_with atom-id references into rule_links.

    Scans *.yaml across all dirs to index atoms by id, resolves both endpoints
    to already-written rules rows, and writes each link once (idempotent; a
    tension existing in either direction is not duplicated). References whose
    atom id or rules row can't be found are reported, not written.

    Returns {written: [(atom_id, link_type, target_atom_id)],
             unresolved: [(atom_id, link_type, target_atom_id)],
             skipped_existing: int}.
    """
    index = {}
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(d, "*.yaml"))):
            _, _, atoms = load_atoms_file(path)
            for a in atoms:
                if a.get("id"):
                    index[str(a["id"])] = a

    written, unresolved, skipped = [], [], 0
    for aid, a in index.items():
        for field, ltype in LINK_FIELDS:
            target = a.get(field)
            if not target:
                continue
            target = str(target)
            target_atom = index.get(target)
            rule_a = resolve_rule_id(conn, a["statement"], domain)
            rule_b = (resolve_rule_id(conn, target_atom["statement"], domain)
                      if target_atom else None)
            if rule_a is None or rule_b is None:
                unresolved.append((aid, ltype, target))
                continue
            both_ways = " OR (rule_a = ? AND rule_b = ?)" if ltype == "tension" else ""
            params = (ltype, rule_a, rule_b) + ((rule_b, rule_a) if ltype == "tension" else ())
            if conn.execute(
                    "SELECT 1 FROM rule_links WHERE link_type = ? AND "
                    "((rule_a = ? AND rule_b = ?)" + both_ways + ")", params).fetchone():
                skipped += 1
                continue
            db.add_rule_link(conn, rule_a, rule_b, ltype,
                             as_of=a.get("source_date"), source=a.get("source"))
            written.append((aid, ltype, target))
    return {"written": written, "unresolved": unresolved, "skipped_existing": skipped}
