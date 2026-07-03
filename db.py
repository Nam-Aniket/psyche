import sqlite3
import os
import numpy as np
from datetime import datetime, timezone

# Current schema version. Bump this whenever the schema changes and append a
# matching (version, callable) pair to MIGRATIONS below.
SCHEMA_VERSION = 5


def _create_atomic_memory_tables(conn: sqlite3.Connection):
    """Creates the atomic memory tables (v2). Idempotent; shared by init_db
    and the v1->v2 migration."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS atomic_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact TEXT NOT NULL,
            agent_id TEXT,
            run_id TEXT,
            category TEXT,
            embedding_blob BLOB,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            superseded_by INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memory_entities (
            memory_id INTEGER NOT NULL,
            entity TEXT NOT NULL,
            PRIMARY KEY (memory_id, entity),
            FOREIGN KEY (memory_id) REFERENCES atomic_memories (id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS atomic_memories_fts USING fts5(
            memory_id UNINDEXED,
            fact
        )
    """)


def _migrate_v2_atomic_memories(conn: sqlite3.Connection):
    _create_atomic_memory_tables(conn)


def _migrate_v3_actionable_and_project(conn: sqlite3.Connection):
    """v3: project scoping + retrieval ranking on atomic_memories; plan_id
    linking goals/experiments back to a generated guidance plan."""
    cur = conn.cursor()
    for ddl in (
        "ALTER TABLE atomic_memories ADD COLUMN project TEXT",
        "ALTER TABLE atomic_memories ADD COLUMN retrieval_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE atomic_memories ADD COLUMN last_retrieved_at TEXT",
        "ALTER TABLE goals ADD COLUMN plan_id TEXT",
        "ALTER TABLE experiments ADD COLUMN plan_id TEXT",
    ):
        try:
            cur.execute(ddl)
        except sqlite3.OperationalError:
            pass


