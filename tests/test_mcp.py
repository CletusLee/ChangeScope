from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from changescope.application import (
    CatalogRegisterRepositoryRequest,
    ChangeScopeApplication,
)
from changescope.mcp import ChangeScopeMCPServer, MCPError, MCPServerConfig


class MCPIntegrationTests(unittest.TestCase):
    def make_repository(self, root: Path) -> None:
        source = root / 'src/main/java/example/OrderService.java'
        source.parent.mkdir(parents=True)
        source.write_text(
            'package example;\n'
            'public class OrderService {\n'
            '  public void placeOrder() {\n'
            '    save();\n'
            '  }\n'
            '  private void save() {}\n'
            '}\n',
            encoding='utf-8',
        )

    def test_registration_resources_and_configured_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = ChangeScopeMCPServer(MCPServerConfig(repository_root=root))
            self.assertEqual(
                [tool['name'] for tool in server.list_tools()],
                ['index_repository', 'analyze_impact', 'get_evidence', 'read_source_range'],
            )
            self.assertEqual(len(server.list_resources()), 3)
            response = server.handle_request(
                {
                    'jsonrpc': '2.0',
                    'id': 1,
                    'method': 'tools/call',
                    'params': {
                        'name': 'index_repository',
                        'arguments': {'repository_id': 'other'},
                    },
                }
            )
            self.assertEqual(response['error']['code'], -32602)
            self.assertNotIn('repository_root', response['error'].get('data', {}))

    def test_explicit_indexing_progress_and_missing_index_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            server = ChangeScopeMCPServer(MCPServerConfig(repository_root=root))
            missing = server.call_tool(
                'analyze_impact',
                {'repository_id': 'current', 'target': 'OrderService#placeOrder'},
            )
            self.assertEqual(missing['structuredContent']['outcome'], 'index_missing')
            self.assertEqual(
                missing['structuredContent']['next_action']['tool'],
                'index_repository',
            )

            progress: list[dict[str, object]] = []
            indexed = server.call_tool(
                'index_repository',
                {'repository_id': 'current'},
                progress_token='index-1',
                progress_sink=progress.append,
            )
            self.assertEqual(indexed['structuredContent']['outcome'], 'indexed')
            self.assertEqual(
                [item['params']['message'] for item in progress],
                ['discovery', 'parsing', 'framework_analysis', 'persistence', 'complete'],
            )
            status = server.read_resource('changescope://repositories/current/status')
            status_payload = json.loads(status['contents'][0]['text'])
            self.assertEqual(status_payload['outcome'], 'ready')
            self.assertGreater(status_payload['indexed_file_count'], 0)

    def test_impact_evidence_source_and_stdio_serialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            server = ChangeScopeMCPServer(MCPServerConfig(repository_root=root))
            server.call_tool('index_repository', {'repository_id': 'current'})

            impact = server.call_tool(
                'analyze_impact',
                {'repository_id': 'current', 'target': 'OrderService#placeOrder'},
            )
            report = impact['structuredContent']
            self.assertEqual(report['outcome'], 'resolved')
            handle = report['target']['evidence_handle']
            evidence = server.call_tool(
                'get_evidence',
                {'repository_id': 'current', 'evidence_handle': handle},
            )
            self.assertIn('class OrderService', evidence['structuredContent']['content'])
            source = server.call_tool(
                'read_source_range',
                {
                    'repository_id': 'current',
                    'path': 'src/main/java/example/OrderService.java',
                    'start_line': 1,
                    'end_line': 2,
                },
            )
            self.assertEqual(source['structuredContent']['outcome'], 'resolved')

            output = StringIO()
            server.run_stdio(
                StringIO(
                    json.dumps(
                        {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/list'}
                    )
                    + '\n'
                ),
                output,
            )
            message = json.loads(output.getvalue())
            self.assertEqual(message['id'], 4)
            self.assertEqual(len(message['result']['tools']), 4)

    def test_workspace_mode_reads_only_registered_repository_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / 'workspace'
            repository = root / 'repository'
            workspace.mkdir()
            repository.mkdir()
            ChangeScopeApplication().execute(
                CatalogRegisterRepositoryRequest(workspace, 'orders', repository)
            )
            server = ChangeScopeMCPServer(MCPServerConfig(workspace_root=workspace))
            self.assertEqual(
                [resource['uri'] for resource in server.list_resources()],
                [
                    'changescope://catalog',
                    'changescope://repositories/orders/status',
                    'changescope://repositories/orders/snapshot',
                ],
            )
            with self.assertRaises(MCPError):
                server.call_tool('index_repository', {'repository_id': 'not-registered'})


if __name__ == '__main__':
    unittest.main()
