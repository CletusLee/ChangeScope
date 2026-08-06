# First runnable Java impact-analysis slice

## Problem Statement

Engineers and AI coding tools working on legacy Java repositories cannot safely estimate the impact of changing a method by searching text alone. They need a local, reviewable answer that identifies the requested Change Target, shows direct callers and tests with source evidence, makes confidence explicit, and refuses to guess when the target is ambiguous or dynamic behavior cannot be proven.

## Solution

Provide the first local ChangeScope workflow for one checked-out Java repository:

1. `changescope index` treats the current directory as the repository root, discovers Java sources, and creates or refreshes a local Repository Index.
2. `changescope impact Class#method` refreshes locally changed files when necessary, resolves the Change Target or reports ambiguity, and emits a compact impact report.
3. `changescope evidence` and `changescope source` let people and AI tools retrieve bounded source evidence without receiving an entire large legacy file by default.

The feature uses JRE-free Structural Java Analysis through Python and Tree-sitter. It reports only direct, statically recognizable method-invocation relationships, along with Confidence and Unresolved Items. It analyzes only local source and records an Index Snapshot in every report.

## User Stories

1. As an engineer, I want to run `changescope index` in a repository root, so that I do not need to configure source paths for ordinary Java projects.
2. As an engineer with a nonstandard legacy layout, I want source discovery to fall back to a constrained recursive Java scan, so that the tool remains useful outside modern build conventions.
3. As an engineer, I want the index summary to show discovered roots, included files, and exclusions, so that I know what source was analyzed.
4. As an engineer, I want build output, repository metadata, dependencies, and recognizable generated output excluded by default, so that analysis does not confuse them with application source.
5. As an engineer, I want a local SQLite Repository Index, so that repeated impact queries do not need to rescan every source file.
6. As an engineer, I want `changescope impact Class#method` to use the index for the current repository, so that the normal query is concise and repeatable.
7. As an engineer, I want a Change Target written as `Class#method`, so that I can begin an analysis without knowing a fully qualified signature.
8. As an engineer, I want an ambiguous Change Target to list every matching overload, so that ChangeScope never silently chooses the wrong method.
9. As an engineer, I want an unambiguous target to identify its resolved declaration, so that I can verify the interpretation before acting on the impact.
10. As an engineer, I want direct, explicit Java method invocations reported as affected callers, so that I can review concrete source relationships.
11. As an engineer, I want direct tests of the Change Target reported, so that I can assess the immediate verification surface.
12. As an engineer, I want every affected item to include source path and line range, so that I can inspect the proof behind the conclusion.
13. As an engineer, I want each relationship to have High, Medium, or Low Confidence, so that I can prioritize review correctly.
14. As an engineer, I want dynamic or insufficiently resolved behavior reported as an Unresolved Item, so that uncertainty remains visible instead of becoming a false dependency.
15. As an AI coding tool, I want a compact JSON impact report with stable Evidence Handles, so that I can decide which source context to retrieve without consuming unnecessary tokens.
16. As an AI coding tool, I want a human-readable default report, so that the same command remains useful in an interactive terminal.
17. As an AI coding tool, I want to retrieve a bounded context window for an Evidence Handle, so that I can inspect the relevant statement and surrounding code.
18. As an AI coding tool, I want to expand an evidence window, retrieve an enclosing symbol, or request explicit source ranges, so that I can navigate poorly structured large files progressively.
19. As an AI coding tool, I want oversized source responses to state that they are truncated and provide continuation ranges, so that no source is silently omitted.
20. As an engineer, I want `impact` to refresh locally changed files incrementally, so that I do not accidentally review a stale index after editing code.
21. As an engineer, I want the report to identify the Index Snapshot, including available Git revision data and working-tree state, so that conclusions are reproducible.
22. As an engineer, I want ChangeScope to analyze only local files and already-fetched Git objects, so that it does not perform cloud calls or claim knowledge of remote-only changes.
23. As a security-conscious organization, I want the first Java analysis feature to run without a local JRE, so that adoption does not require a JVM dependency.
24. As an engineer, I want parse failures and unreadable files surfaced in the index summary or report, so that incomplete analysis is explicit.
25. As a maintainer, I want the CLI to delegate work to one public application service returning structured results, so that CLI, test, and future MCP adapters share one behavior seam.

## Implementation Decisions