def _migrate_v4_naval_rules(conn: sqlite3.Connection):
    """v4: maps layer + temporal model for the naval decision engine.

    Additive columns tag each rule-atom with its mental map, the source's date
    and tier (canonical/foundational/evidence), axiom-vs-derived type, and —
    when the principle evolved — Naval's current stance. The rule_links table
    records typed relationships between rules (tension/evolution/supports)."""
    cur = conn.cursor()
    for ddl in (
        "ALTER TABLE rules ADD COLUMN map TEXT",
        "ALTER TABLE rules ADD COLUMN source_date TEXT",
        "ALTER TABLE rules ADD COLUMN source_tier TEXT",
        "ALTER TABLE rules ADD COLUMN principle_type TEXT",
        "ALTER TABLE rules ADD COLUMN current_stance TEXT",
    ):
        try:
            cur.execute(ddl)
        except sqlite3.OperationalError:
            pass
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rule_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_a INTEGER NOT NULL,
            rule_b INTEGER NOT NULL,
            link_type TEXT NOT NULL,
            as_of TEXT,
            why TEXT,
            source TEXT,
            created_at TEXT NOT NULL
        )
    """)


def _migrate_v5_rule_evidence(conn: sqlite3.Connection):
    """v5: dated Tier-2 evidence quotes attached to rules.

    Podcasts and interviews never mint rules — they cite them. Each row is a
    verbatim quote attached to an existing rule with a stance
    (origin/confirms/refines/strains) and the utterance date."""
    conn.cursor().execute("""
        CREATE TABLE IF NOT EXISTS rule_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER NOT NULL,
            quote TEXT NOT NULL,
            note TEXT,
            stance TEXT,
            source TEXT,
            as_of TEXT,
            created_at TEXT NOT NULL
        )
    """)


# Ordered list of (target_version, migration_callable) pairs. Each callable
# receives an open sqlite3.Connection and upgrades the schema to target_version.
# Version 1 is the baseline schema (created idempotently in init_db).
MIGRATIONS = [
    (2, _migrate_v2_atomic_memories),
    (3, _migrate_v3_actionable_and_project),
    (4, _migrate_v4_naval_rules),
    (5, _migrate_v5_rule_evidence),
]

def resolve_db_path(db_path: str = None) -> str:
    """Resolves relative database paths to ~/.psyche/ folder, while leaving absolute paths intact."""
    if not db_path:
        db_path = os.getenv("DATABASE_PATH", "knowledge.db")
    
    if os.path.isabs(db_path):
        return db_path
        
    basename = os.path.basename(db_path)
    home_dir = os.path.expanduser("~/.psyche")
    return os.path.join(home_dir, basename)

def index_path_for(db_path):
    """Derives the usearch index path for a database file."""
    return os.path.splitext(db_path)[0] + ".usearch"

def _apply_connection_pragmas(conn):
    """Concurrency-safety pragmas applied to EVERY SQLite connection so multiple
    clients (e.g. Claude Desktop and Claude Code each running their own Psyche
    stdio server, plus the web app, all against the same DB) don't trip
    'database is locked': WAL lets readers and the single writer coexist, and
    busy_timeout makes a contending writer wait instead of erroring instantly."""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")

def init_db(db_path: str):
    """Initializes the database schema if it doesn't already exist."""
    resolved_path = resolve_db_path(db_path)
    # Ensure directory exists
    db_dir = os.path.dirname(resolved_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(resolved_path)
    _apply_connection_pragmas(conn)
    cursor = conn.cursor()
    
    # Create sources table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            file_path TEXT,
            checksum TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    
    # Create chunks table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            location TEXT,
            FOREIGN KEY (source_id) REFERENCES sources (id) ON DELETE CASCADE
        )
    """)
    
    # Migration helper: Add location column to chunks if database exists but lacks it
    try:
        cursor.execute("ALTER TABLE chunks ADD COLUMN location TEXT")
    except sqlite3.OperationalError:
        pass
    
    # Create embeddings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id INTEGER UNIQUE NOT NULL,
            embedding_blob BLOB NOT NULL,
            FOREIGN KEY (chunk_id) REFERENCES chunks (id) ON DELETE CASCADE
        )
    """)
    
    # Create FTS5 virtual table for keyword search
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_id UNINDEXED,
            text
        )
    """)
    
    # Create concepts table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS concepts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            definition TEXT,
            category TEXT
        )
    """)
    
    # Create concept_links table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS concept_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_concept_id INTEGER NOT NULL,
            target_concept_id INTEGER NOT NULL,
            relationship TEXT NOT NULL,
            description TEXT,
            FOREIGN KEY (source_concept_id) REFERENCES concepts (id) ON DELETE CASCADE,
            FOREIGN KEY (target_concept_id) REFERENCES concepts (id) ON DELETE CASCADE,
            UNIQUE(source_concept_id, target_concept_id, relationship)
        )
    """)
    
    # Create metadata table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    # Create memory_core table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memory_core (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            updated_at TEXT NOT NULL
        )
    """)
    
    # Create memory_recall table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memory_recall (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            tool_calls TEXT,
            created_at TEXT NOT NULL
        )
    """)
    
    # Create memory_archival table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memory_archival (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id INTEGER UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (chunk_id) REFERENCES chunks (id) ON DELETE CASCADE
        )
    """)
    
    # Create goals table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            stage TEXT DEFAULT 'exploring',
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    
    # Create experiments table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            title TEXT NOT NULL,
            hypothesis TEXT,
            metric_name TEXT,
            success_condition TEXT,
            failure_condition TEXT,
            start_date TEXT,
            review_date TEXT,
            status TEXT DEFAULT 'active',
            outcome TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (goal_id) REFERENCES goals (id) ON DELETE SET NULL
        )
    """)
    
    # Create metric_logs table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS metric_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id INTEGER,
            goal_id INTEGER,
            metric_name TEXT NOT NULL,
            value REAL NOT NULL,
            unit TEXT,
            note TEXT,
            logged_at TEXT NOT NULL,
            FOREIGN KEY (experiment_id) REFERENCES experiments (id) ON DELETE SET NULL,
            FOREIGN KEY (goal_id) REFERENCES goals (id) ON DELETE SET NULL
        )
    """)
    
    # Create reviews table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id INTEGER,
            goal_id INTEGER,
            what_happened TEXT,
            what_worked TEXT,
            what_didnt TEXT,
            lesson TEXT,
            next_action TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (experiment_id) REFERENCES experiments (id) ON DELETE SET NULL,
            FOREIGN KEY (goal_id) REFERENCES goals (id) ON DELETE SET NULL
        )
    """)
    
    # Create rules table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            rule_text TEXT NOT NULL,
            source TEXT,
            confidence TEXT DEFAULT 'tentative',
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    
    # Create atomic memory tables (v2 schema)
    _create_atomic_memory_tables(conn)

    # Apply v3 additive columns so fresh DBs converge with migrated ones
    # (the ALTERs are idempotent via try/except inside).
    _migrate_v3_actionable_and_project(conn)

    # Apply v4 naval-engine columns + rule_links (idempotent, same convergence).
    _migrate_v4_naval_rules(conn)

    # Apply v5 rule_evidence table (idempotent, same convergence).
    _migrate_v5_rule_evidence(conn)

    # Apply schema versioning / migrations now that all tables exist.
    _run_migrations(conn)

    conn.commit()
    conn.close()

def _run_migrations(conn: sqlite3.Connection):
    """Stamps fresh databases with the current SCHEMA_VERSION and applies any
    pending migrations to bring an older database up to date.

    Runs inside the caller's transaction; the caller is responsible for the
    final commit.
    """
    current = get_metadata(conn, "schema_version")
    current = int(current) if current is not None else 0

    if current == 0:
        # Fresh DB (or pre-versioning DB) — the current schema is already
        # created idempotently above, so just stamp it.
        set_metadata(conn, "schema_version", str(SCHEMA_VERSION))
        return

    if current > SCHEMA_VERSION:
        print(
            f"Warning: database schema_version ({current}) is newer than this "
            f"Psyche supports ({SCHEMA_VERSION}). This DB was likely written by "
            f"a newer Psyche; continuing, but some features may misbehave."
        )
        return

    if current < SCHEMA_VERSION:
        for version, migrate in sorted(MIGRATIONS, key=lambda m: m[0]):
            if version > current:
                migrate(conn)
                set_metadata(conn, "schema_version", str(version))
                current = version

def get_connection(db_path: str) -> sqlite3.Connection:
    """Returns a connection to the SQLite database with sqlite-vec loaded if available."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    _apply_connection_pragmas(conn)

    # Try to load sqlite-vec dynamically
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except Exception:
        pass
        
    return conn


