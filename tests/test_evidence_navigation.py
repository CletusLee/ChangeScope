from __future__ import annotations

import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from changescope.application import (
    ChangeScopeApplication,
    EvidenceRequest,
    IndexRequest,
    SourceRequest,
)
from changescope.cli import main


class EvidenceNavigationTests(unittest.TestCase):
    def test_returns_a_bounded_context_for_an_evidence_handle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            source = repository / "src/main/java/example/App.java"
            source.parent.mkdir(parents=True)
            source.write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(
                EvidenceRequest(repository, "invocation:src/main/java/example/App.java:3-3", context_lines=1)
            )

            self.assertEqual(result.path, Path("src/main/java/example/App.java"))
            self.assertEqual((result.start_line, result.end_line), (2, 4))
            self.assertEqual(result.content, "two\nthree\nfour\n")
            self.assertFalse(result.truncated)

    def test_expands_evidence_to_the_enclosing_method(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            source = repository / "src/main/java/example/App.java"
            source.parent.mkdir(parents=True)
            source.write_text(
                "package example;\n\n"
                "class App {\n"
                "    void placeOrder() {\n"
                "        validate();\n"
                "        save();\n"
                "    }\n"
                "}\n",
                encoding="utf-8",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(
                EvidenceRequest(
                    repository,
                    "invocation:src/main/java/example/App.java:5-5",
                    enclosing_symbol=True,
                )
            )

            self.assertEqual((result.start_line, result.end_line), (4, 7))
            self.assertIn("void placeOrder()", result.content)
            self.assertIn("save();", result.content)
            self.assertFalse(result.truncated)

    def test_returns_an_explicit_range_with_an_exact_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            source = repository / "src/main/java/example/App.java"
            source.parent.mkdir(parents=True)
            source.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(
                SourceRequest(repository, Path("src/main/java/example/App.java"), 1, 4, max_characters=8)
            )

            self.assertEqual(result.evidence_handle, "source:src/main/java/example/App.java:1-4")
            self.assertEqual((result.start_line, result.end_line), (1, 2))
            self.assertEqual(result.content, "one\ntwo\n")
            self.assertTrue(result.truncated)
            self.assertEqual(result.continuation_start_line, 3)

    def test_splits_a_large_single_line_with_a_resumable_column(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            source = repository / "src/main/java/example/App.java"
            source.parent.mkdir(parents=True)
            source.write_text("abcdefghij\n", encoding="utf-8")
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            first = application.execute(
                SourceRequest(repository, Path("src/main/java/example/App.java"), 1, 1, max_characters=4)
            )
            second = application.execute(
                SourceRequest(
                    repository, Path("src/main/java/example/App.java"), 1, 1,
                    max_characters=4, start_column=first.continuation_start_column or 0,
                )
            )

            self.assertEqual(first.content, "abcd")
            self.assertEqual((first.continuation_start_line, first.continuation_start_column), (1, 4))
            self.assertEqual(second.content, "efgh")
            self.assertEqual((second.continuation_start_line, second.continuation_start_column), (1, 8))

    def test_expands_evidence_to_an_enclosing_constructor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            source = repository / "src/main/java/example/App.java"
            source.parent.mkdir(parents=True)
            source.write_text(
                "package example;\n\nclass App {\n    App() {\n        initialize();\n    }\n}\n",
                encoding="utf-8",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(
                EvidenceRequest(
                    repository, "invocation:src/main/java/example/App.java:4-4", enclosing_symbol=True
                )
            )

            self.assertEqual((result.start_line, result.end_line), (4, 6))
            self.assertIn("App()", result.content)

    def test_cli_renders_an_explicit_source_range_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            source = repository / "src/main/java/example/App.java"
            source.parent.mkdir(parents=True)
            source.write_text("one\ntwo\n", encoding="utf-8")
            ChangeScopeApplication().execute(IndexRequest(repository))

            with patch("changescope.cli.Path.cwd", return_value=repository):
                with patch("sys.stdout", new_callable=StringIO) as output:
                    exit_code = main(
                        ["source", "src/main/java/example/App.java", "1", "2", "--format", "json"]
                    )

            self.assertEqual(exit_code, 0)
            self.assertIn('"evidence_handle": "source:src/main/java/example/App.java:1-2"', output.getvalue())
