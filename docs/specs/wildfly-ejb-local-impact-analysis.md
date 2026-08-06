# WildFly EJB local impact analysis

## Problem Statement

Engineers maintaining legacy WildFly applications cannot currently use ChangeScope to follow an EJB Business Interface through Container Dispatch to its Session Bean, consumers, configuration, and tests. These relationships are often expressed through EJB annotations or deployment descriptors rather than direct Java construction. Production URLs and deployment state are commonly unavailable before release, so HTTP/REST traversal would not address many of these applications. Engineers need a useful repository-local EJB impact path that proves what source and configuration establish, distinguishes local from remote views, and exposes everything the available evidence cannot resolve.

## Solution

Extend the existing Repository Index and impact report so that `Class#method` can identify either an EJB Business Interface method or its Session Bean method and report the local EJB impact neighborhood. ChangeScope will connect inherited EJB Business Interfaces, Session Bean implementations, EJB Injection Points, explicit method usage, deployment-descriptor declarations and references, and affected tests. It will support both `javax.ejb` and `jakarta.ejb` while retaining local-only, JRE-free Structural Java Analysis.

Every asserted relationship will carry Confidence and an ordered chain of Evidence Handles. Container behavior that cannot be proven without deployment state will remain medium confidence or become an Unresolved Item. The Repository Index remains the source of truth in accordance with ADR-0001.

## User Stories