def check_checksum(conn: sqlite3.Connection, checksum: str) -> int | None:
    """Checks if a file with the given checksum has already been ingested.
    Returns the source_id if it exists, otherwise None.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM sources WHERE checksum = ?", (checksum,))
    row = cursor.fetchone()
    return row[0] if row else None

def add_source(conn: sqlite3.Connection, title: str, author: str | None, file_path: str, checksum: str) -> int:
    """Adds a new source file to the database. Returns the newly created source ID."""
    cursor = conn.cursor()
    created_at = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "INSERT INTO sources (title, author, file_path, checksum, created_at) VALUES (?, ?, ?, ?, ?)",
        (title, author, file_path, checksum, created_at)
    )
    conn.commit()
    return cursor.lastrowid

def remove_source(conn: sqlite3.Connection, source_id: int, db_path: str | None = None):
    """Deletes a source and all associated chunks, embeddings, and virtual table indexes.

    Also purges the corresponding keys from the USearch HNSW index so it stays in
    sync with SQLite. If db_path is not supplied, it is derived from the connection.
    """
    cursor = conn.cursor()
    # Get all chunk IDs first to clean up virtual tables and the USearch index
    cursor.execute("SELECT id FROM chunks WHERE source_id = ?", (source_id,))
    chunk_ids = [row[0] for row in cursor.fetchall()]

    if chunk_ids:
        placeholders = ",".join("?" for _ in chunk_ids)
        # Delete from chunks_fts
        cursor.execute(f"DELETE FROM chunks_fts WHERE chunk_id IN ({placeholders})", chunk_ids)
        # Delete from vec_chunks
        try:
            cursor.execute(f"DELETE FROM vec_chunks WHERE chunk_id IN ({placeholders})", chunk_ids)
        except sqlite3.OperationalError:
            pass

    # Delete from sources (will cascade delete chunks and embeddings)
    cursor.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()

    # Purge the removed chunk_ids from the USearch index so it doesn't return stale keys.
    if chunk_ids:
        _remove_keys_from_usearch_index(conn, chunk_ids, db_path)


def _remove_keys_from_usearch_index(conn: sqlite3.Connection, chunk_ids: list[int], db_path: str | None = None):
    """Removes the given chunk_id keys from the on-disk USearch index, if it exists."""
    try:
        from usearch.index import Index
    except ImportError:
        return

    # Derive the db path from the connection if not provided.
    if not db_path:
        try:
            row = conn.execute("PRAGMA database_list").fetchone()
            db_path = row[2] if row else None
        except sqlite3.Error:
            db_path = None
    if not db_path:
        return

    resolved_path = resolve_db_path(db_path)
    index_path = index_path_for(resolved_path)
    if not os.path.exists(index_path):
        return

    try:
        index = Index.restore(index_path)
        if index is None or len(index) == 0:
            return
        # Only remove keys actually present (usearch can segfault on missing keys).
        present = [k for k in chunk_ids if k in index]
        if not present:
            return
        keys_arr = np.array(present, dtype=np.int64)
        try:
            index.remove(keys_arr)
        except Exception:
            # Batch remove may raise; fall back to per-key removal.
            for key in present:
                try:
                    index.remove(np.int64(key))
                except Exception:
                    pass
        # If the index is now empty, delete the file rather than persisting an
        # empty index: usearch can segfault when an emptied index is reloaded and
        # added to. A fresh index is rebuilt lazily on the next add. This mirrors
        # build_or_update_usearch_index, which also removes the file when no rows.
        if len(index) == 0:
            os.remove(index_path)
        else:
            index.save(index_path)
    except Exception as e:
        import sys
        sys.stderr.write(f"[Psyche] Warning: Could not purge keys from USearch index ({e})\n")

def add_chunk(conn: sqlite3.Connection, source_id: int, chunk_index: int, text: str, location: str | None = None) -> int:
    """Adds a chunk of text to the database with location metadata and indexes it in FTS5. Returns the chunk ID."""
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chunks (source_id, chunk_index, text, location) VALUES (?, ?, ?, ?)",
        (source_id, chunk_index, text, location)
    )
    chunk_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO chunks_fts (chunk_id, text) VALUES (?, ?)",
        (chunk_id, text)
    )
    conn.commit()
    return chunk_id

def add_embedding(conn: sqlite3.Connection, chunk_id: int, embedding: list[float] | np.ndarray):
    """Serializes and adds an embedding vector for a specific chunk, writing to sqlite-vec if available."""
    cursor = conn.cursor()
    # Convert embedding list/array to float32 binary representation
    vector_arr = np.array(embedding, dtype=np.float32)
    blob = vector_arr.tobytes()
    cursor.execute(
        "INSERT INTO embeddings (chunk_id, embedding_blob) VALUES (?, ?)",
        (chunk_id, blob)
    )
    conn.commit()
    
    # Try to write to sqlite-vec virtual table
    try:
        dim = len(vector_arr)
        cursor.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(chunk_id INTEGER PRIMARY KEY, embedding FLOAT[{dim}] distance_metric=cosine)")
        cursor.execute(
            "INSERT OR REPLACE INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, blob)
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass

def search_vector_vec(conn: sqlite3.Connection, query_vector: list[float] | np.ndarray, limit: int = 20) -> list[tuple[int, float]]:
    """Performs vector search using sqlite-vec if the vec_chunks virtual table exists."""
    cursor = conn.cursor()
    # Check if vec_chunks exists
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vec_chunks'")
        if not cursor.fetchone():
            return []
            
        vec_arr = np.array(query_vector, dtype=np.float32)
        blob = vec_arr.tobytes()
        
        # sqlite-vec MATCH query returns distance. For cosine distance, similarity = 1 - distance
        cursor.execute("""
            SELECT 
                chunk_id, 
                distance
            FROM vec_chunks
            WHERE embedding MATCH ? AND k = ?
        """, (blob, limit))
        rows = cursor.fetchall()
        return [(int(row[0]), 1.0 - float(row[1])) for row in rows]
    except sqlite3.OperationalError:
        return []


def get_all_embeddings_with_chunks(conn: sqlite3.Connection) -> list[dict]:
    """Retrieves all chunk texts, locations, and deserializes their corresponding embeddings."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            c.id, 
            c.text, 
            c.location,
            s.title, 
            s.author, 
            e.embedding_blob 
        FROM chunks c
        JOIN sources s ON c.source_id = s.id
        LEFT JOIN embeddings e ON e.chunk_id = c.id
    """)
    rows = cursor.fetchall()
    
    results = []
    for chunk_id, text, location, source_title, source_author, blob in rows:
        embedding = np.frombuffer(blob, dtype=np.float32) if blob is not None else None
        results.append({
            "chunk_id": chunk_id,
            "text": text,
            "location": location,
            "source_title": source_title,
            "source_author": source_author,
            "embedding": embedding
        })
    return results

def get_all_embeddings_only(conn: sqlite3.Connection) -> list[dict]:
    """Retrieves only chunk IDs and their corresponding deserialized embeddings."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            chunk_id, 
            embedding_blob 
        FROM embeddings
    """)
    rows = cursor.fetchall()
    
    results = []
    for chunk_id, blob in rows:
        embedding = np.frombuffer(blob, dtype=np.float32) if blob is not None else None
        results.append({
            "chunk_id": chunk_id,
            "embedding": embedding
        })
    return results

def get_chunks_by_ids(conn: sqlite3.Connection, chunk_ids: list[int]) -> list[dict]:
    """Retrieves detailed records (text, location, source info) for a specific list of chunk IDs, maintaining input order."""
    if not chunk_ids:
        return []
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in chunk_ids)
    cursor.execute(f"""
        SELECT 
            c.id, 
            c.text, 
            c.location,
            s.title, 
            s.author
        FROM chunks c
        JOIN sources s ON c.source_id = s.id
        WHERE c.id IN ({placeholders})
    """, chunk_ids)
    rows = cursor.fetchall()
    
    records_map = {}
    for cid, text, location, source_title, source_author in rows:
        records_map[cid] = {
            "chunk_id": cid,
            "text": text,
            "location": location,
            "source_title": source_title,
            "source_author": source_author
        }
        
    results = []
    for cid in chunk_ids:
        if cid in records_map:
            results.append(records_map[cid])
    return results


def search_fts_ids(conn: sqlite3.Connection, query_text: str, limit: int = 20) -> list[tuple[int, float]]:
    """Searches the FTS5 virtual table for keyword matches and returns only chunk IDs and BM25 scores."""
    cursor = conn.cursor()
    # Sanitize search term to prevent syntax errors in MATCH (e.g. trailing wildcards, special characters)
    # FTS5 queries should be clean alphanumeric or quoted phrases.
    clean_query = query_text.replace("'", " ").replace('"', ' ').strip()
    if not clean_query:
        return []
    # Force literal matching so FTS5 operators (AND/OR/NOT/*/:) are treated as plain tokens
    fts_query = " ".join(f'"{tok}"' for tok in clean_query.split() if tok)
    try:
        cursor.execute("""
            SELECT
                chunk_id,
                bm25(chunks_fts) AS score
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
            ORDER BY score ASC
            LIMIT ?
        """, (fts_query, limit))
        rows = cursor.fetchall()
        return [(int(row[0]), float(row[1])) for row in rows]
    except sqlite3.OperationalError:
        cursor.execute("""
            SELECT id 
            FROM chunks
            WHERE text LIKE ?
            LIMIT ?
        """, (f"%{query_text}%", limit))
        rows = cursor.fetchall()
        return [(int(row[0]), 0.0) for row in rows]

def search_fts(conn: sqlite3.Connection, query_text: str, limit: int = 20) -> list[dict]:
    """Searches the FTS5 virtual table for keyword matches and returns the original chunk records, ordered by BM25."""
    cursor = conn.cursor()
    clean_query = query_text.replace("'", " ").replace('"', ' ').strip()
    if not clean_query:
        return []
    # Force literal matching so FTS5 operators (AND/OR/NOT/*/:) are treated as plain tokens
    fts_query = " ".join(f'"{tok}"' for tok in clean_query.split() if tok)
    try:
        cursor.execute("""
            SELECT
                c.id,
                c.text,
                c.location,
                s.title,
                s.author,
                e.embedding_blob
            FROM chunks_fts fts
            JOIN chunks c ON fts.chunk_id = c.id
            JOIN sources s ON c.source_id = s.id
            LEFT JOIN embeddings e ON e.chunk_id = c.id
            WHERE chunks_fts MATCH ?
            ORDER BY bm25(chunks_fts) ASC
            LIMIT ?
        """, (fts_query, limit))
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        cursor.execute("""
            SELECT 
                c.id, 
                c.text, 
                c.location,
                s.title, 
                s.author,
                e.embedding_blob
            FROM chunks c
            JOIN sources s ON c.source_id = s.id
            LEFT JOIN embeddings e ON e.chunk_id = c.id
            WHERE c.text LIKE ?
            LIMIT ?
        """, (f"%{query_text}%", limit))
        rows = cursor.fetchall()
        
    results = []
    for chunk_id, text, location, source_title, source_author, blob in rows:
        embedding = np.frombuffer(blob, dtype=np.float32) if blob is not None else None
        results.append({
            "chunk_id": chunk_id,
            "text": text,
            "location": location,
            "source_title": source_title,
            "source_author": source_author,
            "embedding": embedding
        })
    return results


def add_concept(conn: sqlite3.Connection, name: str, definition: str | None = None, category: str | None = None) -> int:
    """Inserts a concept if it doesn't exist, or updates its definition and category. Returns concept ID."""
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO concepts (name, definition, category)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            definition = COALESCE(excluded.definition, definition),
            category = COALESCE(excluded.category, category)
    """, (name.strip(), definition, category))
    conn.commit()
    
    cursor.execute("SELECT id FROM concepts WHERE name = ?", (name.strip(),))
    return cursor.fetchone()[0]

def add_concept_link(conn: sqlite3.Connection, source_name: str, target_name: str, relationship: str, description: str | None = None) -> int:
    """Creates a directed relationship between two concepts by name. Auto-creates concepts if missing."""
    source_id = add_concept(conn, source_name)
    target_id = add_concept(conn, target_name)
    
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO concept_links (source_concept_id, target_concept_id, relationship, description)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_concept_id, target_concept_id, relationship) DO UPDATE SET
            description = COALESCE(excluded.description, description)
    """, (source_id, target_id, relationship.strip(), description))
    conn.commit()
    return cursor.lastrowid

