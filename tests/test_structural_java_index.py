from __future__ import annotations

import sqlite3
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from importlib.metadata import version
from pathlib import Path

from changescope.application import ChangeScopeApplication, IndexRequest


class StructuralJavaIndexTests(unittest.TestCase):
    def test_uses_tree_sitter_runtime_compatible_with_java_grammar(self) -> None:
        self.assertEqual(version("tree-sitter"), "0.25.2")

    def test_records_java_declarations_and_explicit_invocation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
public class OrderService {
    public void placeOrder(String orderId) {
        validate(orderId);
        gateway.submit(orderId);
    }

    private void validate(String orderId) {}
}
""",
            )

            result = ChangeScopeApplication().execute(IndexRequest(repository))

            self.assertEqual(
                [
                    (declaration.kind, declaration.qualified_name, declaration.signature)
                    for declaration in result.declarations
                ],
                [
                    ("class", "example.OrderService", "example.OrderService"),
                    ("method", "example.OrderService#placeOrder", "example.OrderService#placeOrder(String)"),
                    ("method", "example.OrderService#validate", "example.OrderService#validate(String)"),
                ],
            )
            self.assertEqual(
                [(invocation.name, invocation.receiver, invocation.caller) for invocation in result.invocations],
                [
                    ("validate", None, "example.OrderService#placeOrder"),
                    ("submit", "gateway", "example.OrderService#placeOrder"),
                ],
            )
            self.assertTrue(all(not declaration.is_test for declaration in result.declarations))
            self.assertEqual(result.parse_failures, ())

            with closing(sqlite3.connect(repository / ".changescope/index.sqlite")) as connection:
                declarations = connection.execute(
                    "SELECT kind, qualified_name, signature, start_line, end_line, is_test "
                    "FROM java_declarations ORDER BY start_line"
                ).fetchall()
                invocations = connection.execute(
                    "SELECT name, receiver, caller, start_line, end_line, is_test "
                    "FROM java_invocations ORDER BY start_line"
                ).fetchall()
            self.assertEqual(
                declarations,
                [
                    ("class", "example.OrderService", "example.OrderService", 2, 9, 0),
                    ("method", "example.OrderService#placeOrder", "example.OrderService#placeOrder(String)", 3, 6, 0),
                    ("method", "example.OrderService#validate", "example.OrderService#validate(String)", 8, 8, 0),
                ],
            )
            self.assertEqual(
                invocations,
                [
                    ("validate", None, "example.OrderService#placeOrder", 4, 4, 0),
                    ("submit", "gateway", "example.OrderService#placeOrder", 5, 5, 0),
                ],
            )

    def test_marks_test_source_facts_without_inferring_test_frameworks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/test/java/example/OrderServiceTest.java",
                """package example;
class OrderServiceTest {
    void placesAnOrder() { new OrderService().placeOrder("42"); }
}
""",
            )

            result = ChangeScopeApplication().execute(IndexRequest(repository))

            self.assertTrue(result.declarations[0].is_test)
            self.assertTrue(result.invocations[0].is_test)
            self.assertEqual(result.invocations[0].name, "placeOrder")

    def test_marks_a_custom_maven_test_source_root_as_test(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "pom.xml",
                """<project><build>
