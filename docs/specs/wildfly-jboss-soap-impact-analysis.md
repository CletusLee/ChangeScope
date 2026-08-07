# WildFly and JBoss SOAP impact analysis

## Problem Statement

Engineers maintaining legacy JBoss EAP, JBoss AS, and WildFly applications cannot currently use ChangeScope to follow a SOAP contract change through WSDL and XML Schema, generated or handwritten Java views, JAX-WS clients, JBossWS container dispatch, endpoint implementations, handlers, configuration, payload types, and tests. The existing WildFly capability stops at EJB relationships. A Session Bean may also be a SOAP Endpoint, but the externally consumed contract and consumers remain invisible.

The target estate spans portable `javax.*` JAX-WS/JWS/JAXB applications, newer `jakarta.*` Jakarta XML Web Services applications, JBossWS Native and CXF-era deployment evidence, code-first endpoints, contract-first generated artifacts, POJO endpoints, and EJB endpoints. Runtime URLs, deployed WSDL, server configuration, and the original code-generation environment are often unavailable before implementation begins. ChangeScope therefore needs a contract-first, local, reviewable analysis path that works across these generations without pretending to certify a particular server deployment.

## Solution

Add a first-class SOAP Contract Boundary to the existing Repository Index, Workspace Catalog model, and impact report. ChangeScope will index local WSDL 1.1 and XML Schema contracts, portable `javax.*` and `jakarta.*` XML Web Services metadata, source-controlled generated contract artifacts, common JBossWS descriptors, explicit client construction, handlers, payload mappings, endpoint packaging, and tests. It will connect a changed Java method or SOAP operation to the strongest locally provable impact neighborhood and later continue through an explicitly registered Workspace Catalog mapping into another repository.

SOAP Contract Identity is independent of the application-server product and generation in accordance with ADR-0003. WSDL qualified names and message structure, or an explicit Workspace Catalog mapping to them, identify the contract. JBoss EAP or AS generation, WildFly generation, package namespace, JBossWS stack, endpoint address, deployment name, and Java class name remain evidence attributes rather than identity.

Every asserted relationship carries Confidence and an ordered Evidence Chain. Remote WSDL locations are recorded but never fetched. Dynamic construction, external server configuration, deployment-generated WSDL, legacy stacks, and incomplete contract matches become Unresolved Items rather than guessed links. Analysis remains local-only, JRE-free, and does not run JBossWS tools, Maven, Gradle, an application server, or code generation.

## Supported Generations

The capability is organized by inspectable evidence generation rather than a runtime-certification matrix:

| Evidence generation | Complete first-stage intent | Generation-specific treatment |
| --- | --- | --- |
| Portable `javax.*` JAX-WS, JWS, and JAXB | Source and contract evidence used by JBoss AS, JBoss EAP 5–7, and pre-Jakarta WildFly applications | Do not infer the exact server or JBossWS stack from the namespace alone |
| Portable `jakarta.*` XML Web Services, Web Services Metadata, and XML Binding | Source and contract evidence used by JBoss EAP 8 and current Jakarta-based WildFly applications | Use the same relationship model as `javax.*` while retaining the namespace generation |
| JBossWS and CXF deployment evidence | Common `jboss-webservices.xml`, `jaxws-endpoint-config.xml`, legacy `jbossws-cxf.xml`, JBossWS annotations, build plugins, and packaging evidence | Schema and product variants are evidence flavors; unsupported properties remain visible |
| Legacy JAX-RPC, Apache Axis, RPC/encoded, or stack-specific dynamic APIs | Recognize the boundary and source location | Report unresolved until a dedicated legacy adapter is justified by representative repositories |

Support means that ChangeScope understands the listed source and contract evidence. It does not mean that it has reproduced the runtime behavior of every JBoss EAP, JBoss AS, WildFly, JBossWS, or CXF version.

## User Stories

### Contract discovery and targets

