# Quarkus local impact analysis

## Problem Statement

Engineers maintaining Quarkus applications cannot currently use ChangeScope to follow a changed Java method through Quarkus CDI, configuration, HTTP entry points, REST clients, security policy, tests, and GraalVM native-image metadata. These relationships are frequently established during Quarkus augmentation rather than by ordinary Java construction, and many target applications run primarily as native images. A direct-call report therefore omits material impact even when the repository contains explicit evidence.

The affected estate includes legacy Quarkus applications using `javax.*`, current applications using `jakarta.*`, both Quarkus REST and RESTEasy Classic, imperative and reactive HTTP handlers, and multi-module Maven or Gradle builds. Engineers need one conservative repository-local report that covers this breadth without running Quarkus, a JRE, GraalVM, Maven, Gradle, a container, or an application. It must distinguish build-time from runtime profile behavior, treat Native Image as an evidence boundary rather than verified reachability, and expose unsupported framework behavior instead of guessing it.

## Solution

Extend the Repository Index and impact report so that a resolved Java `Class#method` Change Target can expose its Quarkus Local Analysis neighborhood. ChangeScope will recognize build evidence, Quarkus CDI Evidence, Quarkus Configuration Evidence, Quarkus REST Contracts, Quarkus HTTP Routes, Quarkus REST Client Contracts, Quarkus Security Evidence, Quarkus Test Evidence, Quarkus Persistence Boundaries, and Native Image Boundaries. Every asserted relationship will carry Confidence and an ordered Evidence Chain, and every recognizable mechanism that cannot be proven structurally will remain an Unresolved Item.

The feature will support both `javax.*` and `jakarta.*` generations and will distinguish Quarkus Build Profile Selection from Quarkus Runtime Profile Selection. It will analyze only source-controlled local evidence by default. It will not execute augmentation or native-image compilation, consume generated build outputs as authoritative evidence, or claim production configuration or runtime dispatch. The Repository Index remains the source of truth under ADR-0001, and native-image conclusions follow ADR-0002.

## User Stories

1. As an engineer maintaining a Quarkus application, I want a `Class#method` impact report to include Quarkus-managed relationships, so that framework behavior is not reduced to direct Java calls.
2. As an engineer, I want the existing `Class#method` target form preserved, so that Quarkus analysis fits the established workflow.
3. As an engineer, I want overloaded targets to remain ambiguous, so that Quarkus support never silently selects a signature.
4. As an engineer, I want interface and implementation methods to be targetable symmetrically when explicit Quarkus contract evidence connects them, so that I can begin from either side.
5. As an engineer, I want every Quarkus conclusion backed by source or configuration Evidence Handles, so that I can review why it was reported.
6. As an engineer, I want every multi-location Quarkus conclusion to expose an ordered Evidence Chain, so that build, contract, injection, configuration, and invocation facts remain inspectable.
7. As an engineer, I want unsupported or ambiguous Quarkus behavior reported as an Unresolved Item, so that incomplete structural analysis is visible.
8. As a security-conscious organization, I want Quarkus analysis to remain local-only, so that source and configuration are not sent to remote services.
9. As an engineer without a local JRE, I want Quarkus repositories analyzed structurally, so that the feature remains usable in constrained environments.
10. As an engineer, I want the Index Snapshot to identify the exact local state supporting the report, so that framework conclusions are reproducible.

11. As an engineer maintaining Quarkus 1 or 2, I want common `javax.*` CDI and JAX-RS evidence recognized, so that legacy applications are not excluded.
12. As an engineer maintaining Quarkus 3 or later, I want corresponding `jakarta.*` evidence recognized, so that current applications use the same relationship model.
13. As an engineer, I want annotations accepted only when imports or fully qualified names prove the expected namespace, so that unrelated same-named annotations do not become Quarkus evidence.
14. As an engineer, I want Maven Quarkus platform, plugin, extension, and profile declarations indexed, so that framework flavor and capabilities have build evidence.
15. As an engineer, I want Gradle Groovy and Kotlin DSL Quarkus plugin and dependency declarations indexed, so that build-tool choice does not hide the framework.
16. As an engineer maintaining a multi-module build, I want module-local and inherited Quarkus build evidence connected conservatively, so that application modules are distinguished from unrelated library modules.
17. As an engineer, I want missing or ambiguous build descriptors to leave framework generation or flavor unresolved, so that source annotations do not prove a complete Quarkus build.
18. As an engineer, I want older Repository Indexes refreshed when Quarkus or Native Image schemas are absent, so that feature adoption does not require manual repair.

