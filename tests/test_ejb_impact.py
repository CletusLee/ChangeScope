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

    def test_follows_an_inherited_business_interface_method_to_the_session_bean(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/BaseService.java",
                """package example;
public interface BaseService { void run(); }
""",
            )
            self._write(
                repository / "src/main/java/example/ChildService.java",
                """package example;
import javax.ejb.Local;
@Local public interface ChildService extends BaseService {}
""",
            )
            self._write(
                repository / "src/main/java/example/ServiceBean.java",
                """package example;
import javax.ejb.Stateless;
@Stateless public class ServiceBean implements ChildService {
    public void run() {}
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "BaseService#run"))
            child_result = application.execute(ImpactRequest(repository, "ChildService#run"))

            implementation = next(
                relationship
                for relationship in result.relationships
                if relationship.kind == "ejb_business_implementation"
            )
            self.assertEqual(implementation.caller, "example.ServiceBean#run()")
            self.assertEqual(implementation.business_view, "local")
            self.assertTrue(
                any("ChildService.java" in handle for handle in implementation.evidence_chain)
            )
            self.assertEqual(child_result.outcome, "resolved")
            self.assertTrue(
                any(
                    relationship.kind == "ejb_business_implementation"
                    and relationship.caller == "example.ServiceBean#run()"
                    and any("ChildService.java" in handle for handle in relationship.evidence_chain)
                    for relationship in child_result.relationships
                )
            )

    def test_inherits_an_ejb_business_view_from_an_unannotated_child_interface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/InheritedView.java",
                """package example;
import javax.ejb.Local;
@Local interface ParentService { void run(); }
interface ChildService extends ParentService {}
""",
            )
            self._write(
                repository / "src/main/java/example/InheritedViewBean.java",
                """package example;
import javax.ejb.Stateless;
@Stateless class InheritedViewBean implements ChildService { public void run() {} }
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "ChildService#run"))

            self.assertTrue(
                any(
                    relationship.kind == "ejb_business_implementation"
                    and relationship.caller == "example.InheritedViewBean#run()"
                    for relationship in result.relationships
                )
            )

    def test_includes_every_edge_in_a_multi_level_inherited_evidence_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/MultiLevel.java",
                """package example;
import javax.ejb.Local;
@Local interface GrandService { void run(); }
@Local interface ParentService extends GrandService {}
interface ChildService extends ParentService {}
""",
            )
            self._write(
                repository / "src/main/java/example/MultiLevelBean.java",
                """package example;
import javax.ejb.Stateless;
@Stateless class MultiLevelBean implements ChildService { public void run() {} }
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "ChildService#run"))

            implementation = next(
                relationship
                for relationship in result.relationships
                if relationship.kind == "ejb_business_implementation"
            )
            self.assertGreaterEqual(len(implementation.evidence_chain), 5)

            parent_result = application.execute(ImpactRequest(repository, "ParentService#run"))
            parent_implementation = next(
                relationship
                for relationship in parent_result.relationships
                if relationship.kind == "ejb_business_implementation"
            )
            self.assertGreaterEqual(len(parent_implementation.evidence_chain), 5)

    def test_matches_identical_concrete_generic_parameter_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/GenericService.java",
                """package example;
import java.util.List;
import javax.ejb.Local;
@Local interface GenericService { void run(List<String> values); }
""",
            )
            self._write(
                repository / "src/main/java/example/GenericBean.java",
                """package example;
import java.util.List;
import javax.ejb.Stateless;
@Stateless class GenericBean implements GenericService {
    public void run(List<String> values) {}
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "GenericService#run"))

            self.assertTrue(
                any(
                    relationship.kind == "ejb_business_implementation"
                    for relationship in result.relationships
                )
            )

    def test_leaves_unimported_same_named_types_in_different_packages_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(repository / "src/main/java/one/Type.java", "package one; public class Type {}\n")
            self._write(repository / "src/main/java/two/Type.java", "package two; public class Type {}\n")
            self._write(
                repository / "src/main/java/one/Service.java",
                """package one;
import javax.ejb.Local;
@Local public interface Service { void run(Type value); }
""",
            )
            self._write(
                repository / "src/main/java/two/Bean.java",
                """package two;
import one.Service;
import javax.ejb.Stateless;
@Stateless public class Bean implements Service { public void run(Type value) {} }
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "Service#run"))

            self.assertFalse(
                any(
                    relationship.kind == "ejb_business_implementation"
                    for relationship in result.relationships
                )
            )

    def test_leaves_nested_generic_types_in_different_packages_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(repository / "one/Type.java", "package one; public class Type {}\n")
            self._write(repository / "two/Type.java", "package two; public class Type {}\n")
            self._write(
                repository / "one/Service.java",
                """package one;
import java.util.List;
import javax.ejb.Local;
@Local public interface Service { void run(List<Type> value); }
""",
            )
            self._write(
                repository / "two/Bean.java",
                """package two;
import java.util.List;
import one.Service;
import javax.ejb.Stateless;
@Stateless public class Bean implements Service { public void run(List<Type> value) {} }
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "Service#run"))

            self.assertFalse(
                any(
                    relationship.kind == "ejb_business_implementation"
                    for relationship in result.relationships
                )
            )

    def test_terminates_safely_on_an_ejb_interface_inheritance_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Cyclic.java",
                """package example;
import javax.ejb.Local;
@Local interface First extends Second { void run(); }
interface Second extends First {}
""",
            )
            self._write(
                repository / "src/main/java/example/CyclicBean.java",
                """package example;
import javax.ejb.Stateless;
@Stateless class CyclicBean implements First { public void run() {} }
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "First#run"))

            relationships = [
                relationship
                for relationship in result.relationships
                if relationship.kind == "ejb_business_implementation"
            ]
            self.assertEqual(len(relationships), 1)

    def test_does_not_match_an_ejb_method_by_name_when_parameters_differ(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Mismatch.java",
                """package example;
import javax.ejb.Local;
@Local interface MismatchService { void run(String value); }
""",
            )
            self._write(
                repository / "src/main/java/example/MismatchBean.java",
                """package example;
import javax.ejb.Stateless;
@Stateless class MismatchBean implements MismatchService {
    public void run(Integer value) {}
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "MismatchService#run"))

            self.assertFalse(
                any(
                    relationship.kind == "ejb_business_implementation"
                    for relationship in result.relationships
                )
            )
            self.assertTrue(
                any("matching Session Bean" in item.message for item in result.unresolved_items)
            )

    def test_leaves_generic_interface_substitution_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/GenericService.java",
                """package example;
public interface GenericService<T> { void run(T value); }
""",
            )
            self._write(
                repository / "src/main/java/example/StringService.java",
                """package example;
import javax.ejb.Local;
@Local interface StringService extends GenericService<String> {}
""",
            )
            self._write(
                repository / "src/main/java/example/GenericBean.java",
                """package example;
import javax.ejb.Stateless;
@Stateless class GenericBean implements StringService {
    public void run(String value) {}
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "GenericService#run"))

            self.assertFalse(
                any(
                    relationship.kind == "ejb_business_implementation"
                    for relationship in result.relationships
                )
            )
            self.assertTrue(result.unresolved_items)

    def test_does_not_alias_inherited_methods_for_non_ejb_interfaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Plain.java",
                """package example;
interface PlainParent { void run(); }
interface PlainChild extends PlainParent {}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "PlainChild#run"))

            self.assertEqual(result.outcome, "not_found")

    def test_keeps_distinct_direct_and_inherited_overloads_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Overloads.java",
                """package example;
import javax.ejb.Local;
interface OverloadParent { void run(); }
@Local interface OverloadChild extends OverloadParent { void run(int value); }
""",
            )
            self._write(
                repository / "src/main/java/example/OverloadBean.java",
                """package example;
import javax.ejb.Stateless;
@Stateless class OverloadBean implements OverloadChild {
    public void run() {}
    public void run(int value) {}
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "OverloadChild#run"))

            self.assertEqual(result.outcome, "ambiguous")
            self.assertEqual(len(result.candidates), 2)

    def test_leaves_same_named_parameter_types_with_conflicting_imports_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(repository / "src/main/java/a/Type.java", "package a; public class Type {}\n")
            self._write(repository / "src/main/java/b/Type.java", "package b; public class Type {}\n")
            self._write(
                repository / "src/main/java/example/ImportedService.java",
                """package example;
import a.Type;
import javax.ejb.Local;
@Local interface ImportedService { void run(Type value); }
""",
            )
            self._write(
                repository / "src/main/java/example/ImportedBean.java",
                """package example;
import b.Type;
import javax.ejb.Stateless;
@Stateless class ImportedBean implements ImportedService {
    public void run(Type value) {}
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "ImportedService#run"))

            self.assertFalse(
                any(
                    relationship.kind == "ejb_business_implementation"
                    for relationship in result.relationships
                )
            )
            self.assertTrue(result.unresolved_items)

    def test_leaves_same_named_parameter_types_with_conflicting_wildcard_imports_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/WildcardService.java",
                """package example;
import a.*;
import javax.ejb.Local;
@Local interface WildcardService { void run(Type value); }
""",
            )
            self._write(
                repository / "src/main/java/example/WildcardBean.java",
                """package example;
import b.*;
import javax.ejb.Stateless;
@Stateless class WildcardBean implements WildcardService {
    public void run(Type value) {}
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "WildcardService#run"))

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