1. As an engineer, I want local WSDL and XSD files discovered under conventional source, resource, and web-service locations, so that contract-first applications participate in impact analysis.
2. As an engineer, I want WSDL imports and XML Schema imports/includes followed only when they resolve to local source-controlled files, so that the contract graph is useful without network access.
3. As an engineer, I want unresolved, cyclic, malformed, remote, and path-escaping imports reported explicitly, so that incomplete contract analysis is visible and safe.
4. As an engineer, I want WSDL 1.1 definitions, services, ports, bindings, port types, operations, messages, faults, SOAP actions, and SOAP addresses indexed with source ranges, so that conclusions point to reviewable contract evidence.
5. As an engineer, I want XML Schema namespaces, elements, complex and simple types, attributes, inheritance, and references indexed conservatively, so that payload changes can be followed through a SOAP operation.
6. As an engineer, I want the existing `Class#method` Change Target to work for an endpoint implementation, endpoint interface, client invocation, handler, or XML-bound payload method, so that SOAP analysis fits the current workflow.
7. As an engineer, I want a SOAP operation to be a Change Target through a structured request containing a WSDL path, port-type QName, operation name, and optional input/output message identity, so that analysis can begin from the contract.
8. As a CLI user, I want dedicated `--soap-wsdl`, `--soap-port-type`, and `--soap-operation` arguments to resolve or return explicit candidates, so that a contract operation is addressable without encoding a namespace URI into the existing Java target string.
9. As an engineer, I want overloaded or otherwise indistinguishable WSDL operations to remain ambiguous unless input/output evidence disambiguates them, so that ChangeScope never selects an operation by name alone.
10. As an engineer, I want code-first Java metadata without a source WSDL reported as a derived contract with medium Confidence, so that useful evidence is retained without claiming to know the deployed WSDL.

### Endpoints and container dispatch

11. As an engineer, I want both `javax.jws` and `jakarta.jws` endpoint metadata recognized only when imports or fully qualified names prove the namespace, so that same-named annotations do not create false SOAP endpoints.
12. As an engineer, I want `@WebService`, `@WebMethod`, `@WebParam`, `@WebResult`, `@Oneway`, and `@SOAPBinding` mapped to the corresponding operation evidence, so that Java and WSDL views can be compared.
13. As an engineer, I want `endpointInterface` followed to the service endpoint interface and then to the implementation method, so that container dispatch is reviewable.
14. As an engineer, I want `serviceName`, `portName`, `targetNamespace`, `name`, `operationName`, and `action` retained as explicit metadata, so that defaults and overrides are distinguishable.
15. As an engineer, I want POJO, stateless EJB, and explicit provider endpoints represented separately, so that packaging and invocation assumptions remain clear.
16. As an engineer, I want a SOAP-exposed Session Bean connected to its existing EJB Business Interfaces and EJB consumers, so that SOAP and EJB views of one implementation form one impact neighborhood.
17. As an engineer, I want `@WebServiceProvider` and `Provider<T>` endpoints recognized as SOAP endpoints, but payload-to-operation dispatch left unresolved unless a WSDL binding proves it, so that low-level providers do not disappear.
18. As an engineer, I want `@BindingType` and WSDL binding evidence distinguish SOAP 1.1, SOAP 1.2, and unsupported non-SOAP bindings, so that transport flavor is not guessed.
19. As an engineer, I want `@RequestWrapper`, `@ResponseWrapper`, `@WebFault`, `@Action`, and fault actions connected to their wrappers and faults, so that generated and exception-sensitive impact is visible.
20. As an engineer, I want explicit endpoint declarations in standard and JBoss descriptors connected to Java implementations when the class, EJB name, and port component are uniquely proven, so that descriptor-driven deployments are analyzable.
21. As an engineer, I want deployment-generated WSDL and runtime endpoint publication reported as unverified, so that a code-first result is not mistaken for the deployed contract.

