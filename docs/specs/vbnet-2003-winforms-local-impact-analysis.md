# VB.NET 2003 WinForms local impact analysis

## Problem Statement

ChangeScope can analyze Java applications, but the target estate also contains .NET Framework 1.1-era VB.NET Windows Forms applications. In these repositories, a user action is commonly expressed through designer-authored controls, `WithEvents`, `Handles`, or `AddHandler`; business work may then cross project, process, configuration, ADO.NET, stored-procedure, and COM boundaries. A method-only text search misses the UI entry point and hides the places an engineer must verify manually. Requiring Visual Studio 2003, an old .NET Framework installation, application execution, or COM registration would make the capability difficult to adopt and unsafe for repeatable local analysis.

## Solution

Add Runtime-free VB.NET Analysis to the existing Repository Index and impact report. The first runnable slice resolves a VB.NET `Sub` or `Function` supplied as `Class#member`, reports direct callers and callees, and connects an explicit WinForms Event Binding to its handler with source-path-and-line evidence, Confidence, assumptions, Unresolved Items, and Manual Verification Surfaces.

The complete capability extends that slice into one repository-local VB.NET WinForms Local Impact Path:

```text
Form or control event
→ WinForms Event Binding
→ handler
→ business calls
→ application configuration
→ VB.NET Data Access Boundary or COM Interop Boundary
→ automated tests and Manual Verification Surfaces
```

The same repository or solution may contain multiple executable projects. Explicit process launches may cross a Local Process Boundary when project output identity proves a unique target. An executable outside the current repository or solution remains an External Process Boundary in this capability; analysis does not automatically continue into another repository.

## User Stories

