from __future__ import annotations

import sys
import types
import importlib.util
import zipfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from xml.etree import ElementTree

from bridge import ocr_worker


class OfficeOxideConversionTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("office_oxide"), "Install the pinned OfficeOxide wheel for the real DOC regression")
    def test_real_binary_doc_preserves_chinese_table_and_image(self) -> None:
        source = Path(__file__).parent / "fixtures" / "legacy-with-table-and-image.doc"
        with TemporaryDirectory() as temporary:
            with (
                mock.patch.object(ocr_worker, "convert_with_word_com", side_effect=RuntimeError("Word unavailable")),
                mock.patch.object(ocr_worker, "libreoffice_executable", return_value=None),
            ):
                converted = ocr_worker.convert_legacy_doc(source, Path(temporary))
            with zipfile.ZipFile(converted) as archive:
                self.assertIsNone(archive.testzip())
                root = ElementTree.fromstring(archive.read("word/document.xml"))
                ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
                text = "".join(node.text or "" for node in root.iter(ns + "t"))
                for expected in ("一种太阳能电池处理设备", "说明书第12段", "END-20261016"):
                    self.assertIn(expected, text)
                self.assertEqual(len(list(root.iter(ns + "tbl"))), 1)
                self.assertTrue(any(name.startswith("word/media/") for name in archive.namelist()))
                self.assertTrue(any(node.tag.endswith("}blip") for node in root.iter()))

    def test_office_oxide_creates_a_docx_through_its_python_binding(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "legacy.doc"
            target = root / "converted.docx"
            source.write_bytes(b"legacy-doc-placeholder")

            class FakeDocument:
                @staticmethod
                def open(path: str):
                    self.assertEqual(path, str(source))
                    return FakeDocument()

                def save_as(self, path: str) -> None:
                    Path(path).write_bytes(b"docx-placeholder")

            fake_module = types.SimpleNamespace(Document=FakeDocument)
            with mock.patch.dict(sys.modules, {"office_oxide": fake_module}):
                converted = ocr_worker.convert_with_office_oxide(source, target)

            self.assertEqual(converted, target)
            self.assertEqual(target.read_bytes(), b"docx-placeholder")

    def test_legacy_conversion_uses_office_oxide_after_word_is_unavailable(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "legacy.doc"
            source.write_bytes(b"legacy-doc-placeholder")
            expected = root / "converted" / "legacy.docx"

            def fake_office_oxide(_source: Path, target: Path) -> Path:
                target.write_bytes(b"converted")
                return target

            with (
                mock.patch.object(ocr_worker, "convert_with_word_com", side_effect=RuntimeError("Word unavailable")),
                mock.patch.object(ocr_worker, "convert_with_office_oxide", side_effect=fake_office_oxide),
                mock.patch.object(ocr_worker, "libreoffice_executable", return_value=None),
            ):
                converted = ocr_worker.convert_legacy_doc(source, root)

            self.assertEqual(converted, expected)
            self.assertTrue(expected.is_file())


if __name__ == "__main__":
    unittest.main()
