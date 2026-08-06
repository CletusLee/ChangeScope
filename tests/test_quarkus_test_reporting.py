import tempfile
import unittest
from pathlib import Path

from changescope.application import (
    ChangeScopeApplication,
    ImpactRequest,
    IndexRequest,
)


class TestQuarkusTestReporting(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_inject_mock_wiring(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-junit5</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/MyService.java",
                "package com.example;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "@ApplicationScoped\n"
                "public class MyService {\n"
                "    public String serve() { return \"real\"; }\n"
                "}\n",
            )
            self._write(
                repository / "src/test/java/com/example/MyServiceTest.java",
                "package com.example;\n"
                "import io.quarkus.test.junit.QuarkusTest;\n"
                "import io.quarkus.test.InjectMock;\n"
                "import org.junit.jupiter.api.Test;\n"
                "@QuarkusTest\n"
                "public class MyServiceTest {\n"
                "    @InjectMock\n"
                "    MyService myService;\n"
                "    @Test\n"
                "    public void testServe() {}\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "MyService#serve"))
            self.assertEqual(result.outcome, "resolved")
            test_rels = [r for r in result.relationships if "MyServiceTest" in r.caller or "MyServiceTest" in str(r)]
            self.assertTrue(len(test_rels) > 0)
            self.assertEqual(test_rels[0].confidence, "high")

    def test_test_http_endpoint_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-junit5</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/UserResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "@Path(\"/users\")\n"
                "public class UserResource {\n"
                "    @GET\n"
                "    public String getUsers() { return \"users\"; }\n"
                "}\n",
            )
            self._write(
                repository / "src/test/java/com/example/UserResourceTest.java",
                "package com.example;\n"
                "import io.quarkus.test.junit.QuarkusTest;\n"
                "import io.quarkus.test.common.http.TestHTTPEndpoint;\n"
                "import org.junit.jupiter.api.Test;\n"
                "@QuarkusTest\n"
                "@TestHTTPEndpoint(UserResource.class)\n"
                "public class UserResourceTest {\n"
                "    @Test\n"
                "    public void testEndpoint() {}\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "UserResource#getUsers"))
            self.assertEqual(result.outcome, "resolved")
            test_rels = [r for r in result.relationships if "UserResourceTest" in r.caller or "UserResourceTest" in str(r)]
            self.assertTrue(len(test_rels) > 0)

    def test_test_http_resource_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-junit5</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/OrderResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "@Path(\"/api/orders\")\n"
                "public class OrderResource {\n"
                "    @GET\n"
                "    public String getOrders() { return \"orders\"; }\n"
                "}\n",
            )
            self._write(
                repository / "src/test/java/com/example/OrderResourceTest.java",
                "package com.example;\n"
                "import io.quarkus.test.junit.QuarkusTest;\n"
                "import io.quarkus.test.common.http.TestHTTPResource;\n"
                "import java.net.URL;\n"
                "import org.junit.jupiter.api.Test;\n"
                "@QuarkusTest\n"
                "public class OrderResourceTest {\n"
                "    @TestHTTPResource(\"/api/orders\")\n"
                "    URL url;\n"
                "    @Test\n"
                "    public void testOrders() {}\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "OrderResource#getOrders"))
            self.assertEqual(result.outcome, "resolved")
            test_rels = [r for r in result.relationships if "OrderResourceTest" in r.caller or "OrderResourceTest" in str(r)]
            self.assertTrue(len(test_rels) > 0)

    def test_rest_assured_literal_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-junit5</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/PingResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "@Path(\"/ping\")\n"
                "public class PingResource {\n"
                "    @GET\n"
                "    public String ping() { return \"pong\"; }\n"
                "}\n",
            )
            self._write(
                repository / "src/test/java/com/example/PingTest.java",
                "package com.example;\n"
                "import io.quarkus.test.junit.QuarkusTest;\n"
                "import static io.restassured.RestAssured.given;\n"
                "import org.junit.jupiter.api.Test;\n"
                "@QuarkusTest\n"
                "public class PingTest {\n"
                "    @Test\n"
                "    public void testPing() {\n"
                "        given().get(\"/ping\").then().statusCode(200);\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "PingResource#ping"))
            self.assertEqual(result.outcome, "resolved")
            test_rels = [r for r in result.relationships if "PingTest" in r.caller or "PingTest" in str(r)]
            self.assertTrue(len(test_rels) > 0)

    def test_blackbox_integration_test_medium_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-junit5</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/ItemResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "@Path(\"/items\")\n"
                "public class ItemResource {\n"
                "    @GET\n"
                "    public String getItems() { return \"items\"; }\n"
                "}\n",
            )
            self._write(
                repository / "src/test/java/com/example/ItemIT.java",
                "package com.example;\n"
                "import io.quarkus.test.junit.QuarkusIntegrationTest;\n"
                "import io.quarkus.test.common.http.TestHTTPEndpoint;\n"
                "import org.junit.jupiter.api.Test;\n"
                "@QuarkusIntegrationTest\n"
                "@TestHTTPEndpoint(ItemResource.class)\n"
                "public class ItemIT {\n"
                "    @Test\n"
                "    public void testIT() {}\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "ItemResource#getItems"))
            self.assertEqual(result.outcome, "resolved")
            test_rels = [r for r in result.relationships if "ItemIT" in r.caller or "ItemIT" in str(r)]
            self.assertTrue(len(test_rels) > 0)
            self.assertEqual(test_rels[0].confidence, "medium")

    def test_dynamic_mock_installation_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-junit5</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/test/java/com/example/DynamicMockTest.java",
                "package com.example;\n"
                "import io.quarkus.test.junit.QuarkusMock;\n"
                "import io.quarkus.test.junit.QuarkusTest;\n"
                "import org.junit.jupiter.api.BeforeEach;\n"
                "import org.mockito.Mockito;\n"
                "@QuarkusTest\n"
                "public class DynamicMockTest {\n"
                "    @BeforeEach\n"
                "    public void setup() {\n"
                "        QuarkusMock.installMockForType(Mockito.mock(MyService.class), MyService.class);\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "DynamicMockTest"))
            self.assertEqual(result.outcome, "resolved")
            unresolved = [u for u in result.unresolved_items if "dynamic mock" in u.message.lower() or "quarkusmock" in u.message.lower()]
            self.assertTrue(len(unresolved) > 0)


if __name__ == "__main__":
    unittest.main()
