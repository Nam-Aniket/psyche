#!/usr/bin/env python3
import os
import sys
import sqlite3
import threading
import argparse
import numpy as np
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.markdown import Markdown
from rich.status import Status
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import WordCompleter

# Ensure current directory is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import get_connection, get_all_embeddings_with_chunks, search_fts, resolve_db_path, check_and_migrate_embeddings, get_all_embeddings_only, get_chunks_by_ids, search_fts_ids, search_vector_vec, index_path_for
from llm_client import LLMClient

# Initialize rich console
console = Console()
err_console = Console(stderr=True)

# Load environment variables
load_dotenv()

_ranker = None
_ranker_loaded = False
_ranker_lock = threading.Lock()

def get_ranker():
    global _ranker, _ranker_loaded
    if _ranker_loaded:
        return _ranker

    # Lock so a background prewarm thread and a concurrent first tool call can't
    # both construct the (slow) reranker — double-checked under the lock.
    with _ranker_lock:
        if _ranker_loaded:
            return _ranker

        rerank_provider = os.getenv("RERANK_PROVIDER", "flashrank").lower()
        if rerank_provider == "none":
            _ranker_loaded = True
            return None

        try:
            from flashrank import Ranker
            _ranker = Ranker()
        except Exception:
            pass
        _ranker_loaded = True
        return _ranker

def prewarm_reranker():
    """Eagerly triggers the lazy reranker initialization (model download/load).

    Intended to be called once at server startup so the first MCP search does
    not pay the model load cost inside the tool call (which can cause client
    timeouts). Failures are non-fatal — search falls back to RRF.
    """
    try:
        get_ranker()
    except Exception as e:
        print(f"prewarm_reranker: failed to initialize reranker ({e})", file=sys.stderr)

def calculate_similarities(query_vector: list[float] | np.ndarray, chunk_records: list[dict]) -> list[tuple[dict, float]]:
    """Calculates cosine similarity between query embedding and all chunk embeddings."""
    q_vec = np.array(query_vector, dtype=np.float32)
    q_norm = np.linalg.norm(q_vec)
    if q_norm == 0:
        return []
        
    results = []
    for record in chunk_records:
        c_vec = record["embedding"]
        if c_vec is None:
            continue
        if len(c_vec) != len(q_vec):
            continue
        c_norm = np.linalg.norm(c_vec)
        if c_norm == 0:
            continue
            
        similarity = np.dot(q_vec, c_vec) / (q_norm * c_norm)
        results.append((record, float(similarity)))
        
    # Sort by similarity descending
    results.sort(key=lambda x: x[1], reverse=True)
    return results

def calculate_similarities_vectorized(query_vector: list[float] | np.ndarray, chunk_ids: np.ndarray, embeddings_matrix: np.ndarray) -> list[tuple[int, float]]:
    """Calculates cosine similarity between query embedding and all chunk embeddings using vectorized matrix operations."""
    q_vec = np.array(query_vector, dtype=np.float32)
    q_norm = np.linalg.norm(q_vec)
    if q_norm == 0 or embeddings_matrix.size == 0:
        return []
        
    norms = np.linalg.norm(embeddings_matrix, axis=1)
    norms = np.where(norms == 0, 1e-10, norms)
    
    similarities = np.dot(embeddings_matrix, q_vec) / (q_norm * norms)
    results = list(zip(chunk_ids.tolist(), similarities.tolist()))
    results.sort(key=lambda x: x[1], reverse=True)
    return results

def reciprocal_rank_fusion(semantic_results: list[tuple], keyword_results: list, k: int = 60) -> list[tuple]:
    """Combines semantic search results and keyword search results using Reciprocal Rank Fusion.
    
    Supports both chunk records (dict) and raw chunk IDs (int).
    """
    rrf_scores = {}
    
    def get_key_and_obj(item):
        if isinstance(item, tuple):
            val = item[0]
        else:
            val = item
            
        if isinstance(val, dict):
            return val["chunk_id"], val
        else:
            return val, val
            
    for rank, item in enumerate(semantic_results, 1):
        key, obj = get_key_and_obj(item)
        if key not in rrf_scores:
            rrf_scores[key] = {"obj": obj, "score": 0.0}
        rrf_scores[key]["score"] += 1.0 / (k + rank)
        
    for rank, item in enumerate(keyword_results, 1):
        key, obj = get_key_and_obj(item)
        if key not in rrf_scores:
            rrf_scores[key] = {"obj": obj, "score": 0.0}
        rrf_scores[key]["score"] += 1.0 / (k + rank)
        
    combined = [(item["obj"], item["score"]) for item in rrf_scores.values()]
    combined.sort(key=lambda x: x[1], reverse=True)
    return combined