def get_all_concepts(conn: sqlite3.Connection) -> list[dict]:
    """Retrieves all concepts from the database."""
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, definition, category FROM concepts")
    rows = cursor.fetchall()
    return [{"id": r[0], "name": r[1], "definition": r[2], "category": r[3]} for r in rows]

def get_concept_links(conn: sqlite3.Connection) -> list[dict]:
    """Retrieves all concept relationship links with name values."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            cl.id, 
            c1.name AS source_name, 
            c2.name AS target_name, 
            cl.relationship, 
            cl.description 
        FROM concept_links cl
        JOIN concepts c1 ON cl.source_concept_id = c1.id
        JOIN concepts c2 ON cl.target_concept_id = c2.id
    """)
    rows = cursor.fetchall()
    return [{"id": r[0], "source": r[1], "target": r[2], "relationship": r[3], "description": r[4]} for r in rows]

def get_metadata(conn: sqlite3.Connection, key: str) -> str | None:
    """Retrieves a metadata value by key."""
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM metadata WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        # Table might not exist yet if database is older
        return None

def set_metadata(conn: sqlite3.Connection, key: str, value: str):
    """Sets a metadata value by key."""
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO metadata (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, value))
    conn.commit()

def check_and_migrate_embeddings(db_path: str, llm):
    """Checks if the configured embedding model matches the model stored in the database.
    If they mismatch, automatically re-generates all embeddings using the new model.
    """
    resolved_path = resolve_db_path(db_path)
    if not os.path.exists(resolved_path):
        return

    init_db(resolved_path)
    conn = get_connection(resolved_path)
    try:
        # Ensure metadata table exists
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()

        # Check if database is empty
        cursor.execute("SELECT COUNT(*) FROM chunks")
        chunk_count = cursor.fetchone()[0]
        if chunk_count == 0:
            set_metadata(conn, "embed_model", llm.embed_model)
            return

        db_model = get_metadata(conn, "embed_model")
        config_model = llm.embed_model

        # If LLM client or embed model is a unit test mock, skip migration to avoid side-effects in unrelated tests
        if 'Mock' in type(config_model).__name__:
            return

        if db_model == config_model:
            return

        # If running in a non-interactive environment (like MCP server), skip heavy migration to prevent timeouts
        import sys
        if (not sys.stdin.isatty() or os.getenv("PSYCHE_NONINTERACTIVE") == "1") and "unittest" not in sys.modules and os.getenv("TESTING") != "true":
            sys.stderr.write(
                f"[Psyche] Mismatched embedding model detected (DB: {db_model or 'none'} vs Config: {config_model}).\n"
                "[Psyche] Skipped automatic embedding migration in non-interactive session to prevent timeout.\n"
                "[Psyche] Please run 'psyche query' or 'psyche ingest' in the terminal to migrate your database.\n"
            )
            return

        # We have a mismatch!
        from rich.console import Console
        from rich.progress import Progress
        import sys as _sys
        _quiet = ("unittest" in _sys.modules) or os.getenv("TESTING") == "true"
        console = Console(quiet=_quiet)

        console.print(f"\n[bold yellow]🔄 Mismatched embedding model detected in database![/bold yellow]")
        console.print(f"  [dim]Database model: {db_model or 'None (legacy)'}[/dim]")
        console.print(f"  [dim]Configured model: {config_model}[/dim]")

        if config_model == "none":
            console.print("[yellow]AI-Free mode configured. Clearing existing database embeddings...[/yellow]")
            cursor.execute("DELETE FROM embeddings")
            try:
                cursor.execute("DROP TABLE IF EXISTS vec_chunks")
            except sqlite3.OperationalError:
                pass
            conn.commit()
            set_metadata(conn, "embed_model", "none")
            console.print("[green]✨ Database embeddings cleared successfully.[/green]\n")
            return

        console.print(f"[cyan]Automatically re-generating embeddings for {chunk_count} chunks using '{config_model}'...[/cyan]")

        # Fetch all chunks
        cursor.execute("SELECT id, text FROM chunks ORDER BY id")
        chunk_rows = cursor.fetchall()
        chunk_ids = [r[0] for r in chunk_rows]
        texts = [r[1] for r in chunk_rows]

        # Generate new embeddings in batches
        embeddings = []
        batch_size = 50
        with Progress(console=console) as progress:
            task = progress.add_task("[cyan]Re-generating embeddings...", total=len(texts))
            for i in range(0, len(texts), batch_size):
                sub_texts = texts[i:i+batch_size]
                sub_embeddings = llm.get_embeddings_batch(sub_texts)
                embeddings.extend(sub_embeddings)
                progress.update(task, advance=len(sub_texts))

        # Clear old embeddings and save new ones
        cursor.execute("DELETE FROM embeddings")
        try:
            cursor.execute("DROP TABLE IF EXISTS vec_chunks")
        except sqlite3.OperationalError:
            pass
        
        # We can insert using add_embedding logic but inline for efficiency
        import numpy as np
        dim = len(embeddings[0]) if len(embeddings) > 0 else 0
        if dim > 0:
            try:
                cursor.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(chunk_id INTEGER PRIMARY KEY, embedding FLOAT[{dim}] distance_metric=cosine)")
            except sqlite3.OperationalError:
                pass
                
        for cid, emb in zip(chunk_ids, embeddings):
            vector_arr = np.array(emb, dtype=np.float32)
            blob = vector_arr.tobytes()
            cursor.execute(
                "INSERT INTO embeddings (chunk_id, embedding_blob) VALUES (?, ?)",
                (cid, blob)
            )
            try:
                cursor.execute(
                    "INSERT OR REPLACE INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)",
                    (cid, blob)
                )
            except sqlite3.OperationalError:
                pass
        
        set_metadata(conn, "embed_model", config_model)
        conn.commit()
        console.print(f"[bold green]✨ Database embeddings successfully migrated to '{config_model}'![/bold green]\n")

    except Exception as e:
        console = Console(stderr=True)
        console.print(f"[bold red]Error during automatic embedding migration:[/bold red] {e}")
        conn.rollback()
    finally:
        conn.close()
        build_or_update_usearch_index(db_path)

