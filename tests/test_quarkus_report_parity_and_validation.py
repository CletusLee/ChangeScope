import tempfile
import unittest
from pathlib import Path

from changescope.application import (
    ChangeScopeApplication,
    EvidenceRequest,
    ImpactRequest,
    IndexRequest,
)
from changescope.cli import _impact_report, _index_report


class TestQuarkusReportParityAndValidation(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_evidence_navigation_all_quarkus_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            sample_file = repository / "src/main/java/com/example/Sample.java"
            self._write(
                sample_file,
                "package com.example;\n"
                "public class Sample {\n"
                "    public void run() {}\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            kinds = [
                "declaration",
                "invocation",
                "spring",
                "ejb",
                "source",
                "quarkus_build",
                "quarkus_config",
                "quarkus_cdi",
                "quarkus_rest",
                "quarkus_route",
                "quarkus_security",
                "quarkus_test",
                "quarkus_native",
                "quarkus_boundary",
            ]

            for kind in kinds:
                handle = f"{kind}:src/main/java/com/example/Sample.java:2-3"
                nav = app.execute(EvidenceRequest(repository, handle, context_lines=1))
                self.assertEqual(nav.evidence_handle, handle)
                self.assertEqual(nav.start_line, 1)
                self.assertIn("class Sample", nav.content)

    def test_text_json_report_parity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-resteasy-reactive</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "%dev.quarkus.datasource.db-kind=postgresql\n",
            )
            self._write(
                repository / "src/main/java/com/example/OrderDTO.java",
                "package com.example;\n"
                "import io.quarkus.runtime.annotations.RegisterForReflection;\n"
                "@RegisterForReflection\n"
                "public class OrderDTO {\n"
                "    public String id;\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/OrderResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "@Path(\"/orders\")\n"
                "public class OrderResource {\n"
                "    @GET\n"
                "    public OrderDTO getOrder() { return new OrderDTO(); }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            index_res = app.execute(IndexRequest(repository))
            index_rep = _index_report(index_res)

            self.assertIn("quarkus_rest_evidence_count", index_rep)
            self.assertIn("quarkus_configuration_evidence_count", index_rep)
            self.assertIn("quarkus_native_evidence_count", index_rep)

            impact_res = app.execute(ImpactRequest(repository, "OrderResource#getOrder", build_profiles=("dev",)))
            impact_rep = _impact_report(impact_res)

            self.assertEqual(impact_rep["outcome"], "resolved")
            self.assertIsNotNone(impact_rep["target"])
            self.assertTrue(len(impact_rep["relationships"]) > 0)
            self.assertTrue(len(impact_rep["assumptions"]) > 0)

            for rel in impact_rep["relationships"]:
                self.assertIn("kind", rel)
                self.assertIn("caller", rel)
                self.assertIn("confidence", rel)
                self.assertIn("evidence_handle", rel)
                self.assertIn("evidence_chain", rel)

    def test_complete_quarkus_public_quickstart_validation(self) -> None:
        """Full vertical validation exercising CDI, Config, REST, REST Client, Routes, Security, Testing, Native Evidence, and Boundaries."""
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-resteasy-reactive</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "%prod.quarkus.http.port=8080\n"
                "quarkus.package.type=native\n",
            )
            self._write(
                repository / "src/main/java/com/example/CustomerDTO.java",
                "package com.example;\n"
                "import io.quarkus.runtime.annotations.RegisterForReflection;\n"
                "@RegisterForReflection\n"
                "public class CustomerDTO {\n"
                "    public String name;\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/CustomerService.java",
                "package com.example;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "@ApplicationScoped\n"
                "public class CustomerService {\n"
                "    public CustomerDTO findCustomer() { return new CustomerDTO(); }\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/CustomerResource.java",
                "package com.example;\n"
                "import jakarta.inject.Inject;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "import io.quarkus.security.Authenticated;\n"
                "@Path(\"/customers\")\n"
                "@Authenticated\n"
                "public class CustomerResource {\n"
                "    @Inject\n"
                "    CustomerService service;\n"
                "    @GET\n"
                "    public CustomerDTO getCustomer() { return service.findCustomer(); }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "CustomerResource#getCustomer", build_profiles=("prod",)))
            self.assertEqual(result.outcome, "resolved")
            rel_kinds = {r.kind for r in result.relationships}
            self.assertIn("quarkus_security_policy", rel_kinds)
            self.assertIn("quarkus_native_dto", rel_kinds)
            self.assertIn("quarkus_native_config", rel_kinds)
            self.assertIn("quarkus_rest_contract", rel_kinds)


if __name__ == "__main__":
    unittest.main()
