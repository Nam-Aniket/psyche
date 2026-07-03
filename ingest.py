#!/usr/bin/env python3
import os
import sys
import argparse
import hashlib
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress
from rich.status import Status
from rich.table import Table

# Ensure current directory is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import init_db, get_connection, check_checksum, add_source, add_chunk, add_embedding, resolve_db_path, check_and_migrate_embeddings, build_or_update_usearch_index, remove_source
from parsers import extract_text
from llm_client import LLMClient

# Initialize rich console
console = Console()
err_console = Console(stderr=True)

# Load environment variables
load_dotenv()

def calculate_sha256(file_path: str) -> str:
    """Calculates the SHA-256 checksum of a file to prevent duplicate ingestion."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def clean_title_from_filename(filename: str) -> str:
    """Derives a clean title from a filename ("1667121038Craft.pdf" → "Craft",
    hash names → "Untitled (2e5acdc5…)")."""
    import re
    name, _ = os.path.splitext(os.path.basename(filename))
    # Pure hex/hash names carry no information.
    if len(name) >= 16 and re.fullmatch(r"[0-9a-fA-F]+", name):
        return f"Untitled ({name[:8].lower()}…)"
    cleaned = name.replace("_", " ").replace("-", " ")
    cleaned = re.sub(r"^\d{6,}\s*", "", cleaned)            # leading timestamp/id runs
    cleaned = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", cleaned)  # split camelCase
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.title() if cleaned else name


def resolve_source_meta(path: str, title_override: str = None, author_override: str = None) -> tuple[str, str | None]:
    """(title, author) for a source: explicit override > embedded document
    metadata > cleaned filename; author None (not 'Unknown') when absent."""
    from parsers import extract_metadata
    meta = extract_metadata(path)
    title = title_override or meta.get("title") or clean_title_from_filename(path)
    author = author_override or meta.get("author") or None
    return title, author

def chunk_text(text: str, chunk_size: int = 1500, overlap: int = 300) -> list[str]:
    """Splits text into chunks of character length chunk_size with overlap."""
    chunks = []
    if not text:
        return chunks
    
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break
            
        split_idx = text.rfind("\n", end - 150, end)
        if split_idx == -1 or split_idx <= start:
            split_idx = text.rfind(" ", end - 100, end)
            
        if split_idx != -1 and split_idx > start:
            end = split_idx
            
        chunks.append(text[start:end].strip())
        start = end - overlap
        
    return [c for c in chunks if len(c.strip()) > 50]

def main():
    parser = argparse.ArgumentParser(description="Ingest books or documents into the local RAG database.")
    parser.add_argument("paths", nargs="*", help="One or more paths to book files or directories to ingest.")
    parser.add_argument("--path", help="Legacy argument for path to a file or directory.")
    parser.add_argument("--title", help="Manual title override. Only applied when a single file is ingested.")
    parser.add_argument("--author", help="Author of the document. Default for directory scanning.")
    parser.add_argument("--db-path", help="Database file path override. Default is read from .env (DATABASE_PATH).")
    parser.add_argument("--chunk-size", type=int, default=1500, help="Target chunk size in characters.")
    parser.add_argument("--overlap", type=int, default=300, help="Overlap between chunks in characters.")
    parser.add_argument("--ext", help="Comma-separated file extensions to filter by during directory scanning (e.g. 'epub,pdf').")
    parser.add_argument("--force", "-f", action="store_true", help="Force re-ingestion of already ingested files.")

    
    args = parser.parse_args()
    
    # Resolve paths
    raw_paths = list(args.paths)
    if args.path:
        raw_paths.append(args.path)
        
    if not raw_paths:
        parser.error("At least one path must be specified. Usage: knowledge ingest <path1> <path2>...")
        
    db_path = resolve_db_path(args.db_path or os.getenv("DATABASE_PATH", "knowledge.db"))
    init_db(db_path)
    
    # Check extensions
    allowed_exts = None
    if args.ext:
        allowed_exts = {f".{ext.strip().lower().lstrip('.')}" for ext in args.ext.split(',')}
    else:
        allowed_exts = {".pdf", ".epub", ".txt", ".md", ".markdown", ".docx", ".html", ".htm", ".org"}
        
    files_to_ingest = []
    for rp in raw_paths:
        resolved_path = os.path.abspath(rp)
        if not os.path.exists(resolved_path):
            err_console.print(f"[bold red]Warning:[/bold red] Path not found at '{rp}'. Skipping.")
            continue
            
        if os.path.isdir(resolved_path):
            for root, dirs, files in os.walk(resolved_path):
                # Prune hidden and system directories in-place (starting with '.')
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                for file in files:
                    if file.startswith('.') or file.endswith(('.canvas', '.excalidraw')):
                        continue
                    ext = os.path.splitext(file)[1].lower()
                    if ext in allowed_exts:
                        files_to_ingest.append(os.path.join(root, file))
        else:
            files_to_ingest.append(resolved_path)
            
    files_to_ingest.sort()
    
    if not files_to_ingest:
        err_console.print(f"[bold yellow]Warning:[/bold yellow] No supported files found in provided paths.")
        sys.exit(0)
        
    success_count = 0
    skipped_count = 0
    failed_count = 0
    
    try:
        console.print("[yellow]Initializing LLM Client...[/yellow]")
        llm_client = LLMClient()
    except Exception as e:
        err_console.print(f"[bold red]Error initializing LLM client:[/bold red] {e}")
        sys.exit(1)

    # Check and run database embedding migrations if LLM config changed
    check_and_migrate_embeddings(db_path, llm_client)
    
    conn = get_connection(db_path)
        
    for path in files_to_ingest:
        try:
            checksum = calculate_sha256(path)
            existing_id = check_checksum(conn, checksum)
            if existing_id is not None:
                if args.force:
                    console.print(f"[yellow]🔄 Re-ingesting (forced): {os.path.basename(path)}[/yellow]")
                    remove_source(conn, existing_id, db_path=db_path)
                else:
                    console.print(f"[dim]⏭️  Skipping (already ingested): {os.path.basename(path)}[/dim]")
                    skipped_count += 1
                    continue

                
            title_override = args.title if (len(files_to_ingest) == 1 and args.title) else None
            title, author = resolve_source_meta(path, title_override, args.author)

            console.print(f"\n[bold green]Ingesting:[/bold green] [italic]'{title}'[/italic] by [bold]{author or 'Unknown'}[/bold]")
            console.print(f"[dim]File: {os.path.relpath(path)}[/dim]")
            
            # Extract Text
            with console.status("[bold cyan]Extracting text and locations...") as status:
                extracted_blocks = extract_text(path)
                
            if not extracted_blocks:
                err_console.print(f"[bold red]Failed:[/bold red] No text could be extracted from '{path}'.")
                failed_count += 1
                continue
                
            # Chunk Text
            chunks = []
            for block in extracted_blocks:
                block_chunks = chunk_text(block["text"], chunk_size=args.chunk_size, overlap=args.overlap)
                for c in block_chunks:
                    chunks.append({
                        "text": c,
                        "location": block["location"]
                    })
                    
            if not chunks:
                err_console.print(f"[bold red]Failed:[/bold red] No text chunks created. Content is too short.")
                failed_count += 1
                continue
                
            console.print(f"📄 Document split into [bold cyan]{len(chunks)}[/bold cyan] text chunks.")
            
            # Generate embeddings
            embeddings = []
            if llm_client.provider != "none":
                batch_size = 50
                with Progress(console=console) as progress:
                    task = progress.add_task("[cyan]Requesting API embeddings...", total=len(chunks))
                    for i in range(0, len(chunks), batch_size):
                        sub_batch = chunks[i:i+batch_size]
                        sub_texts = [c["text"] for c in sub_batch]
                        try:
                            sub_embeddings = llm_client.get_embeddings_batch(sub_texts)
                            embeddings.extend(sub_embeddings)
                            progress.update(task, advance=len(sub_batch))
                        except Exception as api_err:
                            err_console.print(f"\n[bold red]API Error during batch starting at chunk {i}:[/bold red] {api_err}")
                            raise api_err
                            
                if len(embeddings) != len(chunks):
                    err_console.print(f"[bold red]Error:[/bold red] Generated {len(embeddings)} embeddings for {len(chunks)} chunks.")
                    failed_count += 1
                    continue
            else:
                console.print("[yellow]AI-Free mode: Skipping embedding generation.[/yellow]")
                
            # Write to database
            source_id = add_source(conn, title, author, path, checksum)
            with Progress(console=console) as progress:
                task = progress.add_task("[green]Storing chunks in SQLite...", total=len(chunks))
                for idx, chunk_data in enumerate(chunks):
                    chunk_id = add_chunk(conn, source_id, idx, chunk_data["text"], location=chunk_data["location"])
                    if llm_client.provider != "none":
                        add_embedding(conn, chunk_id, embeddings[idx])
                    progress.update(task, advance=1)
                    
            console.print(f"✨ [bold green]Successfully ingested:[/bold green] {title}")
            success_count += 1
            
        except Exception as file_err:
            err_console.print(f"[bold red]Failed to ingest {os.path.basename(path)}:[/bold red] {file_err}")
            failed_count += 1
            conn.rollback()
            
    conn.close()
    build_or_update_usearch_index(db_path)
    
    # Sync Summary Table
    console.print("\n[bold green]🔄 Ingestion Sync Summary[/bold green]")
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Category", style="bold")
    table.add_column("Count", justify="right")
    
    table.add_row("Ingested (New)", f"[bold green]{success_count}[/bold green]")
    table.add_row("Skipped (Existing)", f"[bold yellow]{skipped_count}[/bold yellow]")
    table.add_row("Failed", f"[bold red]{failed_count}[/bold red]")
    
    console.print(table)
    console.print("")

if __name__ == "__main__":
    main()
