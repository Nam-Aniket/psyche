#!/usr/bin/env python3
import os
import sys
import argparse
import json
import numpy as np
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

# Ensure current directory is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import get_connection, get_all_embeddings_with_chunks, add_concept, add_concept_link, resolve_db_path, check_and_migrate_embeddings
from llm_client import LLMClient

console = Console()
err_console = Console(stderr=True)

def kmeans(embeddings: np.ndarray, num_clusters: int, max_iter: int = 20):
    """Performs K-Means clustering on normalized embeddings using cosine similarity."""
    num_samples, dim = embeddings.shape
    if num_samples <= num_clusters:
        return np.arange(num_samples), embeddings
        
    # Standardize/normalize vectors to unit length
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    norm_embeddings = embeddings / norms
    
    # Initialize centroids randomly
    np.random.seed(42)  # For deterministic runs
    indices = np.random.choice(num_samples, num_clusters, replace=False)
    centroids = norm_embeddings[indices].copy()
    
    labels = np.zeros(num_samples, dtype=int)
    for iteration in range(max_iter):
        # Cosine similarity matrix (num_samples, num_clusters)
        similarities = np.dot(norm_embeddings, centroids.T)
        new_labels = np.argmax(similarities, axis=1)
        
        # Check convergence
        if np.array_equal(labels, new_labels):
            break
        labels = new_labels
        
        # Update centroids
        new_centroids = np.zeros_like(centroids)
        for c in range(num_clusters):
            cluster_points = norm_embeddings[labels == c]
            if len(cluster_points) > 0:
                mean_vec = cluster_points.mean(axis=0)
                norm = np.linalg.norm(mean_vec)
                if norm > 0:
                    new_centroids[c] = mean_vec / norm
                else:
                    new_centroids[c] = mean_vec
            else:
                new_centroids[c] = norm_embeddings[np.random.choice(num_samples)]
        centroids = new_centroids
        
    return labels, centroids