def build_or_update_usearch_index(db_path: str):
    """Rebuilds or updates the corresponding USearch HNSW index file from all SQLite embeddings."""
    try:
        from usearch.index import Index
    except ImportError:
        return
        
    resolved_path = resolve_db_path(db_path)
    index_path = index_path_for(resolved_path)

    conn = get_connection(resolved_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT chunk_id, embedding_blob FROM embeddings")
        rows = cursor.fetchall()
        if not rows:
            if os.path.exists(index_path):
                os.remove(index_path)
            return
            
        import numpy as np
        chunk_ids = []
        vectors = []
        for cid, blob in rows:
            vec = np.frombuffer(blob, dtype=np.float32)
            chunk_ids.append(cid)
            vectors.append(vec)
            
        dim = len(vectors[0])
        index = Index(ndim=dim, metric="cosine")
        
        keys_arr = np.array(chunk_ids, dtype=np.int64)
        vectors_matrix = np.vstack(vectors)
        index.add(keys_arr, vectors_matrix)
        index.save(index_path)
    except Exception as e:
        import sys
        sys.stderr.write(f"[Psyche] Warning: Could not update USearch index ({e})\n")
    finally:
        conn.close()

def update_usearch_index_incrementally(db_path: str, chunk_id: int, vector: list[float]):
    """Dynamically appends a vector to the USearch index without rebuilding from scratch."""
    try:
        from usearch.index import Index
    except ImportError:
        return
        
    resolved_path = resolve_db_path(db_path)
    index_path = index_path_for(resolved_path)
    dim = len(vector)
    
    index = Index(ndim=dim, metric="cosine")
    
    # If index exists, load existing keys/vectors
    if os.path.exists(index_path):
        try:
            index.load(index_path)
        except Exception as e:
            import sys
            sys.stderr.write(f"[Psyche] Failed to load index for update. Re-initializing: {e}\n")
            
    # Add new key and vector
    keys_arr = np.array([chunk_id], dtype=np.int64)
    vectors_matrix = np.array([vector], dtype=np.float32)
    if len(vectors_matrix.shape) == 1:
        vectors_matrix = np.expand_dims(vectors_matrix, axis=0)

    # Remove any existing entry for this key first so re-adds are idempotent
    # (re-ingest reuses keys; usearch.add() would otherwise orphan the old vector).
    # Guard against an empty index: usearch can segfault on remove() when size==0,
    # and only attempt removal when the key is actually present.
    try:
        if len(index) > 0 and chunk_id in index:
            index.remove(keys_arr)
    except Exception:
        # Removing a non-existent key may raise; that's fine.
        pass

    index.add(keys_arr, vectors_matrix)
    
    # Save back to disk
    index.save(index_path)

def sync_memories_hook(db_path: str):
    """Scans ~/.psyche/memories/ and programmatically runs ingest on it if new memories exist."""
    try:
        memories_dir = os.path.expanduser("~/.psyche/memories")
        if not os.path.exists(memories_dir):
            return
            
        # Get list of md files
        files = [os.path.join(memories_dir, f) for f in os.listdir(memories_dir) if f.endswith(".md")]
        if not files:
            return
            
        # Check if there's any file that hasn't been ingested yet by verifying checksums in the database
        conn = sqlite3.connect(db_path)
        _apply_connection_pragmas(conn)
        try:
            cursor = conn.cursor()
            import hashlib
            has_unindexed = False
            for fpath in files:
                sha256_hash = hashlib.sha256()
                with open(fpath, "rb") as f:
                    for byte_block in iter(lambda: f.read(4096), b""):
                        sha256_hash.update(byte_block)
                checksum = sha256_hash.hexdigest()
                
                cursor.execute("SELECT id FROM sources WHERE checksum = ?", (checksum,))
                if not cursor.fetchone():
                    has_unindexed = True
                    break
        finally:
            conn.close()
            
        if not has_unindexed:
            return
            
        # Run ingest.py
        import subprocess
        import sys
        current_dir = os.path.dirname(os.path.abspath(__file__))
        ingest_script = os.path.join(current_dir, "ingest.py")
        subprocess.run(
            [sys.executable, ingest_script, memories_dir, "--db-path", db_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception:
        pass


# ─── Guidance Layer CRUD ─────────────────────────────────────────────

def add_goal(conn: sqlite3.Connection, domain: str, title: str, description: str = None, stage: str = 'exploring') -> int:
    """Creates a new goal. Returns the goal ID."""
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "INSERT INTO goals (domain, stage, title, description, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
        (domain, stage, title, description, now, now)
    )
    conn.commit()
    return cursor.lastrowid

def get_goals(conn: sqlite3.Connection, domain: str = None, status: str = 'active') -> list[dict]:
    """Retrieves goals, optionally filtered by domain and status."""
    cursor = conn.cursor()
    query = "SELECT id, domain, stage, title, description, status, created_at, updated_at FROM goals WHERE 1=1"
    params = []
    if domain:
        query += " AND domain = ?"
        params.append(domain)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY updated_at DESC"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    return [{"id": r[0], "domain": r[1], "stage": r[2], "title": r[3], "description": r[4], "status": r[5], "created_at": r[6], "updated_at": r[7]} for r in rows]

def update_goal(conn: sqlite3.Connection, goal_id: int, **kwargs):
    """Updates a goal's fields. Accepts any combination of: domain, stage, title, description, status."""
    allowed = {'domain', 'stage', 'title', 'description', 'status'}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields['updated_at'] = datetime.now(timezone.utc).isoformat()
    set_clause = ', '.join(f'{k} = ?' for k in fields)
    values = list(fields.values()) + [goal_id]
    cursor = conn.cursor()
    cursor.execute(f"UPDATE goals SET {set_clause} WHERE id = ?", values)
    conn.commit()

def add_experiment(conn: sqlite3.Connection, goal_id: int, title: str, hypothesis: str = None, metric_name: str = None, success_condition: str = None, failure_condition: str = None, start_date: str = None, review_date: str = None) -> int:
    """Creates a new experiment linked to a goal. Returns the experiment ID."""
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    if not start_date:
        start_date = now
    cursor.execute(
        "INSERT INTO experiments (goal_id, title, hypothesis, metric_name, success_condition, failure_condition, start_date, review_date, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
        (goal_id, title, hypothesis, metric_name, success_condition, failure_condition, start_date, review_date, now)
    )
    conn.commit()
    return cursor.lastrowid

def get_experiments(conn: sqlite3.Connection, goal_id: int = None, status: str = 'active') -> list[dict]:
    """Retrieves experiments, optionally filtered by goal and status."""
    cursor = conn.cursor()
    query = "SELECT id, goal_id, title, hypothesis, metric_name, success_condition, failure_condition, start_date, review_date, status, outcome, created_at FROM experiments WHERE 1=1"
    params = []
    if goal_id is not None:
        query += " AND goal_id = ?"
        params.append(goal_id)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    return [{"id": r[0], "goal_id": r[1], "title": r[2], "hypothesis": r[3], "metric_name": r[4], "success_condition": r[5], "failure_condition": r[6], "start_date": r[7], "review_date": r[8], "status": r[9], "outcome": r[10], "created_at": r[11]} for r in rows]

def update_experiment(conn: sqlite3.Connection, experiment_id: int, **kwargs):
    """Updates an experiment's fields. Accepts: title, hypothesis, metric_name, success_condition, failure_condition, review_date, status, outcome."""
    allowed = {'title', 'hypothesis', 'metric_name', 'success_condition', 'failure_condition', 'review_date', 'status', 'outcome'}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ', '.join(f'{k} = ?' for k in fields)
    values = list(fields.values()) + [experiment_id]
    cursor = conn.cursor()
    cursor.execute(f"UPDATE experiments SET {set_clause} WHERE id = ?", values)
    conn.commit()

def add_metric_log(conn: sqlite3.Connection, metric_name: str, value: float, unit: str = None, note: str = None, experiment_id: int = None, goal_id: int = None) -> int:
    """Logs a metric data point. Returns the log entry ID."""
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "INSERT INTO metric_logs (experiment_id, goal_id, metric_name, value, unit, note, logged_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (experiment_id, goal_id, metric_name, value, unit, note, now)
    )
    conn.commit()
    return cursor.lastrowid

def get_metric_logs(conn: sqlite3.Connection, metric_name: str = None, experiment_id: int = None, goal_id: int = None, limit: int = 50) -> list[dict]:
    """Retrieves metric log entries, optionally filtered."""
    cursor = conn.cursor()
    query = "SELECT id, experiment_id, goal_id, metric_name, value, unit, note, logged_at FROM metric_logs WHERE 1=1"
    params = []
    if metric_name:
        query += " AND metric_name = ?"
        params.append(metric_name)
    if experiment_id is not None:
        query += " AND experiment_id = ?"
        params.append(experiment_id)
    if goal_id is not None:
        query += " AND goal_id = ?"
        params.append(goal_id)
    query += " ORDER BY logged_at DESC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = cursor.fetchall()
    return [{"id": r[0], "experiment_id": r[1], "goal_id": r[2], "metric_name": r[3], "value": r[4], "unit": r[5], "note": r[6], "logged_at": r[7]} for r in rows]

def add_review(conn: sqlite3.Connection, what_happened: str, what_worked: str = None, what_didnt: str = None, lesson: str = None, next_action: str = None, experiment_id: int = None, goal_id: int = None) -> int:
    """Creates a review entry. Returns the review ID."""
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "INSERT INTO reviews (experiment_id, goal_id, what_happened, what_worked, what_didnt, lesson, next_action, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (experiment_id, goal_id, what_happened, what_worked, what_didnt, lesson, next_action, now)
    )
    conn.commit()
    return cursor.lastrowid

