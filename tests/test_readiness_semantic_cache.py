import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.readiness_cache import compute_semantic_cache_key


class TestSemanticCacheKey(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_file_returns_empty(self):
        self.assertEqual(compute_semantic_cache_key(self.base_path / "missing.py"), "")

    def test_non_python_strips_whitespace(self):
        file_path = self.base_path / "test.txt"
        file_path.write_text("hello \nworld\n  ", encoding="utf-8")

        # normalized should be "hello\nworld"
        expected = hashlib.sha256(b"hello\nworld").hexdigest()
        self.assertEqual(compute_semantic_cache_key(file_path), expected)

    def test_python_ast_ignores_comments(self):
        file1 = self.base_path / "test1.py"
        file2 = self.base_path / "test2.py"

        file1.write_text("def a():\n    return 1\n", encoding="utf-8")
        file2.write_text("# comment\ndef a():\n    return 1 # inline\n\n", encoding="utf-8")

        self.assertEqual(compute_semantic_cache_key(file1), compute_semantic_cache_key(file2))

    def test_python_ast_ignores_whitespace(self):
        file1 = self.base_path / "test1.py"
        file2 = self.base_path / "test2.py"

        file1.write_text("def a():\n    return 1\n", encoding="utf-8")
        file2.write_text("\n\ndef a():\n\n\n    return 1\n\n", encoding="utf-8")

        self.assertEqual(compute_semantic_cache_key(file1), compute_semantic_cache_key(file2))

    def test_python_ast_detects_semantic_change(self):
        file1 = self.base_path / "test1.py"
        file2 = self.base_path / "test2.py"

        file1.write_text("def a():\n    return 1\n", encoding="utf-8")
        file2.write_text("def a():\n    return 2\n", encoding="utf-8")

        self.assertNotEqual(compute_semantic_cache_key(file1), compute_semantic_cache_key(file2))

    def test_python_syntax_error_fallback(self):
        file1 = self.base_path / "test1.py"

        content = "def a():\n  return 1\n syntax error here"
        file1.write_text(content, encoding="utf-8")

        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.assertEqual(compute_semantic_cache_key(file1), expected)

if __name__ == "__main__":
    unittest.main()
