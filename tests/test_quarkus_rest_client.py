import tempfile
import unittest
from pathlib import Path

from changescope.application import (
    ChangeScopeApplication,
    ImpactRequest,
    IndexRequest,
)


class TestQuarkusRESTClient(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_jakarta_typed_rest_client_with_cdi_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-rest-client-jackson</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "com.example.OrderClient/mp-rest/url=https://orders.example.com\n",
            )
            self._write(
                repository / "src/main/java/com/example/OrderClient.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "import jakarta.ws.rs.PathParam;\n"
                "import org.eclipse.microprofile.rest.client.inject.RegisterRestClient;\n"
                "@Path(\"/orders\")\n"
                "@RegisterRestClient\n"
                "public interface OrderClient {\n"
                "    @GET\n"
                "    @Path(\"/{id}\")\n"
                "    String getOrder(@PathParam(\"id\") String id);\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/OrderService.java",
                "package com.example;\n"
                "import jakarta.inject.Inject;\n"
                "import org.eclipse.microprofile.rest.client.inject.RestClient;\n"
                "public class OrderService {\n"
                "    @Inject\n"
                "    @RestClient\n"
                "    OrderClient client;\n"
                "    public String fetchOrder(String id) {\n"
                "        return client.getOrder(id);\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "OrderClient#getOrder"))
            self.assertEqual(result.outcome, "resolved")

            contracts = [r for r in result.relationships if r.kind in ("quarkus_rest_contract", "quarkus_cdi_dispatch", "quarkus_cdi_injection")]
            self.assertTrue(len(contracts) > 0)

            flavors = [r for r in result.relationships if hasattr(r, 'business_view') and r.business_view and "quarkus_rest_client" in r.business_view]
            self.assertTrue(len(flavors) > 0 or any("quarkus_rest_client" in str(r) for r in result.relationships))

            configs = [r for r in result.relationships if r.kind in ("quarkus_config", "quarkus_config_consumer")]
            self.assertTrue(len(configs) > 0)

    def test_javax_legacy_resteasy_client_with_config_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-resteasy-client</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "quarkus.rest-client.\"user-api\".url=https://users.example.com\n",
            )
            self._write(
                repository / "src/main/java/com/example/UserClient.java",
                "package com.example;\n"
                "import javax.ws.rs.POST;\n"
                "import javax.ws.rs.Path;\n"
                "import org.eclipse.microprofile.rest.client.inject.RegisterRestClient;\n"
                "@Path(\"/users\")\n"
                "@RegisterRestClient(configKey = \"user-api\")\n"
                "public interface UserClient {\n"
                "    @POST\n"
                "    void createUser(String userData);\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/UserService.java",
                "package com.example;\n"
                "import javax.inject.Inject;\n"
                "import org.eclipse.microprofile.rest.client.inject.RestClient;\n"
                "public class UserService {\n"
                "    @Inject\n"
                "    @RestClient\n"
                "    UserClient userClient;\n"
                "    public void registerUser(String data) {\n"
                "        userClient.createUser(data);\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "UserClient#createUser"))
            self.assertEqual(result.outcome, "resolved")

            configs = [r for r in result.relationships if r.kind in ("quarkus_config", "quarkus_config_consumer")]
            self.assertTrue(len(configs) > 0)

    def test_dynamic_provider_unresolved_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-rest-client</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/FilteredClient.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "import org.eclipse.microprofile.rest.client.annotation.RegisterProvider;\n"
                "import org.eclipse.microprofile.rest.client.inject.RegisterRestClient;\n"
                "@Path(\"/filtered\")\n"
                "@RegisterRestClient\n"
                "@RegisterProvider(CustomFilter.class)\n"
                "public interface FilteredClient {\n"
                "    @GET\n"
                "    String getData();\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "FilteredClient#getData"))
            self.assertEqual(result.outcome, "resolved")
            unresolved = [u for u in result.unresolved_items if "filter" in u.message.lower() or "provider" in u.message.lower()]
            self.assertTrue(len(unresolved) > 0)

    def test_evidence_navigation_and_incremental_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-rest-client</artifactId></dependency></dependencies></project>",
            )
            client_file = repository / "src/main/java/com/example/PaymentClient.java"
            self._write(
                client_file,
                "package com.example;\n"
                "import jakarta.ws.rs.POST;\n"
                "import jakarta.ws.rs.Path;\n"
                "import org.eclipse.microprofile.rest.client.inject.RegisterRestClient;\n"
                "@Path(\"/payments\")\n"
                "@RegisterRestClient\n"
                "public interface PaymentClient {\n"
                "    @POST\n"
                "    void pay();\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result1 = app.execute(ImpactRequest(repository, "PaymentClient#pay"))
            self.assertEqual(result1.outcome, "resolved")

            # Incremental update: modify PaymentClient
            self._write(
                client_file,
                "package com.example;\n"
                "import jakarta.ws.rs.POST;\n"
                "import jakarta.ws.rs.Path;\n"
                "import org.eclipse.microprofile.rest.client.inject.RegisterRestClient;\n"
                "@Path(\"/payments\")\n"
                "@RegisterRestClient\n"
                "public interface PaymentClient {\n"
                "    @POST\n"
                "    @Path(\"/v2\")\n"
                "    void pay();\n"
                "}\n",
            )
            app.execute(IndexRequest(repository))
            result2 = app.execute(ImpactRequest(repository, "PaymentClient#pay"))
            self.assertEqual(result2.outcome, "resolved")


if __name__ == "__main__":
    unittest.main()