19. As an engineer, I want class beans with standard CDI scopes recognized as Quarkus CDI Evidence, so that managed implementations participate in impact analysis.
20. As an engineer, I want producer methods and producer fields recognized, so that third-party and configured types can be connected to their consumers.
21. As an engineer, I want field injection recognized when type and qualifiers identify one local candidate, so that explicit consumers are visible.
22. As an engineer, I want constructor injection recognized, including Quarkus single-constructor injection without `@Inject`, so that Quarkus-specific injection syntax is covered.
23. As an engineer, I want initializer-method injection recognized, so that multi-parameter CDI initialization participates in the relationship model.
24. As an engineer, I want explicit custom qualifiers indexed and matched, so that multiple implementations are not treated as ambiguous when source evidence disambiguates them.
25. As an engineer, I want producer parameters treated as Injection Points, so that producer dependencies participate in Evidence Chains.
26. As an engineer, I want an explicit invocation through a proven Injection Point connected to the selected bean method, so that container-managed calls are not reduced to class-level wiring.
27. As an engineer, I want CDI Container Dispatch reported conservatively, so that build-time source selection is not presented as verified runtime invocation.
28. As an engineer, I want unsatisfied or multiply eligible candidates reported as unresolved, so that ChangeScope does not select a bean by name similarity.
29. As an engineer, I want `Instance<T>`, `Provider<T>`, `@All List<T>`, programmatic lookup, and dynamic iteration reported as unresolved when the selected target cannot be proven, so that runtime selection remains visible.
30. As an engineer, I want synthetic beans and extension-generated beans reported as an unresolved coverage boundary, so that unavailable augmentation facts are explicit.
31. As an engineer, I want alternatives, priorities, default beans, selected alternatives, and conditional bean rules reflected only when local evidence and selected profiles prove them, so that ArC selection is not guessed.
32. As an engineer, I want interceptors, decorators, proxies, and lifecycle behavior recorded as behavioral wrappers or unresolved dispatch, so that they remain visible without pretending to be direct calls.

33. As an engineer, I want Quarkus Build Profile Selection and Quarkus Runtime Profile Selection represented separately, so that augmentation-time and runtime behavior are not conflated.
34. As an engineer, I want repeatable build and runtime profile inputs, so that multi-profile Quarkus scenarios can be analyzed.
35. As an engineer, I want profile precedence and parent-profile evidence followed when locally explicit, so that matching configuration sources are interpreted consistently.
36. As an engineer who supplies no profiles, I want profile-specific facts reported as conditional rather than assumed active, so that ChangeScope does not infer a deployment mode.
37. As an engineer, I want `@IfBuildProfile`, `@UnlessBuildProfile`, `@IfBuildProperty`, and `@UnlessBuildProperty` connected to build-time conditions, so that conditional beans and endpoints carry the right scenario.
38. As an engineer, I want runtime-only profile changes prevented from altering build-fixed conclusions, so that the report respects Quarkus augmentation boundaries.

39. As an engineer, I want `@ConfigProperty` consumers connected to their keys, defaults, and optionality, so that local configuration impact is visible.
40. As an engineer, I want `@ConfigMapping` interfaces connected to nested and renamed keys, so that grouped configuration participates in impact analysis.
41. As an engineer, I want explicit naming strategies, `@WithName`, and `@WithParentName` applied when constructing configuration keys, so that mapping evidence is not based on method names alone.
42. As an engineer, I want repository-local properties, YAML, profile-aware files, inline profile keys, and MicroProfile configuration sources indexed, so that common Quarkus configuration layouts are covered.
43. As an engineer, I want property expressions connected through ordered Evidence Chains, so that derived keys expose their dependencies.
44. As an engineer, I want local configuration precedence evaluated under the selected build and runtime profiles, so that competing local sources remain reviewable.
45. As an engineer, I want environment variables, system properties, `.env`, Kubernetes ConfigMaps, Vault, and other higher-priority external sources treated as possible overrides, so that a local file value is not misreported as production state.
46. As an engineer, I want reports centered on configuration key dependencies, sources, and precedence rather than claimed effective runtime values, so that impact conclusions remain conservative.

