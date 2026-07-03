"""Humanized source titles/authors (T6): embedded metadata > cleaned filename,
and no literal 'Unknown' stored for missing authors."""
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ingest import clean_title_from_filename, resolve_source_meta
from parsers import extract_metadata, _clean_meta_field


def _make_epub(path, title="The Real Title", creator="Jane Author"):
    opf = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title><dc:creator>{creator}</dc:creator>
  </metadata>
  <manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="c1"/></spine>
</package>"""
    container = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml", container)
        z.writestr("content.opf", opf)
        z.writestr("c1.xhtml", "<html><body><p>Enough text to matter.</p></body></html>")


class TestCleanTitle(unittest.TestCase):
    def test_timestamp_prefix_stripped_and_camelcase_split(self):
        self.assertEqual(clean_title_from_filename("1667121038Craft.pdf"), "Craft")
        self.assertEqual(clean_title_from_filename("1667121038DeepWork.pdf"), "Deep Work")

    def test_hash_names_become_untitled(self):
        t = clean_title_from_filename("2E5Acdc530578Ab1E8F102C0E1914F7B77801Be2.pdf")
        self.assertTrue(t.startswith("Untitled ("), t)
        self.assertNotIn("2E5Acdc530578", t)

    def test_normal_names_still_prettified(self):
        self.assertEqual(clean_title_from_filename("the-almanack_of-naval.epub"), "The Almanack Of Naval")


class TestMetaField(unittest.TestCase):
    def test_junk_rejected(self):
        # Real junk observed in Aniket's 128-source corpus dry-run.
        for junk in (None, "", "  ", "untitled", "Unknown", "book.pdf", "final.indd",
                     "runfm.dvi", "**Final_The War of Art_6x9_Final**", "me", "x" * 201):
            self.assertIsNone(_clean_meta_field(junk), repr(junk))

    def test_real_values_kept(self):
        self.assertEqual(_clean_meta_field(" Deep Work "), "Deep Work")
        self.assertEqual(_clean_meta_field("Sapiens: A Brief History of Humankind - PDFDrive.com"),
                         "Sapiens: A Brief History of Humankind")


class TestExtractMetadata(unittest.TestCase):
    def test_epub_title_and_creator(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "8f2c9a11b3.epub")
            _make_epub(p)
            meta = extract_metadata(p)
            self.assertEqual(meta.get("title"), "The Real Title")
            self.assertEqual(meta.get("author"), "Jane Author")

    def test_unsupported_type_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "notes.md")
            open(p, "w").write("# hi")
            self.assertEqual(extract_metadata(p), {})

    def test_pdf_metadata_when_fitz_available(self):
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF not installed")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "1667121038Craft.pdf")
            doc = fitz.open()
            doc.new_page().insert_text((72, 72), "hello world")
            doc.set_metadata({"title": "Craft: Real Title", "author": "Real Author"})
            doc.save(p)
            meta = extract_metadata(p)
            self.assertEqual(meta.get("title"), "Craft: Real Title")
            self.assertEqual(meta.get("author"), "Real Author")


class TestResolveSourceMeta(unittest.TestCase):
    def test_precedence_override_metadata_filename(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "9a3b_notes.epub")
            _make_epub(p, title="Meta Title", creator="Meta Author")
            # metadata wins over filename
            self.assertEqual(resolve_source_meta(p), ("Meta Title", "Meta Author"))
            # explicit override wins over metadata
            self.assertEqual(resolve_source_meta(p, "My Title", "Me"), ("My Title", "Me"))

    def test_author_is_none_not_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "plain-notes.md")
            open(p, "w").write("# hi")
            title, author = resolve_source_meta(p)
            self.assertEqual(title, "Plain Notes")
            self.assertIsNone(author)


if __name__ == "__main__":
    unittest.main()
