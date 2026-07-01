"""Load hand-authored atom YAML files into the naval topic's rules table.

Two-phase and fail-closed: every rule-atom across every file is validated first;
only if all pass are any written. A single invalid atom aborts the whole load
with nothing written, so the brain never ends up half-populated with garbage.
"""
import glob
import os

import yaml

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
