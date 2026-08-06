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

    def test_interface_resource_and_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-resteasy-reactive</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/AccountResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "import jakarta.ws.rs.PathParam;\n"
                "@Path(\"/accounts\")\n"
                "public interface AccountResource {\n"
                "    @GET\n"
                "    @Path(\"/{accountId}\")\n"
                "    String getAccount(@PathParam(\"accountId\") String accountId);\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/AccountResourceImpl.java",
                "package com.example;\n"
                "public class AccountResourceImpl implements AccountResource {\n"
                "    @Override\n"
                "    public String getAccount(String accountId) {\n"
                "        return \"account-\" + accountId;\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            # Direct direction 1: Analyze implementation method
            res_impl = app.execute(ImpactRequest(repository, "AccountResourceImpl#getAccount"))
            self.assertEqual(res_impl.outcome, "resolved")
            contracts_impl = [r for r in res_impl.relationships if r.kind == "quarkus_rest_contract"]
            self.assertTrue(len(contracts_impl) > 0)
            self.assertIn("GET /accounts/{accountId}", contracts_impl[0].caller)
            # Evidence chain must contain handles for both interface and implementation
            self.assertTrue(len(contracts_impl[0].evidence_chain) >= 2)

            # Direct direction 2: Analyze interface method
            res_iface = app.execute(ImpactRequest(repository, "AccountResource#getAccount"))
            self.assertEqual(res_iface.outcome, "resolved")
            contracts_iface = [r for r in res_iface.relationships if r.kind == "quarkus_rest_contract"]
            self.assertTrue(len(contracts_iface) > 0)
            self.assertIn("GET /accounts/{accountId}", contracts_iface[0].caller)

    def test_subresource_locator_composition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-resteasy-reactive</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/OrderItemResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "import jakarta.ws.rs.PathParam;\n"
                "public class OrderItemResource {\n"
                "    @GET\n"
                "    @Path(\"/{itemId}\")\n"
                "    public String getItem(@PathParam(\"itemId\") String itemId) { return itemId; }\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/CustomerResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.Path;\n"
                "import jakarta.ws.rs.PathParam;\n"
                "@Path(\"/customers\")\n"
                "public class CustomerResource {\n"
                "    @Path(\"/{customerId}/items\")\n"
                "    public OrderItemResource getItems(@PathParam(\"customerId\") String customerId) {\n"
                "        return new OrderItemResource();\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "OrderItemResource#getItem"))
            self.assertEqual(result.outcome, "resolved")
            contracts = [r for r in result.relationships if r.kind == "quarkus_rest_contract"]
            self.assertTrue(len(contracts) > 0)
            self.assertIn("GET /customers/{customerId}/items/{itemId}", contracts[0].caller)

    def test_ambiguous_subresource_locator(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "src/main/java/com/example/DynamicResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.Path;\n"
                "@Path(\"/dynamic\")\n"
                "public class DynamicResource {\n"
                "    @Path(\"/sub\")\n"
                "    public Object getSubResource() { return null; }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "DynamicResource#getSubResource"))
            self.assertTrue(len(result.unresolved_items) > 0)
            self.assertTrue(any("subresource" in u.message.lower() for u in result.unresolved_items))

    def test_conditional_build_profile_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-resteasy-reactive</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/DevDebugResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "import io.quarkus.arc.profile.IfBuildProfile;\n"
                "@Path(\"/debug\")\n"
                "@IfBuildProfile(\"dev\")\n"
                "public class DevDebugResource {\n"
                "    @GET\n"
                "    public String debugInfo() { return \"debug\"; }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "DevDebugResource#debugInfo"))
            self.assertEqual(result.outcome, "resolved")
            contracts = [r for r in result.relationships if r.kind == "quarkus_rest_contract"]
            self.assertTrue(len(contracts) > 0)
            self.assertIn("dev", contracts[0].business_view)

    def test_reactive_blocking_and_sse_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-resteasy-reactive</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/ReactiveEventResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "import jakarta.ws.rs.Produces;\n"
                "import jakarta.ws.rs.core.MediaType;\n"
                "import io.smallrye.common.annotation.Blocking;\n"
                "import io.smallrye.mutiny.Multi;\n"
                "import io.smallrye.mutiny.Uni;\n"
                "@Path(\"/events\")\n"
                "public class ReactiveEventResource {\n"
                "    @GET\n"
                "    @Path(\"/uni\")\n"
                "    public Uni<String> getUniEvent() { return null; }\n"
                "\n"
                "    @GET\n"
                "    @Path(\"/stream\")\n"
                "    @Produces(MediaType.SERVER_SENT_EVENTS)\n"
                "    public Multi<String> streamEvents() { return null; }\n"
                "\n"
                "    @GET\n"
                "    @Path(\"/blocking\")\n"
                "    @Blocking\n"
                "    public String blockingCall() { return \"done\"; }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            res_uni = app.execute(ImpactRequest(repository, "ReactiveEventResource#getUniEvent"))
            contracts_uni = [r for r in res_uni.relationships if r.kind == "quarkus_rest_contract"]
            self.assertTrue(len(contracts_uni) > 0)
            self.assertIn("reactive", contracts_uni[0].business_view)
            self.assertIn("Uni", contracts_uni[0].business_view)

            res_stream = app.execute(ImpactRequest(repository, "ReactiveEventResource#streamEvents"))
            contracts_stream = [r for r in res_stream.relationships if r.kind == "quarkus_rest_contract"]
            self.assertTrue(len(contracts_stream) > 0)
            self.assertIn("server_sent_events", contracts_stream[0].business_view)

            res_blocking = app.execute(ImpactRequest(repository, "ReactiveEventResource#blockingCall"))
            contracts_blocking = [r for r in res_blocking.relationships if r.kind == "quarkus_rest_contract"]
            self.assertTrue(len(contracts_blocking) > 0)
            self.assertIn("blocking", contracts_blocking[0].business_view)

    def test_unresolved_filters_mappers_and_servlet_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "src/main/java/com/example/LoggingFilter.java",
                "package com.example;\n"
                "import jakarta.ws.rs.container.ContainerRequestFilter;\n"
                "import jakarta.ws.rs.ext.Provider;\n"
                "@Provider\n"
                "public class LoggingFilter implements ContainerRequestFilter {}\n",
            )
            self._write(
                repository / "src/main/java/com/example/ServletResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "import jakarta.ws.rs.core.Context;\n"
                "import jakarta.servlet.http.HttpServletRequest;\n"
                "@Path(\"/servlet\")\n"
                "public class ServletResource {\n"
                "    @GET\n"
                "    public String handle(@Context HttpServletRequest req) { return \"ok\"; }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "ServletResource#handle"))
            self.assertTrue(len(result.unresolved_items) > 0)
            self.assertTrue(
                any("servlet" in u.message.lower() or "filter" in u.message.lower() for u in result.unresolved_items)
            )

