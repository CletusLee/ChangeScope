from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from changescope.mcp import ChangeScopeMCPServer, MCPServerConfig


class RESTDiscoveryMCPTests(unittest.TestCase):
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
