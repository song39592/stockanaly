import unittest
from unittest.mock import patch

import stock_routes


class ResearchTests(unittest.TestCase):
    def test_request_rejects_invalid_code(self):
        with self.assertRaises(Exception):
            stock_routes.ResearchRequest(code="123", name="测试")

    def test_evidence_prompt_keeps_ids(self):
        text = stock_routes.evidence_to_prompt([{
            "id": "ANN-1", "kind": "announcement", "title": "测试公告",
            "published_at": "2026-01-01", "source": "测试源", "content": "测试正文",
        }])
        self.assertIn("[ANN-1]", text)
        self.assertIn("测试正文", text)

    @patch.object(stock_routes, "call_llm", return_value="## 报告\n事实 [BASIC-1]")
    @patch.object(stock_routes, "collect_evidence", return_value=([{
        "id": "BASIC-1", "kind": "company_profile", "title": "简介",
        "published_at": None, "source": "测试源", "source_url": "https://example.com", "content": "主营测试",
    }], {"basic": "ok"}, []))
    def test_research_returns_auditable_payload(self, _collect, _llm):
        markdown, evidence, coverage, notes = stock_routes.research("000001", "测试")
        self.assertTrue(markdown.endswith(stock_routes.DISCLAIMER))
        self.assertEqual(evidence[0]["id"], "BASIC-1")
        self.assertEqual(coverage["basic"], "ok")
        self.assertEqual(notes, [])


if __name__ == "__main__":
    unittest.main()
