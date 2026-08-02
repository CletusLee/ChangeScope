from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from changescope.application import index_repository


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

            result = index_repository(repository)

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
            self._write(repository / "target/Generated.java", "class Generated {}\n")

            result = index_repository(repository)

            self.assertEqual(result.source_roots, (Path("."),))
            self.assertEqual(
                result.indexed_files, (Path("legacy/orders/OrderService.java"),)
            )
            self.assertEqual(
                result.excluded_directories, (Path("target"), Path("vendor")))

    def test_indexes_eclipse_source_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / ".classpath",
                "<classpath><classpathentry kind=\"src\" path=\"java\" /></classpath>",
            )
            self._write(repository / "java/example/LegacyApp.java", "class LegacyApp {}\n")

            result = index_repository(repository)

            self.assertEqual(result.source_roots, (Path("java"),))
            self.assertEqual(result.indexed_files, (Path("java/example/LegacyApp.java"),))

    @staticmethod
    def _write(path: Path, contents: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
