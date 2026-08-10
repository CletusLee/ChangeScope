from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from changescope.application import ChangeScopeApplication, ImpactRequest, IndexRequest
from changescope.mcp import ChangeScopeMCPServer, MCPError, MCPServerConfig


class BoundedImpactTests(unittest.TestCase):
    def make_repository(self, root: Path) -> None:
        source = root / 'src/main/java/example/OrderService.java'
        source.parent.mkdir(parents=True)
        source.write_text(
            'package example;\n'
            'class OrderService {\n'
            '    void placeOrder() { validate(); }\n'
            '    void retryOrder() { placeOrder(); }\n'
            '    private void validate() {}\n'
            '}\n',
            encoding='utf-8',
        )
        (source.parent / 'OrderCaller.java').write_text(
            'package example;\n'
            'class OrderCaller {\n'
            '    void place() { new OrderService().placeOrder(); }\n'
            '}\n',
            encoding='utf-8',
        )
        (source.parent / 'OrderFacade.java').write_text(
            'package example;\n'
            'class OrderFacade {\n'
            '    void place() { new example.OrderService().placeOrder(); }\n'
            '}\n',
            encoding='utf-8',
        )
        (source.parent / 'OrderResource.java').write_text(
            'package example;\n'
            'import javax.ws.rs.GET;\n'
            'import javax.ws.rs.Path;\n'
            '@Path("/orders")\n'
            'class OrderResource {\n'
            '    @GET String list() { return "ok"; }\n'
            '}\n',
            encoding='utf-8',
        )
        (source.parent / 'UserResource.java').write_text(
            'package example;\n'
            'import javax.ws.rs.GET;\n'
            'import javax.ws.rs.Path;\n'
            '@Path("/users")\n'
            'class UserResource {\n'
            '    @GET String list() { return "ok"; }\n'
            '}\n',
            encoding='utf-8',
        )

    def test_application_shapes_primary_handles_only_and_context_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            application = ChangeScopeApplication()
            application.execute(IndexRequest(root))

            primary = application.execute(
                ImpactRequest(root, 'OrderService#placeOrder', max_characters=80)
            )
            self.assertEqual(primary.evidence_mode, 'primary')
            self.assertIsNotNone(primary.primary_source_context)
            self.assertIn('placeOrder', primary.primary_source_context.content)
            self.assertLessEqual(len(primary.primary_source_context.content), 80)
            self.assertEqual(len(primary.source_contexts), 1)

            handles_only = application.execute(
                ImpactRequest(root, 'OrderService#placeOrder', evidence_mode='handles_only')
            )
            self.assertEqual(handles_only.evidence_mode, 'handles_only')
            self.assertIsNone(handles_only.primary_source_context)
            self.assertEqual(handles_only.source_contexts, ())
            self.assertTrue(all(relationship.evidence_handle for relationship in handles_only.relationships))

            bundle = application.execute(
                ImpactRequest(
                    root,
                    'OrderService#placeOrder',
                    evidence_mode='context_bundle',
                    max_items=2,
                    max_characters=160,
                )
            )
            self.assertEqual(bundle.evidence_mode, 'context_bundle')
            self.assertGreaterEqual(len(bundle.source_contexts), 1)
            self.assertLessEqual(
                sum(len(context.content) for context in bundle.source_contexts),
                160,
            )

    def test_mcp_publishes_bounded_response_schema_and_rejects_invalid_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            server = ChangeScopeMCPServer(MCPServerConfig(repository_root=root))
            server.call_tool('index_repository', {'repository_id': 'current'})

            tools = {tool['name']: tool for tool in server.list_tools()}
            properties = tools['analyze_impact']['inputSchema']['properties']
            self.assertEqual(
                properties['evidence_mode']['enum'],
                ['handles_only', 'primary', 'context_bundle'],
            )
            self.assertIn('max_items', properties)
            self.assertIn('max_characters', properties)
            self.assertIn({'required': ['rest']}, tools['analyze_impact']['inputSchema']['anyOf'])

            first_contract_page = server.call_tool(
                'discover_contracts',
                {'repository_id': 'current', 'limit': 1},
            )['structuredContent']
            self.assertIsInstance(first_contract_page['next_cursor'], str)
            second_contract_page = server.call_tool(
                'discover_contracts',
                {'repository_id': 'current', 'cursor': first_contract_page['next_cursor']},
            )['structuredContent']
            self.assertEqual(second_contract_page['offset'], 1)

            report = server.call_tool(
                'analyze_impact',
                {
                    'repository_id': 'current',
                    'target': 'OrderService#placeOrder',
                    'evidence_mode': 'handles_only',
                    'max_items': 1,
                },
            )['structuredContent']
            self.assertEqual(report['evidence_mode'], 'handles_only')
            self.assertIsNone(report['primary_source_context'])
            self.assertTrue(report['truncated'])
            self.assertLessEqual(len(report['relationships']), 1)

            with self.assertRaises(MCPError):
                server.call_tool(
                    'analyze_impact',
                    {
                        'repository_id': 'current',
                        'target': 'OrderService#placeOrder',
                        'evidence_mode': 'inline_everything',
                    },
                )

    def test_mcp_source_cursor_is_opaque_and_snapshot_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            server = ChangeScopeMCPServer(MCPServerConfig(repository_root=root))
            server.call_tool('index_repository', {'repository_id': 'current'})
            impact = server.call_tool(
                'analyze_impact',
                {
                    'repository_id': 'current',
                    'target': 'OrderService#placeOrder',
                    'evidence_mode': 'handles_only',
                },
            )['structuredContent']
            first = server.call_tool(
                'get_evidence',
                {
                    'repository_id': 'current',
                    'evidence_handle': impact['target']['evidence_handle'],
                    'max_characters': 4,
                },
            )['structuredContent']
            self.assertTrue(first['truncated'])
            self.assertIsInstance(first['snapshot'], dict)
            self.assertIsInstance(first['next_cursor'], str)
            resumed = server.call_tool(
                'get_evidence',
                {'repository_id': 'current', 'cursor': first['next_cursor']},
            )['structuredContent']
            self.assertNotEqual(resumed['content'], first['content'])
            with self.assertRaises(MCPError):
                server.call_tool(
                    'get_evidence',
                    {
                        'repository_id': 'current',
                        'cursor': first['next_cursor'],
                        'max_characters': 8,
                    },
                )
            (root / 'src/main/java/example/OrderService.java').write_text(
                (root / 'src/main/java/example/OrderService.java').read_text(encoding='utf-8')
                + '// edited after cursor creation\n',
                encoding='utf-8',
            )
            with self.assertRaises(MCPError):
                server.call_tool(
                    'get_evidence',
                    {'repository_id': 'current', 'cursor': first['next_cursor']},
                )


if __name__ == '__main__':
    unittest.main()
