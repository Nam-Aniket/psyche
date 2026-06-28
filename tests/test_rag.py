import unittest
import unittest.mock as mock
import os
import sqlite3
import tempfile
import numpy as np

# Ensure project root is in path
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db
import parsers
import ingest
import query

class TestRAGDatabase(unittest.TestCase):
    def setUp(self):
        # Create a temp file for database
        self.db_fd, self.db_path = tempfile.mkstemp()
        db.init_db(self.db_path)
        self.conn = db.get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_database_schema_and_inserts(self):
        # Test adding a source
        source_id = db.add_source(
            self.conn, 
            title="Test Book", 
            author="Test Author", 
            file_path="tests/dummy.txt", 
            checksum="abc123checksum"
        )
        self.assertEqual(source_id, 1)

        # Test duplicate checksum check
        existing_id = db.check_checksum(self.conn, "abc123checksum")
        self.assertEqual(existing_id, 1)
        
        non_existing = db.check_checksum(self.conn, "wrongchecksum")
        self.assertIsNone(non_existing)

        # Test adding chunk
        chunk_id = db.add_chunk(self.conn, source_id, chunk_index=0, text="This is sample text.", location="Page 12")
        self.assertEqual(chunk_id, 1)

        # Test adding embedding
        embedding_vector = [0.1, 0.2, 0.3, 0.4]
        db.add_embedding(self.conn, chunk_id, embedding_vector)

        # Test retrieving embeddings and chunks
        records = db.get_all_embeddings_with_chunks(self.conn)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["chunk_id"], 1)
        self.assertEqual(records[0]["text"], "This is sample text.")
        self.assertEqual(records[0]["location"], "Page 12")
        self.assertEqual(records[0]["source_title"], "Test Book")
        self.assertEqual(records[0]["source_author"], "Test Author")
        np.testing.assert_array_almost_equal(records[0]["embedding"], np.array(embedding_vector, dtype=np.float32))

    def test_fts_search(self):
        # 1. Add source and chunk
        source_id = db.add_source(
            self.conn, 
            title="Book A", 
            author="Author A", 
            file_path="tests/dummy.txt", 
            checksum="unique_checksum_fts"
        )
        chunk_id = db.add_chunk(self.conn, source_id, chunk_index=0, text="The quick brown fox jumps over the lazy dog.", location="Chapter 1")
        db.add_embedding(self.conn, chunk_id, [0.1, 0.2])
        
        # 2. Search FTS5
        results = db.search_fts(self.conn, "brown fox")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["chunk_id"], chunk_id)
        self.assertEqual(results[0]["text"], "The quick brown fox jumps over the lazy dog.")
        self.assertEqual(results[0]["location"], "Chapter 1")
        self.assertEqual(results[0]["source_title"], "Book A")
        
        # 3. Search with non-matching query
        no_results = db.search_fts(self.conn, "nonexistentword")
        self.assertEqual(len(no_results), 0)