### Clients and invocations

22. As an engineer, I want `@WebServiceClient` service views and `@WebEndpoint` port factories connected to their WSDL service and port QNames, so that generated clients participate in impact analysis.
23. As an engineer, I want both field and setter `@WebServiceRef` injection connected to a generated service or service endpoint interface when the type and contract are unique, so that container-managed clients are visible.
24. As an engineer, I want typed `Service#getPort` and generated port factory calls connected to the returned service endpoint interface, so that subsequent method calls reach one SOAP operation.
25. As an engineer, I want structurally explicit `Service.create`, QName, WSDL URL, and `getPort` flows recognized when constants and assignments resolve locally, so that handwritten clients are not limited to generated wrappers.
26. As an engineer, I want explicit `BindingProvider.ENDPOINT_ADDRESS_PROPERTY` overrides reported as deployment-location evidence rather than contract identity, so that environment changes do not create false services.
27. As an engineer, I want endpoint addresses originating in properties or XML connected to their consumers when the key is explicit, so that configuration impact remains visible.
28. As an engineer, I want dynamic `Dispatch`, dynamic QNames, reflection, custom proxy factories, and direct CXF bus construction reported as unresolved unless they can be tied to one operation, so that low-level client behavior is not guessed.
29. As an engineer, I want asynchronous polling/callback APIs and executor configuration exposed as an unsupported invocation mode when recognizable, so that synchronous results do not imply complete client coverage.

### Payloads, handlers, policy, and configuration

30. As an engineer, I want WSDL input, output, and fault messages connected through XSD elements/types to corresponding `javax.xml.bind` or `jakarta.xml.bind` Java types, so that payload changes expose affected operations and clients.
31. As an engineer, I want package-level XML namespace declarations, `@XmlType`, `@XmlRootElement`, `@XmlElement`, adapters, and wrapper metadata included in an Evidence Chain, so that non-default mappings remain reviewable.
32. As an engineer, I want source-controlled generated service views, endpoint interfaces, object factories, and payload classes indexed as Generated Contract Evidence, so that contract-first applications remain useful even when build-time code generation is not run.
33. As an engineer, I want generated artifacts under `target`, `build`, or other build-output directories excluded from authoritative evidence, so that stale generated code cannot strengthen a conclusion.
34. As an engineer, I want `wsconsume`, `wsprovide`, `wsdl2java`, JAX-WS/JAXB Maven plugins, Ant tasks, and their configured source contracts indexed as generation provenance, so that a generated source tree can be traced back to its contract.
35. As an engineer, I want binding customization files connected to their WSDL/XSD and generated package/type intent when paths are explicit, so that changes to custom mappings surface affected artifacts.
36. As an engineer, I want `@HandlerChain` and local handler-chain XML connected to endpoint or client handlers in declared order, so that authentication, logging, routing, and payload-processing impact is visible.
37. As an engineer, I want programmatic handler-chain mutation reported when explicit handlers are instantiated, but dynamic ordering left unresolved, so that code-based configuration is not silently omitted.
38. As an engineer, I want `jboss-webservices.xml`, `jaxws-endpoint-config.xml`, legacy `jbossws-cxf.xml`, and standard web-service descriptors discovered in WAR, EJB-JAR, and EAR locations, so that JBoss-specific deployment evidence participates in the report.
39. As an engineer, I want descriptor precedence recorded when EAR and module-level JBoss descriptors coexist, so that an overridden setting is not treated as simultaneously active.
40. As an engineer, I want context roots, port component URIs, config names/files, handler definitions, CXF feature/interceptor class names, and WSDL publication locations indexed when explicit, so that deployment configuration changes expose their consumers.
41. As an engineer, I want WS-Policy attachments, WS-Security, WS-Addressing, MTOM, reliable-messaging, TLS, and authentication configuration reported as SOAP policy boundaries, so that security and wire-behavior risk is visible.
42. As an engineer, I want policy execution, credential resolution, server-wide subsystem configuration, handler/interceptor runtime order, and effective endpoint address left unresolved without local evidence, so that ChangeScope does not simulate JBossWS or CXF.
43. As an engineer, I want RPC/literal and document/literal recorded as binding metadata and RPC/encoded recognized as an unsupported legacy binding, so that obsolete interoperability behavior is explicit.