47. As an engineer, I want Quarkus REST and RESTEasy Classic detected as distinct implementation flavors of one Quarkus REST Contract, so that legacy and current HTTP stacks share a model without erasing differences.
48. As an engineer, I want class-level and method-level paths combined with HTTP method annotations, so that a changed handler exposes its route contract.
49. As an engineer, I want `@Produces`, `@Consumes`, path parameters, query parameters, headers, forms, bodies, and response types included as contract evidence, so that relevant wire-shape changes are visible.
50. As an engineer, I want resource interfaces and implementations connected when explicit Java structure proves the contract, so that interface-driven endpoints are navigable in both directions.
51. As an engineer, I want inherited resource contracts and explicit subresource locators followed only when the target remains unique, so that route composition does not become a heuristic.
52. As an engineer, I want `@ApplicationPath`, Quarkus REST path, and HTTP root-path configuration included when constructing a local route, so that configuration changes can affect endpoint identity.
53. As an engineer, I want reactive return types, blocking annotations, streaming, and server-sent-event modes recorded as execution or response metadata, so that they remain visible without creating a second endpoint model.
54. As an engineer, I want conditional endpoint enablement linked to its property or build condition, so that endpoint presence is not assumed across profiles.
55. As an engineer, I want REST flavor ambiguity, unsupported servlet coupling, filters, readers, writers, exception mappers, and runtime provider selection reported conservatively, so that framework differences remain reviewable.

56. As an engineer using Reactive Routes, I want `@Route`, repeatable routes, and `@RouteBase` recognized as Quarkus HTTP Routes, so that non-JAX-RS handlers participate in impact analysis.
57. As an engineer, I want route path, HTTP methods, consumes, produces, handler type, and order recorded, so that route behavior is reviewable.
58. As an engineer, I want explicit literal Vert.x router registrations recognized, so that programmatic but statically obvious handlers are not omitted.
59. As an engineer, I want a route handler connected to its CDI dependencies, callees, configuration, security, native evidence, and tests, so that the local impact path remains complete.
60. As an engineer, I want regex routes, dynamically composed paths, cross-method router assembly, and conflicting route order reported as unresolved when no unique handler can be proven, so that ChangeScope does not simulate Vert.x routing.

61. As an engineer using MicroProfile REST Client, I want `@RegisterRestClient` interfaces recognized as Quarkus REST Client Contracts, so that typed outbound calls participate in impact analysis.
62. As an engineer, I want `@RestClient` injection and explicit invocation connected to the client contract method, so that affected consumers are visible.
63. As an engineer, I want both current Quarkus REST Client and legacy RESTEasy Client extensions recognized, so that client generation is interpreted in the correct flavor.
64. As an engineer, I want FQCN-based and `configKey`-based URL or URI configuration connected to the typed client, so that endpoint configuration changes are visible.
65. As an engineer, I want programmatic REST client builders resolved only when interface type, base URI, and builder flow are structurally unique, so that dynamic builder behavior is not guessed.
66. As an engineer using Vert.x WebClient, I want explicit HTTP methods and literal or configuration-backed URLs recorded as HTTP client evidence, so that obvious outbound boundaries are not invisible.
67. As an engineer, I want dynamic URLs, redirects, filters, provider mutation, and runtime client construction reported as unresolved, so that raw HTTP calls are not promoted to typed contracts.
68. As an engineer, I want a local client connected to a local server only when shared annotated interfaces, explicit mappings, or proven local base configuration establish contract identity, so that identical paths alone never create a relationship.
69. As an engineer, I want path-only or ambiguous client/server candidates retained as unresolved, so that local coincidence is not mistaken for dispatch.

70. As an engineer, I want standard and Quarkus security annotations associated with affected REST endpoints, HTTP routes, and CDI methods, so that authorization policy changes are visible.
71. As an engineer, I want literal or profile-backed HTTP permission paths connected to matching local routes, so that configuration-based security participates in impact analysis.
72. As an engineer, I want role and permission property expressions connected to their configuration keys, so that security behavior carries configuration evidence.
73. As an engineer, I want custom authentication mechanisms, identity providers, augmentors, inclusive authentication, OIDC runtime state, and custom policy decisions reported as unresolved, so that ChangeScope does not claim an access outcome.

