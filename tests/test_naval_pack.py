import unittest
import os
import guidance


class TestNavalPack(unittest.TestCase):
    """Task 2: the naval.yaml domain pack — explicit maps layer + core lenses."""

    def _source_path(self):
        base = os.path.dirname(os.path.abspath(guidance.__file__))
        return os.path.join(base, "psyche", "domain_packs", "naval.yaml")

    def test_source_pack_has_seven_maps_and_six_lenses(self):
        pack = guidance._load_pack_file(self._source_path())
        self.assertEqual(pack["domain"], "naval")
        self.assertEqual(len(pack["maps"]), 7, "expected the 7 mental maps")
        self.assertEqual(len(pack["core_lenses"]), 6, "expected the 6 always-on lenses")
        for key, m in pack["maps"].items():
            self.assertIn("name", m, f"map '{key}' missing display name")
        # Still a valid pack consumed by generate_guidance today.
        self.assertIn("diagnostic_questions", pack)
        self.assertIn("search_terms", pack)

    def test_load_domain_pack_resolves_naval_not_general(self):
        # Force a fresh seed-copy so a newly added pack is discoverable.
        dst = os.path.join(guidance.DOMAINS_DIR, "naval.yaml")
        if os.path.exists(dst):
            os.remove(dst)
        guidance._PACKS_SEEDED = False
        pack = guidance.load_domain_pack("naval")
        self.assertEqual(pack.get("domain"), "naval")
        self.assertIn("maps", pack)


if __name__ == "__main__":
    unittest.main()
