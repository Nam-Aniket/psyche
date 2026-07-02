"""Load hand-authored atom YAML files into the naval topic's rules table.

Two-phase and fail-closed: every rule-atom across every file is validated first;
only if all pass are any written. A single invalid atom aborts the whole load
with nothing written, so the brain never ends up half-populated with garbage.
"""
import glob
import os
from datetime import datetime, timezone

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

EVIDENCE_STANCES = {"origin", "confirms", "refines", "strains"}


def _index_atoms(dirs):
    """atom-id -> atom dict across all *.yaml directly under each dir."""
    index = {}
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(d, "*.yaml"))):
            _, _, atoms = load_atoms_file(path)
            for a in atoms:
                if a.get("id"):
                    index[str(a["id"])] = a
    return index


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
    index = _index_atoms(dirs)

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


def load_evidence_dir(conn, evidence_dir, atoms_dirs, domain="naval"):
    """Loads Tier-2 evidence YAMLs: dated quotes attached to existing rules,
    plus evolution links. Two-phase and fail-closed like load_atoms_dir.

    Evidence never mints rules. Each file carries source/source_date; each item
    carries {id, rule, stance, quote, note}; each evolution carries
    {from, to, as_of, why, current_stance} — the link is written to rule_links
    (type 'evolution', rule_a = earlier principle) and current_stance is set on
    the `from` rule. Idempotent: an existing (rule_id, quote) evidence row or
    (rule_a, rule_b) evolution link is skipped; current_stance is (re)applied.

    Returns {evidence, skipped, evolutions, stances_set, by_rule}.
    """
    index = _index_atoms(atoms_dirs)

    def rule_id_for(atom_id, ctx, errors):
        atom = index.get(str(atom_id))
        if not atom:
            errors.append(f"{ctx}: unknown atom id '{atom_id}'")
            return None
        rid = resolve_rule_id(conn, atom["statement"], domain)
        if rid is None:
            errors.append(f"{ctx}: atom '{atom_id}' has no rules row (not loaded yet?)")
        return rid

    # Phase 1 — parse + validate everything. No DB writes yet.
    errors, to_write, evolutions = [], [], []
    for path in sorted(glob.glob(os.path.join(evidence_dir, "*.yaml"))):
        doc = yaml.safe_load(open(path, encoding="utf-8")) or {}
        fname = os.path.basename(path)
        source, as_of = doc.get("source"), doc.get("source_date")
        if not source or not as_of:
            errors.append(f"{fname}: missing file-level source/source_date")
        for it in (doc.get("items") or []):
            ctx = it.get("id", f"{fname}:item")
            for field in ("id", "rule", "quote", "stance"):
                if not str(it.get(field, "")).strip():
                    errors.append(f"{ctx}: missing {field}")
            if it.get("stance") and it["stance"] not in EVIDENCE_STANCES:
                errors.append(f"{ctx}: invalid stance '{it['stance']}'")
            rid = rule_id_for(it.get("rule"), ctx, errors)
            if rid is not None:
                to_write.append((rid, it, source, as_of))
        for ev in (doc.get("evolutions") or []):
            ctx = ev.get("id", f"{fname}:evolution")
            for field in ("from", "to", "as_of", "why", "current_stance"):
                if not str(ev.get(field, "")).strip():
                    errors.append(f"{ctx}: missing {field}")
            a = rule_id_for(ev.get("from"), ctx, errors)
            b = rule_id_for(ev.get("to"), ctx, errors)
            if a is not None and b is not None:
                evolutions.append((a, b, ev, source))
    if errors:
        raise ValueError("; ".join(errors))

    # Phase 2 — write the validated set.
    written = skipped = links = stances = 0
    by_rule = {}
    for rid, it, source, as_of in to_write:
        if conn.execute("SELECT 1 FROM rule_evidence WHERE rule_id = ? AND quote = ?",
                        (rid, it["quote"])).fetchone():
            skipped += 1
            continue
        db.add_rule_evidence(conn, rid, it["quote"], note=it.get("note"),
                             stance=it["stance"], source=source, as_of=as_of)
        written += 1
        by_rule[it["rule"]] = by_rule.get(it["rule"], 0) + 1
    for a, b, ev, source in evolutions:
        if not conn.execute(
                "SELECT 1 FROM rule_links WHERE link_type = 'evolution' AND rule_a = ? AND rule_b = ?",
                (a, b)).fetchone():
            db.add_rule_link(conn, a, b, "evolution",
                             as_of=ev["as_of"], why=ev["why"], source=source)
            links += 1
        conn.execute("UPDATE rules SET current_stance = ?, updated_at = ? WHERE id = ?",
                     (ev["current_stance"], datetime.now(timezone.utc).isoformat(), a))
        conn.commit()
        stances += 1
    return {"evidence": written, "skipped": skipped, "evolutions": links,
            "stances_set": stances, "by_rule": by_rule}