def perform_hybrid_search(db_path: str, query_text: str, chunk_ids: np.ndarray, embeddings_matrix: np.ndarray, llm: LLMClient, usearch_index=None, limit: int = 30) -> list[tuple[dict, float]]:
    """Runs semantic and keyword search, combining them via Reciprocal Rank Fusion (RRF) and post-reranked via Cross-Encoder if available."""
    ranker = get_ranker()
    search_limit = max(50, limit * 3) if ranker is not None else limit

    # 1. Semantic search
    semantic_results = []
    if llm.provider != "none":
        try:
            q_vector = llm.get_embedding(query_text)
            
            # Tier 1: Try USearch index search
            if usearch_index is not None:
                try:
                    q_arr = np.array(q_vector, dtype=np.float32)
                    matches = usearch_index.search(q_arr, search_limit)
                    semantic_results = [(int(k), 1.0 - float(d)) for k, d in zip(matches.keys, matches.distances)]
                except Exception:
                    pass
            
            # Tier 2: Try SQLite-vec C-level vector search
            if not semantic_results:
                conn = get_connection(db_path)
                try:
                    semantic_results = search_vector_vec(conn, q_vector, limit=search_limit)
                finally:
                    conn.close()
                
            # Tier 3: Fallback to NumPy vectorized search
            if not semantic_results and embeddings_matrix.size > 0:
                semantic_results = calculate_similarities_vectorized(q_vector, chunk_ids, embeddings_matrix)
                if len(semantic_results) > search_limit:
                    semantic_results = semantic_results[:search_limit]
        except Exception as sem_err:
            console.print(f"[dim]Note: Semantic search failed, falling back to keyword-only. ({sem_err})[/dim]")
    
    # 2. Keyword search
    conn = get_connection(db_path)
    try:
        keyword_results = search_fts_ids(conn, query_text, limit=search_limit)
    except Exception as kw_err:
        console.print(f"[dim]Note: Keyword search failed. ({kw_err})[/dim]")
        keyword_results = []
    finally:
        conn.close()
        
    # 3. Combine via RRF
    combined_ids_scores = reciprocal_rank_fusion(semantic_results, keyword_results)
    
    # Determine candidates pool for fetching
    if ranker is not None:
        candidate_ids_scores = combined_ids_scores[:search_limit]
    else:
        candidate_ids_scores = combined_ids_scores[:limit]
        
    top_ids = [cid for cid, _ in candidate_ids_scores]
    scores_map = dict(candidate_ids_scores)
    
    # 4. Fetch actual chunk texts from DB only for the candidate IDs
    conn = get_connection(db_path)
    try:
        records = get_chunks_by_ids(conn, top_ids)
    finally:
        conn.close()
        
    # 5. Apply local Cross-Encoder reranking if ranker is available
    if ranker is not None and records:
        try:
            # Cap candidates sent to the reranker — cross-encoder cost grows
            # linearly with passage count, and the top RRF candidates are the
            # only ones that realistically survive reranking. The remainder is
            # appended afterwards in original (RRF) order as a fallback tail.
            RERANK_CANDIDATE_CAP = 30
            rerank_records = records[:RERANK_CANDIDATE_CAP]
            tail_records = records[RERANK_CANDIDATE_CAP:]

            passages = []
            for r in rerank_records:
                passages.append({
                    "id": r["chunk_id"],
                    "text": r["text"],
                    "meta": r
                })

            from flashrank import RerankRequest
            req = RerankRequest(query=query_text, passages=passages)
            reranked = ranker.rerank(req)

            results = []
            for item in reranked:
                results.append((item["meta"], float(item["score"])))
            # Append the un-reranked tail in original order, scored by RRF.
            for r in tail_records:
                results.append((r, scores_map[r["chunk_id"]]))
            return results[:limit]
        except Exception as rerank_err:
            console.print(f"[dim]Note: Reranking failed, falling back to RRF. ({rerank_err})[/dim]")
            
    # Fallback/default logic: use RRF scores
    results = []
    for r in records[:limit]:
        cid = r["chunk_id"]
        results.append((r, scores_map[cid]))
        
    # Ensure sorted by score descending
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def format_context(similar_chunks: list[tuple[dict, float]], top_n: int = 5) -> str:
    """Formats retrieved chunks into a standard RAG context block."""
    context_blocks = []
    score_label = "Rerank Score" if get_ranker() is not None else "RRF Rank Score"
    for idx, (record, score) in enumerate(similar_chunks[:top_n], 1):
        loc_str = f" [{record['location']}]" if record.get('location') else ""
        block = (
            f"Source [{idx}]: '{record['source_title']}' by {record['source_author']}{loc_str}\n"
            f"{score_label}: {score:.4f}\n"
            f"Content:\n{record['text']}\n"
        )
        context_blocks.append(block)
    return "\n---\n".join(context_blocks)