class TestRAGParsers(unittest.TestCase):
    def setUp(self):
        # Create a temporary txt file
        self.txt_fd, self.txt_path = tempfile.mkstemp(suffix=".txt")
        with open(self.txt_path, "w", encoding="utf-8") as f:
            f.write("Hello World! This is a parser test.")

    def tearDown(self):
        os.close(self.txt_fd)
        os.unlink(self.txt_path)

    def test_txt_parser(self):
        blocks = parsers.extract_text(self.txt_path)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["text"].strip(), "Hello World! This is a parser test.")
        self.assertEqual(blocks[0]["location"], "Full Document")

    def test_unsupported_parser(self):
        # Create an existing file with unsupported extension
        fd, path = tempfile.mkstemp(suffix=".invalidext")
        try:
            with self.assertRaises(ValueError):
                parsers.extract_text(path)
        finally:
            os.close(fd)
            os.unlink(path)

    def test_obsidian_markdown_parsing(self):
        from parsers import parse_obsidian_markdown
        content = (
            "---\n"
            "title: Stoicism\n"
            "tags: [philosophy, virtue, stoics]\n"
            "---\n"
            "# Introduction to [[Stoicism|Stoics]]\n"
            "The ancient school of [[Stoicism]] focuses on [[Virtue]]."
        )
        cleaned, tags = parse_obsidian_markdown(content)
        self.assertEqual(tags, ["philosophy", "virtue", "stoics"])
        self.assertNotIn("---", cleaned)
        self.assertIn("Introduction to Stoics", cleaned)
        self.assertIn("The ancient school of Stoicism focuses on Virtue", cleaned)

    def test_obsidian_yaml_tags_varied_formats(self):
        from parsers import parse_obsidian_markdown
        
        # Space-separated or comma-separated tags on single line
        content1 = "---\ntags: stoicism, philosophy\n---\nBody"
        _, tags1 = parse_obsidian_markdown(content1)
        self.assertEqual(tags1, ["stoicism", "philosophy"])
        
        # Bulleted tags list
        content2 = "---\ntags:\n  - stoicism\n  - discipline\n---\nBody"
        _, tags2 = parse_obsidian_markdown(content2)
        self.assertEqual(tags2, ["stoicism", "discipline"])

    def test_docx_parser(self):
        import zipfile
        fd, path = tempfile.mkstemp(suffix=".docx")
        os.close(fd)
        try:
            with zipfile.ZipFile(path, 'w') as docx:
                xml_content = (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
                    '<w:body>\n'
                    '<w:p><w:r><w:t>Introduction</w:t></w:r></w:p>\n'
                    '<w:p><w:r><w:t>This is a docx test content.</w:t></w:r></w:p>\n'
                    '</w:body>\n'
                    '</w:document>'
                )
                docx.writestr('word/document.xml', xml_content)
                
            blocks = parsers.extract_text(path)
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0]["location"], "Introduction")
            self.assertEqual(blocks[0]["text"].strip(), "This is a docx test content.")
        finally:
            os.unlink(path)

    def test_org_parser(self):
        fd, path = tempfile.mkstemp(suffix=".org")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    "* Section One\n"
                    "This is some text in section one.\n"
                    "** Subsection\n"
                    "More text.\n"
                )
            blocks = parsers.extract_text(path)
            self.assertEqual(len(blocks), 2)
            self.assertEqual(blocks[0]["location"], "Section One")
            self.assertIn("This is some text in section one.", blocks[0]["text"])
            self.assertEqual(blocks[1]["location"], "Subsection")
            self.assertIn("More text.", blocks[1]["text"])
        finally:
            os.unlink(path)

    def test_html_parser(self):
        fd, path = tempfile.mkstemp(suffix=".html")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    "<html>\n"
                    "<head><title>Test Title</title></head>\n"
                    "<body>\n"
                    "<h1>Header 1</h1>\n"
                    "<p>This is paragraph text.</p>\n"
                    "<h2>Header 2</h2>\n"
                    "<p>Paragraph two content.</p>\n"
                    "</body>\n"
                    "</html>\n"
                )
            blocks = parsers.extract_text(path)
            self.assertEqual(len(blocks), 2)
            self.assertEqual(blocks[0]["location"], "Header 1")
            self.assertIn("This is paragraph text.", blocks[0]["text"])
            self.assertEqual(blocks[1]["location"], "Header 2")
            self.assertIn("Paragraph two content.", blocks[1]["text"])
        finally:
            os.unlink(path)

class TestRAGTextChunking(unittest.TestCase):
    def test_chunk_text(self):
        # Provide a longer text to make sure the chunks generated are longer than 50 characters
        text = (
            "This is a long sentence to make sure we hit the minimum chunk character requirement. "
            "It will be chunked into multiple pieces, each of which should be longer than 50 characters "
            "so it won't get filtered out. Here is another long sentence to keep the text length long enough "
            "for the test to pass successfully without filtering out any chunks."
        )
        chunks = ingest.chunk_text(text, chunk_size=80, overlap=20)
        
        # Verify that we created some chunks and they overlap/contain words
        self.assertTrue(len(chunks) > 0)
        for c in chunks:
            self.assertTrue(len(c) > 50)

