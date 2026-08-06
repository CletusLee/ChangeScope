# ChangeScope

ChangeScope explains the impact of a proposed code change before implementation begins. It produces reviewable conclusions backed by source evidence and makes uncertainty explicit.

## Language

**Change Target**:
The code element, API, or contract that a requested analysis treats as changed. In the first Java slice, a user may identify it as `Class#method`; if this matches multiple methods, the report records an ambiguity instead of selecting one.
_Avoid_: Search query, changed file

**Repository Index**:
The local SQLite representation of source, relationships, contracts, tests, and optional snippets for the repository in the current directory. `changescope index` builds or refreshes it by discovering conventional Java project layouts without requiring path arguments on the normal path. `changescope impact` may incrementally refresh locally changed files before analysis.
_Avoid_: Source mirror, workspace catalog

**Source Discovery**:
The automatic identification of Java source roots when indexing a repository. It detects conventional Maven, Gradle, Eclipse, and standard source layouts first; otherwise it scans Java files recursively while excluding repository metadata, build outputs, dependencies, and recognizable generated output, and reports what it included.
_Avoid_: Silent whole-directory scan

**Index Snapshot**:
The exact local source state represented by a Repository Index, identified by commit information when available and a working-tree status. It does not imply knowledge of commits or source that have not been fetched into the local checkout.
_Avoid_: Remote repository state, production state

**Local-only Provenance**:
The rule that ChangeScope analyzes only the working tree and Git objects already present in local checkouts. Every report identifies the Index Snapshot that supports it; ChangeScope does not fetch remote source or infer production state.
_Avoid_: Live remote analysis, production discovery

**Structural Java Analysis**:
The JRE-free analysis of Java source using Tree-sitter from Python. It produces syntax and source-location evidence for declarations, invocations, imports, and annotations, but does not claim complete type, overload, classpath, or virtual-dispatch resolution.
_Avoid_: Full semantic resolution

**Confidence**:
The strength of the evidence supporting a reported relationship. High means an unambiguous declaration and explicit indexed invocation; medium means strong structural evidence with an unresolved type, overload, or generated/framework detail; low means plausible but incomplete or non-unique evidence; unresolved means no specific relationship is asserted.
_Avoid_: Guarantee, runtime certainty

**Evidence-backed Relationship**:
An inferred connection between code elements that is supported by inspectable source, configuration, or generated-code evidence. It carries a confidence level rather than an assertion of runtime completeness.
_Avoid_: Guessed link, name match

**Evidence Handle**:
A stable identifier in an impact report that points to the source or configuration evidence for a conclusion. It lets a caller retrieve only the needed source context instead of embedding all snippets in the report.
_Avoid_: Inline source dump

**Evidence Chain**:
An ordered set of Evidence Handles that together proves one Evidence-backed Relationship across multiple source or configuration locations.
_Avoid_: Evidence list, source dump

**Evidence Navigation**:
The bounded retrieval of source behind an Evidence Handle. It starts with a small context window and can expand to an enclosing symbol or explicit file ranges; every oversized result declares truncation and continuation ranges.
_Avoid_: Unbounded source response

**Unresolved Item**:
A potentially relevant dynamic behavior that ChangeScope cannot connect to a specific target from available evidence. It remains in the impact report for an engineer to assess.
_Avoid_: Ignored case, unsupported behavior

**Framework Target**:
An application framework or container whose source, annotations, descriptors, and conventions ChangeScope recognizes as evidence for relationships. The target framework family is WildFly-based applications, Spring Framework, Spring Boot, and Quarkus; support is delivered in stages.
_Avoid_: Generic Java application

**Quarkus Local Analysis**:
The repository-local analysis of synchronous Quarkus relationships across CDI-managed code, configuration, REST resources and clients, and tests. Cross-repository traversal and asynchronous dispatch are separate staged capabilities.
_Avoid_: Complete Quarkus runtime model, cross-repository Quarkus analysis

**Quarkus CDI Evidence**:
Quarkus or standard CDI source that explicitly identifies managed beans, producers, qualifiers, and Injection Points. It supports conservative local relationship resolution but does not imply complete ArC build-time or runtime bean selection.
_Avoid_: Complete bean graph, runtime container state

**Quarkus Build Profile Selection**:
The explicit Quarkus profile or profiles used to assess augmentation-time configuration and conditional bean availability. It is distinct from the profile selected when an already-built application runs.
_Avoid_: Runtime profile, inferred deployment profile

**Quarkus Runtime Profile Selection**:
The explicit Quarkus profile or profiles used to assess runtime-overridable configuration. It does not change bean availability or configuration already fixed by the Quarkus Build Profile Selection.
_Avoid_: Build profile, discovered production state

**Quarkus Configuration Evidence**:
Local Quarkus configuration consumers, keys, sources, profile conditions, and precedence facts that prove a configuration dependency. It does not claim an effective runtime value when higher-priority external overrides remain possible.
_Avoid_: Deployment configuration, effective runtime state

**Quarkus REST Contract**:
A repository-local HTTP request/response contract expressed through Java REST annotations and supported by Quarkus REST or RESTEasy Classic evidence. Both implementation flavors share this contract model while retaining their distinct framework and execution constraints.
_Avoid_: Deployed endpoint, generic method call

**Quarkus HTTP Route**:
A repository-local HTTP handler declared through a Quarkus Reactive Route or an explicit Vert.x router registration. It is an HTTP boundary with its own route semantics, not a Quarkus REST Contract.
_Avoid_: JAX-RS resource, generic callback

