from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from changescope.application import ChangeScopeApplication, IndexRequest


class IndexRepositoryTests(unittest.TestCase):
    def test_indexes_a_conventional_java_project_and_reports_its_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(repository / "pom.xml", "<project />")
            self._write(
                repository / "src/main/java/example/OrderService.java",
                "package example; class OrderService {}\n",
            )
            self._write(
                repository / "src/test/java/example/OrderServiceTest.java",
                "package example; class OrderServiceTest {}\n",
            )
            self._write(repository / "target/Generated.java", "class Generated {}\n")

            result = self._index(repository)

            self.assertEqual(
                result.source_roots,
                (Path("src/main/java"), Path("src/test/java")),
            )
            self.assertEqual(
                result.indexed_files,
                (
                    Path("src/main/java/example/OrderService.java"),
                    Path("src/test/java/example/OrderServiceTest.java"),
                ),
            )
            self.assertIn(Path("target"), result.excluded_directories)
            self.assertEqual(result.read_failures, ())
            self.assertTrue((repository / ".changescope/index.sqlite").is_file())
            self.assertEqual(result.snapshot.repository_root, repository)
            self.assertIsNone(result.snapshot.git_commit)
            self.assertEqual(result.snapshot.working_tree_state, "unavailable")

    def test_cli_indexes_the_current_directory_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(repository / "src/main/java/example/App.java", "class App {}\n")
            environment = os.environ | {
                "PYTHONPATH": str(Path(__file__).parents[1] / "src")
            }

            completed = subprocess.run(
                [sys.executable, "-m", "changescope", "index", "--format", "json"],
                capture_output=True,
                check=False,
                cwd=repository,
                env=environment,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            self.assertEqual(report["source_roots"], ["src/main/java"])
            self.assertEqual(report["indexed_files"], ["src/main/java/example/App.java"])
            self.assertTrue((repository / ".changescope/index.sqlite").is_file())

    def test_indexes_an_irregular_layout_without_vendor_or_build_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(repository / "legacy/orders/OrderService.java", "class OrderService {}\n")
            self._write(repository / "vendor/Library.java", "class Library {}\n")
            self._write(repository / "lib/LegacyLibrary.java", "class LegacyLibrary {}\n")
            self._write(repository / ".svn/Metadata.java", "class Metadata {}\n")
            self._write(repository / "target/Generated.java", "class Generated {}\n")

            result = self._index(repository)

            self.assertEqual(result.source_roots, (Path("."),))
            self.assertEqual(
                result.indexed_files, (Path("legacy/orders/OrderService.java"),)
            )
            self.assertEqual(
                result.excluded_directories,
                (Path(".svn"), Path("lib"), Path("target"), Path("vendor")),
            )

    def test_indexes_eclipse_source_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / ".classpath",
                "<classpath><classpathentry kind=\"src\" path=\"java\" /></classpath>",
            )
            self._write(repository / "java/example/LegacyApp.java", "class LegacyApp {}\n")

            result = self._index(repository)

            self.assertEqual(result.source_roots, (Path("java"),))
            self.assertEqual(result.indexed_files, (Path("java/example/LegacyApp.java"),))

    def test_reports_read_failures_without_aborting_the_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            source = repository / "src/main/java/example/Unreadable.java"
            self._write(source, "class Unreadable {}\n")
            original_read_bytes = Path.read_bytes

            def read_bytes(path: Path) -> bytes:
                if path == source:
                    raise PermissionError("denied")
                return original_read_bytes(path)

            with patch.object(Path, "read_bytes", read_bytes):
                result = self._index(repository)

            self.assertEqual(result.indexed_files, ())
            self.assertEqual(
                result.read_failures, (Path("src/main/java/example/Unreadable.java"),)
            )
            self.assertTrue((repository / ".changescope/index.sqlite").is_file())

    def test_captures_available_git_snapshot_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(repository / "src/main/java/example/App.java", "class App {}\n")
            self._git(repository, "init")
            self._git(repository, "config", "user.email", "test@example.com")
            self._git(repository, "config", "user.name", "Test User")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "fixture")

            result = self._index(repository)

            self.assertIsNotNone(result.snapshot.git_commit)
            self.assertEqual(result.snapshot.working_tree_state, "clean")

    @staticmethod
    def _write(path: Path, contents: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    @staticmethod
    def _index(repository: Path):
        return ChangeScopeApplication().execute(IndexRequest(repository))

    @staticmethod
    def _git(repository: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