### Cross-repository continuation and tests

44. As an engineer, I want a local client and endpoint connected only when they share a proven WSDL contract, portable annotated interface, or explicit local mapping, so that matching URLs or names do not create a false relationship.
45. As an engineer, I want a SOAP client continued into a registered target repository only through matching SOAP Contract Identity or an explicit Workspace Catalog mapping, so that cross-repository traversal is evidence-backed.
46. As an engineer, I want WSDL service/port/operation QNames, message identity, source contract fingerprint, and mapping provenance stored in the Workspace Catalog, so that it does not copy application source.
47. As an engineer, I want one shared WSDL or explicit catalog contract key to connect `javax.*` clients to `jakarta.*` endpoints across a migration, so that namespace-generation changes do not sever a logical contract.
48. As an engineer, I want similar operation names, Java types, SOAP actions, or endpoint paths reported only as candidate context, never as a verified cross-repository edge, so that contract matching remains conservative.
49. As an engineer, I want tests using `@WebServiceRef`, generated clients, explicit service/port construction, or SOAP test payloads connected to the target operation when contract evidence is unique, so that affected verification is visible.
50. As an engineer, I want Arquillian deployments and black-box endpoint tests reported at medium or indirect Confidence, so that deployment intent is not confused with a passing server test.
51. As an engineer, I want mock SOAP servers, captured XML, and URL-only tests treated as test context rather than operation proof unless a contract identity is explicit, so that fixtures do not create false production edges.
52. As a CLI or future MCP consumer, I want text and JSON reports to expose equivalent SOAP identities, relationships, evidence generations, Confidence, assumptions, Evidence Chains, Unresolved Items, and provenance, so that all adapters share one result.

## Implementation Decisions

