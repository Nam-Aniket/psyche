"""Tests for the Stop-hook incremental-extraction gate (psyche_stop.py).

Covers:
  1. should_extract: pure gate logic (turns path, timer path, growth guard).
  2. read_extract_state / write_extract_state round-trip + missing-file default.
  3. count_turns: assistant-entry counting over a transcript.

The gate is the load-bearing unit — it decides when the (slow, LLM-backed)
extraction actually runs, so it is tested in isolation with no IO or LLM.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

_HOOKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

import _hook_common as hc          # noqa: E402
from psyche_stop import should_extract  # noqa: E402
from psyche_extract import count_turns  # noqa: E402

# Defaults mirrored from psyche_stop.main()
MIN_TURNS, MIN_MIN, MIN_GROWTH = 4, 10, 800


def _gate(**over):
    base = dict(
        now=1_000_000.0,
        last_ts=None,
        turn_count=0,
        last_turn_count=0,
        transcript_len=0,
        last_len=0,
        min_turns=MIN_TURNS,
        min_minutes=MIN_MIN,
        min_growth_chars=MIN_GROWTH,
    )
    base.update(over)
    return should_extract(**base)


class TestShouldExtract(unittest.TestCase):

    # --- first extraction of the session (last_ts is None) ---
    def test_first_run_enough_turns_and_growth_extracts(self):
        self.assertTrue(_gate(last_ts=None, turn_count=4, transcript_len=2000))

    def test_first_run_too_few_turns_skips(self):
        self.assertFalse(_gate(last_ts=None, turn_count=3, transcript_len=2000))

    def test_first_run_no_growth_skips(self):
        # Enough turns but transcript barely grew -> not worth an LLM call.
        self.assertFalse(_gate(last_ts=None, turn_count=10, transcript_len=500))

    # --- subsequent extractions (last_ts set) ---
    def test_turns_path_extracts(self):
        self.assertTrue(_gate(
            last_ts=1_000_000.0, now=1_000_060.0,   # only 1 min elapsed
            last_turn_count=10, turn_count=14,        # 4 new turns
            last_len=2000, transcript_len=3000,       # +1000 chars
        ))

    def test_timer_path_extracts_even_with_few_turns(self):
        # The "abandoned for a long time" guarantee: time elapses, few new turns,
        # transcript barely grew -> still extract (timer bypasses growth).
        self.assertTrue(_gate(
            last_ts=1_000_000.0, now=1_000_000.0 + 11 * 60,  # 11 min > 10
            last_turn_count=10, turn_count=11,                # only 1 new turn
            last_len=12000, transcript_len=12000,             # saturated window, no growth
        ))

    def test_both_thresholds_unmet_skips(self):
        self.assertFalse(_gate(
            last_ts=1_000_000.0, now=1_000_060.0,   # 1 min
            last_turn_count=10, turn_count=11,        # 1 new turn
            last_len=2000, transcript_len=3000,
        ))

    def test_turns_met_but_no_growth_skips(self):
        # Cost guard: enough turns, time short, but transcript did not grow.
        self.assertFalse(_gate(
            last_ts=1_000_000.0, now=1_000_060.0,
            last_turn_count=10, turn_count=20,        # plenty of turns
            last_len=5000, transcript_len=5100,       # +100 < 800
        ))


class TestExtractState(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = hc._extract_state_path
        hc._extract_state_path = lambda sid: os.path.join(self._tmp, f"{sid}.extract.json")

    def tearDown(self):
        hc._extract_state_path = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_missing_state_returns_empty_dict(self):
        self.assertEqual(hc.read_extract_state("nope"), {})

    def test_roundtrip(self):
        state = {"last_ts": 1234.5, "last_turn_count": 7, "last_len": 8123}
        hc.write_extract_state("sid1", state)
        self.assertEqual(hc.read_extract_state("sid1"), state)

    def test_non_dict_payload_is_ignored(self):
        # A corrupt file (JSON list) must not crash the gate; treated as empty.
        p = hc._extract_state_path("sid2")
        with open(p, "w") as f:
            json.dump([1, 2, 3], f)
        self.assertEqual(hc.read_extract_state("sid2"), {})


class TestCountTurns(unittest.TestCase):

    def _write(self, lines):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as f:
            for obj in lines:
                f.write(json.dumps(obj) + "\n")
        self.addCleanup(os.unlink, path)
        return path

    def test_counts_assistant_entries_only(self):
        path = self._write([
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "message": {"role": "assistant", "content": "a"}},
            {"type": "assistant", "message": {"role": "assistant", "content": "b"}},
            {"type": "system", "message": {}},
        ])
        self.assertEqual(count_turns(path), 2)

    def test_missing_file_returns_zero(self):
        self.assertEqual(count_turns("/tmp/_no_such_transcript_xyz.jsonl"), 0)

    def test_malformed_lines_skipped(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as f:
            f.write('{"type": "assistant"}\n')
            f.write("not json at all\n")
            f.write('{"type": "assistant"}\n')
        self.addCleanup(os.unlink, path)
        self.assertEqual(count_turns(path), 2)


class TestStopHookIntegration(unittest.TestCase):
    """End-to-end of psyche_stop.main(): gate -> detached spawn -> watermark ->
    next-turn suppression, plus the worker-dispatch and re-entrancy guard. All
    side effects (Popen, extract_facts, stdin, state path) are stubbed — no LLM."""

    def setUp(self):
        import psyche_stop as ps
        self.ps = ps
        self._tmp = tempfile.mkdtemp()
        self._saved = {
            "Popen": ps.subprocess.Popen,
            "extract_facts": ps.extract_facts,
            "read_payload": hc.read_payload,
            "state_path": hc._extract_state_path,
        }
        hc._extract_state_path = lambda sid: os.path.join(self._tmp, f"{sid}.extract.json")
        self.spawns = []

        class _FakePopen:
            def __init__(_self, *a, **k):
                self.spawns.append((a, k))
        ps.subprocess.Popen = _FakePopen

        # synthetic transcript: 5 assistant turns, > min growth
        self.tf = os.path.join(self._tmp, "t.jsonl")
        with open(self.tf, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "go " * 20}}) + "\n")
            for i in range(5):
                f.write(json.dumps({"type": "assistant",
                                    "message": {"role": "assistant", "content": f"step {i} " * 30}}) + "\n")
        self.payload = {"session_id": "sid", "transcript_path": self.tf,
                        "cwd": self._tmp, "stop_hook_active": False}
        hc.read_payload = lambda: self.payload

    def tearDown(self):
        self.ps.subprocess.Popen = self._saved["Popen"]
        self.ps.extract_facts = self._saved["extract_facts"]
        hc.read_payload = self._saved["read_payload"]
        hc._extract_state_path = self._saved["state_path"]
        os.environ.pop("PSYCHE_STOP_WORKER", None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_cold_turn_spawns_and_writes_watermark(self):
        self.ps.main()
        self.assertEqual(len(self.spawns), 1)
        self.assertIn("PSYCHE_STOP_WORKER", self.spawns[0][1]["env"])
        st = hc.read_extract_state("sid")
        self.assertEqual(st["last_turn_count"], 5)
        self.assertIsNotNone(st["last_ts"])

    def test_next_turn_suppressed_after_watermark(self):
        self.ps.main()                 # passes
        self.ps.main()                 # no growth / no time -> skip
        self.assertEqual(len(self.spawns), 1)

    def test_timer_path_refires(self):
        self.ps.main()
        st = hc.read_extract_state("sid")
        st["last_ts"] -= 999           # force >10 min elapsed
        hc.write_extract_state("sid", st)
        self.ps.main()
        self.assertEqual(len(self.spawns), 2)

    def test_stop_hook_active_blocks(self):
        self.payload["stop_hook_active"] = True
        self.ps.main()
        self.assertEqual(len(self.spawns), 0)

    def test_worker_mode_dispatches_and_cleans_up(self):
        pf = os.path.join(self._tmp, "payload.json")
        with open(pf, "w") as f:
            json.dump(self.payload, f)
        seen = {}
        self.ps.extract_facts = lambda p, *, source: seen.update(source=source) or 3
        os.environ["PSYCHE_STOP_WORKER"] = pf
        self.ps.main()
        self.assertEqual(seen.get("source"), "stop")
        self.assertFalse(os.path.exists(pf))   # temp payload removed
        self.assertEqual(len(self.spawns), 0)  # worker mode never spawns


if __name__ == "__main__":
    unittest.main()
