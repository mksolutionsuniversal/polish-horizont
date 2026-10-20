"""Unit tests for the public-code safety gate."""
import importlib.util
import pathlib
import unittest

path = pathlib.Path(__file__).resolve().parents[1] / "tools" / "check_repo_hygiene.py"
spec = importlib.util.spec_from_file_location("repo_guard", path)
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class RepositoryGuardTests(unittest.TestCase):
    def test_block_private_paths(self):
        for name in (".env", ".env.production", "keys/server.pem", "data/report.json", "logs/sync.txt", "backup.sqlite"):
            with self.subTest(name=name):
                self.assertIsNotNone(guard.check_path(name))

    def test_allow_public_paths(self):
        for name in (".env.example", "README.md", "docs/architecture.md", "tools/check_repo_hygiene.py"):
            with self.subTest(name=name):
                self.assertIsNone(guard.check_path(name))

    def test_private_key(self):
        self.assertEqual(guard.check_data(b"-----BEGIN " + b"OPENSSH PRIVATE " + b"KEY-----"), "private key")

    def test_regular_document(self):
        self.assertIsNone(guard.check_data(b"PUBLIC CODE ONLY - no real credentials"))


if __name__ == "__main__":
    unittest.main()
