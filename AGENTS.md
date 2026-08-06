# ChangeScope project description

ChangeScope is a local-first Python CLI and MCP tool for explainable impact analysis of legacy codebases.

Its purpose is to answer, before implementation begins: “If this requirement, Git diff, symbol, API, or interface contract changes, what code, services, configuration, and tests are affected—and what source evidence proves that conclusion?”

ChangeScope is not a generic code-memory or code-search product. Its primary output is a reviewable impact report containing:

- The interpreted change and explicit assumptions.
- Affected items grouped by repository, module, contract, configuration, and test.
- Evidence with source path and line range.
- Confidence level: high, medium, or low.
- Unresolved items that require an engineer to confirm.

The product has one unified relationship model and two scopes:

- Repo Mode: analyze impact within one repository, with Java applications as the Version 1 focus.
- Workspace Mode: when Repo Mode reaches a verified Java application or contract boundary, continue the same analysis into an explicitly registered target repository.

Cross-repository analysis is not a separate tool. It is a controlled extension of local analysis through evidence-backed contract relationships.

Each repository has its own SQLite index containing local symbols, relationships, contracts, tests, and optional source snippets. A small Workspace Catalog stores repository identities, indexed commit SHAs, contract keys, and verified cross-repository edges. It must not become a copy of all company source code.

Delivery proceeds in small vertical stages:

1. The first runnable feature analyzes a fixture Java repository for direct, statically recognizable method calls. It reports a resolved or ambiguous change target, source-path-and-line evidence, confidence, and unresolved items.

2. The first release extends that feature into two complete Java paths:

   - A single-repository path:

     changed Java implementation or public interface
     → local callers/callees
     → relevant configuration
     → affected tests

   - A cross-repository HTTP/REST path:

     changed Java implementation, interface, or API contract
     → local callers/callees
     → verified Java client and HTTP/REST contract boundary
     → registered target Java repository
     → endpoint, handler, or service implementation
     → local impact and affected tests

Cross-repository links must never be created only because names are similar. They require contract identity, Java client or generated-code evidence, route evidence, typed client evidence, or an explicit workspace mapping. WildFly/EJB, Spring and Quarkus framework dispatch, JMS, SOAP, gRPC, and CORBA are subsequent extension stages of the same contract model.

Primary languages are Java, Python, JavaScript/TypeScript, C/C++, C#, Go, VB.NET, and VB6. Java is the initial implementation focus. Java source compatibility begins at Java 5. VB.NET and VB6 should provide structural analysis and high-confidence direct relationships; unsupported dynamic behavior, especially VB6 COM late binding, must be reported as unresolved rather than guessed.

Keep the implementation local-only by default. Do not add telemetry, cloud calls, embeddings, vector databases, background daemons, Docker daemon access, or heavyweight external services. Keep dependencies small, pinned, reviewable, and suitable for corporate security approval.

Version 1 must not require a local JRE. Use Python with Tree-sitter for structural Java analysis; any JVM-based semantic resolver is an optional later capability.

Work in small vertical slices. Every ticket must produce a runnable, tested behavior and an observable report result. Prefer evidence and conservative confidence scoring over broad but unverified call graphs. Do not attempt to clone codebase-memory-mcp or fully solve every language’s semantic analysis.

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues using the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five default triage labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository with `CONTEXT.md` at the root and ADRs under `docs/adr/`. See `docs/agents/domain.md`.
