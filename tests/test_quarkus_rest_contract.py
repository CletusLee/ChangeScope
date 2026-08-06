import tempfile
import unittest
from pathlib import Path

from changescope.application import (
    ChangeScopeApplication,
    ImpactRequest,
    IndexRequest,
)


class TestQuarkusRESTContract(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_direct_jaxrs_endpoint_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-resteasy-reactive</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/OrderResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "import jakarta.ws.rs.PathParam;\n"
                "import jakarta.ws.rs.Produces;\n"
                "import jakarta.ws.rs.core.MediaType;\n"
                "@Path(\"/orders\")\n"
                "public class OrderResource {\n"
                "    @GET\n"
                "    @Path(\"/{id}\")\n"
                "    @Produces(MediaType.APPLICATION_JSON)\n"
                "    public String getOrder(@PathParam(\"id\") String id) {\n"
                "        return \"order-\" + id;\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "OrderResource#getOrder"))
            self.assertEqual(result.outcome, "resolved")

            contracts = [r for r in result.relationships if r.kind == "quarkus_rest_contract"]
            self.assertTrue(len(contracts) > 0)
            contract = contracts[0]
            self.assertIn("GET /orders/{id}", contract.caller)
            self.assertEqual(contract.confidence, "high")

    def test_application_path_and_config_route_construction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-resteasy</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "quarkus.http.root-path=/v1\n",
            )
            self._write(
                repository / "src/main/java/com/example/RestApplication.java",
                "package com.example;\n"
                "import jakarta.ws.rs.ApplicationPath;\n"
                "import jakarta.ws.rs.core.Application;\n"
                "@ApplicationPath(\"/api\")\n"
                "public class RestApplication extends Application {}\n",
            )
            self._write(
                repository / "src/main/java/com/example/UserResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.POST;\n"
                "import jakarta.ws.rs.Path;\n"
                "import jakarta.ws.rs.Consumes;\n"
                "@Path(\"/users\")\n"
                "public class UserResource {\n"
                "    @POST\n"
                "    @Consumes(\"application/json\")\n"
                "    public void createUser(String userJson) {}\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "UserResource#createUser"))
            self.assertEqual(result.outcome, "resolved")

            contracts = [r for r in result.relationships if r.kind == "quarkus_rest_contract"]
            self.assertTrue(len(contracts) > 0)
            contract = contracts[0]
            self.assertIn("POST /v1/api/users", contract.caller)

    def test_flavor_detection_and_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "src/main/java/com/example/ItemResource.java",
                "package com.example;\n"
                "import javax.ws.rs.GET;\n"
                "import javax.ws.rs.Path;\n"
                "@Path(\"/items\")\n"
                "public class ItemResource {\n"
                "    @GET\n"
                "    public String listItems() { return \"items\"; }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "ItemResource#listItems"))
            self.assertEqual(result.outcome, "resolved")

            contracts = [r for r in result.relationships if r.kind == "quarkus_rest_contract"]
            self.assertTrue(len(contracts) > 0)
            contract = contracts[0]
            # Without pom.xml build evidence, confidence is medium / flavor unknown
            self.assertEqual(contract.confidence, "medium")

    def test_media_types_parameters_and_cdi_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-resteasy-reactive</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/PaymentService.java",
                "package com.example;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "@ApplicationScoped\n"
                "public class PaymentService {\n"
                "    public void pay() {}\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/PaymentResource.java",
                "package com.example;\n"
                "import jakarta.inject.Inject;\n"
                "import jakarta.ws.rs.POST;\n"
                "import jakarta.ws.rs.Path;\n"
                "import jakarta.ws.rs.QueryParam;\n"
                "import jakarta.ws.rs.Produces;\n"
                "import jakarta.ws.rs.Consumes;\n"
                "@Path(\"/payments\")\n"
                "public class PaymentResource {\n"
                "    @Inject\n"
                "    PaymentService paymentService;\n"
                "    @POST\n"
                "    @Consumes(\"application/json\")\n"
                "    @Produces(\"application/json\")\n"
                "    public String process(@QueryParam(\"currency\") String currency) {\n"
                "        paymentService.pay();\n"
                "        return \"ok\";\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "PaymentResource#process"))
            self.assertEqual(result.outcome, "resolved")

            contracts = [r for r in result.relationships if r.kind == "quarkus_rest_contract"]
            self.assertTrue(len(contracts) > 0)
            contract = contracts[0]
            self.assertIn("POST /payments", contract.caller)
            self.assertTrue(any("quarkus_rest" in h for h in contract.evidence_chain))
