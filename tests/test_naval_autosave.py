import json
import os
import sys
import tempfile
import unittest

import db

HOOKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
sys.path.insert(0, HOOKS_DIR)
import _hook_common as hc  # noqa: E402
import psyche_stop  # noqa: E402


def _transcript_lines():
    return [
        {"type": "user", "message": {"role": "user", "content": "Should I take this deal?"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Leverage says check ownership."}]}},
        {"type": "system", "subtype": "noise"},
    ]


class TestNavalAutosave(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.transcript = os.path.join(self.tmp, "t.jsonl")
        with open(self.transcript, "w") as f:
            for e in _transcript_lines():
                f.write(json.dumps(e) + "\n")
        self.config_path = os.path.join(self.tmp, "config.json")
        os.environ["PSYCHE_CONFIG"] = self.config_path
        self.topic_db = db.resolve_db_path("topic_navaltest.db")
        if os.path.exists(self.topic_db):
            os.remove(self.topic_db)
        db.init_db("topic_navaltest.db")
        self.session_id = "navaltest-session-1"
        self.payload = {
            "session_id": self.session_id,
            "transcript_path": self.transcript,
            "cwd": "/Users/someone/Downloads/NAVALTEST/subdir",
        }

    def tearDown(self):
        os.environ.pop("PSYCHE_CONFIG", None)
        for p in (self.topic_db,
                  os.path.expanduser(f"~/.psyche/sessions/{self.session_id}.extract.json")):
            if os.path.exists(p):
                os.remove(p)

    def _write_config(self, topics):
        with open(self.config_path, "w") as f:
            json.dump({"auto_capture_topics": topics}, f)

    def test_topic_for_cwd_matches_path_component_case_insensitively(self):
        self.assertEqual(hc.topic_for_cwd("/x/Downloads/NAVAL/sub", ["naval"]), "naval")
        self.assertIsNone(hc.topic_for_cwd("/x/Downloads/navalish", ["naval"]))
        self.assertIsNone(hc.topic_for_cwd(None, ["naval"]))

    def test_captures_new_messages_into_topic_recall(self):
        self._write_config(["navaltest"])
        written = psyche_stop.capture_interactions(self.payload)
        self.assertEqual(written, 2)
        conn = db.get_connection(self.topic_db)
        rows = conn.execute(
            "SELECT session_id, role, content FROM memory_recall ORDER BY id").fetchall()
        conn.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], "user")
        self.assertIn("this deal", rows[0][2])
        self.assertEqual(rows[1][1], "assistant")
        self.assertIn("Leverage says", rows[1][2])
        # watermark: re-running the same stop writes nothing new
        self.assertEqual(psyche_stop.capture_interactions(self.payload), 0)
        conn = db.get_connection(self.topic_db)
        n = conn.execute("SELECT count(*) FROM memory_recall").fetchone()[0]
        conn.close()
        self.assertEqual(n, 2)

    def test_capture_off_when_topic_not_in_config(self):
        self._write_config([])
        self.assertEqual(psyche_stop.capture_interactions(self.payload), 0)
        conn = db.get_connection(self.topic_db)
        n = conn.execute("SELECT count(*) FROM memory_recall").fetchone()[0]
        conn.close()
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
