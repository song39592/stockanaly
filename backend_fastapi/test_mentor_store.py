import io
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

import mentor_store


class MentorStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        mentor_store.DATA_DIR = Path(self.temp.name)
        mentor_store.DB_PATH = mentor_store.DATA_DIR / "test.db"
        mentor_store.init_db()

    def tearDown(self):
        self.temp.cleanup()

    def test_text_material_preserves_source_and_deduplicates(self):
        mentor = mentor_store.create_mentor("测试老师")
        material = mentor_store.add_material(mentor["id"], "观点.txt", "控制仓位，逻辑失效就退出".encode())
        self.assertEqual(material["parse_status"], "ok")
        stored = mentor_store.get_material(material["id"])
        self.assertIn("控制仓位", stored["raw_text"])
        with self.assertRaisesRegex(ValueError, "已经上传"):
            mentor_store.add_material(mentor["id"], "重复.txt", "控制仓位，逻辑失效就退出".encode())

    def test_blank_pdf_is_marked_as_needing_ocr(self):
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        stream = io.BytesIO()
        writer.write(stream)
        parsed = mentor_store.parse_material("scan.pdf", stream.getvalue())
        self.assertEqual(parsed["parse_status"], "needs_ocr")
        self.assertEqual(parsed["segments"][0]["source_ref"], "p.1")

    def test_version_promotion_archives_previous_active_version(self):
        mentor = mentor_store.create_mentor("测试老师")
        one = mentor_store.save_skill_version(mentor["id"], {"skillName": "v1"}, "首次")
        e1 = mentor_store.save_evaluation(mentor["id"], one["version"], {"signalCount": 3})
        mentor_store.review_evaluation(e1["id"], "approved", "通过")
        mentor_store.promote_skill(mentor["id"], one["version"])
        two = mentor_store.save_skill_version(mentor["id"], {"skillName": "v2"}, "调整")
        e2 = mentor_store.save_evaluation(mentor["id"], two["version"], {"signalCount": 4})
        mentor_store.review_evaluation(e2["id"], "approved", "通过")
        mentor_store.promote_skill(mentor["id"], two["version"])
        skills = mentor_store.lab_state(mentor["id"])["skill_versions"]
        self.assertEqual(skills[0]["status"], "active")
        self.assertEqual(skills[1]["status"], "archived")


if __name__ == "__main__":
    unittest.main()