class TestRAGCosineSimilarity(unittest.TestCase):
    def test_calculate_similarities(self):
        query_vector = [1.0, 0.0]
        records = [
            {"chunk_id": 1, "text": "Match 1", "embedding": np.array([1.0, 0.0], dtype=np.float32)},
            {"chunk_id": 2, "text": "Match 2", "embedding": np.array([0.0, 1.0], dtype=np.float32)},
            {"chunk_id": 3, "text": "Match 3", "embedding": np.array([0.707, 0.707], dtype=np.float32)}
        ]
        
        similarities = query.calculate_similarities(query_vector, records)
        
        # Match 1 should be highest (score = 1.0)
        self.assertEqual(similarities[0][0]["chunk_id"], 1)
        self.assertAlmostEqual(similarities[0][1], 1.0, places=4)
        
        # Match 3 should be middle (score = 0.707)
        self.assertEqual(similarities[1][0]["chunk_id"], 3)
        self.assertAlmostEqual(similarities[1][1], 0.707, places=3)
        
        # Match 2 should be lowest (score = 0.0)
        self.assertEqual(similarities[2][0]["chunk_id"], 2)
        self.assertAlmostEqual(similarities[2][1], 0.0, places=4)

class TestRAGHybridSearch(unittest.TestCase):
    def test_rrf_reranking(self):
        semantic_results = [
            ({"chunk_id": 101, "text": "Semantic Match A"}, 0.95),
            ({"chunk_id": 102, "text": "Semantic Match B"}, 0.88),
            ({"chunk_id": 103, "text": "Semantic Match C"}, 0.70)
        ]
        keyword_results = [
            {"chunk_id": 103, "text": "Semantic Match C"}, # Keyword ranked #1
            {"chunk_id": 101, "text": "Semantic Match A"}  # Keyword ranked #2
        ]
        
        # Run RRF
        combined = query.reciprocal_rank_fusion(semantic_results, keyword_results, k=60)
        
        # Verify scores and ranking
        self.assertEqual(len(combined), 3)
        self.assertEqual(combined[0][0]["chunk_id"], 101)
        self.assertEqual(combined[1][0]["chunk_id"], 103)
        self.assertEqual(combined[2][0]["chunk_id"], 102)
        
        self.assertAlmostEqual(combined[0][1], 1/61 + 1/62, places=6)
        self.assertAlmostEqual(combined[1][1], 1/63 + 1/61, places=6)
        self.assertAlmostEqual(combined[2][1], 1/62, places=6)

class TestRAGDirectorySync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        
        # Create test files with different extensions
        self.file_txt = os.path.join(self.temp_dir.name, "book1.txt")
        self.file_epub = os.path.join(self.temp_dir.name, "book2.epub")
        self.file_pdf = os.path.join(self.temp_dir.name, "book3.pdf")
        self.file_invalid = os.path.join(self.temp_dir.name, "other.log")
        
        # Subdirectory scanning test
        self.sub_dir = os.path.join(self.temp_dir.name, "subdir")
        os.makedirs(self.sub_dir, exist_ok=True)
        self.file_sub_txt = os.path.join(self.sub_dir, "nested.txt")
        
        for fpath in [self.file_txt, self.file_epub, self.file_pdf, self.file_invalid, self.file_sub_txt]:
            with open(fpath, "w") as f:
                f.write("test content")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_directory_scan_default_extensions(self):
        # Default behavior scans: .pdf, .epub, .txt, .md, .markdown
        allowed_exts = {".pdf", ".epub", ".txt", ".md", ".markdown"}
        scanned_files = []
        for root, dirs, files in os.walk(self.temp_dir.name):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in allowed_exts:
                    scanned_files.append(os.path.basename(file))
                    
        self.assertIn("book1.txt", scanned_files)
        self.assertIn("book2.epub", scanned_files)
        self.assertIn("book3.pdf", scanned_files)
        self.assertIn("nested.txt", scanned_files)
        self.assertNotIn("other.log", scanned_files)
        self.assertEqual(len(scanned_files), 4)

    def test_directory_scan_filtered_extensions(self):
        # Filter by custom extensions (e.g. text/epub only)
        allowed_exts = {".txt", ".epub"}
        scanned_files = []
        for root, dirs, files in os.walk(self.temp_dir.name):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in allowed_exts:
                    scanned_files.append(os.path.basename(file))
                    
        self.assertIn("book1.txt", scanned_files)
        self.assertIn("book2.epub", scanned_files)
        self.assertIn("nested.txt", scanned_files)
        self.assertNotIn("book3.pdf", scanned_files)
        self.assertNotIn("other.log", scanned_files)
        self.assertEqual(len(scanned_files), 3)