74. As an engineer, I want `@QuarkusTest`, `@QuarkusIntegrationTest`, component tests, and legacy native tests classified separately, so that test intent and execution mode are clear.
75. As an engineer, I want test profiles and test resources connected to affected configuration and components, so that scenario-specific tests are visible.
76. As an engineer, I want `@TestHTTPEndpoint`, `@TestHTTPResource`, and explicit literal REST-assured calls connected to matching local handlers, so that endpoint tests are discoverable.
77. As an engineer, I want `@InjectMock`, `@InjectSpy`, and supported Quarkus mock wiring connected to the replaced bean, so that affected test substitutions are visible.
78. As an engineer, I want black-box integration tests reported at indirect Confidence, so that test intent is not confused with a successful artifact launch.
79. As an engineer, I want dynamic mock installation and test-resource behavior reported as unresolved when the target cannot be proven, so that runtime test setup is not guessed.

80. As an engineer whose application runs as a GraalVM native image, I want Native Image Boundaries treated as first-class impact, so that JVM-only evidence does not hide deployment risk.
81. As an engineer, I want `@RegisterForReflection`, `@RegisterForProxy`, resource and bundle registration annotations connected to their target types or resources, so that registration impact is reviewable.
82. As an engineer, I want source-controlled reflection, proxy, resource, serialization, JNI, and related native-image metadata indexed, so that class and resource renames expose affected configuration.
83. As an engineer, I want `META-INF/services` providers connected to their interfaces and implementation classes, so that service-provider changes expose native-image risk.
84. As an engineer, I want explicit reflection, dynamic-proxy, resource-lookup, and serialization usage connected to registration evidence when structurally provable, so that covered and uncovered native-sensitive paths are distinguishable.
85. As an engineer, I want REST request and response types connected to relevant serialization and Native Image evidence, so that DTO changes surface deployment-sensitive impact.
86. As an engineer, I want native build/profile configuration and native-oriented tests reported, so that the native verification surface is visible.
87. As an engineer, I want extension-generated native metadata described as unverified when augmentation has not run, so that absence of source registration is not automatically reported as a defect.
88. As an engineer, I want generated build outputs excluded from authoritative evidence by default, so that stale artifacts from another commit or profile cannot create high-confidence conclusions.
89. As an engineer, I want the report to state that ChangeScope has not built the native image or reconstructed complete closed-world reachability, so that native-aware analysis is not mistaken for build verification.

90. As an engineer using Panache or Quarkus persistence extensions, I want a Quarkus Persistence Boundary reported, so that generated persistence behavior does not disappear silently.
91. As an engineer, I want user-defined repository methods and ordinary Java calls preserved, so that the unsupported persistence boundary does not hide proven local code.
92. As an engineer, I want generated CRUD, active-record methods, query interpretation, persistence units, transactions, and database dispatch reported as unresolved, so that the first Quarkus stage does not pretend to solve persistence semantics.
93. As an engineer maintaining a command-mode application, I want `@QuarkusMain` and `QuarkusApplication` entry points recognized, so that non-HTTP Quarkus applications retain a useful local path.
94. As an engineer, I want explicit startup and lifecycle entry evidence connected conservatively, so that framework-triggered initialization remains visible.
95. As an engineer, I want scheduler, messaging, CDI events, GraphQL, gRPC, servlet, and other recognized extension boundaries reported as unsupported rather than ignored, so that later stages have an observable seam.
96. As an engineer in a mixed-language repository, I want Kotlin and Scala source presence reported as an analysis coverage gap, so that Java-only results do not imply full repository coverage.
97. As a CLI or future MCP consumer, I want text and JSON reports to expose equivalent Quarkus relationships, profiles, flavors, native metadata, Confidence, assumptions, Evidence Chains, Unresolved Items, and provenance, so that all adapters share one result.
98. As an existing ChangeScope user, I want Java, Spring, and EJB behavior preserved, so that adding Quarkus support does not regress completed framework paths.

## Implementation Decisions

