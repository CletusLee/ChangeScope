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

    def test_reports_javax_and_jakarta_field_and_setter_injection_and_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Service.java",
                """package example;
import javax.ejb.Local;
@Local interface Service { void run(); }
""",
            )
            self._write(
                repository / "src/main/java/example/ServiceBean.java",
                """package example;
import javax.ejb.Stateless;
@Stateless class ServiceBean implements Service { public void run() {} }
""",
            )
            self._write(
                repository / "src/main/java/example/Consumers.java",
                """package example;
import javax.ejb.EJB;
class JavaxConsumer {
    @EJB private Service service;
    void use() { service.run(); }
}
class JakartaConsumer {
    private Service service;
    @jakarta.ejb.EJB public void setService(Service service) { this.service = service; }
    void use() { service.run(); }
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "Service#run"))

            injections = [
                relationship
                for relationship in result.relationships
                if relationship.kind == "ejb_injection"
            ]
            dispatches = [
                relationship
                for relationship in result.relationships
                if relationship.kind == "ejb_container_dispatch"
            ]
            self.assertEqual(len(injections), 2)
            self.assertEqual(len(dispatches), 2)
            self.assertTrue(all(relationship.confidence == "medium" for relationship in dispatches))
            self.assertTrue(all("invocation:" in relationship.evidence_chain[1] for relationship in dispatches))
            self.assertTrue(all(any(handle.startswith("ejb:") for handle in relationship.evidence_chain) for relationship in injections))

            bean_result = application.execute(ImpactRequest(repository, "ServiceBean#run"))
            self.assertEqual(
                len(
                    [
                        relationship
                        for relationship in bean_result.relationships
                        if relationship.kind == "ejb_container_dispatch"
                    ]
                ),
                2,
            )

    def test_keeps_multiple_ejb_candidates_unresolved_for_an_injection_point(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Ambiguous.java",
                """package example;
import javax.ejb.EJB;
import javax.ejb.Local;
import javax.ejb.Stateless;
@Local interface Service { void run(); }
@Stateless class FirstServiceBean implements Service { public void run() {} }
@Stateless class SecondServiceBean implements Service { public void run() {} }
class Consumer { @EJB private Service service; void use() { service.run(); } }
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "Service#run"))

            self.assertFalse(
                any(
                    relationship.kind == "ejb_container_dispatch"
                    for relationship in result.relationships
                )
            )
            self.assertTrue(
                any("Multiple eligible Session Beans" in item.message for item in result.unresolved_items)
            )

    def test_reports_unique_container_dispatch_without_an_explicit_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/NoCall.java",
                """package example;
import javax.ejb.EJB;
import javax.ejb.Local;
import javax.ejb.Stateless;
@Local interface Service { void run(); }
@Stateless class ServiceBean implements Service { public void run() {} }
class Consumer { @EJB private Service service; }
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "Service#run"))

            dispatch = next(
                relationship
                for relationship in result.relationships
                if relationship.kind == "ejb_container_dispatch"
            )
            self.assertEqual(dispatch.caller, "example.Consumer#service")
            self.assertEqual(dispatch.confidence, "medium")
            self.assertFalse(any(handle.startswith("invocation:") for handle in dispatch.evidence_chain))

    def test_leaves_injected_calls_with_wrong_argument_count_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Arity.java",
                """package example;
import javax.ejb.EJB;
import javax.ejb.Local;
import javax.ejb.Stateless;
@Local interface Service { void run(String value); }
@Stateless class ServiceBean implements Service { public void run(String value) {} }
class Consumer { @EJB private Service service; void use() { service.run(); } }
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "Service#run"))

            self.assertFalse(
                any(
                    relationship.kind == "ejb_container_dispatch"
                    for relationship in result.relationships
                )
            )
            self.assertTrue(any("argument count" in item.message for item in result.unresolved_items))

    def test_leaves_shadowed_injected_receivers_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Shadowed.java",
                """package example;
import javax.ejb.EJB;
import javax.ejb.Local;
import javax.ejb.Stateless;
@Local interface Service { void run(); }
@Stateless class ServiceBean implements Service { public void run() {} }
class Consumer {
    @EJB private Service service;
    void use(Service service) { service.run(); }
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "Service#run"))

            self.assertFalse(
                any(
                    relationship.kind == "ejb_container_dispatch"
                    for relationship in result.relationships
                )
            )
            self.assertTrue(any("receiver scope" in item.message for item in result.unresolved_items))

    def test_withholds_dispatch_when_the_session_bean_method_signature_is_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Signature.java",
                """package example;