- Add SOAP Contract Boundary, SOAP Contract Identity, SOAP Endpoint, SOAP Client, SOAP Payload Contract, and Source-controlled Generated Contract Evidence to the project's ubiquitous language.
- Preserve ADR-0001: the Repository Index and Workspace Catalog remain authoritative for verified relationships, Confidence, unresolved items, and provenance. WSDL is strong contract evidence but does not independently prove a deployed relationship.
- Follow ADR-0003: identify SOAP contracts independently of WildFly/JBoss product generation, package namespace generation, JBossWS stack, endpoint address, deployment name, and Java naming.
- Implement WSDL 1.1 and XML Schema as the first contract formats. WSDL 2.0 and non-XSD schema languages remain later work.
- Parse XML with external entity and network resolution disabled. Resolve only repository-local imports/includes that remain inside the repository root; report remote, missing, cyclic, or escaping references as unresolved.
- Add a structured SOAP Change Target with WSDL path, port-type QName in Clark notation, operation name, and optional input/output message QNames. Expose it through dedicated `--soap-wsdl`, `--soap-port-type`, `--soap-operation`, and optional message arguments while retaining the positional `Class#method` form; reject requests that mix Java and SOAP target forms.
- Store contract facts for WSDL definitions, imports, services, ports, bindings, port types, operations, input/output/fault messages, SOAP actions and addresses, and XSD namespaces, elements, types, and references.
- Store Java facts for portable XML Web Services annotations, XML Binding annotations, generated service views, endpoint interfaces, clients, handlers, providers, payloads, and source-controlled generation provenance.
- Store deployment facts for standard web-service descriptors, JBossWS descriptors and annotations, EAR/WAR/EJB-JAR placement, context roots, port components, config references, and explicit CXF handler/feature/interceptor class names.
- Detect the portable `javax.*` and `jakarta.*` generations separately, but map them into the same relationship kinds. Do not infer the exact JBoss EAP, JBoss AS, WildFly, JBossWS Native, or JBossWS-CXF version from imports alone.
- Treat source-controlled generated artifacts inside authoritative source roots as evidence and mark their origin. Continue excluding build-output directories and do not run a generator.
- For WSDL-first code, connect generated Java evidence to the source contract through `wsdlLocation`, build-plugin paths, matching QNames, and generated metadata. For code-first endpoints without source WSDL, derive only explicit/default metadata that the portable specification makes deterministic and state that deployed WSDL remains unverified.
- Use WSDL qualified names and message structure for contract identity. Store service, port, binding, port type, and operation identities separately because one logical interface may be exposed through multiple ports or bindings.
- Match endpoint and client methods by explicit operation metadata and structurally normalized parameter/return evidence. Do not connect methods by Java name alone.
- Reuse existing EJB relationships when a SOAP Endpoint is a Session Bean. SOAP container dispatch remains medium Confidence because deployment assembly and server configuration are not executed.
- Add relationship kinds for `soap_contract_operation`, `soap_endpoint_implementation`, `soap_container_dispatch`, `soap_client_contract`, `soap_client_invocation`, `soap_payload`, `soap_fault`, `soap_handler`, `soap_policy_boundary`, `soap_configuration`, `soap_test`, and `soap_cross_repository`.
- Assign high Confidence to a complete local WSDL graph, explicit portable metadata-to-contract match, and unique source-controlled generated client/endpoint match. Assign medium Confidence to code-first derived contracts, container dispatch, descriptor-dependent packaging, and black-box test intent. Do not assert a low-confidence cross-repository edge; leave it unresolved.
- Treat SOAP addresses as deployment-location evidence only. A literal address may help locate configuration or distinguish candidates for human review, but it never creates SOAP Contract Identity by itself.
- Connect a client to a local or remote endpoint only through a shared contract artifact/fingerprint, complete compatible QName/message evidence, portable shared interface, or explicit Workspace Catalog mapping. Never connect by URL, service name, operation name, Java name, or SOAP action alone.
- Record WS-Policy, WS-Security, WS-Addressing, MTOM, reliable-messaging, TLS, authentication, handler, and CXF customization evidence as affected boundaries. Do not evaluate policy compatibility, secrets, runtime subsystem state, or effective interceptor order.
- Recognize JAX-RPC, Axis, WSDD, RPC/encoded, direct low-level CXF construction, and dynamic `Dispatch` as legacy or dynamic SOAP boundaries. Emit source-located Unresolved Items rather than silently omitting them.
- Extend old-index refresh checks for SOAP contract, payload, descriptor, and relationship schemas.
- Keep reports local-only and JRE-free. Do not run WildFly, JBoss EAP/AS, JBossWS, CXF, `wsconsume`, `wsprovide`, Maven, Gradle, Ant, Java, a container, or a network request.

## Delivery Slices

1. **Index and target one local WSDL operation**: discover WSDL/XSD, build a bounded local import graph, resolve a SOAP Change Target, and report messages/payload schema evidence.
2. **Connect portable endpoints**: map `javax.*` and `jakarta.*` endpoint interfaces, POJO endpoints, EJB endpoints, provider boundaries, and implementation methods to one WSDL operation or code-first derived contract.
3. **Trace typed clients**: connect generated services, `@WebServiceRef`, explicit `Service`/port flows, configuration-backed addresses, and invocation sites to one contract operation.
4. **Attach payload, handler, and deployment impact**: connect XML-bound types, faults, handler chains, JBossWS descriptors, packaging, build-generation provenance, and policy boundaries.
5. **Continue through the Workspace Catalog**: add SOAP contract keys and verified client-to-endpoint traversal without copying source into the catalog. This slice consumes the shared Workspace Catalog capability rather than creating a SOAP-specific catalog.
6. **Expose legacy gaps and validate representative applications**: detect JAX-RPC/Axis/dynamic boundaries, complete text/JSON parity and evidence navigation, and smoke-test pinned local representative JBoss EAP/WildFly applications.