- Extend the existing public application-service seam; the CLI remains a thin adapter and a future MCP adapter must consume the same structured results.
- Preserve `Class#method` as the only Change Target form in this capability. HTTP paths, configuration keys, classes, native resources, requirements, and diffs remain later target-expansion work.
- Add explicit Repository Index facts for Quarkus build evidence, CDI beans and producers, qualifiers, Injection Points, profile conditions, configuration consumers and sources, REST resources and clients, HTTP routes, security policy, tests, persistence boundaries, and Native Image evidence.
- Continue using one Evidence-backed Relationship model rather than framework-specific report types. Add distinct relationship kinds for Quarkus CDI implementation, injection, Container Dispatch, configuration boundary and consumers, REST endpoint and client contracts, local contract implementation, HTTP routes, security, tests, persistence boundaries, and Native Image boundaries.
- Preserve ordered Evidence Chains and current primary evidence fields for backward compatibility.
- Detect Quarkus from explicit Maven or Gradle platform, plugin, and extension evidence. Build evidence determines known capability and flavor; annotation evidence without build proof remains conditional where flavor or augmentation matters.
- Support multi-module Maven and Gradle projects without running the build. Inherited or shared build declarations may provide context but must not assign an extension to a module unless local build structure proves it.
- Recognize both `javax.*` and `jakarta.*` generations. Simple annotation names require matching imports or other explicit namespace proof; fully qualified annotations are accepted.
- Analyze Java source only. Parse Gradle Kotlin DSL as build configuration, detect Kotlin and Scala application sources as coverage gaps, and do not add a language parser in this capability.
- Model Quarkus CDI Evidence with class beans, producer methods and fields, field injection, constructor injection including the single-constructor Quarkus rule, initializer injection, producer parameters, and explicit qualifiers.
- Resolve CDI relationships only when structurally normalized types, qualifiers, profile conditions, and local candidates identify one result. Do not select candidates by simple name, field name, or convention.
- Treat explicit unique bean and consumer selection as high Confidence. Treat Container Dispatch and other source-supported augmentation behavior as medium Confidence because a Quarkus build is not executed.
- Treat `Instance`, `Provider`, `@All`, programmatic Arc/CDI lookup, synthetic beans, alternatives, default beans, selected alternatives, conditional beans, interceptors, decorators, and proxy dispatch conservatively. Prove only locally deterministic selections; otherwise emit Unresolved Items.
- Add repeatable Quarkus Build Profile Selection and Quarkus Runtime Profile Selection to the structured impact request and result. Expose distinct CLI options for each; retain the existing Spring profile behavior without reinterpreting it as Quarkus build selection.
- Apply build profiles to augmentation-time conditions and runtime profiles only to runtime-overridable configuration. When no relevant selection is supplied, profile-specific facts remain conditional.
- Recognize local properties, YAML, profile-aware files, inline profile keys, configuration mappings, configuration property injection, key renaming, nesting, naming strategies, defaults, optionality, and property expressions.
- Report configuration dependencies, sources, profile conditions, and precedence. Do not claim an effective runtime value when environment, system, `.env`, Kubernetes, Vault, custom ConfigSource, or another higher-priority external source may override it.
- Use one Quarkus REST Contract model for Quarkus REST, its earlier RESTEasy Reactive names, and RESTEasy Classic. Record the detected implementation flavor and preserve flavor-specific uncertainty.
- Build endpoint identity from explicit application/root paths, class and method paths, HTTP methods, and relevant configuration. Record media types, parameter roles, response types, reactive/streaming mode, and blocking metadata as contract evidence rather than attempting runtime routing.
- Follow resource interfaces, implementations, inheritance, and subresource locators only when each edge is explicit and the resulting target is unique.
- Model `@Route`, repeatable routes, `@RouteBase`, and explicit literal Vert.x router registrations as Quarkus HTTP Routes distinct from REST Contracts. Dynamic and regex routing remains unresolved unless a specific handler can be proven.
- Model typed MicroProfile REST Client interfaces, CDI injection, client configuration, and explicit calls as Quarkus REST Client Contracts. Support both current and legacy Quarkus client extensions.
- Resolve programmatic REST client builders only when interface type, base URI, and builder flow are explicit and unique. Treat raw Vert.x WebClient use as HTTP client evidence rather than a typed contract.
- Connect a local client contract to a local server only through shared annotated interfaces, an explicit local mapping, or uniquely proven local base configuration plus contract evidence. Matching names or paths alone never create the relationship.
- Associate standard and Quarkus security annotations and explicit path-based permission configuration with REST Contracts, HTTP Routes, and CDI methods. Do not simulate authentication, identity production, or authorization outcomes.
- Recognize Quarkus unit, component, integration, HTTP endpoint, test-profile, test-resource, and mock evidence. Integration and native test wiring is indirect, medium-confidence evidence rather than proof that an artifact starts or a test passes.
- Make the Native Image Boundary first-class in accordance with ADR-0002. Index source-controlled reflection, proxy, resources, resource bundles, serialization, JNI, service-provider, registration annotations, native configuration, DTO usage, and native test evidence.
- Do not run GraalVM, Mandrel, Quarkus augmentation, Maven, Gradle, Docker, or Podman. Do not claim complete native-image reachability or successful native compilation.
- Continue excluding generated build outputs from authoritative source discovery. A future opt-in Build Evidence Import must carry commit, build/runtime profiles, Quarkus/GraalVM versions, and build provenance before it can strengthen a conclusion.
- Recognize Quarkus Persistence Boundaries and preserve ordinary direct code relationships, but do not resolve Panache-generated methods, query languages, persistence-unit selection, transaction dispatch, or database behavior.
- Recognize Java command-mode and explicit startup/lifecycle entry evidence. Report scheduler, messaging, CDI event, GraphQL, gRPC, servlet, and other unsupported extension boundaries as unresolved when recognizable.
- Refresh older indexes automatically when required Quarkus, configuration, REST, test, or Native Image schemas are absent.
- Preserve Local-only Provenance, bounded Evidence Navigation, incremental refresh, Python and Tree-sitter implementation, small pinned dependencies, and the no-JRE requirement.

