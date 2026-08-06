"""Fixture-driven coverage for Quarkus Reactive Routes impact analysis.

Acceptance criteria mirror issue #27 of the Quarkus Local Analysis spec.
The tests rely only on the public application service so that they
exercise the same seam as the CLI and any future MCP adapter.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from changescope.application import (
    ChangeScopeApplication,
    ImpactRequest,
    IndexRequest,
)


class TestQuarkusReactiveRoutes(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _build_pom(self, *, artifact: str = "quarkus-reactive-routes") -> str:
        return (
            "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
            f"<artifactId>{artifact}</artifactId></dependency></dependencies></project>"
        )

    # ---------- declarative @Route ----------

    def test_simple_route_with_path_and_methods(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(repository / "pom.xml", self._build_pom())
            self._write(
                repository / "src/main/java/com/example/HelloRoutes.java",
                "package com.example;\n"
                "import io.quarkus.vertx.web.Route;\n"
                "import io.quarkus.vertx.web.Route.HttpMethod;\n"
                "import io.vertx.ext.web.RoutingContext;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "@ApplicationScoped\n"
                "public class HelloRoutes {\n"
                "    @Route(path = \"/hello\", methods = HttpMethod.GET)\n"
                "    void hello(RoutingContext rc) {\n"
                "        rc.response().end(\"hello\");\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))
            result = app.execute(ImpactRequest(repository, "HelloRoutes#hello"))

            self.assertEqual(result.outcome, "resolved")
            routes = [r for r in result.relationships if r.kind == "quarkus_http_route"]
            self.assertEqual(len(routes), 1, routes)
            route = routes[0]
            self.assertIn("GET", route.caller)
            self.assertIn("/hello", route.caller)
            self.assertEqual(route.confidence, "high")
            payload = json.loads(route.business_view or "{}")
            self.assertEqual(payload.get("handler_type"), "NORMAL")
            self.assertEqual(payload.get("produces"), [])
            self.assertEqual(payload.get("consumes"), [])

    def test_route_metadata_carries_produces_consumes_order_and_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(repository / "pom.xml", self._build_pom())
            self._write(
                repository / "src/main/java/com/example/ItemRoutes.java",
                "package com.example;\n"
                "import io.quarkus.vertx.web.Route;\n"
                "import io.quarkus.vertx.web.Route.HttpMethod;\n"
                "import io.vertx.ext.web.RoutingContext;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "@ApplicationScoped\n"
                "public class ItemRoutes {\n"
                "    @Route(\n"
                "        path = \"/items\",\n"
                "        methods = {HttpMethod.POST},\n"
                "        produces = \"application/json\",\n"
                "        consumes = \"application/json\",\n"
                "        order = 5,\n"
                "        type = Route.HandlerType.BLOCKING\n"
                "    )\n"
                "    void handle(RoutingContext rc) {\n"
                "        rc.response().end(\"ok\");\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))
            result = app.execute(ImpactRequest(repository, "ItemRoutes#handle"))

            routes = [r for r in result.relationships if r.kind == "quarkus_http_route"]
            self.assertEqual(len(routes), 1)
            payload = json.loads(routes[0].business_view or "{}")
            self.assertEqual(payload.get("handler_type"), "BLOCKING")
            self.assertEqual(payload.get("order"), 5)
            self.assertIn("application/json", payload.get("produces", []))
            self.assertIn("application/json", payload.get("consumes", []))

    def test_repeatable_route_produces_distinct_relationships(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(repository / "pom.xml", self._build_pom())
            self._write(
                repository / "src/main/java/com/example/RepeatRoutes.java",
                "package com.example;\n"
                "import io.quarkus.vertx.web.Route;\n"
                "import io.quarkus.vertx.web.Route.HttpMethod;\n"
                "import io.vertx.ext.web.RoutingContext;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "@ApplicationScoped\n"
                "public class RepeatRoutes {\n"
                "    @Route(path = \"/first\", methods = HttpMethod.GET)\n"
                "    @Route(path = \"/second\", methods = HttpMethod.POST)\n"
                "    void handle(RoutingContext rc) {\n"
                "        rc.response().end(\"ok\");\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))
            result = app.execute(ImpactRequest(repository, "RepeatRoutes#handle"))

            routes = [r for r in result.relationships if r.kind == "quarkus_http_route"]
            self.assertEqual(len(routes), 2)
            identifiers = sorted(route.caller for route in routes)
            self.assertTrue(any("GET" in ident and "/first" in ident for ident in identifiers))
            self.assertTrue(any("POST" in ident and "/second" in ident for ident in identifiers))

    def test_route_base_prepends_class_path_and_default_produces(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(repository / "pom.xml", self._build_pom())
            self._write(
                repository / "src/main/java/com/example/SimpleRoutes.java",
                "package com.example;\n"
                "import io.quarkus.vertx.web.Route;\n"
                "import io.quarkus.vertx.web.RouteBase;\n"
                "import io.vertx.ext.web.RoutingContext;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "@RouteBase(path = \"simple\", produces = \"text/plain\")\n"
                "@ApplicationScoped\n"
                "public class SimpleRoutes {\n"
                "    @Route(path = \"ping\")\n"
                "    void ping(RoutingContext rc) {\n"
                "        rc.response().end(\"pong\");\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))
            result = app.execute(ImpactRequest(repository, "SimpleRoutes#ping"))

            routes = [r for r in result.relationships if r.kind == "quarkus_http_route"]
            self.assertEqual(len(routes), 1)
            self.assertIn("/simple/ping", routes[0].caller)
            payload = json.loads(routes[0].business_view or "{}")
            self.assertEqual(payload.get("produces"), ["text/plain"])

    # ---------- programmatic Vert.x router registration ----------

    def test_literal_router_registration_connects_handler(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(repository / "pom.xml", self._build_pom())
            self._write(
                repository / "src/main/java/com/example/ProgrammaticRoutes.java",
                "package com.example;\n"
                "import io.quarkus.runtime.StartupEvent;\n"
                "import io.vertx.ext.web.Router;\n"
                "import io.vertx.ext.web.RoutingContext;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "import jakarta.enterprise.event.Observes;\n"
                "@ApplicationScoped\n"
                "public class ProgrammaticRoutes {\n"
                "    public void init(@Observes Router router, StartupEvent ev) {\n"
                "        router.get(\"/programmatic\").handler(this::handle);\n"
                "    }\n"
                "    void handle(RoutingContext rc) {\n"
                "        rc.response().end(\"ok\");\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))
            result = app.execute(ImpactRequest(repository, "ProgrammaticRoutes#handle"))

            routes = [r for r in result.relationships if r.kind == "quarkus_http_route"]
            self.assertEqual(len(routes), 1)
            self.assertIn("/programmatic", routes[0].caller)
            self.assertIn("GET", routes[0].caller)

    def test_dynamic_router_path_remains_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(repository / "pom.xml", self._build_pom())
            self._write(
                repository / "src/main/java/com/example/DynamicRoutes.java",
                "package com.example;\n"
                "import io.quarkus.runtime.StartupEvent;\n"
                "import io.vertx.ext.web.Router;\n"
                "import io.vertx.ext.web.RoutingContext;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "import jakarta.enterprise.event.Observes;\n"
                "@ApplicationScoped\n"
                "public class DynamicRoutes {\n"
                "    public void init(@Observes Router router, StartupEvent ev) {\n"
                "        String prefix = System.getProperty(\"prefix\", \"/dyn\");\n"
                "        router.get(prefix + \"/items\").handler(this::handle);\n"
                "    }\n"
                "    void handle(RoutingContext rc) {\n"
                "        rc.response().end(\"ok\");\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))
            result = app.execute(ImpactRequest(repository, "DynamicRoutes#handle"))

            routes = [r for r in result.relationships if r.kind == "quarkus_http_route"]
            self.assertEqual(len(routes), 0)
            self.assertTrue(
                any("dynamic" in item.message.lower() for item in result.unresolved_items),
                result.unresolved_items,
            )

    def test_regex_route_remains_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(repository / "pom.xml", self._build_pom())
            self._write(
                repository / "src/main/java/com/example/RegexRoutes.java",
                "package com.example;\n"
                "import io.quarkus.vertx.web.Route;\n"
                "import io.vertx.ext.web.RoutingContext;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "@ApplicationScoped\n"
                "public class RegexRoutes {\n"
                "    @Route(regex = \"/items/[^/]+\", methods = Route.HttpMethod.GET)\n"
                "    void handle(RoutingContext rc) {\n"
                "        rc.response().end(\"ok\");\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))
            result = app.execute(ImpactRequest(repository, "RegexRoutes#handle"))

            routes = [r for r in result.relationships if r.kind == "quarkus_http_route"]
            self.assertEqual(len(routes), 0)
            self.assertTrue(
                any("regex" in item.message.lower() for item in result.unresolved_items),
                result.unresolved_items,
            )

    def test_handler_lambda_without_symbol_remains_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(repository / "pom.xml", self._build_pom())
            self._write(
                repository / "src/main/java/com/example/LambdaRoutes.java",
                "package com.example;\n"
                "import io.quarkus.runtime.StartupEvent;\n"
                "import io.vertx.ext.web.Router;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "import jakarta.enterprise.event.Observes;\n"
                "@ApplicationScoped\n"
                "public class LambdaRoutes {\n"
                "    public void init(@Observes Router router, StartupEvent ev) {\n"
                "        router.get(\"/inline\").handler(rc -> rc.response().end(\"ok\"));\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))
            result = app.execute(ImpactRequest(repository, "LambdaRoutes#init"))

            routes = [r for r in result.relationships if r.kind == "quarkus_http_route"]
            self.assertEqual(len(routes), 0)
            self.assertTrue(
                any(
                    "lambda" in item.message.lower() or "handler" in item.message.lower()
                    for item in result.unresolved_items
                ),
                result.unresolved_items,
            )

    # ---------- distinctness from REST contracts ----------

    def test_reactive_route_distinct_from_rest_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(repository / "pom.xml", self._build_pom())
            self._write(
                repository / "src/main/java/com/example/DualRoutes.java",
                "package com.example;\n"
                "import io.quarkus.vertx.web.Route;\n"
                "import io.quarkus.vertx.web.Route.HttpMethod;\n"
                "import io.vertx.ext.web.RoutingContext;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "@ApplicationScoped\n"
                "public class DualRoutes {\n"
                "    @Route(path = \"/reactive\", methods = HttpMethod.GET)\n"
                "    void reactive(RoutingContext rc) {\n"
                "        rc.response().end(\"reactive\");\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))
            result = app.execute(ImpactRequest(repository, "DualRoutes#reactive"))

            kinds = sorted({r.kind for r in result.relationships})
            self.assertIn("quarkus_http_route", kinds)
            self.assertNotIn("quarkus_rest_contract", kinds)
            self.assertEqual(
                [r.kind for r in result.relationships if r.kind == "quarkus_http_route"],
                ["quarkus_http_route"],
            )

    def test_route_facts_appear_in_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(repository / "pom.xml", self._build_pom())
            self._write(
                repository / "src/main/java/com/example/IndexedRoutes.java",
                "package com.example;\n"
                "import io.quarkus.vertx.web.Route;\n"
                "import io.quarkus.vertx.web.Route.HttpMethod;\n"
                "import io.vertx.ext.web.RoutingContext;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "@ApplicationScoped\n"
                "public class IndexedRoutes {\n"
                "    @Route(path = \"/indexed\", methods = HttpMethod.GET)\n"
                "    void handle(RoutingContext rc) {\n"
                "        rc.response().end(\"ok\");\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            index = app.execute(IndexRequest(repository))

            route_facts = [f for f in index.quarkus_route_facts if f.kind == "route_method"]
            self.assertTrue(route_facts, "expected at least one route_method fact")
            payload = json.loads(route_facts[0].value or "{}")
            self.assertEqual(payload.get("path"), "/indexed")


if __name__ == "__main__":
    unittest.main()