1. As an engineer maintaining a WildFly application, I want to analyze an EJB Business Interface method, so that I can see the local impact behind the container-managed contract.
2. As an engineer maintaining a WildFly application, I want to analyze a Session Bean method, so that I can see the EJB Business Interfaces and consumers that may depend on its implementation.
3. As an engineer, I want interface and implementation targets to use the existing `Class#method` form, so that EJB analysis fits the established ChangeScope workflow.
4. As an engineer, I want overloaded targets to remain ambiguous when the existing target syntax cannot select a signature, so that ChangeScope does not silently choose an overload.
5. As an engineer, I want `javax.ejb` annotations recognized, so that legacy Java EE applications are supported.
6. As an engineer, I want `jakarta.ejb` annotations recognized, so that newer Jakarta EE applications use the same analysis path.
7. As an engineer, I want EJB annotations recognized only when their imports or fully qualified names prove the EJB namespace, so that unrelated annotations with the same simple name are not treated as WildFly Container Evidence.
8. As an engineer, I want `@Local` EJB Business Interfaces reported as local views, so that their scope is clear.
9. As an engineer, I want `@Remote` EJB Business Interfaces reported as remote views, so that I do not mistake repository-local findings for a complete consumer list.
10. As an engineer targeting a remote view, I want the report to state that external consumers may exist outside the Repository Index, so that cross-repository uncertainty remains visible.
11. As an engineer, I want `@Stateless` Session Beans recognized, so that the most common EJB service implementation participates in impact analysis.
12. As an engineer, I want `@Stateful` Session Beans recognized, so that conversational EJB implementations participate in impact analysis.
13. As an engineer, I want `@Singleton` Session Beans recognized, so that singleton EJB implementations participate in impact analysis.
14. As an engineer, I want a proven EJB Business Interface-to-Session Bean relationship, so that I can review the implementation behind a changed contract.
15. As an engineer, I want transitive EJB Business Interface inheritance followed safely, so that inherited business methods are not omitted.
16. As an engineer, I want every inheritance edge in a conclusion backed by source evidence, so that transitive results remain reviewable.
17. As an engineer, I want inheritance cycles handled safely, so that malformed or unusual source cannot make analysis loop indefinitely.
18. As an engineer, I want business methods matched by name and structurally normalized parameter types, so that methods are not connected by name alone.
19. As an engineer, I want uncertain generic substitution, imported-type equivalence, and override dispatch reported as unresolved, so that Structural Java Analysis does not claim semantic resolution.
20. As an engineer, I want `@EJB` field injection recognized when its declared type identifies one EJB Business Interface and Session Bean, so that affected consumers are visible.
21. As an engineer, I want `@EJB` setter injection recognized under the same conservative rules, so that legacy setter-based consumers are visible.
22. As an engineer, I want explicit calls through an EJB Injection Point connected to the target method when syntax and the indexed contract prove the relationship, so that relevant callers are not reduced to class-level wiring.
23. As an engineer, I want multiple eligible Session Beans reported as unresolved, so that ChangeScope never selects a bean by a name heuristic.
24. As an engineer, I want `beanName`, `mappedName`, `lookup`, and other naming-based selection reported conservatively when it cannot be proven locally, so that deployment naming is not guessed.
25. As an engineer maintaining descriptor-driven applications, I want explicit `ejb-jar.xml` Session Bean declarations indexed, so that annotations are not required for useful analysis.
26. As an engineer maintaining descriptor-driven applications, I want explicit local and remote business-interface declarations indexed, so that descriptor-backed contracts participate in the same relationship model.
27. As an engineer, I want explicit descriptor EJB references connected when the interface and bean link are locally provable, so that legacy XML wiring becomes reviewable impact evidence.
28. As an engineer, I want descriptor overrides and indirection reported as unresolved when they cannot be reconciled, so that configuration uncertainty is visible.
29. As an engineer, I want each EJB relationship to expose an ordered evidence chain, so that I can inspect the interface, inheritance, bean, injection, invocation, and descriptor evidence behind one conclusion.
30. As an existing API consumer, I want the current primary evidence fields retained, so that adding evidence chains does not unnecessarily break existing report consumers.
31. As an engineer, I want interface-to-implementation structure reported with high confidence when every edge is explicit and unique, so that strong source evidence remains distinguishable from container assumptions.
32. As an engineer, I want Container Dispatch reported with medium confidence, so that a source-supported candidate is not presented as verified runtime deployment behavior.
33. As an engineer, I want tests containing EJB Injection Points reported as `ejb_test`, so that the affected integration-test surface is visible.
34. As an engineer, I want EJB test wiring reported with medium confidence, so that test intent is not confused with successful application-server deployment.
35. As an engineer, I want existing direct test-call findings preserved, so that EJB support does not replace current Java relationships.
36. As an engineer, I want recognizable `@MessageDriven`, `@LocalBean`, implicit no-interface, JNDI, and unsupported inheritance behavior represented as Unresolved Items, so that unsupported mechanisms do not disappear silently.
37. As an engineer, I want older Repository Indexes refreshed when the EJB relationship schema is absent, so that adopting the feature does not require manual database repair.
38. As a CLI or future MCP consumer, I want human-readable and JSON reports to expose the same EJB relationships, view type, Confidence, evidence chains, assumptions, and Unresolved Items, so that all adapters share one result.
39. As a security-conscious organization, I want analysis to remain local-only and free of telemetry or remote source discovery, so that the feature remains suitable for corporate review.
40. As an engineer working without a JRE or application server, I want fixture repositories to be analyzable structurally, so that legacy WildFly analysis remains easy to run.

## Implementation Decisions