## Testing Decisions

- Test externally observable behavior primarily through the existing public application service using isolated fixture repositories. Use index, impact, and evidence-navigation requests through that single seam.
- A good test asserts Change Target resolution, relationship kind and direction, framework flavor, selected or conditional profiles, Confidence, ordered Evidence Handles, assumptions, Unresolved Items, and Index Snapshot provenance. It does not assert Tree-sitter traversal order, regex implementation, SQLite queries, or internal fact ordering.
- Keep CLI coverage thin. Prove build-profile and runtime-profile arguments, text/JSON parity, non-resolved exit behavior, and representative Quarkus/native rendering without duplicating the fixture matrix.
- Preserve the complete Java, Spring, EJB, indexing, evidence-navigation, incremental-refresh, and CLI suites as regression coverage.
- Cover both `javax.*` and `jakarta.*`, explicit and wildcard imports, fully qualified annotations, and false-positive same-named annotations.
- Cover Maven and Gradle Groovy/Kotlin DSL builds, current and legacy extension names, multi-module inheritance, missing descriptors, and ambiguous module capability.
- Cover class and producer beans, all supported Injection Point forms, single-constructor injection, explicit qualifiers, unique and ambiguous candidates, producer dependencies, invocation-backed dispatch, profiles, build properties, programmatic lookup, synthetic/default/alternative boundaries, and interceptor uncertainty.
- Cover ConfigProperty and ConfigMapping consumers, nested and renamed mappings, naming strategies, properties and YAML, inline and file-based profiles, parent and multiple profiles, property expressions, defaults, optionality, precedence, and external-override uncertainty.
- Cover Quarkus REST, earlier RESTEasy Reactive naming, RESTEasy Classic, resource interfaces and implementations, inherited contracts, subresources, root-path configuration, conditional endpoints, reactive/blocking methods, streaming metadata, filters/providers, and flavor ambiguity.
- Cover declarative Reactive Routes, repeated routes, route bases, literal programmatic router registration, blocking and failure handlers, streaming/SSE metadata, regex paths, dynamic composition, and conflicting route order.
- Cover typed REST clients, current and legacy client extensions, config keys and FQCN configuration, CDI injection and invocation, deterministic and dynamic programmatic builders, raw WebClient evidence, proven local contract identity, path-only candidates, and ambiguous local servers.
- Cover authorization annotations, permission annotations, role/property expressions, path-based security configuration, root paths, profile conditions, custom authentication/identity boundaries, and runtime authorization uncertainty.
- Cover Quarkus tests, integration tests, component tests, test profiles, test resources, HTTP endpoint/resource annotations, REST-assured literal calls, mock and spy wiring, legacy native tests, dynamic mocks, and black-box Confidence.
- Cover Native Image annotations, reflection/proxy/resource/serialization/JNI metadata, service-provider files, DTO associations, resource moves, class renames, native configuration, native tests, extension-generated uncertainty, and excluded stale build outputs.
- Cover Panache and persistence boundary detection, ordinary repository method calls, generated CRUD uncertainty, command-mode entry points, lifecycle evidence, unsupported extension boundaries, and mixed-language coverage gaps.
- Verify every Java, configuration, build, JSON, YAML, properties, and service-provider Evidence Handle through bounded Evidence Navigation.
- Smoke-test the completed capability against pinned local checkouts of representative official Quarkus quickstarts for CDI, configuration, REST, REST Client, Reactive Routes, security, testing, and native-oriented metadata. Public-project validation supplements but does not replace isolated fixtures.
- Verify no automated test requires a JRE, Java compiler, Quarkus CLI, Maven, Gradle, GraalVM, Mandrel, Docker, Podman, a running application, network access, remote Git access, database server, telemetry, cloud service, or background daemon.

