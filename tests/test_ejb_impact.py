from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from changescope.application import ChangeScopeApplication, EvidenceRequest, ImpactRequest, IndexRequest
from changescope.cli import main


class EJBImpactReportTests(unittest.TestCase):
    def test_reports_a_local_business_interface_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import javax.ejb.Local;

@Local
public interface OrderService {
    void place(String orderId);
}
""",
            )
            self._write(
                repository / "src/main/java/example/OrderServiceBean.java",
                """package example;
import javax.ejb.Stateless;

@Stateless
public class OrderServiceBean implements OrderService {
    public void place(String orderId) {}
}
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "OrderService#place"))

            self.assertEqual(result.outcome, "resolved")
            implementation = next(
                relationship
                for relationship in result.relationships
                if relationship.kind == "ejb_business_implementation"
            )
            self.assertEqual(implementation.caller, "example.OrderServiceBean#place(String)")
            self.assertEqual(implementation.confidence, "high")
            self.assertEqual(implementation.business_view, "local")
            self.assertGreaterEqual(len(implementation.evidence_chain), 2)
            evidence = application.execute(
                EvidenceRequest(repository, implementation.evidence_chain[0])
            )
            self.assertIn("@Local", evidence.content)

    def test_reports_the_business_interface_when_targeting_the_session_bean(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import javax.ejb.Local;
@Local interface OrderService { void place(String orderId); }
""",
            )
            self._write(
                repository / "src/main/java/example/OrderServiceBean.java",
                """package example;
import javax.ejb.Stateless;
@Stateless class OrderServiceBean implements OrderService {
    public void place(String orderId) {}
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "OrderServiceBean#place"))

            self.assertEqual(result.outcome, "resolved")
            implementation = next(
                relationship
                for relationship in result.relationships
                if relationship.kind == "ejb_business_implementation"
            )
            self.assertEqual(implementation.caller, "example.OrderService#place(String)")
            self.assertEqual(implementation.business_view, "local")

    def test_supports_jakarta_remote_and_all_synchronous_session_bean_types(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Contracts.java",
                """package example;
import jakarta.ejb.*;
@Remote interface RemoteService { void run(); }
@Local interface LocalService { void run(); }
""",
            )
            self._write(
                repository / "src/main/java/example/Beans.java",
                """package example;
import jakarta.ejb.*;
@Stateful class RemoteServiceBean implements RemoteService { public void run() {} }
@Singleton class LocalSingleton implements LocalService { public void run() {} }
@Stateless class LocalStateless implements LocalService { public void run() {} }
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            remote_result = application.execute(ImpactRequest(repository, "RemoteService#run"))
            local_result = application.execute(ImpactRequest(repository, "LocalService#run"))

            self.assertEqual(
                next(
                    relationship
                    for relationship in remote_result.relationships
                    if relationship.kind == "ejb_business_implementation"
                ).business_view,
                "remote",
            )
            self.assertTrue(
                any(
                    "outside the local Repository Index" in item.message
                    for item in remote_result.unresolved_items
                )
            )
            self.assertEqual(
                len(
                    [
                        relationship
                        for relationship in local_result.relationships
                        if relationship.kind == "ejb_business_implementation"
                    ]
                ),
                2,
            )
            output = io.StringIO()
            with patch("changescope.cli.Path.cwd", return_value=repository), redirect_stdout(output):
                self.assertEqual(
                    main(["impact", "RemoteService#run", "--format", "json"]),
                    0,
                )
            report = json.loads(output.getvalue())
            relationship = next(
                item
                for item in report["relationships"]
                if item["kind"] == "ejb_business_implementation"
            )
            self.assertEqual(relationship["business_view"], "remote")
            self.assertGreaterEqual(len(relationship["evidence_chain"]), 2)
            self.assertTrue(all(handle.startswith("ejb:") for handle in relationship["evidence_chain"]))

    def test_does_not_treat_same_named_non_ejb_annotations_as_container_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Fake.java",
                """package example;
@interface Local {}
@interface Stateless {}
@Local interface FakeService { void run(); }
@Stateless class FakeBean implements FakeService { public void run() {} }
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "FakeService#run"))

            self.assertFalse(
                any(
                    relationship.kind == "ejb_business_implementation"
                    for relationship in result.relationships
                )
            )

    def test_explicit_non_ejb_import_overrides_a_wildcard_ejb_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Conflicting.java",
                """package example;
import javax.ejb.*;
import other.Stateless;
import other.Local;
@Local interface ConflictingService { void run(); }
@Stateless class ConflictingBean implements ConflictingService { public void run() {} }
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "ConflictingService#run"))

            self.assertFalse(
                any(
                    relationship.kind == "ejb_business_implementation"
                    for relationship in result.relationships
                )
            )

    def test_ignores_import_text_inside_comments_and_string_literals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Commented.java",
                """package example;
class TextHolder { String ignored = "import javax.ejb.*;"; }
/* import javax.ejb.Local; */
@Local interface CommentedService { void run(); }
@Stateless class CommentedBean implements CommentedService { public void run() {} }
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "CommentedService#run"))

            self.assertFalse(
                any(
                    relationship.kind == "ejb_business_implementation"
                    for relationship in result.relationships
                )
            )

    @staticmethod
    def _write(path: Path, contents: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