class TestRAGOllamaSupport(unittest.TestCase):
    @mock.patch('requests.post')
    def test_ollama_embedding(self, mock_post):
        # Configure mock response for /api/embed
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "embeddings": [[0.1, 0.2, 0.3]]
        }
        mock_post.return_value = mock_response

        from llm_client import LLMClient
        with mock.patch.dict(os.environ, {
            "LLM_PROVIDER": "ollama",
            "OLLAMA_HOST": "http://localhost:11434",
            "EMBED_MODEL": "nomic-embed-text",
            "CHAT_MODEL": "llama3",
            "TESTING": "true"
        }):
            client = LLMClient()
            emb = client.get_embedding("test query")
            self.assertEqual(emb, [0.1, 0.2, 0.3])
            
    @mock.patch('requests.post')
    def test_ollama_completion(self, mock_post):
        # Configure mock response for /api/chat
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {
                "role": "assistant",
                "content": "hello there"
            }
        }
        mock_post.return_value = mock_response

        from llm_client import LLMClient
        with mock.patch.dict(os.environ, {
            "LLM_PROVIDER": "ollama",
            "OLLAMA_HOST": "http://localhost:11434",
            "EMBED_MODEL": "nomic-embed-text",
            "CHAT_MODEL": "llama3",
            "TESTING": "true"
        }):
            client = LLMClient()
            res = client.generate_completion("instruction", "prompt")
            self.assertEqual(res, "hello there")