def clean_json_text(text: str) -> str:
    """Strips Markdown JSON wrappers from LLM output."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned

# Closed-class / function words + sentence openers + high-frequency adverbs.
# Phrases may not start or end with any of these, and 1-grams in this set are dropped.
FUNCTION_WORDS = frozenset("""
a an the of to in on for with as at by from into about over under above below between among through during before after
is are was were be been being am has have had do does did will would shall should can could may might must ought
this that these those it its it's he she they them him her us we you i me my mine your yours our ours his hers their theirs
who whom whose which what how when where why whether than then there here
not no nor so but and or yet because however therefore thus hence moreover furthermore nevertheless otherwise indeed
perhaps maybe also still just very really quite rather somewhat too although while since though if unless until
all any some each every both few more most other such only own same one two three many much several another
let out up down off away back again ever never always often sometimes now once even instead within without upon
thee thou thy thine ye unto hath doth shalt thyself thus whilst herein therein wherein
""".split())

# Common "light" verbs (and inflections) that read as concepts but carry no topic.
# Phrases may not start OR end with these, and 1-grams in this set are dropped.
WEAK_VERBS = frozenset("""
like likes liked liking get gets getting got gotten make makes making made say says saying said see sees seeing saw seen
recall recalls recalled recalling regard regards regarded relate relates related
go goes going went gone come comes coming came take takes taking took taken give gives giving gave given
know knows knowing knew known think thinks thinking thought want wants wanting wanted use uses using used
find finds finding found tell tells telling told ask asks asking asked look looks looking looked feel feels feeling felt
become becomes becoming became leave leaves leaving left put puts mean means meaning meant keep keeps keeping kept
begin begins beginning began begun seem seems seeming seemed help helps helping helped show shows showing showed shown
hear hears hearing heard turn turns turning turned start starts starting started call calls calling called try tries trying tried
work works working worked live lives living lived believe believes believed bring brings bringing brought
happen happens happened write writes writing wrote written provide provides provided sit sits sitting sat
stand stands standing stood lose loses losing lost pay pays paying paid meet meets meeting met include includes including included
continue continues continued set sets setting learn learns learning learned learnt change changes changing changed
lead leads leading led understand understands understanding understood watch watches following follow follows followed
stop stops stopping stopped create creates creating created speak speaks speaking spoke spoken read reads reading
spend spends spending spent grow grows growing grew grown open opens opening opened walk walks walking walked
win wins winning won offer offers offered remember remembers consider considers appear appears appeared buy buys bought
wait waits serve serves serving served die dies dying died send sends sent expect expects expected stay stays
fall falls falling fell cut cuts reach reaches remain remains remaining suggest suggests raise raises pass passes
sell sells selling sold require requires required decide decides pull pulls return returns explain explains hope hopes
develop develops carry carries break breaks broke broken receive receives agree agrees support supports produce produces
cover covers draw draws choose chooses chose chosen
""".split())

# Vague nouns / adjectives / adverbs that are too generic to be a concept.
# Dropped as 1-grams, but allowed inside/at the edge of multiword phrases.
WEAK_NOUNS = frozenset("""
thing things way ways time times day days year years man men woman women people person world life lot kind sort
fact facts case cases point points part parts number numbers place places hand hands eye eyes end side group problem
idea bit today tomorrow yesterday someone something anything everything nothing nobody anyone everyone
good great new old big small large little long high low right wrong sure able real full last next same different
important certain whole possible available free easy hard true false main early late general common simple clear single
recent kind type word words name names line lines order example reason result fellow
first second third fourth fifth sixth seventh eighth ninth tenth former latter
""".split())

# Domain-meta noise that should never be a concept.
META_STOP = frozenset("""
chapter section page pages volume book books author intro introduction preface appendix figure table note notes
footnote copyright edition isbn part contents foreword paragraph quote excerpt purport purports verse text
com www http https etc etc. eg ie pdf epub html
reserved permission ibid publisher publishing trademark distributed printed
classi cation guration cient fide eva ation inform
""".split())

_SENT_SPLIT = __import__("re").compile(r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?|!)\s')
_TOKEN_RE = __import__("re").compile(r"[A-Za-z][A-Za-z'\-]+")


def _iter_sentences(text):
    for s in _SENT_SPLIT.split(text):
        s = s.strip()
        if s:
            yield s


def _phrase_windows(tokens_orig):
    """Yield (phrase, first_token_orig, start_index) for valid 1/2/3-grams."""
    toks = [t.lower() for t in tokens_orig]
    n = len(toks)
    for L in (1, 2, 3):
        for i in range(0, n - L + 1):
            w = toks[i:i + L]
            if any(len(t) < 3 for t in w):
                continue
            if any(t in META_STOP for t in w):
                continue
            if L == 1:
                # 1-grams must be a content word: no function/verb/vague words
                if w[0] in FUNCTION_WORDS or w[0] in WEAK_VERBS or w[0] in WEAK_NOUNS:
                    continue
            else:
                # multiword: never bookend with a function word or a light verb
                if w[0] in FUNCTION_WORDS or w[-1] in FUNCTION_WORDS:
                    continue
                if w[0] in WEAK_VERBS or w[-1] in WEAK_VERBS:
                    continue
                if w[1] in META_STOP if L == 3 else False:
                    continue
            yield " ".join(w), tokens_orig[i], i


def build_topic_graph(db_path: str, num_clusters=None, concepts_per_cluster: int = 5,
                      max_concepts: int = 36, seed: int = 42,
                      df_floor: int = None, cap_ratio: float = 0.6):
    """Offline (no-LLM) concept-graph builder: BERTopic-lite.

    KMeans-clusters chunk embeddings into topics (categories), extracts the most
    characteristic noun-phrases per topic via c-TF-IDF (the concepts), writes an
    extractive definition per concept, and links them with bounded, typed edges.
    Pure numpy; no chat model required.
    """
    import re
    from collections import Counter, defaultdict

    conn = get_connection(db_path)
    try:
        records = get_all_embeddings_with_chunks(conn)
    finally:
        conn.close()

    records = [r for r in records if r.get("text")]
    if not records:
        console.print("[bold red]Error:[/bold red] Database is empty. Ingest some documents first.")
        return

    has_embeddings = records[0].get("embedding") is not None
    rng = np.random.default_rng(seed)

    # ── 1. sample + cluster ──────────────────────────────────────────────────
    n = len(records)
    if n > 8000:
        idx = np.sort(rng.choice(n, 8000, replace=False))
        records = [records[i] for i in idx]
        n = 8000
    console.print(f"\n[bold green]Building concept graph from {n} chunks (offline topic model)...[/bold green]")

    centroids = None
    if has_embeddings and n >= 30:
        K = num_clusters if num_clusters else int(round((n / 2) ** 0.5))
        K = max(4, min(12, K))
        emb = np.array([r["embedding"] for r in records], dtype=np.float32)
        with console.status("[bold cyan]Clustering text into themes..."):
            labels, centroids = kmeans(emb, K)
        labels = np.asarray(labels)
    else:
        K = 1
        labels = np.zeros(n, dtype=int)

    # active clusters (drop tiny ones), remap to 0..C-1
    counts = Counter(int(l) for l in labels)
    active = [c for c in sorted(counts) if counts[c] >= (3 if K > 1 else 1)]
    if not active:
        active = sorted(counts)
    remap = {c: i for i, c in enumerate(active)}
    C = len(active)
    if centroids is not None:
        centroids = centroids[active]

    cluster_records = defaultdict(list)
    for r, l in zip(records, labels):
        l = int(l)
        if l in remap:
            cluster_records[remap[l]].append(r)

    # ── 2. count phrase candidates ───────────────────────────────────────────
    tf = [Counter() for _ in range(C)]           # per-cluster term freq
    df = Counter()                               # per-chunk document freq
    cap_count, total_count = Counter(), Counter()
    for c in range(C):
        for r in cluster_records[c]:
            seen = set()
            for sent in _iter_sentences(r["text"]):
                toks = _TOKEN_RE.findall(sent)
                for phrase, first_orig, start_i in _phrase_windows(toks):
                    tf[c][phrase] += 1
                    total_count[phrase] += 1
                    if start_i > 0 and first_orig[0].isupper():
                        cap_count[phrase] += 1
                    seen.add(phrase)
            for p in seen:
                df[p] += 1

    # ── 2e. prune vocabulary ─────────────────────────────────────────────────
    floor = df_floor if df_floor else max(3, int(0.0005 * n))

    def prune(floor_):
        vocab = []
        # "Too common across the corpus" only signals a stopword when there are
        # multiple topics to contrast. With a single cluster the common terms ARE
        # the topic, so don't cap document frequency there.
        upper = (n + 1) if C <= 1 else max(floor_, int(0.6 * n))
        for phrase, d in df.items():
            if d < floor_ or d > upper:
                continue
            tot = total_count[phrase]
            if tot and cap_count[phrase] / tot >= cap_ratio:
                continue  # proper noun
            if " " in phrase and tot < 2:
                continue
            vocab.append(phrase)
        return vocab

    vocab = prune(floor)
    if len(vocab) < max(C, 8):
        vocab = prune(2)
    if not vocab:
        console.print("[bold yellow]Warning:[/bold yellow] no meaningful concepts could be extracted.")
        return
    vocab.sort(key=lambda p: (-df[p], p))
    vidx = {p: i for i, p in enumerate(vocab)}

    # ── 3. c-TF-IDF ──────────────────────────────────────────────────────────
    X = np.zeros((C, len(vocab)), dtype=np.float64)
    for c in range(C):
        for p, cnt in tf[c].items():
            j = vidx.get(p)
            if j is not None:
                X[c, j] = cnt
    A = X.sum(axis=1, keepdims=True)
    A[A == 0] = 1.0
    tfn = X / A
    f = (X > 0).sum(axis=0)
    idf = np.log(1.0 + (C / np.maximum(f, 1)))
    ctfidf = tfn * idf

    # ── 4. select concepts (per cluster, dedup, global cap) ───────────────────
    def is_unigram(p):
        return " " not in p

    # Multi-word keyphrases are far more specific/meaningful than single words
    # ("value proposition" >> "value", "devotional service" >> "service"), so
    # weight bigrams highest, trigrams next, unigrams lowest when ranking.
    def length_weight(p):
        spaces = p.count(" ")
        return 2.4 if spaces == 1 else 1.8 if spaces == 2 else 1.0

    def wscore(c, j):
        return ctfidf[c, j] * length_weight(vocab[j])

    candidates = []  # (weighted_score, phrase, cluster)
    for c in range(C):
        order = sorted(range(len(vocab)), key=lambda j: (-wscore(c, j), vocab[j]))
        picked = []
        for j in order:
            if ctfidf[c, j] <= 0:
                break
            p = vocab[j]
            if any(p in q or q in p for q in picked):
                continue  # substring dup within cluster (keeps the higher-ranked form)
            picked.append(p)
            candidates.append((wscore(c, j), p, c))
            if len(picked) >= concepts_per_cluster * 2:
                break

    # cross-cluster dedup: keep each phrase in its best-scoring cluster
    best = {}
    for score, p, c in candidates:
        if p not in best or score > best[p][0]:
            best[p] = (score, c)
    surviving = sorted(((s, p, c) for p, (s, c) in best.items()), key=lambda x: (-x[0], x[1]))
    surviving = surviving[:max_concepts]

    cluster_concepts = defaultdict(list)  # c -> [(phrase, score)] sorted desc
    for s, p, c in surviving:
        cluster_concepts[c].append((p, s))
    for c in cluster_concepts:
        cluster_concepts[c] = cluster_concepts[c][:concepts_per_cluster]

    # ── 5. category labels = each cluster's top (multi-word) concept ──────────
    used_labels = set()
    labels_for = {}
    for c in range(C):
        ranked = [p for p, _ in cluster_concepts.get(c, [])]
        if not ranked:
            ranked = [vocab[j] for j in sorted(range(len(vocab)), key=lambda j: -wscore(c, j))[:2]]
        # prefer a multi-word phrase for the label (far more specific than a unigram)
        multiword = [p for p in ranked if " " in p]
        ordered = multiword + [p for p in ranked if " " not in p]
        label = (ordered[0] if ordered else f"Theme {c + 1}").title()
        if label in used_labels and len(ordered) > 1:
            label = ordered[1].title()
        base = label
        k = 2
        while label in used_labels:
            label = f"{base} ({k})"
            k += 1
        used_labels.add(label)
        labels_for[c] = label

    # ── 6. extractive definitions ────────────────────────────────────────────
    def definition_for(phrase, c):
        top = [p for p, _ in cluster_concepts[c][:10]]
        pat = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
        best_sent, best_score = None, -1.0
        scanned = 0
        for r in cluster_records[c]:
            for sent in _iter_sentences(r["text"]):
                if scanned > 200:
                    break
                if not pat.search(sent):
                    continue
                scanned += 1
                low = sent.lower()
                score = sum(1 for t in top if t in low)
                m = pat.search(sent)
                if m and m.start() < 60:
                    score += 0.5
                L = len(sent)
                if L < 40 or L > 220:
                    score -= 1.0
                if score > best_score:
                    best_score, best_sent = score, sent.strip()
            if scanned > 200:
                break
        if best_sent:
            return best_sent if len(best_sent) <= 300 else best_sent[:297] + "..."
        return f"A recurring theme in '{labels_for[c]}'."

    # ── write concepts ───────────────────────────────────────────────────────
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM concept_links")
    cur.execute("DELETE FROM concepts")
    conn.commit()

    name_to_cluster = {}
    anchor_of = {}  # c -> top concept name
    n_concepts = 0
    for c in range(C):
        for rank, (phrase, score) in enumerate(cluster_concepts[c]):
            name = phrase.title() if phrase.islower() else phrase
            add_concept(conn, name, definition_for(phrase, c), labels_for[c])
            name_to_cluster[name] = c
            if rank == 0:
                anchor_of[c] = name
            n_concepts += 1
    conn.commit()

    # ── 7. edges ─────────────────────────────────────────────────────────────
    edges = []  # (src, tgt, rel, desc, strength)
    # 7a membership: each concept -> cluster anchor
    for c in range(C):
        anchor = anchor_of.get(c)
        if not anchor:
            continue
        cmax = cluster_concepts[c][0][1] or 1.0
        for phrase, score in cluster_concepts[c]:
            name = phrase.title() if phrase.islower() else phrase
            if name == anchor:
                continue
            strength = round(min(1.0, score / cmax), 2)
            edges.append((name, anchor, "part of theme", f"Both central to the '{labels_for[c]}' theme. [strength={strength}]", strength))

    # 7b co-occurrence (clean concepts, capped)
    concept_names = list(name_to_cluster)
    pats = {nm: re.compile(r"\b" + re.escape(nm) + r"\b", re.IGNORECASE) for nm in concept_names}
    co = Counter()
    for c in range(C):
        for r in cluster_records[c]:
            present = [nm for nm in concept_names if pats[nm].search(r["text"])]
            present.sort()
            for i in range(len(present)):
                for j in range(i + 1, len(present)):
                    co[(present[i], present[j])] += 1
    n_co = int(min(1.5 * max(1, n_concepts), 40))
    for (src, tgt), cnt in sorted(co.items(), key=lambda x: (-x[1], x[0]))[:n_co]:
        if cnt < 3:
            continue
        edges.append((src, tgt, "co-occurs with", f"Appear together in {cnt} passages. [strength={cnt}]", cnt))

    # 7c cross-theme via centroid cosine
    if centroids is not None and C > 1:
        S = centroids @ centroids.T
        for i in range(C):
            for j in range(i + 1, C):
                sim = float(S[i, j])
                if sim >= 0.64 and anchor_of.get(i) and anchor_of.get(j):
                    edges.append((anchor_of[i], anchor_of[j], "related theme",
                                  f"Themes '{labels_for[i]}' and '{labels_for[j]}' are semantically adjacent. [strength={sim:.2f}]", sim))

    # density budget: keep edges under 3x nodes, dropping weakest co-occurrence first
    budget = 3 * max(1, n_concepts)
    if len(edges) > budget:
        co_edges = [e for e in edges if e[2] == "co-occurs with"]
        keep = [e for e in edges if e[2] != "co-occurs with"]
        co_edges.sort(key=lambda e: (-e[4], e[0], e[1]))
        edges = keep + co_edges[: max(0, budget - len(keep))]

    n_links = 0
    for src, tgt, rel, desc, _ in edges:
        add_concept_link(conn, src, tgt, rel, desc)
        n_links += 1
    conn.commit()
    conn.close()

    rels = sorted({e[2] for e in edges})
    sample = ", ".join(list(name_to_cluster)[:8])
    console.print(f" ✅ Extracted [bold green]{n_concepts}[/bold green] concepts in [bold]{C}[/bold] themes: {sample}")
    console.print(f" ✅ Created [bold blue]{n_links}[/bold blue] links ({', '.join(rels)})")
    console.print(f" ✅ Categories: {', '.join(labels_for[c] for c in range(C))}")
    console.print(f"\n✨ [bold green]Concept Graph Construction Completed![/bold green]\n")


def build_cooccurrence_graph(db_path: str, min_cooccur: int = 2, max_concepts: int = 30):
    """Offline graph builder — delegates to the topic-model pipeline."""
    build_topic_graph(db_path)

def build_concept_graph(db_path: str, num_clusters: int):
    # 1. LLM Setup first to check provider mode
    try:
        llm = LLMClient()
    except Exception as e:
        console.print(f"[bold red]Error initializing LLM client:[/bold red] {e}")
        sys.exit(1)
        
    # Check and run database embedding migrations if LLM config changed
    check_and_migrate_embeddings(db_path, llm)
        
    if llm.provider in ("none", "local") or llm.chat_model == "none":
        build_cooccurrence_graph(db_path)
        return

    # 2. Fetch chunks and embeddings
    conn = get_connection(db_path)
    try:
        records = get_all_embeddings_with_chunks(conn)
    finally:
        conn.close()
        
    if not records:
        console.print("[bold red]Error:[/bold red] Database is empty. Ingest some documents first.")
        sys.exit(1)
        
    console.print(f"\n[bold green]Creating GraphRAG Concept Graph from {len(records)} chunks...[/bold green]")
    
    # 3. Extract embeddings matrix
    embeddings_list = [r["embedding"] for r in records]
    embeddings = np.array(embeddings_list, dtype=np.float32)
    
    # Run K-Means clustering
    with console.status("[bold cyan]Semantically clustering text chunks...") as status:
        labels, centroids = kmeans(embeddings, num_clusters)
        
    # Group records by cluster label
    clusters = {c: [] for c in range(num_clusters)}
    for idx, label in enumerate(labels):
        clusters[label].append(records[idx])
        
    # 4. Cluster-by-cluster extraction with sampling constraints
    conn = get_connection(db_path)
    total_concepts = 0
    total_links = 0

    try:
        # Rebuild from scratch: clear any prior graph so re-runs don't accumulate
        # stale orphan concepts/links (mirrors the offline build_topic_graph path).
        conn.execute("DELETE FROM concept_links")
        conn.execute("DELETE FROM concepts")
        conn.commit()
        for c in range(num_clusters):
            cluster_records = clusters[c]
            if not cluster_records:
                continue
                
            # Sample max 3 chunks per source title
            source_groups = {}
            for r in cluster_records:
                title = r["source_title"]
                if title not in source_groups:
                    source_groups[title] = []
                if len(source_groups[title]) < 3:
                    source_groups[title].append(r)
                    
            sampled_records = []
            for group in source_groups.values():
                sampled_records.extend(group)
                
            console.print(f"\n[bold cyan]Processing Theme {c+1}/{num_clusters} ({len(sampled_records)} sampled chunks from {len(source_groups)} sources)...[/bold cyan]")
            
            # Prepare summarized context for the LLM
            context_text = ""
            for idx, r in enumerate(sampled_records, 1):
                context_text += f"Document: {r['source_title']} ({r['location'] or 'Unknown'})\nContent:\n{r['text']}\n---\n"
                
            # LLM Prompt to extract concepts and connection links
            system_instruction = (
                "You are an expert knowledge graph extractor. Analyze the text representing a semantic theme. "
                "Identify the key high-level concepts, define them, categorize them, and document the direct relationships between them."
            )
            prompt = (
                f"Identify 2-4 key concepts and relationship links from the text below.\n\n"
                f"TEXT CONTENT:\n{context_text}\n\n"
                "You MUST output your response in raw JSON format matching this schema exactly:\n"
                "{\n"
                "  \"concepts\": [\n"
                "    {\"name\": \"Concept Name\", \"definition\": \"A clear, 1-2 sentence definition\", \"category\": \"e.g., Philosophy, Science, Habit, Finance\"}\n"
                "  ],\n"
                "  \"links\": [\n"
                "    {\"source\": \"Concept Name\", \"target\": \"Concept Name\", \"relationship\": \"e.g., implements / connects to / opposes\", \"description\": \"1 sentence description of connection\"}\n"
                "  ]\n"
                "}"
            )
            
            try:
                with console.status(f"[yellow]Extracting graph connections for Theme {c+1}...") as status:
                    raw_response = llm.generate_completion(system_instruction, prompt)
                    
                cleaned_response = clean_json_text(raw_response)
                graph_data = json.loads(cleaned_response)
                
                # Insert extracted concepts
                concepts_added = []
                for concept in graph_data.get("concepts", []):
                    name = concept.get("name")
                    definition = concept.get("definition")
                    category = concept.get("category")
                    if name:
                        add_concept(conn, name, definition, category)
                        concepts_added.append(name)
                        total_concepts += 1
                        
                # Insert links
                links_added = []
                for link in graph_data.get("links", []):
                    src = link.get("source")
                    tgt = link.get("target")
                    rel = link.get("relationship")
                    desc = link.get("description")
                    if src and tgt and rel:
                        add_concept_link(conn, src, tgt, rel, desc)
                        links_added.append(f"{src} --({rel})--> {tgt}")
                        total_links += 1
                        
                console.print(f" ✅ Extracted [bold green]{len(concepts_added)}[/bold green] Concepts: {', '.join(concepts_added[:4])}")
                if links_added:
                    console.print(f" ✅ Extracted [bold blue]{len(links_added)}[/bold blue] Links: {', '.join(links_added[:2])}")
                    
            except Exception as extract_err:
                console.print(f" ❌ Failed to extract Theme {c+1}: {extract_err}")
                
        console.print(f"\n✨ [bold green]Concept Graph Construction Completed![/bold green] Total added: {total_concepts} concepts, {total_links} links.\n")
        
    finally:
        conn.close()

def main():
    parser = argparse.ArgumentParser(description="Build a semantic concept graph from ingested chunks.")
    parser.add_argument("--clusters", type=int, default=6, help="Number of semantic cluster themes to identify.")
    parser.add_argument("--db-path", help="Database file path override. Default is read from .env (DATABASE_PATH).")
    
    args = parser.parse_args()
    db_path = resolve_db_path(args.db_path or os.getenv("DATABASE_PATH", "knowledge.db"))
    
    if not os.path.exists(db_path):
        err_console.print(f"[bold red]Error:[/bold red] Database file '{db_path}' not found. Please ingest some files first.")
        sys.exit(1)
        
    build_concept_graph(db_path, args.clusters)

if __name__ == "__main__":
    main()
