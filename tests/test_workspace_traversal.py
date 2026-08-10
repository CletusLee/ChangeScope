import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from changescope.application import (
    CatalogRegisterMappingRequest,
    CatalogRegisterRepositoryRequest,
    ChangeScopeApplication,
    ImpactRequest,
    IndexRequest,
    RESTChangeTarget,
    SOAPChangeTarget,
)
from changescope.mcp import ChangeScopeMCPServer, MCPServerConfig


class WorkspaceTraversalTests(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _repo(self, root: Path, class_name: str) -> None:
        self._write(
            root / "src/main/java/example/OrderResource.java",
            f"""package example;
            import javax.ws.rs.GET;
            import javax.ws.rs.Path;
            import javax.ws.rs.PathParam;

            @Path("/api")
            public class OrderResource {{
                @GET
                @Path("/orders/{{id}}")
                public String getOrder(@PathParam("id") String id) {{ return "{class_name}"; }}
            }}
            """,
        )

    def _setup_workspace(self, *, mapping: bool = True) -> tuple[Path, Path, Path, ChangeScopeApplication]:
        temporary_directory = tempfile.TemporaryDirectory()
        workspace = Path(temporary_directory.name)
        # Keep the temporary directory alive for the duration of each test.
        self.addCleanup(temporary_directory.cleanup)
        repo_a = workspace / "consumer"
        repo_b = workspace / "provider"
        self._repo(repo_a, "consumer")
        self._repo(repo_b, "provider")
        app = ChangeScopeApplication()
        app.execute(IndexRequest(repo_a))
        app.execute(IndexRequest(repo_b))
        app.execute(CatalogRegisterRepositoryRequest(workspace, "consumer", repo_a))
        app.execute(CatalogRegisterRepositoryRequest(workspace, "provider", repo_b))
        if mapping:
            app.execute(
                CatalogRegisterMappingRequest(
                    workspace,
                    "consumer",
                    "rest",
                    "GET /api/orders/{id}",
                    "provider",
                    "GET /api/orders/{id}",
                    "verified fixture contract mapping",
                )
            )
        return workspace, repo_a, repo_b, app

    def _request(self, workspace: Path, repo_a: Path, **kwargs) -> ImpactRequest:
        repository_id = kwargs.pop("repository_id", "consumer")
        return ImpactRequest(
            repository_root=repo_a,
            rest_target=RESTChangeTarget("GET", "/api/orders/{id}"),
            traversal_mode="verified_workspace",
            workspace_root=workspace,
            repository_id=repository_id,
            **kwargs,
        )

    def test_verified_rest_link_continues_and_retains_both_snapshots(self) -> None:
        workspace, repo_a, _, app = self._setup_workspace()

        result = app.execute(self._request(workspace, repo_a, depth_limit=1))

        self.assertEqual(result.outcome, "resolved")
        self.assertEqual(result.traversed_repositories, ("consumer", "provider"))
        self.assertEqual(
            {repository_id for repository_id, _ in result.repository_snapshots},
            {"consumer", "provider"},
        )
        self.assertTrue(any(relationship.repository_id == "provider" for relationship in result.relationships))
        self.assertEqual(len(result.verified_links), 1)

    def test_unverified_similar_rest_contract_is_unresolved_and_stops(self) -> None:
        workspace, repo_a, _, app = self._setup_workspace(mapping=False)

        result = app.execute(self._request(workspace, repo_a))

        self.assertEqual(result.outcome, "resolved")
        self.assertEqual(result.traversed_repositories, ("consumer",))
        unresolved = "\n".join(item.message for item in result.unresolved_items)
        self.assertIn("Verified Cross-Repository Link", unresolved)
        self.assertTrue(any(item.next_action for item in result.unresolved_items))

    def test_stale_verified_target_is_partial_without_losing_local_impact(self) -> None:
        workspace, repo_a, _, app = self._setup_workspace()
        catalog_db = workspace / ".changescope" / "catalog.sqlite"
        connection = sqlite3.connect(catalog_db)
        connection.execute(
            "UPDATE catalog_repositories SET git_commit = 'stale-target' WHERE repository_id = 'provider'"
        )
        connection.commit()
        connection.close()

        result = app.execute(self._request(workspace, repo_a))

        self.assertEqual(result.outcome, "partial")
        self.assertTrue(any(relationship.repository_id == "consumer" for relationship in result.relationships))
        self.assertEqual(result.traversed_repositories, ("consumer",))
        self.assertIn("stale", "\n".join(item.message for item in result.unresolved_items).lower())

    def test_repository_id_must_match_the_requested_root(self) -> None:
        workspace, repo_a, _, app = self._setup_workspace()

        result = app.execute(
            self._request(workspace, repo_a, repository_id="provider")
        )

        self.assertEqual(result.traversed_repositories, ())
        self.assertTrue(any("does not identify" in item.message for item in result.unresolved_items))

    def test_repository_depth_and_relationship_limits_are_partial(self) -> None:
        workspace, repo_a, _, app = self._setup_workspace()

        depth_limited = app.execute(self._request(workspace, repo_a, depth_limit=0))
        repository_limited = app.execute(self._request(workspace, repo_a, repository_limit=1))
        relationship_limited = app.execute(self._request(workspace, repo_a, relationship_limit=1))
        response_limited = app.execute(self._request(workspace, repo_a, response_limit=1))

        for result in (depth_limited, repository_limited, relationship_limited, response_limited):
            self.assertEqual(result.outcome, "partial")
            self.assertEqual(result.traversed_repositories, ("consumer",))
            self.assertTrue(result.traversal_limited)

    def test_mcp_workspace_traversal_exposes_catalog_authority(self) -> None:
        workspace, repo_a, _, app = self._setup_workspace()
        server = ChangeScopeMCPServer(MCPServerConfig(workspace_root=workspace), application=app)

        payload = server.call_tool(
            "analyze_impact",
            {
                "repository_id": "consumer",
                "target": {
                    "kind": "rest",
                    "http_method": "GET",
                    "path": "/api/orders/{id}",
                },
                "depth_limit": 1,
            },
        )["structuredContent"]

        self.assertEqual(payload["outcome"], "resolved")
        self.assertEqual(payload["traversal"]["mode"], "verified_workspace")
        self.assertEqual(set(payload["snapshots"]), {"consumer", "provider"})
        self.assertTrue(payload["authority"]["verified_links_only"])

        catalog_before = json.dumps(server.read_resource("changescope://catalog"), sort_keys=True)
        server.list_tools()
        catalog_after = json.dumps(server.read_resource("changescope://catalog"), sort_keys=True)
        self.assertEqual(catalog_before, catalog_after)

    def test_verified_soap_link_continues_using_contract_identity_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            repo_a = workspace / "consumer"
            repo_b = workspace / "provider"
            wsdl = """<?xml version="1.0" encoding="UTF-8"?>
            <definitions xmlns="http://schemas.xmlsoap.org/wsdl/"
                         targetNamespace="http://example.org/orders">
                <portType name="OrderPortType"><operation name="placeOrder"/></portType>
            </definitions>"""
            for repository, namespace in ((repo_a, "client"), (repo_b, "service")):
                self._write(repository / "wsdl/order.wsdl", wsdl)
                self._write(
                    repository / f"src/main/java/{namespace}/OrderService.java",
                    """package %s;
                    import javax.jws.WebService;
                    @WebService(targetNamespace = "http://example.org/orders")
                    public class OrderService { public void placeOrder() {} }
                    """ % namespace,
                )
            app = ChangeScopeApplication()
            app.execute(IndexRequest(repo_a))
            app.execute(IndexRequest(repo_b))
            app.execute(CatalogRegisterRepositoryRequest(workspace, "consumer", repo_a))
            app.execute(CatalogRegisterRepositoryRequest(workspace, "provider", repo_b))
            app.execute(
                CatalogRegisterMappingRequest(
                    workspace,
                    "consumer",
                    "soap",
                    "{http://example.org/orders}OrderPortType#placeOrder",
                    "provider",
                    "{http://example.org/orders}OrderPortType#placeOrder",
                    "verified WSDL fixture mapping",
                )
            )

            result = app.execute(
                ImpactRequest(
                    repository_root=repo_a,
                    soap_target=SOAPChangeTarget(Path("wsdl/order.wsdl"), "OrderPortType", "placeOrder"),
                    traversal_mode="verified_workspace",
                    workspace_root=workspace,
                    repository_id="consumer",
                    depth_limit=1,
                )
            )

            self.assertEqual(result.outcome, "resolved")
            self.assertEqual(result.traversed_repositories, ("consumer", "provider"))
            self.assertEqual(len(result.verified_links), 1)


if __name__ == "__main__":
    unittest.main()
