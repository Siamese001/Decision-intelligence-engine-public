import tempfile
import unittest
from pathlib import Path

from tools.readiness_cache import compute_semantic_cache_key


class TestSemanticCacheAdversarial(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_adversarial_docstring_mutation(self):
        """Docstrings are AST elements, they MUST trigger a cache invalidation."""
        file1 = self.base_path / "test1.py"
        file2 = self.base_path / "test2.py"

        file1.write_text("def a():\n    '''Old doc'''\n    return 1\n", encoding="utf-8")
        file2.write_text("def a():\n    '''New doc'''\n    return 1\n", encoding="utf-8")

        self.assertNotEqual(compute_semantic_cache_key(file1), compute_semantic_cache_key(file2))

    def test_adversarial_binary_file_as_py(self):
        """Binary files disguised as .py should fallback safely and not crash the AST parser."""
        file1 = self.base_path / "test.py"
        # Write some binary garbage
        with open(file1, "wb") as f:
            f.write(b"\x00\xff\xfe\x10bad ast")

        # Should catch SyntaxError or UnicodeDecodeError and fallback
        result = compute_semantic_cache_key(file1)
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) >= 0)

    def test_adversarial_huge_python_file(self):
        """Huge Python files should parse or fallback without blowing up memory."""
        file1 = self.base_path / "huge.py"
        # 100k lines of comments
        content = "# comment\n" * 100000 + "def a(): pass\n"
        file1.write_text(content, encoding="utf-8")

        result = compute_semantic_cache_key(file1)
        self.assertTrue(len(result) == 64) # valid SHA256

if __name__ == "__main__":
    unittest.main()
