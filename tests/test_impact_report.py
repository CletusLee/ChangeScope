from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import json
import io
import os
import subprocess
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

from changescope.application import ChangeScopeApplication, ImpactRequest, IndexRequest
from changescope.cli import main


class ImpactReportTests(unittest.TestCase):
    def test_resolves_a_unique_java_method_target_from_the_local_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
class OrderService {
    void placeOrder(String orderId) {}
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertEqual(result.outcome, "resolved")
            self.assertEqual(result.target.signature, "example.OrderService#placeOrder(String)")
            self.assertEqual(result.target.evidence_handle, "declaration:src/main/java/example/OrderService.java:3-3")

    def test_reports_direct_callers_and_tests_with_evidence_and_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
class OrderService {
    void placeOrder(String orderId) {}
    void retryOrder(String orderId) { placeOrder(orderId); }
}
class OrderFacade {
    void place(OrderService service) { new example.OrderService().placeOrder("42"); }
}
""",
            )
            self._write(
                repository / "src/test/java/example/OrderServiceTest.java",
                """package example;
class OrderServiceTest {
    void placesAnOrder() { new example.OrderService().placeOrder("42"); }
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertEqual(
                [(relationship.kind, relationship.caller, relationship.confidence) for relationship in result.relationships],
                [
                    ("possible_caller", "example.OrderService#retryOrder", "medium"),
                    ("direct_caller", "example.OrderFacade#place", "high"),
                    ("direct_test", "example.OrderServiceTest#placesAnOrder", "high"),
                ],
            )
            self.assertEqual(
                result.relationships[0].evidence_handle,
                "invocation:src/main/java/example/OrderService.java:4-4",
            )
            self.assertIn("Structural analysis", result.assumptions[0])

    def test_reports_overloads_as_ambiguous_and_missing_targets_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
class OrderService {
    void placeOrder(String orderId) {}
    void placeOrder(String orderId, boolean express) {}
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            ambiguous = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))
            missing = application.execute(ImpactRequest(repository, "OrderService#cancelOrder"))

            self.assertEqual(ambiguous.outcome, "ambiguous")
            self.assertEqual(len(ambiguous.candidates), 2)
            self.assertEqual(missing.outcome, "not_found")

    def test_leaves_unknown_receivers_as_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
class OrderService {
    static void placeOrder(String orderId) {}
}
class ExternalCaller {
    void retry(OrderService service) { service.placeOrder("42"); }
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertEqual(
                [(relationship.kind, relationship.confidence) for relationship in result.relationships],
                [],
            )
            self.assertIn("does not resolve receiver types", result.unresolved_items[0].message)
            self.assertEqual(
                result.unresolved_items[1].evidence_handle,
                "invocation:src/main/java/example/OrderService.java:6-6",
            )

    def test_cli_renders_the_same_resolved_impact_result_as_json_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
class OrderService {
    void placeOrder(String orderId) {}
    void retryOrder(String orderId) { placeOrder(orderId); }
}
""",
            )
            ChangeScopeApplication().execute(IndexRequest(repository))
            environment = os.environ | {
                "PYTHONPATH": os.pathsep.join(
                    filter(None, (os.environ.get("PYTHONPATH"), str(Path(__file__).parents[1] / "src")))
                )
            }

            json_result = subprocess.run(
                [sys.executable, "-m", "changescope", "impact", "OrderService#placeOrder", "--format", "json"],
                capture_output=True, check=False, cwd=repository, env=environment, text=True,
            )
            text_result = subprocess.run(
                [sys.executable, "-m", "changescope", "impact", "OrderService#placeOrder"],
                capture_output=True, check=False, cwd=repository, env=environment, text=True,
            )

            self.assertEqual(json_result.returncode, 0, json_result.stderr)
            self.assertEqual(text_result.returncode, 0, text_result.stderr)
            report = json.loads(json_result.stdout)
            self.assertEqual(report["outcome"], "resolved")
            self.assertEqual(report["relationships"][0]["caller"], "example.OrderService#retryOrder")
            self.assertIn("Resolved target: example.OrderService#placeOrder(String)", text_result.stdout)
            self.assertIn("example.OrderService#retryOrder", text_result.stdout)
            self.assertIn("Snapshot:", text_result.stdout)

    def test_cli_returns_nonzero_for_a_non_resolved_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                "class OrderService { void placeOrder() {} }\n",
            )
            ChangeScopeApplication().execute(IndexRequest(repository))

            with patch("changescope.cli.Path.cwd", return_value=repository):
                with redirect_stdout(io.StringIO()):
                    exit_code = main(["impact", "OrderService#missing", "--format", "json"])

            self.assertEqual(exit_code, 2)

    @staticmethod
    def _write(path: Path, contents: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
