from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from changescope.application import (
    ChangeScopeApplication,
    ContractDiscoveryRequest,
    ImpactRequest,
    IndexRequest,
)


class RESTDiscoveryTests(unittest.TestCase):
    def test_indexes_normalizes_and_resolves_portable_jaxrs_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            quote = chr(34)
            source = (
                'package example;\n'
                'import javax.ws.rs.ApplicationPath;\n'
                'import javax.ws.rs.Consumes;\n'
                'import javax.ws.rs.POST;\n'
                'import javax.ws.rs.Path;\n'
                'import javax.ws.rs.Produces;\n'
                'import javax.ws.rs.core.Application;\n'
                '@ApplicationPath(' + quote + '/api//' + quote + ')\n'
                'class JaxrsApplication extends Application {}\n'
                '@Path(' + quote + '/orders//' + quote + ')\n'
                'public class OrderResource {\n'
                '    @POST\n'
                '    @Path(' + quote + '/{orderId}/' + quote + ')\n'
                '    @Consumes(' + quote + 'application/json' + quote + ')\n'
                '    @Produces({' + quote + 'application/json' + quote + ', ' + quote + 'application/xml' + quote + '})\n'
                '    public String create(String orderId) { return orderId; }\n'
                '}\n'
            )
            path = root / 'src/main/java/example/OrderResource.java'
            path.parent.mkdir(parents=True)
            path.write_text(source, encoding='utf-8')

            application = ChangeScopeApplication()
            indexed = application.execute(IndexRequest(root))
            self.assertTrue(any(f.kind == 'rest_application' for f in indexed.rest_facts))
            endpoint = next(f for f in indexed.rest_facts if f.kind == 'rest_endpoint')
            metadata = json.loads(endpoint.value)
            self.assertEqual(metadata['consumes'], 'application/json')
            self.assertEqual(metadata['produces'], ['application/json', 'application/xml'])

            discovery = application.execute(ContractDiscoveryRequest(
                root,
                rest_http_method='post',
                rest_path='/api/orders/{orderId}',
            ))
            self.assertEqual(discovery.outcome, 'resolved')
            self.assertEqual(len(discovery.candidates), 1)
            candidate = discovery.candidates[0]
            self.assertEqual(candidate.target.signature, 'POST /api/orders/{orderId}')
            self.assertEqual(candidate.target.route_shape, '/api/orders/{}')
            self.assertEqual(candidate.provenance.application_paths, ('/api//',))
            self.assertEqual(candidate.source_resolution, 'resolved')
            self.assertTrue(candidate.evidence_handles)

            impact = application.execute(ImpactRequest(root, rest_target=candidate.target))
            self.assertEqual(impact.outcome, 'resolved')
            self.assertEqual(impact.target.signature, candidate.target.signature)
            self.assertTrue(any(rel.kind == 'rest_contract' for rel in impact.relationships))


if __name__ == '__main__':
    unittest.main()