- Provide one public application service that accepts a repository context and an operation request, then returns a structured result. The CLI is an adapter that parses arguments, renders text or JSON, and selects an exit code.
- Implement the four initial operations: index a repository, analyze the impact of a Change Target, retrieve Evidence Handle context, and retrieve bounded source or enclosing-symbol content.
- Treat the current working directory as the repository root for the normal indexing and impact workflow.
- Discover Maven, Gradle, Eclipse, and standard Java source layouts first. When no recognized layout exists, scan Java files recursively with explicit exclusions and report the discovery result.
- Store the Repository Index in local SQLite. It contains the structural facts needed by this slice, source locations, evidence records, and snapshot provenance; it does not mirror the complete source repository.
- Use Tree-sitter through Python for Structural Java Analysis. Do not require a JRE, a build, a classpath, or a running application.
- Support Java source beginning with Java 5 where recognized by the selected parser. Syntax that cannot be parsed becomes an explicit indexing issue rather than a guessed relationship.
- Resolve `Class#method` by matching indexed declarations. One matching declaration is resolved; multiple matching declarations produce an ambiguous outcome with candidates; no match produces a not-found outcome.
- Create direct-call relationships only from explicit invocation syntax that can be structurally tied to the resolved declaration under this slice's conservative rules. Do not claim type, overload, inheritance, reflection, dependency-injection, framework-dispatch, generated-code, or virtual-dispatch certainty that the structural evidence does not establish.
- Assign Confidence according to the agreed glossary: high for an unambiguous declaration and explicit indexed invocation; medium for strong structural evidence with a remaining resolution gap; low for plausible but incomplete or non-unique evidence; unresolved when no specific relationship is asserted.
- Render a compact report by default. JSON rendering exposes the same facts, Evidence Handles, source locations, confidence, snapshot provenance, assumptions, and Unresolved Items.
- Evidence retrieval starts with a bounded context window. Callers can request a larger window, an enclosing symbol, or explicit file ranges. Each response honors a size budget and declares truncation with continuation ranges.
- Before impact analysis, compare current local source state with the Repository Index and incrementally refresh changed files. An explicit index command remains available for a full rebuild.
- Index and report provenance describe only the local working tree and Git data already available. The feature performs no network access, remote fetching, telemetry, or production discovery.
- Keep interfaces structured so the same application service can later serve an MCP adapter, but do not implement MCP in this slice.
- Keep the Workspace Catalog, cross-repository traversal, C4/Structurizr generation, Backstage interoperability, and OpenAPI ingestion outside this implementation slice. ADR-0001 remains the governing decision for their later integration.

## Testing Decisions

- Test externally observable behavior through the public application service using isolated fixture Java repositories; the CLI receives a thin end-to-end test for its normal commands and output modes.
- A good test asserts the reported Change Target outcome, affected relationships, evidence locations, confidence, Unresolved Items, discovery summary, provenance, and bounded retrieval behavior. It does not assert Tree-sitter node traversal or SQLite query implementation details.
- Include fixtures for conventional Java layouts and irregular legacy layouts, with excluded build or generated directories.
- Include fixtures for an unambiguous method, overloaded methods, a missing target, direct invocations, direct test invocations, and an unsupported or unresolved dynamic-looking case.
- Include Java 5-compatible fixture syntax and malformed or unreadable-source cases to confirm explicit partial-analysis reporting.
- Verify text and JSON reports represent the same underlying structured result.
- Verify evidence retrieval honors size budgets, marks truncation, and exposes continuation ranges for large source and large enclosing symbols.
- Verify incremental refresh changes the affected report after a local source edit and that snapshot provenance changes accordingly.
- Verify no test requires a JRE, network access, remote Git access, a build tool, a database server, or a running Java application.

## Out of Scope

- Cross-repository traversal, Workspace Catalog registration, and logical dependency mappings.
- HTTP/REST, remote EJB, JMS/message-driven EJB, SOAP, gRPC, CORBA, and all other cross-application boundaries.
- WildFly, Spring Framework, Spring Boot, Quarkus, CDI, EJB, JAX-RS, servlet, or deployment-descriptor relationship extraction.
- Full Java semantic resolution through a JVM, classpath loading, compiler integration, or a required JRE.
- Complete overload, inheritance, interface, virtual-dispatch, reflection, dependency-injection, generated-code, or framework-dispatch analysis.
- Git fetching, remote repository access, production environment discovery, telemetry, cloud calls, embeddings, vector databases, background daemons, Docker access, or heavyweight services.
- MCP serving, C4/Structurizr output, Backstage catalog import/export, and OpenAPI ingestion.

## Further Notes

- The feature is the first stage of the Java-focused delivery roadmap, not a claim of complete Java impact analysis.
- The report must remain reviewable: every asserted relationship is tied to evidence, and every uncertainty remains an Unresolved Item.
- Future framework and cross-application capabilities must continue to use Evidence-backed Relationships and Local-only Provenance.
