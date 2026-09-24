"""Локальные проверки без DNS-запросов и API-ключа."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from pipeline import filter_existing, build_html, summarize, TARGET
from urlscan_helper import scan_domains


class PipelineTests(unittest.TestCase):
    def test_filter(self):
        good = {"domain": "example.test", "dns_a": ["!ServFail", "192.0.2.1"]}
        rejected = [{}, {"dns_a": []}, {"dns_a": None},
                    {"dns_a": ["", "!NXDOMAIN", None]},
                    {"dns_mx": ["mail.example.test"]}]
        self.assertEqual(filter_existing(rejected + [good]), [good])
        self.assertEqual(filter_existing([]), [])

    def test_demo_report(self):
        results = scan_domains(["example.test", "other.test"], cap=1, demo=True)
        self.assertEqual([r["status"] for r in results], ["done", "skipped"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.html"
            build_html(results, TARGET, out=path, demo=True)
            page = path.read_text(encoding="utf-8")
            self.assertIn("yandex.ru", page)
            self.assertIn("ДЕМО", page)
            self.assertIn("учебные заглушки", page)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            summarize(results, demo=True)
        self.assertIn("реальные сканирования не выполнялись", output.getvalue())


if __name__ == "__main__":
    unittest.main()