class TestRAGConceptGraph(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        db.init_db(self.db_path)
        self.conn = db.get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_concepts_crud(self):
        c1_id = db.add_concept(self.conn, "Stoicism", "An ancient Greek philosophy", "Philosophy")
        c2_id = db.add_concept(self.conn, "Virtue", "Moral excellence", "Ethics")
        
        self.assertEqual(c1_id, 1)
        self.assertEqual(c2_id, 2)
        
        link_id = db.add_concept_link(self.conn, "Stoicism", "Virtue", "values", "Stoics value virtue")
        self.assertEqual(link_id, 1)
        
        concepts = db.get_all_concepts(self.conn)
        self.assertEqual(len(concepts), 2)
        self.assertEqual(concepts[0]["name"], "Stoicism")
        self.assertEqual(concepts[1]["name"], "Virtue")
        
        links = db.get_concept_links(self.conn)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["source"], "Stoicism")
        self.assertEqual(links[0]["target"], "Virtue")
        self.assertEqual(links[0]["relationship"], "values")
        
    def test_kmeans_clustering(self):
        from build_graph import kmeans
        embeddings = np.array([
            [1.0, 0.0],
            [0.9, 0.1],
            [0.0, 1.0],
            [0.1, 0.9]
        ], dtype=np.float32)
        
        labels, centroids = kmeans(embeddings, num_clusters=2)
        self.assertEqual(labels.shape[0], 4)
        self.assertEqual(labels[0], labels[1])
        self.assertEqual(labels[2], labels[3])
        self.assertNotEqual(labels[0], labels[2])
        self.assertEqual(centroids.shape, (2, 2))

    def test_retrieve_concept_context(self):
        db.add_concept(self.conn, "Stoicism", "An ancient Greek philosophy", "Philosophy")
        db.add_concept(self.conn, "Virtue", "Moral excellence", "Ethics")
        db.add_concept_link(self.conn, "Stoicism", "Virtue", "values", "Sole good")
        
        ctx = query.retrieve_concept_context(self.conn, "What is Stoicism and Virtue?")
        self.assertIn("KNOWLEDGE CONCEPT GRAPH", ctx)
        self.assertIn("Stoicism", ctx)
        self.assertIn("Virtue", ctx)
        self.assertIn("values", ctx)

    @mock.patch('build_graph.LLMClient')
    def test_offline_topic_graph_builder(self, mock_llm_class):
        # Offline path (no chat model) → build_topic_graph (BERTopic-lite).
        mock_llm = mock.Mock()
        mock_llm.provider = "none"
        mock_llm.chat_model = "none"
        mock_llm_class.return_value = mock_llm

        db.add_source(self.conn, "Meditations", "Marcus Aurelius", "tests/dummy.txt", "checksum_meditations")
        # Repeat meaningful lowercase concepts across enough chunks to clear the
        # document-frequency floor; "Marcus Aurelius" recurs as a proper noun.
        sentences = [
            "Stoicism teaches the practice of virtue through daily discipline.",
            "Virtue is the sole good in Stoicism, and discipline sustains it.",
            "Marcus Aurelius practised discipline and wrote about virtue.",
            "Discipline turns Stoicism from theory into a way of living virtue.",
            "The discipline of perception and the discipline of action shape virtue.",
            "Stoicism frames virtue as discipline applied to judgement.",
        ]
        for i, s in enumerate(sentences):
            db.add_chunk(self.conn, 1, i, s)

        from build_graph import build_concept_graph
        build_concept_graph(self.db_path, num_clusters=2)

        concepts = db.get_all_concepts(self.conn)
        names = {c["name"].lower() for c in concepts}
        # Meaningful lowercase concepts are extracted...
        self.assertTrue(concepts, "expected at least one concept")
        self.assertTrue(names & {"stoicism", "virtue", "discipline"},
                        f"expected a meaningful concept, got {names}")
        # ...and the proper noun is dropped by the capitalization gate.
        self.assertNotIn("marcus aurelius", names)
        # Links use the new typed vocabulary, not only "co-occurs with".
        links = db.get_concept_links(self.conn)
        self.assertIsInstance(links, list)
        rels = {l["relationship"] for l in links}
        self.assertTrue(rels <= {"part of theme", "co-occurs with", "related theme"} or not rels)

class TestCLIAndRouting(unittest.TestCase):
    def setUp(self):
        self.old_argv = sys.argv.copy()
        self.old_env_db = os.environ.get("DATABASE_PATH")

    def tearDown(self):
        sys.argv = self.old_argv
        if self.old_env_db is not None:
            os.environ["DATABASE_PATH"] = self.old_env_db
        elif "DATABASE_PATH" in os.environ:
            del os.environ["DATABASE_PATH"]

    @mock.patch('ingest.main')
    def test_cli_topic_routing(self, mock_ingest):
        sys.argv = ["psyche", "ingest", "--topic", "custom_topic", "path/to/notes"]
        import cli
        cli.main()
        
        # Verify environment variable was set
        self.assertEqual(os.environ.get("DATABASE_PATH"), db.resolve_db_path("topic_custom_topic.db"))
        # Verify --topic and its value were removed, and subcommand "ingest" was popped
        self.assertEqual(sys.argv, ["psyche", "path/to/notes"])
        mock_ingest.assert_called_once()

    @mock.patch('query.main')
    def test_cli_profile_routing(self, mock_query):
        sys.argv = ["psyche", "query", "--profile", "philosophy", "What is Stoicism?"]
        import cli
        cli.main()
        
        # Verify environment variable was set
        self.assertEqual(os.environ.get("DATABASE_PATH"), db.resolve_db_path("topic_philosophy.db"))
        self.assertEqual(sys.argv, ["psyche", "What is Stoicism?"])
        mock_query.assert_called_once()

