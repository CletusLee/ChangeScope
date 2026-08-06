# Spring and Spring Boot local impact analysis

## Problem Statement

ChangeScope can resolve a concrete Java method and report conservative direct callers, direct test calls, bounded evidence, and local provenance. It cannot yet describe the complete local neighborhood of a method in the Spring and Spring Boot applications that make up much of its legacy-code target: direct callees, bean wiring, configuration properties, profile-specific configuration, legacy XML wiring, and Spring-aware tests remain invisible. Engineers must manually reconstruct those relationships before deciding the impact of a change.

## Solution

Extend the existing local Repository Index and impact report so that a resolved concrete Java method includes proven direct callees and the Spring Configuration Boundary of its owning class. The report will recognize standard Spring/Spring Boot annotation and Java configuration, explicit legacy XML bean configuration, property consumption, property sources, selected Active Profile Selection, and Spring-aware tests. It will preserve the existing conservative evidence model: only explicit, locally indexed facts become asserted Evidence-backed Relationships; ambiguity and dynamic behavior remain Unresolved Items.

## User Stories

1. As an engineer, I want a method impact report to include direct callees, so that I can see the immediate downstream code affected by changing that method.
2. As an engineer, I want every reported direct callee to include source evidence and confidence, so that I can review the conclusion without trusting a hidden call graph.
3. As an engineer, I want direct callers and direct test calls to continue appearing exactly as they do today, so that the new feature preserves the first runnable slice.
4. As an engineer maintaining a Spring application, I want ChangeScope to show that a changed method belongs to a Spring-managed class, so that I can assess its configuration boundary.
5. As an engineer, I want the report to distinguish class-level Spring Configuration Boundary evidence from a direct method call, so that I do not mistake bean wiring for runtime invocation of the changed method.
6. As an engineer, I want ChangeScope to recognize standard Spring stereotype annotations and explicit Java bean factory methods, so that modern annotation-based applications are represented.
7. As an engineer, I want constructor and field injection relationships reported when they identify a single local bean candidate, so that I can see proven local bean consumers.
8. As an engineer, I want ambiguous bean candidates, qualifiers, primary selection, collections, and factory indirection called out for review when they cannot be proven under the selected rules, so that the report remains conservative.
9. As an engineer, I want `@Value` placeholders on the changed class to link to matching configuration keys, so that I can see the configuration input relevant to that class.
10. As an engineer, I want `@ConfigurationProperties` prefixes on the changed class to link to matching configuration keys, so that grouped configuration is visible.
11. As an engineer, I want `application.properties` and `application.yml` sources indexed locally, so that Spring Boot configuration evidence is inspectable.
12. As an engineer, I want to select one or more active Spring profiles for an impact analysis, so that the report reflects the intended local configuration scenario.
13. As an engineer, I want base configuration merged with selected profile-specific configuration, so that selected profile values and sources are reported together.
14. As an engineer who supplies no profile, I want profile-specific configuration reported as conditional evidence rather than assumed active, so that ChangeScope never guesses the runtime environment.
15. As an engineer maintaining a legacy Spring application, I want explicit XML `<bean>` definitions and `<property ref>` wiring indexed, so that XML-managed components participate in local impact analysis.
16. As an engineer, I want XML property-placeholder references recognized, so that explicit legacy property configuration is connected to consuming components.
17. As an engineer, I want component scanning, imported XML contexts, profile expressions, environment-variable overrides, SpEL, and conditional auto-configuration reported as unresolved when no selected rule proves them, so that omission is visible rather than silent.
18. As an engineer, I want Spring tests that explicitly inject or load the changed class to appear as indirect, medium-confidence test evidence, so that relevant integration tests are discoverable without asserting that all application tests are affected.
19. As an engineer, I want JSON and text reports to expose the same new relationship kinds, evidence handles, confidence, assumptions, and Unresolved Items, so that CLI users and future adapters see one consistent result.
20. As an engineer, I want all analysis to remain local-only and JRE-free, so that it remains suitable for legacy repositories and corporate review.
21. As an engineer, I want report provenance to continue identifying the Index Snapshot that supports every conclusion, so that framework findings remain reviewable against the local source state.

## Implementation Decisions