1. As an engineer, I want `changescope index` to discover legacy `.sln`, `.vbproj`, `.vb`, `.resx`, and application-configuration evidence, so that ordinary VB.NET 2003 repositories require no per-file setup.
2. As an engineer, I want the index summary to distinguish Java and VB.NET sources, projects, designer evidence, read failures, and parse failures, so that I can review the analyzed scope.
3. As an engineer, I want source-controlled WinForms designer code and `.resx` files included with generated provenance, so that UI structure is not discarded as build output.
4. As an engineer, I want `bin`, `obj`, compiler output, generated interop binaries, dependencies, and repository metadata excluded, so that stale artifacts do not become authoritative evidence.
5. As an engineer, I want to request `changescope impact OrderForm#SaveButton_Click`, so that VB.NET uses the same concise Change Target form as Java.
6. As an engineer, I want VB.NET identifiers resolved case-insensitively and overloads reported as ambiguous, so that the report follows the language without silently selecting a member.
7. As an engineer, I want `Sub`, `Function`, constructors, owning types, namespaces, project root namespaces, and explicit invocations indexed with source locations, so that target resolution is reviewable.
8. As an engineer, I want direct callers and callees reported only when receiver, member, and argument evidence identify a conservative target, so that the call graph does not rely on name similarity.
9. As an engineer, I want `WithEvents` plus `Handles` and explicit `AddHandler ... AddressOf ...` relationships connected to a unique event handler, so that a changed method exposes its UI entry point.
10. As an engineer, I want Form, control, event, handler, and designer evidence represented as distinct facts, so that WinForms Event Binding is not mistaken for a direct method call.
11. As an engineer, I want `Option Strict`, `Option Explicit`, explicit types, imports, aliases, and project references retained as resolution evidence, so that confidence reflects the available language context.
12. As an engineer, I want calls through `Object`, implicit variables, default members, reflection, or otherwise unproven receiver types reported as VB.NET Late-Bound Calls, so that `Option Strict Off` does not produce guessed edges.
13. As an engineer, I want an `Option Strict Off` file to retain high-confidence explicitly typed relationships, so that one permissive option does not lower the whole file's confidence.
14. As an engineer, I want solution and project references, output type, assembly name, startup object, and entry point indexed, so that multi-project applications and executables are visible.
15. As an engineer, I want explicit `Process.Start` and `Shell` calls connected to a unique executable project when literal or locally resolved configuration evidence proves the target, so that separately launched functions appear in the report.
16. As an engineer, I want dynamic executable paths and targets outside the current repository or solution reported as External Process Boundaries, so that process-oriented architecture remains visible without guessed traversal.
17. As an engineer, I want `app.config` keys and explicit `ConfigurationSettings.AppSettings` consumption linked to affected code, so that relevant configuration inputs appear in the impact path.
18. As an engineer, I want explicit ADO.NET providers, connection keys, commands, stored-procedure names, command types, and parameter bindings reported as a VB.NET Data Access Boundary, so that database-facing impact is reviewable.
19. As an engineer, I want a unique repository-local SQL definition matching a literal stored-procedure name shown as supporting evidence, so that source-controlled database artifacts can be inspected without claiming complete database impact.
20. As an engineer, I want dynamic SQL, dynamic procedure names, and unproven table, trigger, or database behavior reported as unresolved, so that the report does not become a speculative database dependency graph.
21. As an engineer, I want `.vbproj` COM references, source-controlled interop metadata, ActiveX designer controls, and explicit early-bound interop calls reported as a COM Interop Boundary, so that common legacy dependencies do not disappear.
22. As an engineer, I want `CreateObject`, `GetObject`, late-bound COM calls, unregistered components, and behavior inside DLL or OCX binaries reported as unresolved, so that ChangeScope does not claim binary semantics.
23. As an engineer, I want explicit test projects, test attributes, test calls, and test references connected to the affected code, so that available automated verification is visible.
24. As an engineer, I want affected Forms, control events, process launches, data-access boundaries, and COM boundaries listed separately as Manual Verification Surfaces when automated test evidence is missing or incomplete.
25. As an engineer, I want text and JSON reports to expose the same targets, relationships, evidence chains, confidence, unresolved items, and verification surfaces, so that CLI and future MCP users receive one result model.
26. As a security-conscious organization, I want all analysis to remain local-only and independent of Visual Studio, .NET Framework, a compiler, MSBuild, application execution, database access, and COM registration.
27. As a maintainer, I want Java behavior and all existing tests preserved, so that adding a second source language does not regress completed capabilities.
28. As an engineer maintaining Taiwanese legacy source, I want the selected source encoding recorded and undecodable files reported explicitly, so that ANSI or CP950 identifiers and evidence locations are not silently corrupted.
29. As an engineer, I want conditional-compilation branches retained with their conditions unless project constants select a branch, so that the indexing machine's environment does not silently choose application behavior.

## Implementation Decisions