class TestRAGPureRetrieval(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        db.init_db(self.db_path)
        self.conn = db.get_connection(self.db_path)
        
        # Ingest a dummy document without generating embeddings
        source_id = db.add_source(self.conn, "Offline Book", "Offline Author", "tests/dummy.txt", "checksum_offline")
        db.add_chunk(self.conn, source_id, chunk_index=0, text="This is pure local text containing Stoic focus.", location="Chapter 1")

    def tearDown(self):
        self.conn.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    @mock.patch('query.LLMClient')
    def test_pure_retrieval_query_runs_without_llm(self, mock_llm_class):
        # Mock LLMClient to return provider == "none"
        mock_llm = mock.Mock()
        mock_llm.provider = "none"
        mock_llm_class.return_value = mock_llm
        
        import query
        from unittest.mock import patch
        
        # Mock sys.argv to run query
        # Using sys.exit mocking to verify it runs and exits 0
        with patch('sys.argv', ['psyche', 'Stoic focus', '--db-path', self.db_path]), \
             patch('sys.exit') as mock_exit:
            query.main()
            mock_exit.assert_called_with(0)

class TestMCPHostServer(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        db.init_db(self.db_path)
        self.conn = db.get_connection(self.db_path)
        
    def tearDown(self):
        self.conn.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    @mock.patch('sys.stdin')
    @mock.patch('mcp_server.real_stdout')
    def test_mcp_initialize_and_tools(self, mock_stdout, mock_stdin):
        import json
        import mcp_server
        
        # Setup mock stdin lines representing requests
        mock_stdin.readline.side_effect = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n",
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n",
            ""  # EOF to terminate loop
        ]
        
        # Capture output written to stdout
        written_data = []
        def mock_write(data):
            written_data.append(data)
        mock_stdout.write.side_effect = mock_write
        
        # Run main loop
        mcp_server.main()
        
        # Parse the JSON response objects
        responses = [json.loads(line) for line in "".join(written_data).split("\n") if line.strip()]
        
        # Assertions
        self.assertEqual(len(responses), 2)
        
        # Response 1: initialize
        self.assertEqual(responses[0]["id"], 1)
        self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "psyche-mcp")
        
        # Response 2: tools/list
        self.assertEqual(responses[1]["id"], 2)
        tools = responses[1]["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        self.assertIn("search_knowledge", tool_names)
        self.assertIn("retrieve_graph", tool_names)

    @mock.patch('sys.stdin')
    @mock.patch('mcp_server.real_stdout')
    def test_mcp_prompts(self, mock_stdout, mock_stdin):
        import json
        import mcp_server
        
        # Setup mock stdin lines representing requests
        mock_stdin.readline.side_effect = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"}) + "\n",
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "prompts/get", "params": {"name": "psyche", "arguments": {}}}) + "\n",
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "prompts/get", "params": {"name": "psyche", "arguments": {"query": "Stoic focus"}}}) + "\n",
            ""  # EOF to terminate loop
        ]
        
        # Capture output written to stdout
        written_data = []
        def mock_write(data):
            written_data.append(data)
        mock_stdout.write.side_effect = mock_write
        
        with mock.patch.dict(os.environ, {"DATABASE_PATH": self.db_path}):
            # Run main loop
            mcp_server.main()
        
        # Parse the JSON response objects
        responses = [json.loads(line) for line in "".join(written_data).split("\n") if line.strip()]
        
        self.assertEqual(len(responses), 3)
        
        # Response 1: prompts/list
        self.assertEqual(responses[0]["id"], 1)
        prompts = responses[0]["result"]["prompts"]
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]["name"], "psyche")
        self.assertFalse(prompts[0]["arguments"][0]["required"]) # query is optional now
        
        # Response 2: prompts/get without query (fallback)
        self.assertEqual(responses[1]["id"], 2)
        self.assertIn("messages", responses[1]["result"])
        self.assertIn("Ask a question or search for concepts", responses[1]["result"]["messages"][0]["content"]["text"])
        
        # Response 3: prompts/get with query
        self.assertEqual(responses[2]["id"], 3)
        self.assertIn("messages", responses[2]["result"])
        self.assertIn("Use the following retrieved notes and passages", responses[2]["result"]["messages"][0]["content"]["text"])

    @mock.patch('sys.stdin')
    @mock.patch('mcp_server.real_stdout')
    def test_mcp_tools_call(self, mock_stdout, mock_stdin):
        import json
        import mcp_server
        
        # Setup mock database with data
        source_id = db.add_source(self.conn, "Book A", "Author A", "tests/dummy.txt", "unique_checksum_mcp")
        chunk_id = db.add_chunk(self.conn, source_id, chunk_index=0, text="This is some Stoic focus text.", location="Chapter 1")
        # Add a concept too
        db.add_concept(self.conn, "Stoicism", "An ancient Greek philosophy", "Philosophy")
        
        # Setup mock stdin lines representing requests
        mock_stdin.readline.side_effect = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_knowledge", "arguments": {"query": "Stoic focus"}}}) + "\n",
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "retrieve_graph", "arguments": {}}}) + "\n",
            ""  # EOF to terminate loop
        ]
        
        # Capture output written to stdout
        written_data = []
        def mock_write(data):
            written_data.append(data)
        mock_stdout.write.side_effect = mock_write
        
        with mock.patch.dict(os.environ, {"DATABASE_PATH": self.db_path, "LLM_PROVIDER": "none"}):
            # Run main loop
            mcp_server.main()
        
        # Parse the JSON response objects
        responses = [json.loads(line) for line in "".join(written_data).split("\n") if line.strip()]
        
        self.assertEqual(len(responses), 2)
        
        # Response 1: search_knowledge
        self.assertEqual(responses[0]["id"], 1)
        self.assertIn("content", responses[0]["result"])
        text_val = responses[0]["result"]["content"][0]["text"]
        self.assertIn("Stoic focus", text_val)
        
        # Response 2: retrieve_graph
        self.assertEqual(responses[1]["id"], 2)
        self.assertIn("content", responses[1]["result"])
        graph_val = responses[1]["result"]["content"][0]["text"]
        self.assertIn("Stoicism", graph_val)