def get_reviews(conn: sqlite3.Connection, goal_id: int = None, experiment_id: int = None, limit: int = 10) -> list[dict]:
    """Retrieves reviews, optionally filtered by goal or experiment."""
    cursor = conn.cursor()
    query = "SELECT id, experiment_id, goal_id, what_happened, what_worked, what_didnt, lesson, next_action, created_at FROM reviews WHERE 1=1"
    params = []
    if goal_id is not None:
        query += " AND goal_id = ?"
        params.append(goal_id)
    if experiment_id is not None:
        query += " AND experiment_id = ?"
        params.append(experiment_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cursor.execute(query, params)
    rows = cursor.fetchall()
    return [{"id": r[0], "experiment_id": r[1], "goal_id": r[2], "what_happened": r[3], "what_worked": r[4], "what_didnt": r[5], "lesson": r[6], "next_action": r[7], "created_at": r[8]} for r in rows]

def add_rule(conn: sqlite3.Connection, domain: str, rule_text: str, source: str = None,
             confidence: str = 'tentative', map: str = None, source_date: str = None,
             source_tier: str = None, principle_type: str = None,
             current_stance: str = None) -> int:
    """Creates a new personal rule. Returns the rule ID.

    The naval decision engine uses the extra fields: `map` (which mental map the
    rule belongs to), `source_date`/`source_tier` (when/what authority it came
    from), `principle_type` (axiom|derived), and `current_stance` (set when the
    principle has evolved). All default to None so existing callers are unaffected."""
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "INSERT INTO rules (domain, rule_text, source, confidence, active, created_at, updated_at, "
        "map, source_date, source_tier, principle_type, current_stance) "
        "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)",
        (domain, rule_text, source, confidence, now, now,
         map, source_date, source_tier, principle_type, current_stance)
    )
    conn.commit()
    return cursor.lastrowid