<sourceDirectory>legacy-main</sourceDirectory>
<testSourceDirectory>integration-src</testSourceDirectory>
</build></project>""",
            )
            self._write(
                repository / "legacy-main/example/App.java", "class App {}\n"
            )
            self._write(
                repository / "integration-src/example/AppIntegrationTest.java",
                "class AppIntegrationTest { void runs() {} }\n",
            )

            result = ChangeScopeApplication().execute(IndexRequest(repository))

            facts_by_path = {fact.path: fact for fact in result.declarations}
            self.assertFalse(facts_by_path[Path("legacy-main/example/App.java")].is_test)
            self.assertTrue(
                facts_by_path[Path("integration-src/example/AppIntegrationTest.java")].is_test
            )

    def test_marks_a_custom_gradle_test_source_root_as_test(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "build.gradle",
                """sourceSets {
    main { java { srcDirs = ['legacy-main'] } }
    test { java { srcDirs = ['functional'] } }
}
""",
            )
            self._write(
                repository / "legacy-main/example/App.java", "class App {}\n"
            )
            self._write(
                repository / "functional/example/AppFunctionalTest.java",
                "class AppFunctionalTest { void runs() {} }\n",
            )

            result = ChangeScopeApplication().execute(IndexRequest(repository))

            facts_by_path = {fact.path: fact for fact in result.declarations}
            self.assertFalse(facts_by_path[Path("legacy-main/example/App.java")].is_test)
            self.assertTrue(
                facts_by_path[Path("functional/example/AppFunctionalTest.java")].is_test
            )

    def test_marks_an_eclipse_test_source_root_as_test(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / ".classpath",
                """<classpath>
<classpathentry kind="src" path="java" />
<classpathentry kind="src" path="verification">
  <attributes><attribute name="test" value="true" /></attributes>
</classpathentry>
</classpath>""",
            )
            self._write(repository / "java/example/App.java", "class App {}\n")
            self._write(
                repository / "verification/example/AppVerification.java",
                "class AppVerification { void checks() {} }\n",
            )

            result = ChangeScopeApplication().execute(IndexRequest(repository))

            facts_by_path = {fact.path: fact for fact in result.declarations}
            self.assertFalse(facts_by_path[Path("java/example/App.java")].is_test)
            self.assertTrue(
                facts_by_path[Path("verification/example/AppVerification.java")].is_test
            )

    def test_preserves_distinct_signatures_for_overloaded_methods(self) -> None:
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

            result = ChangeScopeApplication().execute(IndexRequest(repository))

            self.assertEqual(
                [fact.signature for fact in result.declarations if fact.kind == "method"],
                [
                    "example.OrderService#placeOrder(String)",
                    "example.OrderService#placeOrder(String, boolean)",
                ],
            )

    def test_indexes_java_5_enum_and_generic_syntax_without_a_jre(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderState.java",
                """package example;
import java.util.List;
enum OrderState {
    NEW;
    void addAll(List<String> orderIds) { orderIds.add("42"); }
}
""",
            )

            result = ChangeScopeApplication().execute(IndexRequest(repository))

            self.assertEqual(
                [(declaration.kind, declaration.qualified_name) for declaration in result.declarations],
                [("enum", "example.OrderState"), ("method", "example.OrderState#addAll")],
            )
            self.assertEqual(result.invocations[0].name, "add")
            self.assertEqual(result.invocations[0].receiver, "orderIds")

    def test_reports_malformed_java_as_an_explicit_parse_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/Broken.java",
                "class Broken { void run( { }\n",
            )

            result = ChangeScopeApplication().execute(IndexRequest(repository))

            self.assertEqual(len(result.parse_failures), 1)
            issue = result.parse_failures[0]
            self.assertEqual(issue.path, Path("src/main/java/example/Broken.java"))
            self.assertGreaterEqual(issue.start_line, 1)
            self.assertIn("syntax", issue.message)

    def test_indexes_nested_anonymous_class_literals_without_native_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/NestedAnonymous.java",
                """package example;
import java.util.HashMap;
import java.util.Map;

class NestedAnonymous {
    void build(String email, String password) {
        Map<String, Object> param = new HashMap<String, Object>() {
            {
                put("user", new HashMap<String, Object>() {
                    {
                        put("email", email);
                        put("password", password);
                    }
                });
            }
        };
    }
}
""",
            )

            source_root = Path(__file__).resolve().parents[1] / "src"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = os.pathsep.join(
                (str(source_root), environment.get("PYTHONPATH", ""))
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; "
                        "from changescope.application import ChangeScopeApplication, IndexRequest; "
                        "ChangeScopeApplication().execute(IndexRequest(Path.cwd()))"
                    ),
                ],
                cwd=repository,
                env=environment,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)

    @staticmethod
    def _write(path: Path, contents: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