Each slice must produce a runnable report result through the public application service. Parser-only, schema-only, or model-only tickets do not satisfy a slice.

Slice 1 is the foundation. Slices 2 and 3 depend on it but can be delivered independently; slice 4 attaches evidence to whichever endpoint and client paths are complete. Slice 5 additionally depends on the shared Workspace Catalog capability and must not block completion of the repository-local SOAP path. Slice 6 closes the parent capability only after the completed local slices and any available cross-repository slice have representative validation.

## Testing Decisions

- Test externally observable behavior through the public application service using isolated fixture repositories. Keep CLI coverage to target parsing, text/JSON parity, representative rendering, and exit behavior.
- Cover WSDL-first and code-first applications; POJO, EJB, provider, and descriptor-backed endpoints; generated and handwritten clients; `javax.*` and `jakarta.*`; and source-controlled versus build-output generated code.
- Cover WSDL imports, XSD imports/includes, cycles, malformed XML, missing local references, remote references, path traversal, namespaces, QNames, multiple services/ports/bindings, SOAP 1.1/1.2, overloaded operations, one-way operations, and faults.
- Cover endpoint interfaces, implementation inheritance, explicit and default operation metadata, wrappers, provider endpoints, ambiguous implementations, EJB/SOAP dual exposure, and code-first uncertainty.
- Cover `@WebServiceClient`, `@WebEndpoint`, `@WebServiceRef`, `Service.create`, `getPort`, explicit QNames, `BindingProvider` address overrides, dynamic values, `Dispatch`, asynchronous clients, and direct CXF construction.
- Cover XML Binding package/type/element metadata, adapters, wrapper types, object factories, binding customization files, build-plugin provenance, payload reuse, and stale build-output exclusion.
- Cover handler annotations and XML, programmatic handlers, JBossWS endpoint/client configuration, `jboss-webservices.xml` placement and precedence, legacy `jbossws-cxf.xml`, CXF class references, WS-Policy/security/MTOM/addressing boundaries, and unsupported runtime ordering.
- Cover local contract identity, explicit local mappings, path/name/action-only false matches, catalog-backed cross-repository traversal, `javax` client to `jakarta` endpoint migration, ambiguous target repositories, and stale index provenance.
- Cover unit-like client tests, `@WebServiceRef` tests, Arquillian deployment evidence, black-box endpoint tests, mock servers, XML fixtures, and unresolved URL-only test intent.
- Verify every Java, WSDL, XSD, descriptor, handler, policy, properties, and build-file Evidence Handle through bounded Evidence Navigation.
- Add pinned local smoke fixtures based on official JBoss EAP/WildFly examples for a POJO endpoint, EJB endpoint, contract-first client, handler/configuration example, and `javax`-to-`jakarta` migration shape. Public-project validation supplements but does not replace isolated fixtures.
- Verify that no automated test requires a JRE, application server, JBossWS/CXF runtime, code generator, build tool execution, network access, remote Git access, database server, telemetry, cloud service, Docker daemon, or background process.

## Out of Scope