def add_rule_link(conn: sqlite3.Connection, rule_a: int, rule_b: int, link_type: str,
                  as_of: str = None, why: str = None, source: str = None) -> int:
    """Records a typed link between two rules. link_type is one of
    tension | evolution | supports | depends. For evolution links, rule_a is the
    earlier principle and rule_b the later/current one. Returns the link ID."""
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "INSERT INTO rule_links (rule_a, rule_b, link_type, as_of, why, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (rule_a, rule_b, link_type, as_of, why, source, now)
    )
    conn.commit()
    return cursor.lastrowid


def add_rule_evidence(conn: sqlite3.Connection, rule_id: int, quote: str, note: str = None,
                      stance: str = None, source: str = None, as_of: str = None) -> int:
    """Attaches a dated Tier-2 evidence quote to an existing rule. Evidence
    never mints rules — it cites them. stance is one of
    origin | confirms | refines | strains. Returns the evidence row ID."""
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "INSERT INTO rule_evidence (rule_id, quote, note, stance, source, as_of, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (rule_id, quote, note, stance, source, as_of, now)
    )
    conn.commit()
    return cursor.lastrowid


def get_rules_by_map(conn: sqlite3.Connection, domain: str, active: bool = True) -> dict:
    """Returns active rules for a domain grouped by their `map` tag:
    {map_name: [rule_dict, ...]}. Rules with no map fall under '_unmapped'."""
    cursor = conn.cursor()
    query = (
        "SELECT id, domain, rule_text, source, confidence, active, created_at, updated_at, "
        "map, source_date, source_tier, principle_type, current_stance "
        "FROM rules WHERE domain = ?"
    )
    params = [domain]
    if active:
        query += " AND active = 1"
    query += " ORDER BY map, updated_at DESC"
    grouped = {}
    for r in cursor.execute(query, params).fetchall():
        rule = {
            "id": r[0], "domain": r[1], "rule_text": r[2], "source": r[3],
            "confidence": r[4], "active": bool(r[5]), "created_at": r[6],
            "updated_at": r[7], "map": r[8], "source_date": r[9],
            "source_tier": r[10], "principle_type": r[11], "current_stance": r[12],
        }
        grouped.setdefault(r[8] or "_unmapped", []).append(rule)
    return grouped