## Out of Scope

- Cross-repository traversal, Workspace Catalog registration, verified remote HTTP continuation, production endpoint discovery, and OpenAPI ingestion.
- Requirement, Git diff, HTTP path, configuration key, class, API-contract, or native-resource Change Target forms; `Class#method` remains the supported target.
- Quarkus augmentation, application startup, Maven or Gradle execution, JVM tests, native-image compilation, container builds, and runtime deployment verification.
- Authoritative ingestion of `target`, `build`, `.quarkus`, generated bytecode, generated native metadata, or other build outputs. Provenance-backed Build Evidence Import is a later capability.
- Complete CDI/ArC behavior, including synthetic beans, programmatic lookup, dynamic alternatives, extension build items, interceptors, decorators, proxy semantics, unused-bean removal, and runtime bean-container state.
- Complete runtime configuration, external ConfigSources, environment or system values, `.env` values, Kubernetes, Vault, Consul, secrets, production profiles, and effective deployment state.
- Full HTTP runtime behavior, including provider ordering, filters, interceptors, exception mapping, content negotiation, route order simulation, network redirects, TLS, DNS, and deployed URLs.
- Standalone servlet and `web.xml` analysis, GraphQL, gRPC, WebSockets, SOAP, CORBA, and other non-REST application boundaries.
- Quarkus Messaging, Kafka, AMQP, RabbitMQ, JMS, CDI event dispatch, Scheduler, Quartz, and other asynchronous or triggered boundaries.
- Complete security behavior, including OIDC/JWT validation, identity providers, identity augmentation, authentication ordering, inclusive authentication, custom policy execution, and access-decision verification.
- Complete Panache, Hibernate ORM/Reactive, MongoDB Panache, Quarkus Data, Jakarta Persistence, query, transaction, persistence-unit, datasource, migration, and database analysis.
- Kotlin, Scala, Groovy application-source analysis. Gradle Kotlin DSL build evidence remains supported.
- Complete GraalVM closed-world reachability, extension-generated registration, native-image substitutions, class initialization analysis, binary inspection, or proof that reflection, proxy, resources, serialization, or JNI work at runtime.
- MCP adapter implementation; the feature extends the shared structured service result a later adapter can expose.
- Network access, Git fetching, telemetry, cloud calls, embeddings, vector databases, heavyweight services, Docker daemon access, or background daemons.

## Further Notes

- This is a broad parent capability and should be delivered as small vertical slices: framework/build detection; CDI and profile/configuration evidence; REST server contracts; Reactive Routes; REST clients and local contract identity; security and tests; Native Image Boundary; and final documentation plus public-project validation.
- Each child slice must produce a runnable, externally testable report behavior rather than only adding parser or schema infrastructure.
- Quarkus REST reactive execution still belongs to the synchronous HTTP request/response boundary for this capability. Messaging, Scheduler, and CDI events remain separate asynchronous or triggered stages.
- A local client/server relationship requires contract identity or explicit mapping evidence. Neither type names nor identical paths alone are sufficient.
- Native-aware analysis is mandatory because GraalVM is a primary deployment form for target applications, but ADR-0002 deliberately separates useful source evidence from build verification.
- The official references used to establish the capability boundary include the Quarkus guides for [CDI](https://quarkus.io/guides/cdi-reference), [configuration](https://quarkus.io/guides/config-reference), [REST](https://quarkus.io/guides/rest), [REST Client](https://quarkus.io/guides/rest-client), [Reactive Routes](https://quarkus.io/guides/reactive-routes), [testing](https://quarkus.io/guides/getting-started-testing), [security](https://quarkus.io/guides/security-authorize-web-endpoints-reference), and [native applications](https://quarkus.io/guides/writing-native-applications-tips).
