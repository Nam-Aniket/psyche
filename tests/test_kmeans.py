"""Regression guard for build_graph.kmeans cluster collapse.

Two blobs of identical vectors used to collapse into a single cluster: uniform
random init could seed identical centroids, and the convergence check fired on
iteration 0 (all-zeros labels == all-zeros first assignment) before any centroid
update. This pins that separable data yields the expected non-empty clusters,
deterministically.
"""
import os
import sys
import unittest
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import build_graph as bg


class TestKMeansNoCollapse(unittest.TestCase):
    def test_two_identical_blobs_split_into_two_clusters(self):
        # The exact reported repro: 30 copies of [1,0] + 30 copies of [0,1].
        m = np.array([[1.0, 0.0]] * 30 + [[0.0, 1.0]] * 30, dtype=np.float32)
        labels, _ = bg.kmeans(m, 2)
        counts = Counter(labels.tolist())
        self.assertEqual(len(counts), 2, f"collapsed to {len(counts)} cluster(s): {dict(counts)}")
        self.assertTrue(all(v > 0 for v in counts.values()), f"empty cluster: {dict(counts)}")
        # each blob must be pure and the two blobs must land in different clusters
        self.assertEqual(len(set(labels[:30].tolist())), 1)
        self.assertEqual(len(set(labels[30:].tolist())), 1)
        self.assertNotEqual(labels[0], labels[30])

    def test_three_separable_blobs(self):
        # Exercises the k-means++ seeding loop past its first extra seed.
        m = np.array([[1.0, 0.0, 0.0]] * 20 + [[0.0, 1.0, 0.0]] * 20
                     + [[0.0, 0.0, 1.0]] * 20, dtype=np.float32)
        labels, _ = bg.kmeans(m, 3)
        self.assertEqual(len(Counter(labels.tolist())), 3)

    def test_deterministic_across_runs(self):
        # Other callers rely on the seed making runs reproducible.
        m = np.array([[1.0, 0.0]] * 30 + [[0.0, 1.0]] * 30, dtype=np.float32)
        a, _ = bg.kmeans(m, 2)
        b, _ = bg.kmeans(m, 2)
        self.assertTrue(np.array_equal(a, b), "kmeans is no longer deterministic")


if __name__ == "__main__":
    unittest.main()