- Runtime deployment verification, application-server startup, deployed WSDL retrieval, management API queries, production endpoint discovery, and effective subsystem configuration.
- Full certification of any JBoss EAP, JBoss AS, WildFly, JBossWS Native, JBossWS-CXF, Apache CXF, or Jakarta XML Web Services runtime version.
- JAX-RPC, Apache Axis/WSDD semantic analysis, RPC/encoded interoperability, SAAJ message-flow analysis, and CORBA/IIOP web-service bridges. Recognizable use remains unresolved.
- Complete dynamic `Dispatch`, provider payload dispatch, reflection, custom proxy factory, CXF bus, Spring CXF XML, or runtime-generated client resolution.
- WSDL 2.0, non-XSD schema languages, arbitrary XSLT, Schematron, sample-message inference, or full XML Schema validation and type-system equivalence.
- Executing `wsconsume`, `wsprovide`, `wsdl2java`, XJC, Maven, Gradle, Ant, a compiler, or importing generated build output as authoritative evidence.
- Proving wire compatibility, JAXB marshalling success, XML canonicalization, attachment contents, MTOM thresholds, WS-Policy compatibility, WS-Security credentials, encryption/signature correctness, reliable-delivery guarantees, TLS identity, or handler/interceptor runtime order.
- Creating cross-repository relationships from URLs, DNS, deployment names, Java names, operation names, SOAP actions, or similar payload classes alone.
- A SOAP-specific replacement for the Workspace Catalog. Cross-repository SOAP traversal depends on the shared catalog and evidence model.
- JMS transport semantics and asynchronous messaging continuation. SOAP-over-JMS is reported as a SOAP transport boundary and hands off to a later Asynchronous Boundary capability.
- MCP adapter implementation; this capability extends the shared structured application-service result that a later adapter can expose.

## Completion Criteria

The capability is complete when an engineer can start from either a Java `Class#method` or one WSDL operation and receive a report that:

- resolves or explicitly disambiguates the intended SOAP contract operation;
- connects locally provable endpoints, implementations, clients, invocations, payloads, faults, handlers, configuration, and tests;
- reuses EJB evidence when the endpoint is a Session Bean;
- continues through one verified Workspace Catalog SOAP mapping into a registered target repository;
- shows ordered source-backed Evidence Chains, Confidence, assumptions, Unresolved Items, and Index Snapshot provenance in both text and JSON;
- recognizes unsupported JAX-RPC/Axis/dynamic/server-state behavior rather than hiding it; and
- passes the isolated fixture matrix plus representative pinned JBoss EAP/WildFly smoke validation without requiring a JRE, server, build, generator, or network.

## Further Notes

- This is a P0 capability for the target estate because SOAP/JAX-WS is a common cross-application contract, not merely a framework feature.
- The official WildFly documentation confirms JBossWS integration over Apache CXF, POJO and EJB endpoints, generated and dynamic clients, `@WebServiceRef`, handler chains, JBoss descriptors, and WS-* configuration: [WildFly Developer Guide](https://docs.wildfly.org/38/Developer_Guide.html).
- Red Hat documents JAX-WS and JBossWS-CXF across [JBoss EAP 5](https://docs.redhat.com/en/documentation/red_hat_jboss_enterprise_application_platform/5/html/web_services_cxf_user_guide/index), [JBoss EAP 7](https://docs.redhat.com/en/documentation/red_hat_jboss_enterprise_application_platform/7.4/html-single/developing_web_services_applications/index), and the [JBoss EAP 8 migration boundary](https://docs.redhat.com/en/documentation/red_hat_jboss_enterprise_application_platform/8.0/html-single/migration_guide/index). EAP 8 explicitly requires migration away from JAX-RPC, which supports treating JAX-RPC as a visible legacy boundary rather than the portable first-stage model.
- Portable metadata and XML binding semantics are grounded in the [Jakarta XML Web Services API](https://jakarta.ee/specifications/xml-web-services/4.0/apidocs/), [Jakarta Web Services Metadata](https://jakarta.ee/specifications/web-services-metadata/3.0/ws-metadata-spec-3.0), and [Jakarta XML Binding](https://jakarta.ee/specifications/xml-binding/3.0/jakarta-xml-binding-spec-3.0).
- Contract-first support must not depend on regenerated Java code. The source WSDL/XSD remains contract evidence, while committed generated artifacts provide traceable implementation and consumer evidence.