class TestDatabaseMigration(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        db.init_db(self.db_path)
        self.conn = db.get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_metadata_get_set(self):
        db.set_metadata(self.conn, "test_key", "test_val")
        val = db.get_metadata(self.conn, "test_key")
        self.assertEqual(val, "test_val")

    @mock.patch('rich.console.Console')
    @mock.patch('rich.progress.Progress')
    def test_automatic_migration_on_mismatch(self, mock_progress, mock_console):
        # 1. Ingest a mock chunk with embedding of dim 5
        source_id = db.add_source(self.conn, "Test Title", "Test Author", "dummy.txt", "checksum_dummy")
        chunk_id = db.add_chunk(self.conn, source_id, 0, "Hello stoichiometry virtue Stoicism.")
        db.add_embedding(self.conn, chunk_id, [1.0, 2.0, 3.0, 4.0, 5.0])
        
        # Manually set metadata to "old_model"
        db.set_metadata(self.conn, "embed_model", "old_model")
        
        # 2. Mock LLMClient to return new model "new_model" and embeddings of dim 3
        mock_llm = mock.Mock()
        mock_llm.embed_model = "new_model"
        mock_llm.get_embeddings_batch.return_value = [[10.0, 20.0, 30.0]]
        
        # 3. Trigger check_and_migrate_embeddings
        db.check_and_migrate_embeddings(self.db_path, mock_llm)
        
        # 4. Verify metadata updated
        meta = db.get_metadata(self.conn, "embed_model")
        self.assertEqual(meta, "new_model")
        
        # 5. Verify embeddings are updated to the mocked values of dim 3
        records = db.get_all_embeddings_with_chunks(self.conn)
        self.assertEqual(len(records), 1)
        self.assertEqual(list(records[0]["embedding"]), [10.0, 20.0, 30.0])

    def test_remove_source(self):
        # 1. Add source, chunk, embedding
        source_id = db.add_source(
            self.conn, 
            title="Book to Delete", 
            author="Author X", 
            file_path="tests/delete.txt", 
            checksum="delete_checksum"
        )
        chunk_id = db.add_chunk(self.conn, source_id, chunk_index=0, text="This is text to be deleted from FTS.", location="Page 1")
        db.add_embedding(self.conn, chunk_id, [0.1, 0.2])
        
        # Verify it exists in FTS and normal tables
        results = db.search_fts(self.conn, "deleted from FTS")
        self.assertEqual(len(results), 1)
        
        # 2. Remove the source
        db.remove_source(self.conn, source_id)
        
        # 3. Verify it is gone from sources, chunks, embeddings, and FTS
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sources WHERE id = ?", (source_id,))
        self.assertEqual(cursor.fetchone()[0], 0)
        
        cursor.execute("SELECT COUNT(*) FROM chunks WHERE source_id = ?", (source_id,))
        self.assertEqual(cursor.fetchone()[0], 0)
        
        # Verify FTS clean up
        results_after = db.search_fts(self.conn, "deleted from FTS")
        self.assertEqual(len(results_after), 0)

class TestRAGReIngest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        db.init_db(self.db_path)
        self.conn = db.get_connection(self.db_path)
        
        # Create temp files to ingest
        self.temp_file_fd, self.temp_file_path = tempfile.mkstemp(suffix=".txt")
        with open(self.temp_file_path, "w", encoding="utf-8") as f:
            f.write("This is document content that is longer than fifty characters to avoid being filtered out by the chunking threshold.")


    def tearDown(self):
        self.conn.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)
        os.close(self.temp_file_fd)
        os.unlink(self.temp_file_path)

    @mock.patch('ingest.LLMClient')
    def test_ingest_duplicate_and_force(self, mock_llm_class):
        mock_llm = mock.Mock()
        mock_llm.provider = "none"
        mock_llm.embed_model = "none"
        mock_llm_class.return_value = mock_llm
        
        # 1. Ingest first time
        with mock.patch('sys.argv', ['psyche', self.temp_file_path, '--db-path', self.db_path]):
            ingest.main()
            
        # Verify it has 1 source and 1 chunk
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sources")
        self.assertEqual(cursor.fetchone()[0], 1)
        cursor.execute("SELECT id FROM sources")
        first_source_id = cursor.fetchone()[0]
        
        # 2. Ingest second time without --force (should skip)
        with mock.patch('sys.argv', ['psyche', self.temp_file_path, '--db-path', self.db_path]):
            ingest.main()
            
        # Verify still 1 source and it has the same ID
        cursor.execute("SELECT COUNT(*) FROM sources")
        self.assertEqual(cursor.fetchone()[0], 1)
        cursor.execute("SELECT id FROM sources")
        self.assertEqual(cursor.fetchone()[0], first_source_id)
        
        # 3. Ingest third time with --force (should re-ingest and get new ID)
        with mock.patch('sys.argv', ['psyche', self.temp_file_path, '--db-path', self.db_path, '--force']):
            ingest.main()
            
        # Verify still 1 source, but ID has changed
        cursor.execute("SELECT COUNT(*) FROM sources")
        self.assertEqual(cursor.fetchone()[0], 1)
        cursor.execute("SELECT id FROM sources")
        self.assertNotEqual(cursor.fetchone()[0], first_source_id)

if __name__ == "__main__":
    unittest.main()

