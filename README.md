# ChangeScope

Local-first, evidence-backed impact analysis for Java, Spring Boot, and WildFly repositories.

## The problem

Before changing legacy code, an engineer needs to know more than where a name appears in a text search. The useful question is:

> If this method, component, configuration key, or contract changes, what local code and tests may be affected, and what source evidence proves it?

ChangeScope answers a deliberately smaller version of that question for a checked-out repository. It builds a local SQLite index, analyzes Java source structurally with Tree-sitter, and produces a reviewable report containing:

- the interpreted change target;
- affected relationships grouped by their evidence-backed kind;
- source paths and line ranges;
- confidence levels;
- assumptions and unresolved items; and
- the local Git snapshot used for the analysis.

ChangeScope is not a generic code-search, code-memory, or full semantic call-graph product. Its primary design rule is conservative: an unsupported or ambiguous relationship is reported as unresolved instead of being guessed.

## Current status

The current release is a JRE-free Java, Spring/Spring Boot, and annotation-backed WildFly EJB local-analysis slice. It is intended to answer `Class#method` impact questions inside one repository. The application service is exposed through a small CLI today; an MCP adapter is not implemented yet.

The implementation has been smoke-tested against four public Spring Boot applications: [Spring Petclinic](https://github.com/spring-projects/spring-petclinic), [Spring Petclinic REST](https://github.com/spring-petclinic/spring-petclinic-rest), [Spring Petclinic Modulith](https://github.com/spring-petclinic/spring-petclinic-modulith), and the [RealWorld Spring Boot application](https://github.com/gothinkster/spring-boot-realworld-example-app). In the current validation run, all four completed indexing with zero Java parse failures and zero file-read failures; representative MVC, REST, profile-aware, event-driven, MyBatis, Security, GraphQL, and Spring-test impact queries resolved successfully.

This validates the local indexing and reporting workflow. It does not claim that the reports are complete runtime dependency graphs.

## What it can do today

### Repository indexing

`changescope index` analyzes the current working directory as one repository root. It can:

- discover Maven, Gradle, Eclipse, and conventional `src/main/java` / `src/test/java` layouts;
- fall back to a constrained recursive Java scan for irregular repositories;
- exclude common metadata, build, dependency, vendor, generated-output, and local-index directories;
- index Java declarations, method invocations, annotations, source locations, and test-source classification;
- discover local `.properties`, `.yml`, `.yaml`, and `.xml` configuration files in conventional resource/config locations;
- record unreadable files and Java parse failures instead of silently omitting them;
- store the local Repository Index in `.changescope/index.sqlite`; and
- record available Git commit and working-tree provenance in the Index Snapshot.

The index contains structural facts and evidence locations. It is not a mirror of the entire source repository.

### Method target resolution

`changescope impact Class#method` resolves a target from indexed Java declarations.

- One matching method produces a `resolved` target with its signature and declaration evidence.
- Multiple matches produce an `ambiguous` result with candidates; overloaded methods are not silently selected.
- No match produces a `not_found` result.
- A resolved target is not the same as a complete impact graph. The report can still contain unresolved items.

### Local Java relationships

For a resolved target, the current structural rules can report:

- explicit direct callers when the receiver is provably the target owner or a direct construction of it;
- possible same-owner callers when the syntax is strong but the receiver is not fully resolved;
- direct test calls when the caller is in a discovered test source root;
- direct callees when a call can be tied to exactly one local declaration under the conservative rules; and
- source evidence and confidence for every asserted relationship.

The report also records unresolved calls whose receiver, overload, or dispatch target cannot be proven.

### Local Spring and Spring Boot evidence

The current Spring slice can recognize and connect evidence for:

- stereotype-managed classes using `@Component`, `@Service`, `@Repository`, `@Controller`, `@RestController`, or `@Configuration`;
- explicit Java `@Bean` factory methods;
- unique local field or constructor injection using `@Autowired`, `@Inject`, or `@Resource` when the selected rules identify one candidate;
- `@Value` property consumers;
- `@ConfigurationProperties` prefixes;
- keys and values from `application.properties`, profile-specific properties, `application.yml`, and `application.yaml`;
- explicit Spring XML `<bean>` definitions and `<property ref="...">` references;
- XML property placeholders and matching local property sources; and
- Spring-aware tests that explicitly load or target a changed class through supported test annotations.

These findings are represented as distinct relationship kinds, including `spring_configuration_boundary`, `bean_consumer`, `property_consumer`, `property_source`, and `spring_test`. A Spring configuration boundary is class-level, indirect evidence; it must not be read as proof that Spring directly invokes the changed method.

### WildFly EJB evidence

The current WildFly slice can recognize and connect evidence for:

- `javax.ejb` and `jakarta.ejb` `@Local` and `@Remote` business interfaces;
- `@Stateless`, `@Stateful`, and `@Singleton` Session Beans;
- explicit interface-to-implementation relationships in both target directions;
- local and remote view metadata, including uncertainty about consumers outside the Repository Index;
- unique `@EJB` field and setter Injection Points with explicit invocation evidence and medium-confidence container dispatch;
- descriptor-backed `ejb-jar.xml` Session Beans, business views, and explicit injection targets;
- EJB-aware test wiring under Maven/Gradle/Eclipse test source roots, reported separately from production injection; and
- ordered Evidence Chains for the relationship facts supporting each conclusion.

Descriptor conflicts, incomplete links, and naming-based selection remain explicit unresolved items. Inherited EJB Business Interfaces and injection-backed dispatch are supported with conservative structural matching; implicit no-interface views, Session Bean class inheritance, message-driven beans, CDI, arbitrary JNDI lookup or naming selection, and other container mechanisms remain unresolved or out of scope for the current slice.

### Profiles, reports, and evidence navigation

The impact command accepts repeatable profile selection:

```text
--profile h2 --profile spring-data-jpa
```

Selected profiles are treated as active. Base configuration is combined with matching profile-specific configuration. Without an explicit profile, profile-specific findings remain conditional instead of being assumed active.

Reports are available as human-readable text or JSON. JSON exposes the same structured facts as text, including:

- the requested and resolved target;
- relationships, confidence, profile, conditional status, and evidence handles;
- assumptions;
- unresolved items; and
- Index Snapshot provenance.

Evidence handles support bounded retrieval so a caller can inspect only the relevant source context:

- `changescope evidence <handle>` returns a small context window;
- `--enclosing-symbol` expands to the enclosing method or constructor;
- `--max-characters` applies a response budget; and
- `changescope source <path> <start-line> <end-line>` retrieves an explicit bounded range.

Oversized results report truncation and a continuation position rather than silently returning incomplete source.

## What it cannot do yet

The following limitations are intentional and should be treated as part of the current contract.

### Java and runtime limitations

ChangeScope currently cannot:

- perform complete Java type or classpath resolution;
- compile the application or require a JRE/JDK;
- prove all overload selection, inheritance, interface implementation, virtual dispatch, or generic type relationships;
- resolve reflection, method handles, dynamic proxies, generated code, annotation processors, or framework-generated calls;
- infer relationships from names alone;
- analyze Python, JavaScript/TypeScript, C/C++, C#, Go, VB.NET, or VB6 source in the current CLI; or
- accept a requirement, Git diff, API contract, or arbitrary symbol as a target. The current target form is `Class#method`.

### Spring and Spring Boot limitations

The current Spring support does not fully resolve:

- component scanning and imported configuration;
- conditional auto-configuration;
- profile expressions such as `!prod`, `prod & region`, or other dynamic profile logic;
- environment-variable overrides and SpEL expressions;
- `@Primary`, qualifiers, named resources, collection injection, or multiple bean candidates;
- inherited configuration, factory indirection, proxy/advice behavior, AOP dispatch, or runtime bean selection;
- all Spring test loading behavior; or
- runtime property values, deployment environment, actuator state, or application startup behavior.

Unsupported mechanisms appear as unresolved items when the analyzer can identify them. They are not silently promoted to high-confidence relationships.

### Repository and application-boundary limitations

The current release analyzes one local checkout at a time. It does not yet:

- traverse a Workspace Catalog or cross-repository relationship;
- connect an HTTP/REST client to a remote handler or target repository;
- analyze CDI, JAX-RS, JMS, SOAP, gRPC, CORBA, or other non-Spring application boundaries;
- ingest OpenAPI or other external contract catalogs;
- run the Spring Boot application or its Maven/Gradle tests as part of impact analysis; or
- discover production state, remote source, or remote Git objects.

The REST and GraphQL sample applications used for validation are therefore analyzed as local Java repositories. Their network endpoints are not treated as proven cross-repository contracts.

## Installation

Requirements:

- Python 3.10 or newer;
- no local JRE or JDK requirement for ChangeScope itself; and
- a checked-out repository to analyze.

Create an environment and install the package in editable mode:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

On macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

The Java parser dependencies are intentionally pinned. In particular, `tree-sitter==0.25.2` is paired with `tree-sitter-java==0.23.5`; using the incompatible 0.26 runtime can cause native parser failures on real-world Java syntax.

## CLI quick start

Run these commands from the root of the repository you want to analyze:

```bash
# Build or refresh the local SQLite index.
changescope index

# Ask for a human-readable impact report.
changescope impact OwnerController#processFindForm

# Select one or more Spring profiles and return JSON.
changescope impact OwnerController#processFindForm \
  --profile h2 --profile spring-data-jpa --format json

# Retrieve evidence returned by an impact report.
changescope evidence "declaration:src/main/java/example/OrderService.java:3-8" \
  --enclosing-symbol --format json

# Retrieve an explicit bounded source range.
changescope source src/main/java/example/OrderService.java 3 8 --format json
```

The CLI also works as a module:

```bash
python -m changescope --help
```

`impact` exits successfully only when the requested target is resolved. Ambiguous and not-found targets use a non-zero exit code so automation cannot mistake an uncertain target for a completed analysis.

## Development and verification

Run the repository test suite with:

```bash
python -m unittest discover -s tests -v
```

The current test suite covers repository discovery, Java structural facts, direct callers and callees, ambiguity, incremental refresh, Spring configuration, profiles, properties, YAML, XML, bounded evidence navigation, WildFly EJB contracts, injection-backed dispatch, descriptor-backed contracts, EJB-aware tests, unsupported container behavior, and CLI text/JSON consistency. The latest validation run completed 103 tests successfully.

## Design principles

- Evidence before breadth: every asserted relationship has inspectable local source evidence.
- Conservative confidence: uncertainty is part of the result, not an internal error.
- Local-only by default: no telemetry, cloud calls, remote fetching, embeddings, vector databases, daemons, or Docker daemon access.
- JRE-free analysis: the first Java workflow uses Python and Tree-sitter rather than a JVM semantic resolver.
- One application seam: the CLI delegates to `ChangeScopeApplication.execute`, leaving room for future adapters without duplicating analysis behavior.
- Small vertical slices: new framework and language support should add a runnable, tested report behavior rather than an unverified broad call graph.

## Direction of future work

Future stages may add richer container naming resolution, requirement/diff/API targets, deeper Java resolution, verified HTTP/REST cross-repository analysis, Workspace Catalog support, Quarkus, and additional framework or language adapters. Those capabilities are not part of the current release and should not be inferred from the current reports.