**Quarkus REST Client Contract**:
A typed repository-local HTTP client contract expressed through Java REST and MicroProfile REST Client evidence. It may connect to a local server contract only when shared contract identity or explicit local mapping evidence proves the relationship.
_Avoid_: Path-name match, arbitrary HTTP request

**Quarkus Persistence Boundary**:
Local extension, entity, repository, and configuration evidence that identifies entry into Quarkus-managed persistence behavior. It makes unsupported generated CRUD, query, transaction, and persistence-unit dispatch visible without asserting those relationships.
_Avoid_: Resolved database operation, complete persistence graph

**Quarkus Test Evidence**:
Quarkus-specific source or configuration that explicitly connects a test, test profile, test resource, mock, or HTTP test target to an analyzed item. Black-box integration intent remains indirect evidence rather than proof of successful application startup or dispatch.
_Avoid_: Passing test run, complete affected-test set

**Native Image Boundary**:
The repository-local source, configuration, metadata, resource, proxy, reflection, serialization, service-provider, and test evidence that may affect a GraalVM native image. It identifies native-specific impact and risk without claiming that an image builds or that its closed-world reachability is complete.
_Avoid_: Native build verification, complete reachability graph

**Quarkus Security Evidence**:
Local annotations and configuration that explicitly associate an authorization or authentication policy with a Quarkus REST Contract, Quarkus HTTP Route, or CDI-managed method. It identifies the affected security boundary without claiming an authentication outcome or runtime access decision.
_Avoid_: Verified authorization, production identity state

**Spring Configuration Evidence**:
Spring or Spring Boot source and configuration that explicitly connects a component or property consumer to a bean or property key. The first Spring stage recognizes stereotype and bean-factory wiring, injection, explicit XML `<bean>` and `<property ref>` wiring, XML property placeholders, `@Value` placeholders, `@ConfigurationProperties` prefixes, `application.properties`, `application.yml`, and profile-specific configuration files.
_Avoid_: Assumed Spring behavior, runtime environment state

**Spring Configuration Boundary**:
The class-level Spring wiring and property evidence surrounding a changed method's owning class. It is reported as indirect, medium-confidence impact evidence and does not imply that Spring configuration invokes the changed method itself.
_Avoid_: Direct method caller, proven runtime invocation

**Active Profile Selection**:
The explicit Spring profile or profiles supplied for an analysis. ChangeScope merges base configuration with the selected profile-specific configuration; without a selection, profile-specific evidence is reported as conditional rather than active.
_Avoid_: Inferred runtime environment, default deployment profile

**Source Compatibility Baseline**:
The oldest Java source language level that ChangeScope can analyze. The initial baseline is Java 5, with Java 6 and later also supported.
_Avoid_: Runtime requirement, deployment JVM

**WildFly Container Evidence**:
The WildFly and Java EE evidence ChangeScope recognizes for relationship discovery: EJB local and remote interfaces, `@EJB` injection, JNDI references, CDI injection and qualifiers, JAX-RS resources, servlet mappings, and relevant `web.xml` and `ejb-jar.xml` descriptors.
_Avoid_: Generic framework hint

**EJB Business Interface**:
A Java interface identified as local or remote by explicit EJB source annotation or deployment-descriptor evidence that defines the callable contract exposed by a Session Bean.
_Avoid_: Service name, endpoint

**Session Bean**:
A class marked `@Stateless`, `@Stateful`, or `@Singleton` that implements an EJB Business Interface.
_Avoid_: Generic bean, Spring bean

**EJB Injection Point**:
A field or setter marked `@EJB` through which a consumer declares an EJB Business Interface dependency.
_Avoid_: Direct caller, JNDI lookup

**EJB Business View**:
The local or remote exposure mode through which a Session Bean provides an EJB Business Interface.
_Avoid_: Architecture View, runtime endpoint

**Container Dispatch**:
The WildFly-managed runtime step from an EJB Business Interface reference to a Session Bean. Source evidence can identify candidates, but deployment packaging or configuration may keep the dispatch from being fully proven.
_Avoid_: Direct method call, static dispatch

**Workspace Catalog**:
The authoritative local catalog of registered repositories, logical contract identities, verified cross-repository relationships, and their analysis provenance. It does not become a copy of application source code.
_Avoid_: Architecture diagram, source mirror

**Declared Dependency**:
An application or API relationship supplied by a catalog such as Backstage. It is useful context but is not proof of a code-level relationship until ChangeScope verifies it with evidence.
_Avoid_: Verified relationship

**Architecture View**:
A generated C4/Structurizr visualization of applications and their relationships. It is a readable projection of the Workspace Catalog, not ChangeScope’s source of truth.
_Avoid_: Analysis database

**Cross-application Boundary**:
An evidence-backed interface through which analysis can continue from one registered application repository into another. The first release covers HTTP/REST; remote EJB and other boundary kinds are explicitly staged extension work.
_Avoid_: Production hostname, name-based repository link

**Asynchronous Boundary**:
A cross-application boundary where one application publishes a message and another consumes it without a synchronous call. JMS and message-driven EJB interactions are planned after the initial HTTP/REST release and the WildFly/EJB extension.
_Avoid_: Direct caller relationship

**JMS Evidence**:
Source or configuration evidence that links a message producer to a named queue or topic, or a message-driven EJB consumer to its destination. It includes JNDI, `@Resource`, `JMSContext`, older JMS APIs, and message-driven bean activation configuration.
_Avoid_: Assumed message flow