- Preserve `ChangeScopeApplication.execute` as the external seam used by the CLI, tests, and future adapters. Do not add a parallel VB.NET application interface.
- Introduce an internal language-analysis seam now that Java and VB.NET are two real adapters. Each adapter accepts discovered source and project context and returns normalized declarations, invocations, parse failures, and language-specific facts; parser nodes and grammar details remain inside the adapter implementation.
- Put a ChangeScope-owned VB.NET parser facade at that seam. Relationship extraction consumes normalized types such as declarations, invocations, `WithEvents` fields, `Handles` bindings, and `AddHandler` bindings rather than grammar-specific node names.
- Do not adopt the currently published CodeAnt `tree-sitter-vb-dotnet` grammar as-is: the research note found no `Handles` production and no ordinary executable `AddHandler` statement. First run a bounded acceptance suite against VB.NET 2003-shaped fixtures. If a small pinned fork can satisfy it, ship the generated parser and compatible Python wheel; otherwise implement a narrow logical-line tokenizer and structural parser behind the same facade. Roslyn remains a possible future opt-in semantic adapter, not a baseline dependency.
- Move language-neutral declarations and invocations toward shared indexed records carrying a `language` discriminator. Rebuild a derived local index when an incompatible schema version is encountered instead of maintaining indefinite mixed-schema reads. Retain framework- and language-specific evidence in explicit fact records rather than a source mirror.
- Deepen the impact relationship interface so each relationship can identify a source item, target item, kind, language, ordered Evidence Chain, Confidence, and conditional state. Preserve current Java report fields during a compatibility transition, but do not overload a field named `caller` for events, callees, configuration, processes, and external boundaries indefinitely.
- Preserve `Class#member` as the first VB.NET Change Target syntax. Resolve across indexed declarations; one match is resolved, multiple matches are ambiguous, and no match is `not_found`. Include language and project identity in candidates when a mixed-language repository produces collisions.
- Treat VB.NET identifiers as case-insensitive. Combine an explicit `Namespace` with the `.vbproj` root namespace where locally proven. Do not infer a namespace or project from directory names alone.
- Support the VB.NET 2003 language surface required by the fixture applications, including line continuations, colon-separated statements, attributes, modules, classes, structures, interfaces, `Sub`, `Function`, constructors, overloads, imports, aliases, `Option` statements, events, delegates, `WithEvents`, `Handles`, `AddHandler`, `AddressOf`, and explicit member access. A later-language syntax superset may be accepted by the parser, but the capability is validated against VB.NET 2003 source.
- Assign high Confidence to a unique declaration, a direct invocation on a locally proven receiver, an explicit `Handles` binding on a unique `WithEvents` control, and an explicit `AddHandler` plus `AddressOf` pair whose source, event, and handler are unique. Use medium Confidence for strong project, designer, configuration, or external-boundary evidence with a remaining static-resolution gap. Do not assert a relationship for name-only, late-bound, dynamic, or non-unique evidence.
- Treat `Option Strict Off` as context, not as file-wide uncertainty. Only the invocation or binding whose receiver or target cannot be established becomes a VB.NET Late-Bound Call or other Unresolved Item.
- Include source-controlled designer regions, designer `.vb` files where present, and `.resx` files as Source-controlled WinForms Designer Evidence. Retain designer provenance on every derived fact. Parse `.resx` as XML evidence only; never instantiate declared types or deserialize embedded values. Exclude `bin`, `obj`, packages, generated interop binaries, and other build output.
- Parse `.sln` records, genuine pre-MSBuild Visual Studio .NET 2003 `.vbproj` files, and later MSBuild-converted `.vbproj` files through separate input adapters feeding one project model. Match legacy XML conservatively, preserve unknown fields, and promote only observed-and-tested fields to authoritative evidence. Do not execute or expand MSBuild imports.
- Use project membership for source discovery when available, while reporting committed `.vb`, `.resx`, and configuration files that exist outside declared membership. Use project references and output identity as evidence; ordinary assembly or COM references without local source become external boundaries rather than invented declarations.
- Decode source before parsing. Prefer BOM-declared UTF-8 or UTF-16, then an explicitly configured project or repository code page. Any ANSI fallback such as CP950 must be explicit and recorded in index metadata; never silently use the indexing machine's locale or replacement-decode identifiers. Preserve byte offsets and decoded line starts for reproducible evidence locations.
- Normalize logical statements before structural parsing so explicit `_` continuation and colon-separated statements retain correct source spans. Preserve conditional-compilation facts and their conditions unless explicit project/compiler constants prove the active branch.
- Recognize explicit process launch APIs and their literal or locally resolved configuration arguments. Connect only a unique current-repository or current-solution executable. Do not treat a Form transition as a process launch or automatically traverse an External Process Boundary.
- Recognize .NET 1.1-era local application configuration consumption, including `ConfigurationSettings.AppSettings`, and ADO.NET construction or command evidence without opening a database. Do not assume the .NET 2.0 `connectionStrings`/`ConfigurationManager` model for an unconverted 2003 project. A repository-local SQL definition may support a stored-procedure boundary only when the name match is exact and unique; it does not prove deployed schema identity or internal SQL impact.
- Recognize COM project references, ActiveX designer evidence, and explicit early-bound interop use. Do not load, register, execute, or decompile COM components. `CreateObject`, `GetObject`, reflection, default-member dispatch, and unproven interop targets remain unresolved.
- Traverse only supported Evidence-backed Relationships when assembling the complete VB.NET WinForms Local Impact Path. Make traversal cycle-safe and bounded; if a report limit is reached, state the truncation and remaining frontier rather than silently omitting it.
- Add a dedicated `verification_surfaces` result collection instead of representing Forms, controls, process launches, or external boundaries as tests. Each surface carries its kind, identity, evidence, and reason for manual verification.
- Preserve Local-only Provenance, incremental refresh, bounded Evidence Navigation, text/JSON parity, and the existing Confidence and Unresolved Item semantics.
- Keep parser dependencies small, pinned, reviewable, and compatible with Python 3.10 or later. The selected parser must run without .NET, Visual Studio, a compiler, MSBuild, or a background process; [the accompanying research note](https://github.com/CletusLee/ChangeScope/blob/main/docs/research/vbnet-2003-runtime-free-analysis.md) records the evaluated options and evidence.

## Testing Decisions

- Test observable behavior through `ChangeScopeApplication.execute` using isolated fixture repositories. Parser traversal, tokenization details, and SQLite queries are implementation details and are not direct test surfaces.
- Preserve all existing Java, Spring, EJB, Quarkus, SOAP, catalog, CLI, and evidence-navigation tests as regression coverage.
- Build the first fixture with a VB.NET 2003-style WinForms project containing a Form, `WithEvents` Button, `Handles Button.Click`, handler, direct business call, direct callee, ambiguous overload, and one late-bound `Object` call.
- Add fixtures for explicit `AddHandler`/`AddressOf`, missing or ambiguous controls, handler overloads, form inheritance, constructor and `InitializeComponent` evidence, malformed source, unreadable source, and case-insensitive target resolution.
- Add solution fixtures with multiple `.vbproj` files, project references, library and `WinExe` outputs, startup objects, explicit `Process.Start`, `Shell`, ambiguous executable identities, configuration-derived executable names, and External Process Boundaries.
- Add configuration and data-access fixtures for `app.config`, literal and missing keys, `ConfigurationSettings.AppSettings`, common ADO.NET abstractions and providers, connection keys, text commands, stored procedures, parameters, exact repository-local SQL candidates, and dynamic SQL/procedure uncertainty.
- Add COM fixtures for project COM references, source-controlled interop types, ActiveX designer controls, explicit early-bound calls, `CreateObject`, `GetObject`, missing metadata, and late-bound invocation.
- Add test fixtures for explicit test projects and framework attributes available in the selected representative sources. Verify that absent tests produce no invented test relationship and that Manual Verification Surfaces remain separate.
- Cover source-controlled designer evidence separately from excluded build output. Include UTF-8 with BOM, UTF-16, and an explicitly selected CP950/ANSI fixture with Chinese control text and identifiers; unsupported, ambiguous, or undecodable source must become a visible read or parse failure.
- Cover explicit line continuation, colon-separated statements, bracketed identifiers, case-insensitive lookup, `#Const`/`#If` branches, and a handler-shaped method name with no binding evidence. The last case must not create a WinForms Event Binding.
- Verify target outcome, normalized signature, relationship direction, ordered Evidence Chains, Confidence, Unresolved Items, verification surfaces, discovery summary, Index Snapshot provenance, incremental refresh, and bounded evidence retrieval.
- Verify text and JSON parity for every new relationship and verification-surface kind.
- Verify no automated test requires Visual Studio, .NET Framework, Mono, a compiler, MSBuild, application startup, GUI automation, database connectivity, COM registration, network access, remote Git access, telemetry, Docker, or a background daemon.
- After isolated fixtures pass, smoke-test against at least one pinned, locally available representative VB.NET WinForms codebase or a sanitized estate-derived fixture. Record parser coverage and every unsupported construct; public validation supplements but does not replace estate-representative validation.

## Delivery Slices

1. **First runnable VB.NET WinForms path and parser acceptance gate**: discover one pre-MSBuild-shaped VB.NET project, prove the selected parser path against the bounded VB.NET 2003 fixture suite, index declarations and explicit invocations, resolve `Class#member`, connect `WithEvents`/`Handles` and explicit `AddHandler`/`RemoveHandler`/`AddressOf`, and report direct callers/callees, late binding, evidence, confidence, and Manual Verification Surfaces through text and JSON. The published grammar cannot pass this slice unchanged.
2. **Solution and process structure**: index legacy `.sln`/`.vbproj` metadata, project references, root namespaces, executable identity, startup entry points, `Process.Start`, `Shell`, Local Process Boundaries, and External Process Boundaries.
3. **Configuration and data access**: connect `app.config` consumption, explicit ADO.NET evidence, stored-procedure references, parameter bindings, repository-local SQL candidates, and dynamic database uncertainty.
4. **COM and ActiveX interop**: expose project references, designer controls, source-controlled interop metadata, early-bound calls, and unresolved dynamic COM behavior.
5. **Complete local path and validation**: assemble bounded multi-edge impact paths, complete test evidence and Manual Verification Surfaces, incremental refresh, evidence navigation, report parity, schema migration/rebuild behavior, full Java regression coverage, and representative application validation.

Slices are delivered in order because each must expose a runnable report result. Slice 1 establishes the language-analysis seam and normal target behavior. Slices 2 through 4 add independent evidence families to that seam. Slice 5 closes the capability only after the complete local path is observable and validated.

## Out of Scope

- VB6 parsing or VB6 project, Form, control-array, COM late-binding, and runtime behavior.
- Automatic continuation from an External Process Boundary into another repository. A Registered Workspace may expose a candidate, but the first VB.NET capability remains repository- and solution-local.
- Proving that a deployed executable, configuration file, database, COM registration, or environment matches the indexed source.
- Running Visual Studio, .NET Framework, Mono, `vbc`, MSBuild, the application, GUI automation, a database client, COM registration, DLL loading, or OCX loading.
- Complete compile-time semantic resolution, assembly loading, overload resolution, inheritance and virtual dispatch, delegates created dynamically, reflection, serialization callbacks, remoting, or runtime event subscription.
- Decompiling or analyzing behavior inside referenced .NET assemblies, COM DLLs, type libraries, ActiveX controls, or executables.
- Deserializing `.resx` values, instantiating designer types, or trusting compiled `.resources` as authoritative source evidence.
- Complete SQL parsing, table/column lineage, stored-procedure internals, trigger graphs, dynamic SQL resolution, database schema comparison, or execution-plan analysis.
- Treating Form names, control names, executable names, configuration keys, procedure names, routes, or URLs as sufficient evidence when identity is incomplete or non-unique.
- Git fetching, remote repository discovery, production environment discovery, telemetry, cloud calls, embeddings, vector databases, background daemons, Docker access, or heavyweight external services.

## Completion Criteria

The capability is complete when an engineer can start from a VB.NET 2003 `Class#member` in a local WinForms repository and receive a report that:

- resolves the intended `Sub` or `Function`, or explicitly reports ambiguity or absence;
- connects locally proven Form/control events, handlers, direct callers and callees, project and process relationships, configuration, ADO.NET/stored-procedure evidence, COM boundaries, tests, and Manual Verification Surfaces;
- distinguishes direct calls, WinForms Event Bindings, Local and External Process Boundaries, VB.NET Data Access Boundaries, COM Interop Boundaries, and test evidence;
- retains source-controlled designer evidence while excluding non-authoritative build output;
- exposes VB.NET Late-Bound Calls, dynamic event/process/database/COM behavior, parse failures, and missing metadata as Unresolved Items rather than guessed links;
- shows ordered source-backed Evidence Chains, Confidence, assumptions, report bounds, and Index Snapshot provenance in equivalent text and JSON results;
- passes isolated VB.NET 2003-style fixtures, the complete existing Java regression suite, and at least one representative local or sanitized WinForms validation without requiring .NET, Visual Studio, a build, application execution, database access, COM registration, or network access.

## Further Notes

- This capability extends ChangeScope's unified evidence model; it is not a separate Visual Basic search tool.
- Runtime-free analysis deliberately trades complete compiler and deployment knowledge for local portability, conservative evidence, and explicit uncertainty.
- The first release targets VB.NET 2003 WinForms because it represents the immediate estate need. VB6 remains a later language adapter with its own syntax, project, Form, control-array, and COM late-binding rules.