def retrieve_concept_context(conn: sqlite3.Connection, query_text: str) -> str:
    """Retrieves matching concepts and their relationship links to form a GraphRAG context."""
    import sqlite3
    words = [w.strip("?,.!- ") for w in query_text.lower().split() if len(w.strip("?,.!- ")) > 3]
    if not words:
        return ""
        
    cursor = conn.cursor()
    
    # Avoid crash on older DBs
    try:
        cursor.execute("SELECT id, name, definition, category FROM concepts LIMIT 1")
    except sqlite3.OperationalError:
        return ""
        
    matched_concepts = []
    for word in words:
        cursor.execute("""
            SELECT id, name, definition, category 
            FROM concepts 
            WHERE name LIKE ? OR definition LIKE ? OR category LIKE ?
        """, (f"%{word}%", f"%{word}%", f"%{word}%"))
        rows = cursor.fetchall()
        for r in rows:
            c_dict = {"id": r[0], "name": r[1], "definition": r[2], "category": r[3]}
            if c_dict not in matched_concepts:
                matched_concepts.append(c_dict)
                
    if not matched_concepts:
        return ""
        
    concept_ids = [c["id"] for c in matched_concepts]
    
    # Fetch links
    links_context = []
    placeholders = ",".join("?" for _ in concept_ids)
    cursor.execute(f"""
        SELECT 
            c1.name AS source_name, 
            c2.name AS target_name, 
            cl.relationship, 
            cl.description 
        FROM concept_links cl
        JOIN concepts c1 ON cl.source_concept_id = c1.id
        JOIN concepts c2 ON cl.target_concept_id = c2.id
        WHERE cl.source_concept_id IN ({placeholders}) OR cl.target_concept_id IN ({placeholders})
    """, concept_ids + concept_ids)
    
    rows = cursor.fetchall()
    for r in rows:
        links_context.append(f"Relationship: {r[0]} --({r[2]})--> {r[1]} ({r[3] or ''})")
        
    concept_blocks = []
    concept_blocks.append("### KNOWLEDGE CONCEPT GRAPH:")
    for c in matched_concepts[:5]:
        concept_blocks.append(f"- Concept: {c['name']} (Category: {c['category'] or 'General'}): {c['definition'] or ''}")
        
    if links_context:
        concept_blocks.append("\n### CONNECTIONS / RELATIONSHIPS:")
        for link in links_context[:5]:
            concept_blocks.append(f"- {link}")
            
    return "\n".join(concept_blocks)

