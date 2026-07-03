"""Regression guard for the offline concept extractor (build_graph.build_topic_graph).

The old extractor grabbed capitalized words, so it produced sentence openers
("But", "However", "After") and bare proper nouns ("Seneca", "New York") as
"concepts". These tests pin the candidate filter that fixes that, without needing
embeddings or a chat model.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import build_graph as bg


def phrases(text):
    """All candidate phrases the extractor would consider for one sentence."""
    toks = bg._TOKEN_RE.findall(bg._normalize_quotes(text))
    return {p for p, _first, _i in bg._phrase_windows(toks)}


class TestPhraseCandidates(unittest.TestCase):
    BAD_OPENERS = ["But", "However", "After", "Therefore", "Yet", "Because",
                   "Instead", "Once", "Even", "Why", "Let"]

    def test_sentence_openers_excluded(self):
        for w in self.BAD_OPENERS:
            cands = phrases(f"{w} the market rewards patience")
            self.assertNotIn(w.lower(), cands, f"{w!r} leaked as a concept")

    def test_light_verbs_excluded_as_unigrams(self):
        for v in ["get", "make", "said", "see", "like", "go", "work", "look"]:
            self.assertNotIn(v, phrases(f"people {v} things daily"),
                             f"weak verb {v!r} leaked")

    def test_vague_nouns_excluded_as_unigrams(self):
        for n in ["thing", "way", "time", "people", "good", "new", "great", "first"]:
            self.assertNotIn(n, phrases(f"the {n} matters here"),
                             f"vague word {n!r} leaked")

    def test_meaningful_concepts_kept(self):
        cands = phrases("compounding wealth requires patience and equanimity")
        for good in ["compounding", "wealth", "patience", "equanimity"]:
            self.assertIn(good, cands, f"real concept {good!r} was dropped")

    def test_multiword_noun_phrases_kept(self):
        cands = phrases("natural selection favours the fittest devotional service")
        self.assertIn("natural selection", cands)
        self.assertIn("devotional service", cands)

    def test_phrases_do_not_bookend_with_function_words(self):
        for p in phrases("the price of labour in the country"):
            head, tail = p.split()[0], p.split()[-1]
            self.assertNotIn(head, bg.FUNCTION_WORDS)
            self.assertNotIn(tail, bg.FUNCTION_WORDS)

    def test_meta_words_excluded(self):
        self.assertNotIn("chapter", phrases("chapter seven discusses virtue"))
        self.assertNotIn("purport", phrases("the purport explains the verse"))

    def test_contractions_never_fragment_into_concepts(self):
        # Books use typographic apostrophes: "didn’t" must not yield "didn"
        # (the live "Didn · 11 links" bug), and ASCII "didn't" must not leak either.
        for text in ["he didn’t sell the business", "he didn't sell the business",
                     "the person’s equanimity held"]:
            cands = phrases(text)
            for bad in ["didn", "didn't", "person's"]:
                self.assertNotIn(bad, cands, f"{bad!r} leaked from {text!r}")
            self.assertFalse(any("'" in p for p in cands), f"apostrophe phrase in {cands}")

    def test_academic_boilerplate_excluded(self):
        # misdisinfo-topic garbage class: Fig / Proceedings / Manuscript / Journal
        for w in ["fig", "proceedings", "manuscript", "journal", "conference",
                  "arxiv", "doi", "finally"]:
            cands = phrases(f"the {w} shows misinformation spreading")
            self.assertNotIn(w, cands, f"{w!r} leaked as a concept")
        self.assertNotIn("in proceedings", phrases("published in proceedings yearly"))

    def test_code_sentences_skipped(self):
        # Ingested implementation docs contain code; prose extraction must skip it
        # ("Def Test" / "Self Client" bug class on the default topic).
        for code in ["def test_web(self): self.client.get('/')",
                     "resp = api.get(url)",
                     "see https://example.com/docs for details",
                     "the snake_case identifier survives"]:
            self.assertEqual(list(bg._iter_sentences(code)), [], f"code leaked: {code!r}")
        self.assertEqual(list(bg._iter_sentences("Plain prose stays.")), ["Plain prose stays."])


class TestConstantsCoverBugReport(unittest.TestCase):
    def test_reported_garbage_is_filtered(self):
        # Every concept from the original broken screenshot must be a stop/weak word.
        reported = ["but", "however", "after", "therefore", "yet", "because",
                    "let", "why", "instead", "once", "even", "him"]
        blocked = bg.FUNCTION_WORDS | bg.WEAK_VERBS | bg.WEAK_NOUNS
        for w in reported:
            self.assertIn(w, blocked, f"{w!r} is not in any stop/weak list")


if __name__ == "__main__":
    unittest.main()