- Extend the single public application-service seam, `ChangeScopeApplication.execute`, rather than creating a parallel Spring-specific API. The CLI remains a thin adapter.
- Preserve `Class#method` as the supported Change Target form for this slice. Public-interface targeting and interface-to-implementation propagation remain later work.
- Extend the local Repository Index with explicit facts for configuration sources, Spring component declarations, bean wiring, property consumers, property keys, profile conditions, and Spring test context or injection evidence. Do not store a mirror of all source text.
- Extend the existing relationship model with direct callee, Spring configuration-boundary, bean-consumer, property-consumer, property-source, and Spring-test relationship kinds. Every asserted relationship carries an Evidence Handle, source range, and confidence.
- Derive direct callees only from explicit Java invocation syntax that can be conservatively tied to the resolved method. Calls whose receiver, overload, dispatch target, or generated/framework behavior cannot be proven are Unresolved Items.
- Treat Spring and Spring Boot as one staged framework capability. Recognize standard stereotype annotations, explicit Java `@Bean` factory methods, constructor injection, and field injection only when exactly one local candidate can be established by the selected rules.
- Treat qualifiers, `@Primary`, collection injection, inherited configuration, factory indirection, dynamic proxies, reflection, and unresolved candidate sets as conditional or unresolved rather than selecting a target by heuristic.
- Report the Spring Configuration Boundary at the owning-class level and with medium confidence. It is indirect impact evidence; it must never imply that Spring configuration directly invokes the changed method.
- Recognize `@Value("${…}")` placeholders and `@ConfigurationProperties(prefix = "…")` on the owning class, and link them to locally indexed matching keys from properties and YAML sources.
- Support base configuration plus explicitly selected Active Profile Selection values. The impact command accepts repeatable profile input; when no profile is supplied, profile-specific matches are reported as conditional evidence rather than selected configuration.
- Recognize explicit legacy XML `<bean class="…">`, `<property ref="…">`, and property-placeholder configuration. Component scans, XML imports, and profile expressions remain unresolved in this slice unless a later rule explicitly covers them.
- Classify tests using the existing test-source discovery. Add Spring-aware test evidence only when local source explicitly injects or loads the owning class; retain direct test invocation reporting unchanged.
- Preserve Local-only Provenance, bounded Evidence Navigation, incremental refresh behavior, Python and Tree-sitter implementation, and the no-JRE requirement.

## Testing Decisions

- Test observable behavior through the existing public application-service seam using isolated fixture Java repositories. Keep thin CLI coverage for text and JSON rendering, including the repeatable profile option.
- Preserve all existing tests as regression coverage. Add fixtures for direct callees, annotation-based bean wiring, Java bean factory methods, unique and ambiguous injection, property consumers, properties and YAML sources, selected and unselected profiles, explicit XML beans and references, and Spring-aware tests.
- Assert reported relationship kinds, source evidence, confidence, assumptions, Unresolved Items, profile behavior, and Index Snapshot provenance. Do not assert Tree-sitter traversal or SQLite query mechanics.
- Verify that selected profiles merge with base configuration and that absent profile selection leaves profile-specific findings conditional.
- Verify that ambiguous or unsupported Spring mechanisms produce visible Unresolved Items rather than asserted relationships.
- Verify text and JSON reporting expose equivalent structured facts, and that bounded evidence navigation continues to work for Java, XML, properties, and YAML evidence where applicable.
- Verify no test requires a JRE, a Java build, a running Spring application, network access, remote Git access, a database server, telemetry, cloud services, or background daemons.

## Out of Scope

- Public-interface Change Targets, interface-to-implementation propagation, complete overload or virtual-dispatch resolution, classpath loading, compiler integration, and any required JRE.
- WildFly, EJB, CDI, JAX-RS, servlet, JMS, SOAP, gRPC, CORBA, and other non-Spring framework or container relationship extraction.
- Cross-repository traversal, Workspace Catalog registration, HTTP/REST boundaries, C4/Structurizr output, Backstage integration, and OpenAPI ingestion.
- Complete Spring behavior, including component scans, XML imports, profile expressions, environment-variable overrides, SpEL, conditional auto-configuration, proxy resolution, dynamic configuration, and runtime environment discovery.
- Guessing bean selection from multiple candidates, names, or other weak heuristics.
- Network access, Git fetching, telemetry, cloud calls, embeddings, vector databases, background daemons, Docker access, or heavyweight external services.

## Further Notes

- This is the next vertical stage after the first runnable Java impact-analysis feature. It expands only locally verified evidence and retains the existing Confidence and Unresolved Item semantics.
- The selected Spring support is intentionally broad enough for modern Spring Boot and explicit legacy XML applications, while dynamic and convention-heavy behavior remains visible but unasserted.