import javax.ejb.EJB;
import javax.ejb.Local;
import javax.ejb.Stateless;
@Local interface Service { void run(String value); }
@Stateless class ServiceBean implements Service { public void run(Integer value) {} }
class Consumer { @EJB private Service service; }
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "Service#run"))

            self.assertFalse(
                any(
                    relationship.kind == "ejb_container_dispatch"
                    for relationship in result.relationships
                )
            )
            self.assertTrue(any("matching Session Bean" in item.message for item in result.unresolved_items))

    def test_connects_child_typed_injection_when_targeting_an_inherited_parent_method(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/InheritedInjection.java",
                """package example;
import javax.ejb.EJB;
import javax.ejb.Local;
import javax.ejb.Stateless;
@Local interface ParentService { void run(); }
interface ChildService extends ParentService {}
@Stateless class ServiceBean implements ChildService { public void run() {} }
class Consumer {
    @EJB private ChildService child;
    @EJB private ParentService parent;
    void use() { child.run(); }
    void useParent() { parent.run(); }
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "ParentService#run"))

            self.assertTrue(
                any(
                    relationship.kind == "ejb_injection"
                    and relationship.caller == "example.Consumer#child"
                    for relationship in result.relationships
                )
            )
            dispatch = next(
                relationship
                for relationship in result.relationships
                if relationship.kind == "ejb_container_dispatch"
            )
            self.assertEqual(dispatch.caller, "example.Consumer#use")
            self.assertTrue(any("InheritedInjection.java" in handle for handle in dispatch.evidence_chain))
            self.assertFalse(any("Invocation named run" in item.message for item in result.unresolved_items))

            bean_result = application.execute(ImpactRequest(repository, "ServiceBean#run"))
            self.assertEqual(
                len(
                    [
                        relationship
                        for relationship in bean_result.relationships
                        if relationship.kind == "ejb_container_dispatch"
                    ]
                ),
                2,
            )

    def test_reports_naming_based_ejb_selection_as_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Naming.java",
                """package example;
import javax.ejb.EJB;
import javax.ejb.Local;
@Local interface Service { void run(); }
class Consumer {
    @EJB(beanName = "legacyService") private Service service;
    void use() { service.run(); }
}
class NamedConsumers {
    @EJB(mappedName = "legacy/mapped") private Service mapped;
    @EJB(lookup = "java:global/legacy") private Service lookedUp;
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "Service#run"))

            self.assertFalse(
                any(
                    relationship.kind == "ejb_injection"
                    for relationship in result.relationships
                )
            )
            messages = " ".join(item.message for item in result.unresolved_items)
            self.assertIn("beanName", messages)
            self.assertIn("mappedName", messages)
            self.assertIn("lookup", messages)

    def test_reports_descriptor_backed_session_beans_and_business_views(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/DescriptorService.java",
                """package example;
public interface DescriptorService { void run(); }
""",
            )
            self._write(
                repository / "src/main/java/example/DescriptorBean.java",
                """package example;
public class DescriptorBean { public void run() {} }
""",
            )
            self._write(
                repository / "src/main/resources/META-INF/ejb-jar.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
<ejb-jar xmlns="http://java.sun.com/xml/ns/javaee" version="3.1">
  <enterprise-beans>
    <session>
      <ejb-name>DescriptorService</ejb-name>
      <ejb-class>example.DescriptorBean</ejb-class>
      <session-type>Stateless</session-type>
      <business-local>example.DescriptorService</business-local>
    </session>
  </enterprise-beans>
</ejb-jar>
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "DescriptorService#run"))

            implementation = next(
                relationship
                for relationship in result.relationships
                if relationship.kind == "ejb_business_implementation"
            )
            self.assertEqual(implementation.caller, "example.DescriptorBean#run()")
            self.assertEqual(implementation.business_view, "local")
            self.assertTrue(any("ejb-jar.xml" in handle for handle in implementation.evidence_chain))
            descriptor_evidence = next(
                handle for handle in implementation.evidence_chain if "ejb-jar.xml" in handle
            )
            evidence = application.execute(EvidenceRequest(repository, descriptor_evidence))
            self.assertIn("DescriptorService", evidence.content)

            bean_result = application.execute(ImpactRequest(repository, "DescriptorBean#run"))
            self.assertTrue(
                any(
                    relationship.kind == "ejb_business_implementation"
                    and relationship.caller == "example.DescriptorService#run()"
                    for relationship in bean_result.relationships
                )
            )

    def test_refreshes_descriptor_context_for_changed_ejb_java_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/DescriptorService.java",
                """package example;
public interface DescriptorService { void run(); }
""",
            )
            bean_path = repository / "src/main/java/example/DescriptorBean.java"
            self._write(
                bean_path,
                """package example;
public class DescriptorBean implements DescriptorService { public void run() {} }
""",
            )
            self._write(
                repository / "src/main/resources/META-INF/ejb-jar.xml",
                """<ejb-jar><enterprise-beans><session>
<ejb-name>DescriptorService</ejb-name>
<ejb-class>example.DescriptorBean</ejb-class>
<session-type>Stateless</session-type>
<business-local>example.DescriptorService</business-local>
</session></enterprise-beans></ejb-jar>
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            initial = application.execute(ImpactRequest(repository, "DescriptorBean#run"))
            self.assertFalse(any("class inheritance" in item.message for item in initial.unresolved_items))

            self._write(
                bean_path,
                """package example;
import javax.ejb.Stateless;
@Stateless public class DescriptorBean implements DescriptorService { public void run() {} }
""",
            )
            annotated = application.execute(ImpactRequest(repository, "DescriptorBean#run"))
            self.assertFalse(any("Implicit no-interface" in item.message for item in annotated.unresolved_items))

            self._write(
                repository / "src/main/java/example/DescriptorBase.java",
                """package example;
class DescriptorBase { public void run() {} }
""",
            )
            self._write(
                bean_path,
                """package example;
import javax.ejb.Stateless;
@Stateless public class DescriptorBean extends DescriptorBase implements DescriptorService {
    public void run() {}
}
""",
            )
            inherited = application.execute(ImpactRequest(repository, "DescriptorBean#run"))
            self.assertTrue(any("class inheritance" in item.message for item in inherited.unresolved_items))

    def test_reports_descriptor_ejb_reference_injection_and_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/DescriptorReference.java",
                """package example;
public interface DescriptorService { void run(); }
public class DescriptorBean { public void run() {} }
class Consumer { private DescriptorService service; void use() { service.run(); } }
""",
            )
            self._write(
                repository / "src/main/resources/META-INF/ejb-jar.xml",
                """<ejb-jar xmlns="http://java.sun.com/xml/ns/javaee" version="3.1">
  <enterprise-beans>
    <session>
      <ejb-name>DescriptorService</ejb-name>
      <ejb-class>example.DescriptorBean</ejb-class>
      <session-type>Stateless</session-type>
      <business-local>example.DescriptorService</business-local>
      <ejb-local-ref>
        <ejb-ref-name>ejb/service</ejb-ref-name>
        <ejb-link>DescriptorService</ejb-link>
        <business-local>example.DescriptorService</business-local>
        <injection-target>
          <injection-target-class>example.Consumer</injection-target-class>
          <injection-target-name>service</injection-target-name>
        </injection-target>
      </ejb-local-ref>
    </session>
  </enterprise-beans>
</ejb-jar>
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "DescriptorService#run"))

            self.assertTrue(
                any(
                    relationship.kind == "ejb_injection"
                    and relationship.caller == "example.Consumer#service"
                    for relationship in result.relationships
                )
            )
            dispatch = next(
                relationship
                for relationship in result.relationships
                if relationship.kind == "ejb_container_dispatch"
            )
            self.assertEqual(dispatch.caller, "example.Consumer#use")
            self.assertTrue(any(handle.startswith("ejb:") for handle in dispatch.evidence_chain))

    def test_reports_malformed_descriptor_as_unresolved_index_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            descriptor = repository / "src/main/resources/META-INF/ejb-jar.xml"
            self._write(descriptor, "<ejb-jar><enterprise-beans><session></ejb-jar>")

            index_result = ChangeScopeApplication().execute(IndexRequest(repository))

            self.assertTrue(any(fact.kind == "ejb_unresolved" for fact in index_result.ejb_facts))
            unresolved = next(fact for fact in index_result.ejb_facts if fact.kind == "ejb_unresolved")
            self.assertIn("Malformed EJB deployment descriptor", unresolved.value or "")

    def test_reports_annotation_descriptor_conflicts_as_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/ConflictingDescriptor.java",
                """package example;
import javax.ejb.Local;
import javax.ejb.Stateless;
@Local interface Service { void run(); }
@Stateless class ServiceBean implements Service { public void run() {} }
""",
            )
            self._write(
                repository / "src/main/resources/META-INF/ejb-jar.xml",
                """<ejb-jar xmlns="http://java.sun.com/xml/ns/javaee" version="3.1">
  <enterprise-beans>
    <session>
      <ejb-name>Service</ejb-name>
      <ejb-class>example.ServiceBean</ejb-class>
      <session-type>Stateful</session-type>
      <business-remote>example.Service</business-remote>
    </session>
  </enterprise-beans>
</ejb-jar>
""",
            )
            index_result = ChangeScopeApplication().execute(IndexRequest(repository))

            conflicts = [
                fact.value or ""
                for fact in index_result.ejb_facts
                if fact.kind == "ejb_unresolved"
            ]
            self.assertTrue(any("Conflicting annotation and descriptor" in value for value in conflicts))

    def test_does_not_resolve_an_incomplete_descriptor_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Incomplete.java",
                """package example;
public interface Service { void run(); }
public class ServiceBean { public void run() {} }
""",
            )
            self._write(
                repository / "src/main/resources/META-INF/ejb-jar.xml",
                """<ejb-jar xmlns="http://java.sun.com/xml/ns/javaee">
  <enterprise-beans>
    <session>
      <ejb-class>example.ServiceBean</ejb-class>
      <business-local>example.Service</business-local>
    </session>
  </enterprise-beans>
</ejb-jar>
""",
            )
            application = ChangeScopeApplication()
            index_result = application.execute(IndexRequest(repository))
            self.assertFalse(
                any(
                    fact.kind == "session_bean" and fact.subject == "example.ServiceBean"
                    for fact in index_result.ejb_facts
                )
            )

            result = application.execute(ImpactRequest(repository, "Service#run"))

            self.assertFalse(
                any(
                    relationship.kind == "ejb_business_implementation"
                    for relationship in result.relationships
                )
            )

    def test_does_not_resolve_a_descriptor_reference_without_an_ejb_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Reference.java",
                """package example;
public interface Service { void run(); }
public class ServiceBean { public void run() {} }
class Consumer { private Service service; void use() { service.run(); } }
""",
            )
            self._write(
                repository / "src/main/resources/META-INF/ejb-jar.xml",
                """<ejb-jar xmlns="http://java.sun.com/xml/ns/javaee">
  <enterprise-beans>
    <session>
      <ejb-name>Service</ejb-name>
      <ejb-class>example.ServiceBean</ejb-class>
      <session-type>Stateless</session-type>
      <business-local>example.Service</business-local>
      <ejb-ref>
        <ejb-ref-name>ejb/service</ejb-ref-name>
        <business-local>example.Service</business-local>
        <injection-target>
          <injection-target-class>example.Consumer</injection-target-class>
          <injection-target-name>service</injection-target-name>
        </injection-target>
      </ejb-ref>
    </session>
  </enterprise-beans>
</ejb-jar>
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "Service#run"))

            self.assertFalse(
                any(
                    relationship.kind == "ejb_injection"
                    for relationship in result.relationships
                )
            )
            self.assertTrue(any("no ejb-link" in item.message for item in result.unresolved_items))

    def test_does_not_resolve_descriptor_reference_view_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/ReferenceConflict.java",
                """package example;
public interface Service { void run(); }
public class ServiceBean { public void run() {} }
class Consumer { private Service service; void use() { service.run(); } }
""",
            )
            self._write(
                repository / "src/main/resources/META-INF/ejb-jar.xml",
                """<ejb-jar xmlns="http://java.sun.com/xml/ns/javaee">
  <enterprise-beans>
    <session>
      <ejb-name>Service</ejb-name>
      <ejb-class>example.ServiceBean</ejb-class>
      <session-type>Stateless</session-type>
      <business-remote>example.Service</business-remote>
      <ejb-ref>
        <ejb-ref-name>ejb/service</ejb-ref-name>
        <ejb-link>Service</ejb-link>
        <business-local>example.Service</business-local>
        <injection-target>
          <injection-target-class>example.Consumer</injection-target-class>
          <injection-target-name>service</injection-target-name>
        </injection-target>
      </ejb-ref>
    </session>
  </enterprise-beans>
</ejb-jar>
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "Service#run"))

            self.assertFalse(
                any(
                    relationship.kind == "ejb_injection"
                    for relationship in result.relationships
                )
            )
            self.assertTrue(any("business interface conflicts" in item.message for item in result.unresolved_items))

    def test_reports_ejb_aware_test_wiring_at_medium_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "pom.xml",
                """<project><build><sourceDirectory>src/main/java</sourceDirectory><testSourceDirectory>src/test/java</testSourceDirectory></build></project>
""",
            )
            self._write(
                repository / "src/main/java/example/Service.java",
                """package example;
import javax.ejb.Local;
@Local public interface Service { void run(); }
""",
            )
            self._write(
                repository / "src/main/java/example/ServiceBean.java",
                """package example;
import javax.ejb.Stateless;
@Stateless public class ServiceBean implements Service { public void run() {} }
""",
            )
            self._write(
                repository / "src/test/java/example/ServiceTest.java",
                """package example;
import javax.ejb.EJB;
class ServiceTest {
    @EJB private Service service;
    void verifiesRun() { service.run(); }
}
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "Service#run"))

            test_relationship = next(
                relationship
                for relationship in result.relationships
                if relationship.kind == "ejb_test"
            )
            self.assertEqual(test_relationship.confidence, "medium")
            self.assertIn("ServiceTest#service", test_relationship.caller)
            self.assertTrue(any(handle.startswith("ejb:") for handle in test_relationship.evidence_chain))
            self.assertTrue(
                any(
                    relationship.kind == "ejb_container_dispatch"
                    and relationship.caller == "example.ServiceTest#verifiesRun"
                    for relationship in result.relationships
                )
            )
            self.assertFalse(
                any("Implicit no-interface" in item.message for item in result.unresolved_items)
            )

    def test_reports_unsupported_ejb_container_behavior_as_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Unsupported.java",
                """package example;
import javax.ejb.LocalBean;
import javax.ejb.MessageDriven;
import javax.ejb.Stateless;
class BaseBean { public void run() {} }
interface PlainContract { void run(); }
@Stateless class InheritedBean extends BaseBean { public void run() {} }
@Stateless class NoInterfaceBean { public void run() {} }
@Stateless class UnannotatedContractBean implements PlainContract { public void run() {} }
@LocalBean class LocalBeanService { public void run() {} }
@MessageDriven class MessageBean { public void onMessage() {} }
""",
            )
            self._write(
                repository / "src/main/java/example/JndiClient.java",
                """package example;
import javax.naming.InitialContext;
class JndiClient {
    void lookup() throws Exception { new InitialContext().lookup("java:global/service"); }
    void lookupLink() throws Exception { new InitialContext().lookupLink("java:global/service"); }
    void doLookup() throws Exception { InitialContext.doLookup("java:global/service"); }
}
""",
            )
            self._write(
                repository / "src/main/java/example/DescriptorService.java",
                """package example;
public interface DescriptorService { void run(); }
""",
            )
            self._write(
                repository / "src/main/java/example/DescriptorInheritedBean.java",
                """package example;
class DescriptorBase { public void run() {} }
public class DescriptorInheritedBean extends DescriptorBase implements DescriptorService {
    public void run() {}
}
""",
            )
            self._write(
                repository / "src/main/resources/META-INF/ejb-jar.xml",
                """<ejb-jar><enterprise-beans><session>
<ejb-name>descriptorBean</ejb-name>
<business-local>example.DescriptorService</business-local>
<ejb-class>example.DescriptorInheritedBean</ejb-class>
<session-type>Stateless</session-type>
</session></enterprise-beans></ejb-jar>
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            inherited = application.execute(ImpactRequest(repository, "InheritedBean#run"))
            no_interface = application.execute(ImpactRequest(repository, "NoInterfaceBean#run"))
            unannotated_contract = application.execute(
                ImpactRequest(repository, "UnannotatedContractBean#run")
            )
            local_bean = application.execute(ImpactRequest(repository, "LocalBeanService#run"))
            message = application.execute(ImpactRequest(repository, "MessageBean#onMessage"))
            jndi = application.execute(ImpactRequest(repository, "JndiClient#lookup"))
            jndi_link = application.execute(ImpactRequest(repository, "JndiClient#lookupLink"))
            jndi_static = application.execute(ImpactRequest(repository, "JndiClient#doLookup"))
            descriptor = application.execute(
                ImpactRequest(repository, "DescriptorInheritedBean#run")
            )

            self.assertTrue(any("class inheritance" in item.message for item in inherited.unresolved_items))
            self.assertTrue(any("no-interface" in item.message for item in no_interface.unresolved_items))
            self.assertTrue(
                any("no-interface" in item.message for item in unannotated_contract.unresolved_items)
            )
            self.assertTrue(any("LocalBean" in item.message for item in local_bean.unresolved_items))
            self.assertTrue(any("MessageDriven" in item.message for item in message.unresolved_items))
            self.assertTrue(any("JNDI lookup" in item.message for item in jndi.unresolved_items))
            self.assertTrue(any("JNDI lookup" in item.message for item in jndi_link.unresolved_items))
            self.assertTrue(any("JNDI lookup" in item.message for item in jndi_static.unresolved_items))
            self.assertTrue(any("class inheritance" in item.message for item in descriptor.unresolved_items))

    def test_cli_text_and_json_expose_the_same_ejb_relationships(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Parity.java",
                """package example;
import javax.ejb.EJB;
import javax.ejb.Local;
import javax.ejb.Stateless;
@Local interface Service { void run(); }
@Stateless class ServiceBean implements Service { public void run() {} }
class Consumer { @EJB private Service service; void use() { service.run(); } }
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            text_output = io.StringIO()
            json_output = io.StringIO()
            with patch("changescope.cli.Path.cwd", return_value=repository), redirect_stdout(text_output):
                self.assertEqual(main(["impact", "Service#run", "--format", "text"]), 0)
            with patch("changescope.cli.Path.cwd", return_value=repository), redirect_stdout(json_output):
                self.assertEqual(main(["impact", "Service#run", "--format", "json"]), 0)
            report = json.loads(json_output.getvalue())
            for relationship in report["relationships"]:
                self.assertIn(
                    f"- {relationship['kind']} {relationship['caller']} [{relationship['confidence']}]",
                    text_output.getvalue(),
                )

    @staticmethod
    def _write(path: Path, contents: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