- Extend the existing public application-service seam; the CLI remains a thin adapter and a future MCP adapter must consume the same structured result.
- Preserve `Class#method` as the supported Change Target form. Interface and Session Bean targets work symmetrically; signature-qualified targeting remains later work.
- Add explicit indexed facts for Java type relationships, EJB Business Interfaces, Session Beans, EJB Injection Points, deployment-descriptor declarations and references, view type, and EJB-specific uncertainty.
- Recognize both `javax.ejb` and `jakarta.ejb`. Simple annotation names require matching explicit or wildcard imports; fully qualified annotations are also accepted.
- Recognize `@Local` and `@Remote` on an interface or Session Bean, including explicit interface lists.
- Recognize `@Stateless`, `@Stateful`, and `@Singleton`. Message-driven beans remain part of the later Asynchronous Boundary stage.
- Traverse `interface extends interface` relationships transitively with cycle protection. Session Bean class inheritance, abstract base-bean dispatch, default-method dispatch, and generic substitutions remain unresolved.
- Match corresponding methods by method name and normalized parameter-type syntax. Do not infer semantic equivalence that requires a classpath or compiler.
- Resolve `@EJB` field and setter injection only when the declared EJB Business Interface leads to one indexed Session Bean under the supported rules.
- Recognize explicit descriptor Session Bean declarations, local and remote business interfaces, EJB names, implementation classes, and EJB references. Descriptor evidence participates in the same relationship model as annotation evidence.
- Treat arbitrary JNDI state, unsupported naming attributes, descriptor overrides, and ambiguous candidate sets as unresolved unless explicit local evidence uniquely proves a relationship.
- Add `ejb_business_implementation`, `ejb_injection`, `ejb_container_dispatch`, and `ejb_test` relationship kinds.
- Record `local` or `remote` on EJB relationships. A remote view always declares the possibility of consumers outside the Repository Index.
- Assign high Confidence to explicit unique interface-to-implementation and consumer-to-interface relationships. Assign medium Confidence to Container Dispatch and EJB test wiring because deployment packaging is not proven.
- Extend each relationship with an ordered evidence chain while retaining its current primary evidence fields for compatibility.
- Refresh an existing Repository Index automatically when the required Java type or EJB schema is missing.
- Preserve ADR-0001: indexed evidence, Confidence, Unresolved Items, and provenance remain authoritative; source annotations or deployment descriptors alone do not bypass the evidence model.

## Testing Decisions

- Test externally observable behavior primarily through the existing public application service using isolated fixture repositories. This is the highest stable seam and matches the current Java and Spring test strategy.
- Use one thin CLI end-to-end seam to prove that text and JSON render the same structured EJB result. Do not duplicate the behavioral fixture matrix at the CLI layer.
- A good test asserts Change Target resolution, affected relationship kinds and direction, local or remote view, Confidence, ordered Evidence Handles, Unresolved Items, and Index Snapshot provenance. It does not assert Tree-sitter traversal order, regex implementation, or internal SQLite queries.
- Preserve all existing Java, evidence-navigation, indexing, and Spring tests as regression coverage.
- Cover `javax.ejb` and `jakarta.ejb`, explicit and wildcard imports, false-positive annotation names, all three supported Session Bean types, local and remote views, interface and implementation targets, direct and transitive interface inheritance, inheritance cycles, unique and ambiguous injection, field and setter injection, explicit invocation through an EJB Injection Point, EJB-aware tests, descriptor-backed beans and references, naming/JNDI uncertainty, no-interface and message-driven exclusions, evidence chains, and automatic old-index refresh.
- Verify that no test requires a JRE, Java compiler, WildFly installation, Maven or Gradle execution, network access, remote Git access, database server, telemetry, cloud service, or background daemon.

## Out of Scope

- Message-driven beans, JMS producers and consumers, and all other Asynchronous Boundary analysis.
- CDI injection, qualifiers, interceptors, decorators, and CDI event dispatch.
- JAX-RS, servlet, SOAP, gRPC, CORBA, and other application boundaries.
- Arbitrary JNDI lookup resolution or knowledge of runtime naming registries.
- `@LocalBean`, implicit no-interface views, Session Bean class inheritance, abstract base-bean methods, default-method dispatch, generic substitution, and complete Java semantic resolution.
- Arquillian deployment assembly, embedded-container behavior, or proof that a WildFly deployment starts successfully.
- Cross-repository remote-EJB traversal, Workspace Catalog mappings, and production deployment discovery.
- HTTP/REST cross-repository analysis and Quarkus-specific behavior.
- MCP adapter implementation; this feature extends the shared structured service result that a later adapter can expose.

## Further Notes

- This feature is intentionally larger than a minimal annotation recognizer: it must produce a useful local EJB path for real legacy applications, including explicit deployment descriptors and inherited business interfaces.
- Production URLs are not required because the feature analyzes repository-local EJB contracts and container evidence rather than deployed HTTP endpoint addresses.
- Delivery should still be decomposed into tracer-bullet tickets that each produce a runnable, externally testable behavior.
