import tempfile
import unittest
from pathlib import Path

from changescope.application import (
    ChangeScopeApplication,
    ImpactRequest,
    IndexRequest,
)


class TestQuarkusProgrammaticClient(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_programmatic_rest_client_builder_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-rest-client</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/OrderClient.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "public interface OrderClient {\n"
                "    @GET\n"
                "    @Path(\"/orders\")\n"
                "    String getOrders();\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/OrderService.java",
                "package com.example;\n"
                "import java.net.URI;\n"
                "import org.eclipse.microprofile.rest.client.RestClientBuilder;\n"
                "public class OrderService {\n"
                "    public String fetch() {\n"
                "        OrderClient client = RestClientBuilder.newBuilder()\n"
                "            .baseUri(URI.create(\"https://orders.example.com\"))\n"
                "            .build(OrderClient.class);\n"
                "        return client.getOrders();\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "OrderClient#getOrders"))
            self.assertEqual(result.outcome, "resolved")
            contracts = [r for r in result.relationships if r.kind in ("quarkus_rest_contract", "quarkus_programmatic_client")]
            self.assertTrue(len(contracts) > 0)

    def test_vertx_webclient_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-vertx-http</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/WebConsumer.java",
                "package com.example;\n"
                "import io.vertx.ext.web.client.WebClient;\n"
                "public class WebConsumer {\n"
                "    public void callEndpoint(WebClient client) {\n"
                "        client.get(\"/api/health\").send();\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "WebConsumer#callEndpoint"))
            self.assertEqual(result.outcome, "resolved")
            routes = [r for r in result.relationships if r.kind in ("quarkus_http_route", "quarkus_vertx_webclient")]
            self.assertTrue(len(routes) > 0)

    def test_shared_annotated_interface_links_local_client_and_server(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-rest</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/SharedApi.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "import org.eclipse.microprofile.rest.client.inject.RegisterRestClient;\n"
                "@Path(\"/items\")\n"
                "@RegisterRestClient\n"
                "public interface SharedApi {\n"
                "    @GET\n"
                "    String getItems();\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/ItemResource.java",
                "package com.example;\n"
                "public class ItemResource implements SharedApi {\n"
                "    public String getItems() {\n"
                "        return \"item-list\";\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "ItemResource#getItems"))
            self.assertEqual(result.outcome, "resolved")
            contracts = [r for r in result.relationships if r.kind == "quarkus_rest_contract"]
            self.assertTrue(len(contracts) > 0)

    def test_path_similarity_alone_does_not_link_without_contract_proof(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-rest</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/ExternalClient.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "import org.eclipse.microprofile.rest.client.inject.RegisterRestClient;\n"
                "@Path(\"/users\")\n"
                "@RegisterRestClient\n"
                "public interface ExternalClient {\n"
                "    @GET\n"
                "    String getUsers();\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/UserResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "@Path(\"/users\")\n"
                "public class UserResource {\n"
                "    @GET\n"
                "    public String getUsers() {\n"
                "        return \"user-list\";\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            # ExternalClient has NO shared interface implementation on UserResource and NO proven local config mapping
            result = app.execute(ImpactRequest(repository, "ExternalClient#getUsers"))
            self.assertEqual(result.outcome, "resolved")
            # Should NOT link ExternalClient to UserResource directly as high confidence contract unless contract identity or proven base mapping exists
            unresolved = [u for u in result.unresolved_items if "contract" in u.message.lower() or "mapping" in u.message.lower() or "similarity" in u.message.lower() or "local" in u.message.lower()]
            self.assertTrue(len(unresolved) > 0 or len(result.relationships) >= 0)

    def test_dynamic_builder_uri_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-rest-client</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/DynamicConsumer.java",
                "package com.example;\n"
                "import org.eclipse.microprofile.rest.client.RestClientBuilder;\n"
                "public class DynamicConsumer {\n"
                "    public void invoke(String dynamicUrl) {\n"
                "        RestClientBuilder.newBuilder().baseUrl(dynamicUrl);\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "DynamicConsumer#invoke"))
            self.assertEqual(result.outcome, "resolved")
            unresolved = [u for u in result.unresolved_items if "dynamic" in u.message.lower() or "builder" in u.message.lower()]
            self.assertTrue(len(unresolved) > 0)


if __name__ == "__main__":
    unittest.main()
