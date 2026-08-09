from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from changescope.mcp import ChangeScopeMCPServer, MCPServerConfig


class RESTDiscoveryMCPTests(unittest.TestCase):
    def test_mcp_exposes_copyable_spring_mvc_target_and_conditions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            quote = chr(34)
            source = (
                'package example;\n'
                'import org.springframework.web.bind.annotation.GetMapping;\n'
                'import org.springframework.web.bind.annotation.RequestMapping;\n'
                'import org.springframework.web.bind.annotation.RestController;\n'
                '@RestController\n'
                '@RequestMapping(' + quote + '/api' + quote + ')\n'
                'class OrderController {\n'
                '    @GetMapping(path = ' + quote + '/orders/{id}' + quote + ', params = ' + quote + 'view=summary' + quote + ', headers = ' + quote + 'X-Tenant' + quote + ')\n'
                '    public String get(String id) { return id; }\n'
                '}\n'
            )
            path = root / 'src/main/java/example/OrderController.java'
            path.parent.mkdir(parents=True)
            path.write_text(source, encoding='utf-8')
            server = ChangeScopeMCPServer(MCPServerConfig(repository_root=root))
            server.call_tool('index_repository', {'repository_id': 'current'})
            discovered = server.call_tool(
                'discover_contracts',
                {
                    'repository_id': 'current',
                    'rest': {
                        'http_method': 'GET',
                        'path': '/api/orders/{id}',
                        'params': ['view=summary'],
                        'headers': ['X-Tenant'],
                    },
                },
            )['structuredContent']
            self.assertEqual(discovered['outcome'], 'resolved')
            candidate = discovered['candidates'][0]
            self.assertEqual(candidate['provenance']['flavors'], ['spring_mvc'])
            self.assertEqual(candidate['provenance']['params'], ['view=summary'])
            self.assertEqual(candidate['target']['headers'], ['X-Tenant'])
            self.assertTrue(candidate['evidence_handles'][0].startswith('spring_mvc:'))
            evidence = server.call_tool(
                'get_evidence',
                {
                    'repository_id': 'current',
                    'evidence_handle': candidate['evidence_handles'][0],
                },
            )['structuredContent']
            self.assertEqual(evidence['evidence_handle'], candidate['evidence_handles'][0])
            impact = server.call_tool(
                'analyze_impact',
                {'repository_id': 'current', 'target': candidate['target']},
            )['structuredContent']
            self.assertEqual(impact['outcome'], 'resolved')
            self.assertEqual(impact['target']['signature'], 'GET /api/orders/{id}')

    def test_mcp_returns_copyable_rest_target_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            quote = chr(34)
            source = (
                'package example;\n'
                'import javax.ws.rs.GET;\n'
                'import javax.ws.rs.Path;\n'
                '@Path(' + quote + '/orders/' + quote + ')\n'
                'public class OrderResource {\n'
                '    @GET\n'
                '    public String list() { return ' + quote + 'ok' + quote + '; }\n'
                '}\n'
            )
            path = root / 'src/main/java/example/OrderResource.java'
            path.parent.mkdir(parents=True)
            path.write_text(source, encoding='utf-8')
            server = ChangeScopeMCPServer(MCPServerConfig(repository_root=root))
            server.call_tool('index_repository', {'repository_id': 'current'})
            discovered = server.call_tool(
                'discover_contracts',
                {
                    'repository_id': 'current',
                    'rest': {'http_method': 'GET', 'path': '/orders'},
                },
            )['structuredContent']
            self.assertEqual(discovered['outcome'], 'resolved')
            candidate = discovered['candidates'][0]
            self.assertEqual(candidate['kind'], 'rest')
            self.assertEqual(candidate['target']['http_method'], 'GET')
            self.assertEqual(candidate['target']['path'], '/orders')
            self.assertTrue(candidate['evidence_handles'])
            self.assertNotIn('content', candidate)
            impact = server.call_tool(
                'analyze_impact',
                {'repository_id': 'current', 'target': candidate['target']},
            )['structuredContent']
            self.assertEqual(impact['outcome'], 'resolved')
            self.assertEqual(impact['target']['signature'], 'GET /orders')


if __name__ == '__main__':
    unittest.main()