def get_rules(conn: sqlite3.Connection, domain: str = None, active: bool = True) -> list[dict]:
    """Retrieves personal rules, optionally filtered by domain."""
    cursor = conn.cursor()
    query = "SELECT id, domain, rule_text, source, confidence, active, created_at, updated_at FROM rules WHERE 1=1"
    params = []
    if domain:
        query += " AND domain = ?"
        params.append(domain)
    if active:
        query += " AND active = 1"
    query += " ORDER BY updated_at DESC"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    return [{"id": r[0], "domain": r[1], "rule_text": r[2], "source": r[3], "confidence": r[4], "active": bool(r[5]), "created_at": r[6], "updated_at": r[7]} for r in rows]

def update_rule(conn: sqlite3.Connection, rule_id: int, **kwargs):
    """Updates a rule's fields. Accepts: domain, rule_text, source, confidence, active."""
    allowed = {'domain', 'rule_text', 'source', 'confidence', 'active'}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields['updated_at'] = datetime.now(timezone.utc).isoformat()
    set_clause = ', '.join(f'{k} = ?' for k in fields)
    values = list(fields.values()) + [rule_id]
    cursor = conn.cursor()
    cursor.execute(f"UPDATE rules SET {set_clause} WHERE id = ?", values)
    conn.commit()