def show_brain_splash():
    import time
    from rich.live import Live
    from rich.align import Align
    
    # 6 ASCII letters, each exactly 10 characters wide in every row, and ending with a space to prevent escaping issues
    letters = [
        # P
        [
            "    ____  ",
            "   / __ \\ ",
            "  / /_/ / ",
            " / ____/  ",
            "/_/       "
        ],
        # S
        [
            " _____    ",
            "/ ___/    ",
            "\\__ \\     ",
            "___/ /    ",
            "/____/    "
        ],
        # Y
        [
            "__  __    ",
            "\\ \\/ /    ",
            " \\  /     ",
            "  / /     ",
            " /_/      "
        ],
        # C
        [
            " ______   ",
            "/ ____/   ",
            "/ /       ",
            "/ /___    ",
            "\\____/    "
        ],
        # H
        [
            " __  __   ",
            "/ / / /   ",
            "/ /_/ /   ",
            "/ __  /   ",
            "/_/ /_/   "
        ],
        # E
        [
            " ______   ",
            "/ ____/   ",
            "/ __/     ",
            "/ /___    ",
            "/_____/   "
        ]
    ]
    
    widths = [10, 10, 10, 10, 10, 10]
    
    frames = []
    for i in range(6):
        frame_rows = []
        for L in range(5):
            line_parts = []
            for j in range(6):
                if j <= i:
                    line_parts.append(letters[j][L])
                else:
                    line_parts.append(" " * widths[j])
            
            line_str = "".join(line_parts)
            padded_line = line_str.ljust(60)
            frame_rows.append(f"[bold white]{padded_line}[/bold white]")
            
        if i == 5:
            subtitle_centered = "The AI-Powered Second Brain".center(60)
            frame_rows.append(f"\n[dim white]{subtitle_centered}[/dim white]")
        else:
            frame_rows.append("\n")
            frame_rows.append("")
            
        frames.append("\n".join(frame_rows))
        
    with Live(auto_refresh=False, transient=False) as live:
        for frame in frames:
            live.update(Align.center(frame))
            live.refresh()
            time.sleep(0.12)
        time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser(description="Query the local knowledge base or start a chat session.")
    parser.add_argument("query", nargs="?", help="The search query to answer. If omitted, and --chat is not set, prints database status.")
    parser.add_argument("--chat", action="store_true", help="Start an interactive chat session.")
    parser.add_argument("--top", type=int, default=5, help="Number of context chunks to retrieve.")
    parser.add_argument("--db-path", help="Database file path override. Default is read from .env (DATABASE_PATH).")
    
    args = parser.parse_args()
    
    db_path = resolve_db_path(args.db_path or os.getenv("DATABASE_PATH", "knowledge.db"))
    if not os.path.exists(db_path):
        err_console.print(f"[bold red]Error:[/bold red] Database file '{db_path}' not found. Please ingest some files first.")
        sys.exit(1)
        
    # Initialize LLM client
    try:
        llm = LLMClient()
    except Exception as e:
        err_console.print(f"[bold red]Error initializing LLM client:[/bold red] {e}")
        sys.exit(1)
        
    # Check and run database embedding migrations if LLM config changed
    check_and_migrate_embeddings(db_path, llm)
    from db import sync_memories_hook
    sync_memories_hook(db_path)
        
    # Verify that database has chunks (check if empty)
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM chunks")
        total_chunks = cursor.fetchone()[0]
    finally:
        conn.close()
        
    if total_chunks == 0:
        console.print("[bold yellow]Warning:[/bold yellow] Database is empty. Please run ingest.py to add documents first.")
        sys.exit(0)
        
    if not args.query and not args.chat:
        # Show database status
        console.print("\n[bold green]📊 Database Status[/bold green]")
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Property", style="dim", width=25)
        table.add_column("Value")
        
        table.add_row("Database Path", db_path)
        table.add_row("Total Chunks", str(total_chunks))
        
        conn = get_connection(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT title, author FROM sources")
            sources = cursor.fetchall()
            sources_info = {title: author for title, author in sources}
            
            # Count chunks per book
            book_counts = {}
            for title in sources_info:
                cursor.execute("""
                    SELECT COUNT(*) 
                    FROM chunks c 
                    JOIN sources s ON c.source_id = s.id 
                    WHERE s.title = ?
                """, (title,))
                book_counts[title] = cursor.fetchone()[0]
        finally:
            conn.close()
            
        table.add_row("Total Ingested Books", str(len(sources_info)))
        console.print(table)
        
        console.print("\n[bold cyan]📚 Ingested Books Catalog:[/bold cyan]")
        for title, author in sources_info.items():
            console.print(f" - [bold]{title}[/bold] by {author} [dim]({book_counts[title]} chunks)[/dim]")
        console.print("")
        sys.exit(0)
        
    # Try loading the usearch index FIRST; the full embeddings matrix is only a
    # numpy fallback used when the index is absent.
    records = []
    chunk_ids = np.array([], dtype=np.int32)
    embeddings_matrix = np.array([], dtype=np.float32)
    usearch_index = None
    if llm.provider != "none":
        index_path = index_path_for(db_path)
        try:
            from usearch.index import Index
            if os.path.exists(index_path):
                usearch_index = Index.restore(index_path)
        except Exception:
            usearch_index = None

        if usearch_index is None:
            # No index: fall back to loading all embeddings into a numpy matrix.
            conn = get_connection(db_path)
            try:
                records = get_all_embeddings_only(conn)
            finally:
                conn.close()
            chunk_ids = np.array([r["chunk_id"] for r in records if r["embedding"] is not None], dtype=np.int32)
            valid_embeddings = [r["embedding"] for r in records if r["embedding"] is not None]
            if valid_embeddings:
                embeddings_matrix = np.vstack(valid_embeddings)

    system_instruction = (
        "You are a helpful knowledge assistant. Synthesize a detailed, clear answer based on "
        "the retrieved context chunks below. You must ground your answers strictly in the "
        "provided context. In your answer, you MUST explicitly cite your sources and page numbers/chapters "
        "whenever stating facts from them (e.g. [Title, Page X] or [Title, Chapter Y]). "
        "The Source identifier and location information is provided at the start of each context block. "
        "If the answer cannot be found in the context, be honest and state "
        "that you do not have enough information in the ingested documents to answer."
    )
    
    if args.chat:
        show_brain_splash()
        console.print("\n[bold green]=== Chat Mode Activated ===[/bold green]")
        console.print("Ask any questions about your ingested books. Type [bold red]/help[/bold red] for options, or [bold red]/exit[/bold red] to end.\n")
        
        # Set up prompt session with command history file and completion hints
        history_dir = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(history_dir, exist_ok=True)
        history_file = os.path.join(history_dir, ".chat_history")
        
        commands_completer = WordCompleter([
            '/help', '/exit', '/quit', '/sources', '/status'
        ], ignore_case=True)
        
        session = PromptSession(
            history=FileHistory(history_file),
            completer=commands_completer,
            complete_while_typing=True
        )
        
        show_detailed_sources = False
        chat_history = []
        
        while True:
            try:
                # Use prompt_toolkit instead of simple input
                user_input = session.prompt("You > ")
            except (KeyboardInterrupt, EOFError):
                console.print("\n[bold yellow]Goodbye![/bold yellow]")
                break
                
            clean_input = user_input.strip()
            if not clean_input:
                continue
                
            # Process slash commands
            if clean_input.startswith('/'):
                cmd = clean_input.lower()
                if cmd in ['/exit', '/quit']:
                    console.print("[bold yellow]Goodbye![/bold yellow]")
                    break
                elif cmd == '/help':
                    console.print("\n[bold green]💡 Available Chat Commands:[/bold green]")
                    console.print("  [bold]/help[/bold]    - Show this help menu")
                    console.print("  [bold]/exit[/bold]    - Close the chat session")
                    console.print("  [bold]/quit[/bold]    - Close the chat session")
                    console.print("  [bold]/sources[/bold] - Toggle showing full matching book sections in the output")
                    console.print("  [bold]/status[/bold]  - Show active model configurations and database details\n")
                    continue
                elif cmd == '/sources':
                    show_detailed_sources = not show_detailed_sources
                    state_str = "ENABLED" if show_detailed_sources else "DISABLED"
                    console.print(f"[bold cyan]Detailed context sources display is now {state_str}.[/bold cyan]\n")
                    continue
                elif cmd == '/status':
                    console.print("\n[bold green]🛠️ System Status[/bold green]")
                    console.print(f"  [bold]Database:[/bold] {db_path}")
                    console.print(f"  [bold]Provider:[/bold] {llm.provider.upper()}")
                    console.print(f"  [bold]Embedding Model:[/bold] {llm.embed_model}")
                    console.print(f"  [bold]Chat Model:[/bold] {llm.chat_model}")
                    console.print(f"  [bold]Chunks Loaded:[/bold] {total_chunks}\n")
                    continue
                else:
                    console.print(f"[bold red]Unknown command:[/bold red] {clean_input}. Type [bold]/help[/bold] for instructions.\n")
                    continue
                
            # Perform Hybrid RAG Search
            try:
                with console.status("[bold cyan]Retrieving context (Hybrid Search)...") as status:
                    similarities = perform_hybrid_search(db_path, clean_input, chunk_ids, embeddings_matrix, llm, usearch_index=usearch_index, limit=args.top)
                    context_str = format_context(similarities, top_n=args.top)
                    
                    # Retrieve concept graph context
                    conn = get_connection(db_path)
                    try:
                        graph_context = retrieve_concept_context(conn, clean_input)
                    finally:
                        conn.close()
                
                if llm.provider == "none" or getattr(llm, "chat_model", "none") == "none":
                    console.print(f"\n[bold yellow]Offline / AI-Free (Pure Retrieval) Matches for:[/bold yellow] '{clean_input}'")
                    if not similarities:
                        console.print("  [dim]No matching text passages found.[/dim]")
                    score_label = "Rerank Score" if get_ranker() is not None else "RRF Score"
                    for idx, (r, score) in enumerate(similarities[:min(3, args.top)], 1):
                        loc_suffix = f" [{r['location']}]" if r.get('location') else ""
                        console.print(f"\n  [bold cyan][Passage {idx}][/bold cyan] '{r['source_title']}'{loc_suffix} [dim]({score_label}: {score:.4f})[/dim]")
                        console.print("  " + "-" * 20)
                        indented = "\n".join("      " + line for line in r['text'].strip().split("\n"))
                        console.print(indented)
                        console.print("  " + "-" * 20)
                        
                    if graph_context:
                        console.print("\n  [bold yellow]🧬 Graph Connections Found:[/bold yellow]")
                        indented_graph = "\n".join("      " + line for line in graph_context.strip().split("\n"))
                        console.print(indented_graph)
                    console.print("")
                    
                    chat_history.append(("User", clean_input))
                    chat_history.append(("Assistant", "[Pure Retrieval mode - showed sources]"))
                    continue
                        
                # If LLM is configured, continue to generate completion
                with console.status("[bold cyan]Thinking...") as status:
                    full_context = context_str
                    if graph_context:
                        full_context = f"{graph_context}\n\n---\n\n{context_str}"
                    
                    # Prepare conversation prompt
                    history_str = ""
                    for role, text in chat_history[-6:]:
                        history_str += f"{role}: {text}\n"
                        
                    prompt = (
                        f"### RETRIEVED CONTEXT FROM BOOKS:\n{full_context}\n\n"
                        f"### CONVERSATION HISTORY:\n{history_str}"
                        f"User: {clean_input}\n"
                        f"Assistant:"
                    )
                    response = llm.generate_completion(system_instruction, prompt)
                
                # Render LLM output
                console.print("\n[bold purple]Assistant[/bold purple] >")
                console.print(Markdown(response))
                console.print("")
                
                # Show graph concepts matched if any
                if graph_context:
                    conn = get_connection(db_path)
                    try:
                        cursor = conn.cursor()
                        words = [w.strip("?,.!- ") for w in clean_input.lower().split() if len(w.strip("?,.!- ")) > 3]
                        matched_names = []
                        if words:
                            for word in words:
                                cursor.execute("SELECT name FROM concepts WHERE name LIKE ?", (f"%{word}%",))
                                for row in cursor.fetchall():
                                    if row[0] not in matched_names:
                                        matched_names.append(row[0])
                        if matched_names:
                            console.print(f"[dim]🧬 GraphRAG Concepts Matched: {', '.join(matched_names)}[/dim]")
                    except Exception:
                        pass
                    finally:
                        conn.close()
                        
                # Show sources used (top unique items from the retrieved list)
                sources_list = []
                for r, score in similarities[:args.top]:
                    loc_suffix = f" [{r['location']}]" if r.get('location') else ""
                    sources_list.append(f"'{r['source_title']}'{loc_suffix} [dim](Score: {score:.3f})[/dim]")
                
                if sources_list:
                    console.print(f"[dim]📚 Sources cited (RRF): {', '.join(sources_list)}[/dim]")
                    
                # If show_detailed_sources toggle is enabled, print the full texts
                if show_detailed_sources:
                    console.print("\n[bold yellow]--- DETAILED CONTEXT USED ---[/bold yellow]")
                    for idx, (r, score) in enumerate(similarities[:args.top], 1):
                        loc_suffix = f" [{r['location']}]" if r.get('location') else ""
                        console.print(f"\n[bold magenta][{idx}] {r['source_title']} by {r['source_author']}{loc_suffix} (RRF: {score:.4f})[/bold magenta]")
                        console.print(f"{r['text'].strip()}")
                    console.print("[bold yellow]-----------------------------[/bold yellow]")
                    
                console.print("[dim]" + "-" * 50 + "[/dim]\n")
                
                chat_history.append(("User", clean_input))
                chat_history.append(("Assistant", response))
            except Exception as e:
                console.print(f"[bold red]Error generating answer:[/bold red] {e}\n")
                
    else:
        # Single query mode
        query_text = args.query
        console.print(f"\n[bold]Query:[/bold] [cyan]'{query_text}'[/cyan]")
        
        try:
            with console.status("[bold cyan]Searching database (Hybrid Search)...") as status:
                similarities = perform_hybrid_search(db_path, query_text, chunk_ids, embeddings_matrix, llm, usearch_index=usearch_index, limit=args.top)
                context_str = format_context(similarities, top_n=args.top)
                
                # Retrieve concept graph context
                conn = get_connection(db_path)
                try:
                    graph_context = retrieve_concept_context(conn, query_text)
                finally:
                    conn.close()
            
            if llm.provider == "none" or getattr(llm, "chat_model", "none") == "none":
                console.print("\n" + "=" * 50)
                console.print("[bold yellow]OFFLINE / AI-FREE (PURE RETRIEVAL) RESULT[/bold yellow]")
                console.print("=" * 50)
                console.print(f"[bold green]Top {min(args.top, len(similarities))} matching passages for query:[/bold green] '{query_text}'\n")
                if not similarities:
                    console.print("  [dim]No matching text passages found.[/dim]\n")
                score_label = "Rerank Score" if get_ranker() is not None else "RRF Score"
                for idx, (record, score) in enumerate(similarities[:args.top], 1):
                    loc_suffix = f" [{record['location']}]" if record.get('location') else ""
                    console.print(f"[bold cyan][Passage {idx}][/bold cyan] [bold]{record['source_title']}[/bold] by {record['source_author']}{loc_suffix} [dim]({score_label}: {score:.4f})[/dim]")
                    console.print("-" * 40)
                    console.print(Markdown(record['text']))
                    console.print("-" * 40 + "\n")
                
                if graph_context:
                    console.print("[bold yellow]🧬 Related Concept Graph Connections:[/bold yellow]")
                    console.print(Markdown(graph_context))
                    console.print("")
                console.print("=" * 50 + "\n")
                sys.exit(0)
                
            with console.status("[bold cyan]Synthesizing response...") as status:
                full_context = context_str
                if graph_context:
                    full_context = f"{graph_context}\n\n---\n\n{context_str}"
                
                prompt = (
                    f"### RETRIEVED CONTEXT FROM BOOKS:\n{full_context}\n\n"
                    f"User Query: {query_text}"
                )
                response = llm.generate_completion(system_instruction, prompt)
                
            console.print("\n" + "=" * 50)
            console.print("[bold green]ANSWER:[/bold green]")
            console.print("=" * 50)
            console.print(Markdown(response))
            console.print("=" * 50)
            
            # Show graph concepts matched if any
            if graph_context:
                conn = get_connection(db_path)
                try:
                    cursor = conn.cursor()
                    words = [w.strip("?,.!- ") for w in query_text.lower().split() if len(w.strip("?,.!- ")) > 3]
                    matched_names = []
                    if words:
                        for word in words:
                            cursor.execute("SELECT name FROM concepts WHERE name LIKE ?", (f"%{word}%",))
                            for row in cursor.fetchall():
                                if row[0] not in matched_names:
                                    matched_names.append(row[0])
                    if matched_names:
                        console.print(f"[dim]🧬 GraphRAG Concepts Cited: {', '.join(matched_names)}[/dim]")
                except Exception:
                    pass
                finally:
                    conn.close()
                    
            console.print("\n[bold]📚 Context Sources Cited (RRF):[/bold]")
            for r, score in similarities[:args.top]:
                loc_suffix = f" [{r['location']}]" if r.get('location') else ""
                console.print(f" - [bold]{r['source_title']}[/bold] by {r['source_author']}{loc_suffix} [dim](RRF: {score:.4f})[/dim]")
            console.print("=" * 50 + "\n")
        except Exception as e:
            console.print(f"[bold red]Error generating answer:[/bold red] {e}")

if __name__ == "__main__":
    main()
