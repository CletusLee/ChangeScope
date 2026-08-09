from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from changescope.application import ChangeScopeApplication, ImpactRequest, IndexRequest


class AtomicIndexRefreshTests(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_refresh_rebuilds_quarkus_contract_facts_after_endpoint_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-resteasy-reactive</artifactId></dependency></dependencies></project>",
            )
            source = repository / "src/main/java/example/OrderResource.java"
            self._write(
                source,
                """package example;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
@Path(\"/orders\")
public class OrderResource {
    @GET
    @Path(\"/old\")
    public String getOrder() { return \"old\"; }
}
""",
            )

            application = ChangeScopeApplication()
            first = application.execute(IndexRequest(repository))
            self.assertTrue(first.quarkus_rest_facts)
            initial = application.execute(ImpactRequest(repository, "OrderResource#getOrder"))
            self.assertTrue(any("GET /orders/old" in relationship.caller for relationship in initial.relationships))

            self._write(source, source.read_text(encoding="utf-8").replace('/old', '/new'))

            refreshed = application.execute(ImpactRequest(repository, "OrderResource#getOrder"))

            self.assertTrue(any("GET /orders/new" in relationship.caller for relationship in refreshed.relationships))
            self.assertFalse(any("GET /orders/old" in relationship.caller for relationship in refreshed.relationships))

    def test_refresh_rebuilds_vbnet_facts_after_source_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            source = repository / "src/Legacy.vb"
            self._write(
                source,
                """Public Class LegacyService
    Public Sub Run()
    End Sub
End Class

Public Class LegacyCaller
    Public Sub Execute()
        Dim service As New LegacyService()
        service.Run()
    End Sub
End Class
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            initial = application.execute(ImpactRequest(repository, "LegacyService#Run"))
            self.assertTrue(any("LegacyCaller#Execute" in relationship.caller for relationship in initial.relationships))

            self._write(source, source.read_text(encoding="utf-8").replace("        service.Run()\n", ""))

            refreshed = application.execute(ImpactRequest(repository, "LegacyService#Run"))

            self.assertFalse(any("LegacyCaller#Execute" in relationship.caller for relationship in refreshed.relationships))

    def test_refresh_rebuilds_after_source_root_rename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "pom.xml",
                "<project><build><sourceDirectory>legacy-main</sourceDirectory></build></project>",
            )
            self._write(repository / "legacy-main/example/App.java", "package example; class App { void run() {} }\n")
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            old_path = repository / "legacy-main/example/App.java"
            new_path = repository / "new-main/example/App.java"
            new_path.parent.mkdir(parents=True, exist_ok=True)
            old_path.replace(new_path)
            self._write(
                repository / "pom.xml",
                "<project><build><sourceDirectory>new-main</sourceDirectory></build></project>",
            )

            refreshed = application.execute(ImpactRequest(repository, "App#run"))

            self.assertEqual(refreshed.outcome, "resolved")
            self.assertEqual(refreshed.target.path, Path("new-main/example/App.java"))

    def test_refresh_rebuilds_soap_import_closure_after_xsd_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "wsdl/order.wsdl",
                """<?xml version="1.0"?>
<definitions xmlns="http://schemas.xmlsoap.org/wsdl/"
             xmlns:tns="http://example.org/orders"
             targetNamespace="http://example.org/orders">
  <types><schema xmlns="http://www.w3.org/2001/XMLSchema">
    <import namespace="http://example.org/types" schemaLocation="../schema/types.xsd"/>
  </schema></types>
  <portType name="OrderPortType"><operation name="placeOrder"/></portType>
</definitions>
""",
            )
            schema = repository / "schema/types.xsd"
            self._write(
                schema,
                """<schema xmlns="http://www.w3.org/2001/XMLSchema" targetNamespace="http://example.org/types">
  <complexType name="OrderV1"/>
</schema>
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            application.execute(
                ImpactRequest(
                    repository,
                    soap_wsdl=Path("wsdl/order.wsdl"),
                    soap_port_type="OrderPortType",
                    soap_operation="placeOrder",
                )
            )

            self._write(schema, schema.read_text(encoding="utf-8").replace("OrderV1", "OrderV2"))

            application.execute(
                ImpactRequest(
                    repository,
                    soap_wsdl=Path("wsdl/order.wsdl"),
                    soap_port_type="OrderPortType",
                    soap_operation="placeOrder",
                )
            )

            with closing(sqlite3.connect(repository / ".changescope/index.sqlite")) as connection:
                schema_types = connection.execute(
                    "SELECT subject FROM soap_facts WHERE kind = 'schema_type' ORDER BY subject"
                ).fetchall()
            self.assertEqual(schema_types, [("{http://example.org/types}OrderV2",)])

    def test_failed_refresh_preserves_the_previous_complete_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            source = repository / "src/main/java/example/App.java"
            self._write(source, "package example; class App { void oldMethod() {} }\n")
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            with closing(sqlite3.connect(repository / ".changescope/index.sqlite")) as connection:
                old_hash = connection.execute(
                    "SELECT content_hash FROM source_files WHERE path = ?", (str(Path("src/main/java/example/App.java")),)
                ).fetchone()[0]

            self._write(source, "package example; class App { void newMethod() {} }\n")
            with patch("changescope.application._insert_quarkus_rest_facts", side_effect=RuntimeError("apply failed")):
                with self.assertRaisesRegex(RuntimeError, "apply failed"):
                    application.execute(ImpactRequest(repository, "App#newMethod"))

            with closing(sqlite3.connect(repository / ".changescope/index.sqlite")) as connection:
                current_hash = connection.execute(
                    "SELECT content_hash FROM source_files WHERE path = ?", (str(Path("src/main/java/example/App.java")),)
                ).fetchone()[0]
                declarations = connection.execute(
                    "SELECT name FROM java_declarations WHERE path = ? ORDER BY name",
                    (str(Path("src/main/java/example/App.java")),),
                ).fetchall()
            self.assertEqual(current_hash, old_hash)
            self.assertEqual(declarations, [("App",), ("oldMethod",)])

    def test_refresh_repairs_a_missing_fact_column_before_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            source = repository / "src/main/java/example/App.java"
            self._write(source, "package example; class App { void run() {} }\n")
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            with closing(sqlite3.connect(repository / ".changescope/index.sqlite")) as connection:
                connection.execute("ALTER TABLE quarkus_rest_facts DROP COLUMN flavor")
                connection.commit()

            result = application.execute(ImpactRequest(repository, "App#run"))

            self.assertEqual(result.outcome, "resolved")
            with closing(sqlite3.connect(repository / ".changescope/index.sqlite")) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(quarkus_rest_facts)")
                }
            self.assertIn("flavor", columns)


if __name__ == "__main__":
    unittest.main()
