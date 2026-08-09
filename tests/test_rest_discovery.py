from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from changescope.application import (
    ChangeScopeApplication,
    ContractDiscoveryRequest,
    EvidenceRequest,
    ImpactRequest,
    IndexRequest,
)


class RESTDiscoveryTests(unittest.TestCase):
    def test_indexes_and_resolves_spring_mvc_mapping_variants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            quote = chr(34)
            source = (
                'package example;\n'
                'import org.springframework.web.bind.annotation.GetMapping;\n'
                'import org.springframework.web.bind.annotation.RequestMapping;\n'
                'import org.springframework.web.bind.annotation.RequestMethod;\n'
                'import org.springframework.web.bind.annotation.RestController;\n'
                '@RestController\n'
                '@RequestMapping(path = {' + quote + '/api//' + quote + ', ' + quote + '/v1' + quote + '}, consumes = ' + quote + 'application/json' + quote + ', produces = ' + quote + 'application/json' + quote + ')\n'
                'class OrderController {\n'
                '    @GetMapping(path = {' + quote + '/orders/{id}/' + quote + ', ' + quote + '/orders' + quote + '}, params = ' + quote + 'view=summary' + quote + ', headers = ' + quote + 'X-Tenant' + quote + ')\n'
                '    public String get(String id) { return id; }\n'
                '    @RequestMapping(path = ' + quote + '/orders/{id}' + quote + ', method = RequestMethod.GET, produces = ' + quote + 'application/xml' + quote + ')\n'
                '    public String xml(String id) { return id; }\n'
                '}\n'
            )
            path = root / 'src/main/java/example/OrderController.java'
            path.parent.mkdir(parents=True)
            path.write_text(source, encoding='utf-8')

            application = ChangeScopeApplication()
            indexed = application.execute(IndexRequest(root))
            spring_facts = [fact for fact in indexed.rest_facts if fact.flavor == 'spring_mvc']
            self.assertTrue(any(fact.kind == 'rest_resource' for fact in spring_facts))
            self.assertEqual(
                6,
                len([fact for fact in spring_facts if fact.kind == 'rest_endpoint']),
            )

            ambiguous = application.execute(ContractDiscoveryRequest(
                root,
                rest_http_method='GET',
                rest_path='/api/orders/{id}',
            ))
            self.assertEqual(ambiguous.outcome, 'ambiguous')
            self.assertEqual(len(ambiguous.candidates), 2)
            self.assertEqual(
                {candidate.provenance.flavors for candidate in ambiguous.candidates},
                {('spring_mvc',)},
            )
            self.assertEqual(
                {candidate.target.params for candidate in ambiguous.candidates},
                {(), ('view=summary',)},
            )
            self.assertTrue(all(candidate.evidence_handles for candidate in ambiguous.candidates))

            exact = application.execute(ContractDiscoveryRequest(
                root,
                rest_http_method='GET',
                rest_path='/api/orders/{id}',
                rest_consumes=('application/json',),
                rest_produces=('application/json',),
                rest_params=('view=summary',),
                rest_headers=('X-Tenant',),
            ))
            self.assertEqual(exact.outcome, 'resolved')
            self.assertEqual(len(exact.candidates), 1)
            candidate = exact.candidates[0]
            self.assertEqual(candidate.target.path, '/api/orders/{id}')
            self.assertEqual(candidate.target.params, ('view=summary',))
            self.assertEqual(candidate.target.headers, ('X-Tenant',))
            self.assertEqual(candidate.provenance.class_paths, ('/api//', '/v1'))
            self.assertEqual(candidate.source_resolution, 'resolved')

            evidence = application.execute(EvidenceRequest(root, candidate.evidence_handles[0]))
            self.assertEqual(evidence.evidence_handle, candidate.evidence_handles[0])
            self.assertIn('@GetMapping', evidence.content)

            impact = application.execute(ImpactRequest(root, rest_target=candidate.target))
            self.assertEqual(impact.outcome, 'resolved')
            self.assertEqual(impact.target.signature, candidate.target.signature)
            self.assertTrue(any(relationship.kind == 'rest_contract' for relationship in impact.relationships))

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
