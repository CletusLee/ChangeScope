# Runtime-free VB.NET 2003 WinForms structural analysis

Status: research note

Date: 2026-08-08

Scope: technical options for adding repository-local VB.NET 2003 WinForms analysis to ChangeScope without requiring Visual Studio, the .NET Framework, MSBuild, a VB compiler, application execution, or COM registration.

## Conclusion

A useful first VB.NET 2003 slice is feasible with static files alone. The source tree can establish forms, controls, `WithEvents` fields, `Handles` and `AddHandler` event bindings, `Sub`/`Function` declarations, conservative direct calls, configuration-key references, ADO.NET command boundaries, literal process launches, and COM/ActiveX boundaries. It cannot establish late-bound targets, deployed configuration values, process reachability, registered COM implementations, or behavior inside DLL/OCX binaries.

The best fit with ChangeScope's current architecture is a pinned, locally shipped Tree-sitter grammar plus Python relationship extraction. However, the only VB.NET grammar currently listed by Tree-sitter is not usable as-is for the first WinForms slice: its own README targets VB 16.9/.NET 5 and calls error recovery basic; inspection of its grammar shows no `Handles` production, while `AddHandler` appears only as a custom-event accessor and not as an executable statement. It should therefore pass a bounded fixture spike before adoption. If the missing VB 7.1 constructs can be added cleanly, maintain a small pinned fork and ship the generated C parser. If not, use a narrowly scoped handwritten structural parser for the first slice rather than introducing Roslyn and a .NET runtime.

## Verified platform and language facts

This section states source-backed facts. Recommendations and inferences are separated into later sections.

### Visual Studio .NET 2003 project and solution inputs

- Visual Studio .NET 2003 corresponds to .NET Framework 1.1 in Microsoft's Windows Forms resource-tool compatibility table. [.NET Framework Winres version compatibility](https://learn.microsoft.com/en-us/dotnet/framework/tools/winres-exe-windows-forms-resource-editor#version-compatibility)
- Microsoft classifies .NET projects created by Visual Studio versions that predate MSBuild as "pre-MSBuild projects." A genuine Visual Studio .NET 2003 `.vbproj` must therefore not be assumed to have the later MSBuild `<Project>`/`PropertyGroup`/`ItemGroup` model. [Visual Studio project migration: pre-MSBuild projects](https://learn.microsoft.com/en-us/visualstudio/releases/2022/port-migrate-and-upgrade-visual-studio-projects#pre-msbuild-projects)
- A `.sln` file is text-based and contains `Project(...)` records that identify a project's display name, project path, project-type GUID, and project GUID. The project file holds the project's additional hierarchy information. [Solution file format](https://learn.microsoft.com/en-us/visualstudio/extensibility/internals/solution-dot-sln-file#file-body)
- At the compiler level, `winexe` produces a Windows executable, while `exe` produces a console executable and `library` produces a DLL. The `main` option identifies the class or module containing `Sub Main`; a `Form` class can also be used as the entry class. [Visual Basic `-target`](https://learn.microsoft.com/en-us/dotnet/visual-basic/reference/command-line-compiler/target) and [`-main`](https://learn.microsoft.com/en-us/dotnet/visual-basic/reference/command-line-compiler/main)
- For later MSBuild-converted projects, the corresponding project properties are `OutputType`, `StartupObject`, `RootNamespace`, `OptionStrict`, `OptionExplicit`, and `OptionCompare`. [Common MSBuild project properties](https://learn.microsoft.com/en-us/visualstudio/msbuild/common-msbuild-project-properties)

Because the authoritative classification is "pre-MSBuild," implementation should use actual `.vbproj` samples from the target codebase as compatibility fixtures rather than infer the legacy dialect from modern MSBuild elements.

### VB source parsing rules that affect a structural analyzer

- Visual Basic identifiers and language terminals are case-insensitive. The language specification also describes lexical, syntactic, and preprocessing grammars separately. [Visual Basic language specification introduction](https://learn.microsoft.com/en-us/dotnet/visual-basic/reference/language-specification/introduction)
- A logical statement can span physical lines with a space followed by `_`, and multiple statements can share a physical line when separated by `:`. A line-oriented regex that ignores these rules will misidentify declarations and calls. [Break and combine statements](https://learn.microsoft.com/en-us/dotnet/visual-basic/programming-guide/program-structure/how-to-break-and-combine-statements-in-code)
- Conditional compilation determines which logical lines are passed to the syntactic grammar. Project/compiler constants and source `#Const` values can select different branches. [Preprocessing directives](https://learn.microsoft.com/en-us/dotnet/visual-basic/reference/language-specification/preprocessing-directives) and [`-define`](https://learn.microsoft.com/en-us/dotnet/visual-basic/reference/command-line-compiler/define)
- `Option Strict On` rejects implicit narrowing conversions, late binding, and implicit typing that produces `Object`. If a source file has no `Option Strict` statement, the project/compiler setting applies. Microsoft documents the initial IDE default for new projects as `Off`. [Option Strict statement](https://learn.microsoft.com/en-us/dotnet/visual-basic/language-reference/statements/option-strict-statement)
- An object variable declared as a specific type is early bound. A variable declared `As Object` is late bound; Microsoft's example uses `CreateObject` under `Option Strict Off`. [Early and late binding](https://learn.microsoft.com/en-us/dotnet/visual-basic/programming-guide/language-features/early-late-binding/)

### Encodings

- The VB compiler's `-codepage` option applies one code page to all source files in a compilation. Microsoft states that Visual Studio saves source using the current ANSI code page by default unless another encoding is selected, and that no option is needed for the current ANSI code page, Unicode, or UTF-8 with a signature. [`-codepage`](https://learn.microsoft.com/en-us/dotnet/visual-basic/reference/command-line-compiler/codepage)
- Visual Studio supports explicitly opening and saving files with a selected encoding. [Save and open files with encoding](https://learn.microsoft.com/en-us/visualstudio/ide/how-to-save-and-open-files-with-encoding)

Consequently, a Taiwanese legacy repository can legitimately contain non-UTF-8 `.vb` files without a BOM. Decoding is part of evidence correctness: an incorrect decoder can change identifiers, string literals, and byte-to-line locations.

## Verified WinForms evidence

### `WithEvents`, `Handles`, and `AddHandler`

- A method's `Handles` clause declaratively connects it to one or more events. The event source must be `Me`, `MyBase`, `MyClass`, or a variable in the containing type declared with `WithEvents`; the second name identifies an event member of that source type. [Visual Basic language specification: event handling](https://learn.microsoft.com/en-us/dotnet/visual-basic/reference/language-specification/type-members#event-handling)
- `Handles` can list more than one event, and more than one handler can handle an event. Microsoft shows the designer pattern `Friend WithEvents Button1 As System.Windows.Forms.Button` followed by a handler with `Handles Button1.Click`. [Visual Basic events](https://learn.microsoft.com/en-us/dotnet/visual-basic/programming-guide/language-features/events/#associating-events-with-event-handlers)
- `AddHandler event, AddressOf handler` associates an event and handler at run time; `RemoveHandler` removes that association. This is semantically distinct from the compile-time declarative `Handles` clause. [`AddHandler` statement](https://learn.microsoft.com/en-us/dotnet/visual-basic/language-reference/statements/addhandler-statement)
- `WithEvents` is not just a marker: the language specification describes compiler-generated hookup behavior when the variable is assigned. [Visual Basic language specification: `WithEvents` variables](https://learn.microsoft.com/en-us/dotnet/visual-basic/reference/language-specification/type-members#withevents-variables)

These constructs justify a first-class `winforms_event_binding` relationship rather than representing a button click as an ordinary method call.

### Designer source, `InitializeComponent`, and `.resx`

- A designer-generated type's constructor is expected to call its `InitializeComponent` method. [`BC40054`](https://learn.microsoft.com/en-us/dotnet/visual-basic/misc/bc40054)
- The `DesignerGeneratedAttribute` tells the compiler and Visual Studio that a type is designer-generated and participates in `InitializeComponent` behavior. [`DesignerGeneratedAttribute`](https://learn.microsoft.com/en-us/dotnet/api/microsoft.visualbasic.compilerservices.designergeneratedattribute?view=netframework-4.8)
- Windows Forms state can be stored in XML `.resx` files or compiled binary `.resources` files. The Microsoft tool documentation explicitly lists Visual Studio .NET 2003/.NET Framework 1.1 support and says a form resource can contain UI properties such as text, size, and position. [Winres.exe](https://learn.microsoft.com/en-us/dotnet/framework/tools/winres-exe-windows-forms-resource-editor)
- `.resx` is an XML resource format, but values can carry type information and some form resources can involve unsafe binary deserialization. Microsoft's current documentation warns against opening untrusted form resources with tools that deserialize them. [`ResXResourceReader`](https://learn.microsoft.com/en-us/dotnet/api/system.resources.resxresourcereader) and [WinForms designer safety note](https://learn.microsoft.com/en-us/visualstudio/designers/windows-forms-designer-overview#review-caution-scenarios)

Inference: the analyzer must not assume the modern `FormName.Designer.vb` split. Microsoft's Visual Basic 2005 overview lists partial classes among that release's language enhancements, and Microsoft documents partial classes as the mechanism used to separate Windows Forms designer output from user code. A 2003-era form can therefore keep `InitializeComponent` and generated control fields in the same `.vb` file. [Microsoft's Visual Basic 2005 overview](https://learn.microsoft.com/en-us/archive/msdn-magazine/2006/vs-2005-guided-tour/visual-basic-using-my-namespace-to-navigate-projects-and-net) and [partial classes in the designer](https://learn.microsoft.com/en-us/visualstudio/ide/class-designer/how-to-split-a-class-into-partial-classes)

## Verified boundary evidence

### Local process launches

- `System.Diagnostics.Process.Start(String)` starts a process resource from a document or application file name. [`Process.Start`](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.process.start)
- `Microsoft.VisualBasic.Interaction.Shell` runs an executable program and returns its process ID if it is still running. [`Interaction.Shell`](https://learn.microsoft.com/en-us/dotnet/api/microsoft.visualbasic.interaction)

Both calls can provide structural launch evidence. Neither proves that the named executable exists in a deployed environment or that a dynamically constructed command selects a particular project output.

### Configuration and ADO.NET

- A .NET Framework application configuration file is XML. Visual Studio's source file is normally `App.config`; the deployed file for `myApp.exe` is `myApp.exe.config`. [Configure .NET Framework apps](https://learn.microsoft.com/en-us/dotnet/framework/configure-apps/#application-configuration-files)
- For runtime versions 1.0 and 1.1, `System.Configuration.ConfigurationSettings` is the API for reading configuration sections and common settings. Its `AppSettings` property reads the `appSettings` section. [`ConfigurationSettings`](https://learn.microsoft.com/en-us/dotnet/api/system.configuration.configurationsettings) and [`appSettings`](https://learn.microsoft.com/en-us/dotnet/framework/configure-apps/file-schema/appsettings/appsettings-element-for-configuration)
- The dedicated `connectionStrings` configuration section and `ConfigurationManager` retrieval model are .NET Framework 2.0-era features. They must not be assumed for a .NET 1.1/VS 2003 repository; connection strings may instead be stored under `appSettings`, a custom section, or source. [ADO.NET connection strings and configuration files](https://learn.microsoft.com/en-us/dotnet/framework/data/adonet/connection-strings-and-configuration-files#retrieve-connection-strings-at-run-time)
- When an ADO.NET command has `CommandType.StoredProcedure`, its command text is the stored-procedure name; parameters are available through the command's `Parameters` collection. [Executing an ADO.NET command](https://learn.microsoft.com/en-us/dotnet/framework/data/adonet/executing-a-command)

### COM and ActiveX

- COM type definitions commonly live in type libraries embedded in `.tlb`, `.dll`, `.exe`, `.olb`, or `.ocx` files. `Tlbimp.exe` converts those definitions into an interop assembly; Visual Studio can also perform this conversion. [Import a type library as an assembly](https://learn.microsoft.com/en-us/dotnet/framework/interop/importing-a-type-library-as-an-assembly)
- Modern MSBuild describes a `COMReference` with GUID, major/minor version, locale, and wrapper tool. `tlbimp` produces an interop assembly; `aximp` is the wrapper tool for an ActiveX control. A registry-based COM reference depends on machine registry state. [Common MSBuild project items: `COMReference`](https://learn.microsoft.com/en-us/visualstudio/msbuild/common-msbuild-project-items#comreference)
- `CreateObject(ProgId)` creates and returns a COM object as `Object`; access through that variable is late bound. A literal ProgID identifies a boundary, not a statically resolvable member implementation. [`Interaction.CreateObject`](https://learn.microsoft.com/en-us/dotnet/api/microsoft.visualbasic.interaction.createobject?view=netframework-4.8.1)

## Parser option evaluation

| Option | Primary-source evidence | Fit with runtime-free Python CLI | Assessment |
|---|---|---:|---|
| CodeAnt `tree-sitter-vb-dotnet` as published | Tree-sitter's parser list names it as the current VB.NET grammar with generated grammar/parser data and no external scanner. Its README targets VB 16.9/.NET 5, lists basic error recovery, and provides Node installation. It also contains Python packaging metadata. [Tree-sitter parser list](https://github.com/tree-sitter/tree-sitter/wiki/List-of-parsers), [grammar README](https://github.com/CodeAnt-AI/tree-sitter-vb-dotnet), [Python package metadata](https://github.com/CodeAnt-AI/tree-sitter-vb-dotnet/blob/main/pyproject.toml) | High mechanically | **Reject as-is.** The grammar's method declaration ends after parameters/return type and has no `Handles` production. `WithEvents` is recognized as a modifier, but ordinary `AddHandler event, AddressOf handler` is absent from the statement alternatives; `AddHandler` is present only for custom-event accessor blocks. [grammar: method declaration and modifiers](https://github.com/CodeAnt-AI/tree-sitter-vb-dotnet/blob/main/grammar.js#L169-L253), [grammar: event accessors and statements](https://github.com/CodeAnt-AI/tree-sitter-vb-dotnet/blob/main/grammar.js#L303-L404) |
| A pinned ChangeScope fork of that grammar | Tree-sitter is designed as a dependency-free embeddable C runtime and produces concrete syntax trees with error recovery. [Tree-sitter repository](https://github.com/tree-sitter/tree-sitter) | High | **Recommended only after a spike.** Add the bounded VB 7.1 constructs ChangeScope needs, generate and pin the C parser, expose a Python wheel compatible with ChangeScope's pinned Tree-sitter runtime, and keep grammar tests in-repo. Do not generate or download grammars on an end-user machine. |
| Roslyn `Microsoft.CodeAnalysis.VisualBasic` | Roslyn is Microsoft's open-source C#/VB compiler and exposes full-fidelity syntax and code-analysis APIs. `VisualBasicSyntaxTree.ParseText` parses source into a syntax tree. [Roslyn repository](https://github.com/dotnet/roslyn), [`VisualBasicSyntaxTree.ParseText`](https://learn.microsoft.com/en-us/dotnet/api/microsoft.codeanalysis.visualbasic.visualbasicsyntaxtree.parsetext) | Low | **Not a Version 1 dependency.** It is a .NET assembly/NuGet package with additional managed dependencies, so it violates the agreed no-.NET-runtime baseline. It remains a plausible future opt-in semantic-evidence adapter. [NuGet package and dependencies](https://www.nuget.org/packages/Microsoft.CodeAnalysis.VisualBasic) |
| Handwritten tokenizer/structural parser | The official language documentation demonstrates explicit line continuation, colon-separated statements, conditional compilation, and case-insensitive tokens. | Medium for a narrow subset | **Fallback, not a general parser.** It can safely cover declarations, `Handles`, `AddHandler`, literal calls, and evidence spans if it first tokenizes logical lines. Pure regex over physical lines is not acceptable. |

## Recommended implementation shape

The following are engineering recommendations derived from the facts above.

### 1. Separate input adapters from source semantics

Use three independent adapters behind a common VB project model:

1. A `.sln` text reader that extracts project identities, paths, and explicit solution dependencies without trying to build the solution.
2. A tolerant pre-MSBuild `.vbproj` XML reader for the Visual Studio .NET 2003 dialect. Match XML local names rather than assuming a namespace, preserve unknown elements, and extract only observed-and-tested source files, resource dependencies, imports/references, output identity, root namespace, startup object, compiler options, configuration paths, and COM metadata.
3. A later-MSBuild `.vbproj` XML reader for upgraded repositories. Do not run MSBuild or expand arbitrary imports; unexpanded properties and conditional items become unresolved project evidence.

Source discovery should use project membership when it is available, while also reporting committed `.vb`/`.resx`/configuration files that are present but not included. `bin`, `obj`, generated interop outputs, and deployed `.exe.config` copies remain non-authoritative unless explicitly imported as optional build evidence.

### 2. Put a parser facade in front of Tree-sitter

The relationship extractor should consume ChangeScope-owned nodes (`TypeDecl`, `MethodDecl`, `Invocation`, `WithEventsField`, `HandlesBinding`, `AddHandlerBinding`, and so on), not grammar-specific node names. This keeps a fork, upstream replacement, or narrow fallback parser from leaking into the index schema.

Before adopting the grammar, build a spike with real VB.NET 2003-shaped fixtures and require:

- Exact source spans for class/module, `Sub`, `Function`, fields, calls, and string literals.
- Successful parsing of `WithEvents`, a comma-separated `Handles` list, ordinary `AddHandler`/`RemoveHandler`, `AddressOf`, attributes, explicit `_` continuation, colon-separated statements, and `#If` blocks.
- No false event edge from handler naming convention alone (for example, a method named `SaveButton_Click` without binding evidence).
- Stable parsing of designer `InitializeComponent`, including control construction, property assignment, `Controls.Add`, and event hookup.
- Correct handling of bracketed identifiers and case-insensitive lookup.
- A pinned license, commit, generated parser ABI, and Python wheel build that works with ChangeScope's pinned Tree-sitter runtime.

### 3. Decode before parsing, and preserve evidence coordinates

Use BOM-aware decoding for UTF-8/UTF-16 first. Then use an explicitly configured repository/project code page where available. A strict UTF-8 attempt may be useful, but a no-BOM ANSI fallback must be explicit (for example CP950 in a Taiwanese repository), recorded in index metadata, and surfaced when ambiguous. Never silently use the current machine locale.

Retain the original bytes, selected encoding, decoded line starts, and parser byte offsets so every evidence item can reproduce a source path and line range. A decode failure should skip the file with an unresolved item rather than replacement-decoding identifiers.

### 4. Resolve conservatively

- Normalize lookup keys case-insensitively while preserving source spelling in reports.
- Read `Option Strict` at file level; if absent, use the project setting. Do not lower confidence for every call merely because strictness is off.
- Resolve a receiver only from explicit local type evidence, imports/root namespace, inheritance, and unique project references. An `Object` receiver, omitted/unknown type, `CallByName`, reflection, or members reached after `CreateObject` remain unresolved.
- Index all conditional-compilation branches with their condition as evidence unless the required constants are explicitly known. Do not silently select a branch from the indexing machine's environment.
- Treat designer source as authoritative source evidence but retain `designer-generated` provenance. Parse `.resx` as XML only; do not instantiate types or deserialize values.

### 5. Model boundaries without pretending to execute them

- `Process.Start` or `Shell` with a literal executable can create a local-process boundary only when it uniquely matches a project output. Paths read from config, concatenated command lines, aliases, and duplicate output names remain unresolved.
- A literal configuration key creates an edge to a source configuration entry, not to a deployed value. Machine-level configuration and deployment transforms are outside the baseline.
- A stored-procedure edge requires `CommandType.StoredProcedure` plus a literal/evaluable `CommandText`. Dynamic SQL and dynamically composed procedure names remain unresolved.
- Typed COM calls may end at a COM interop boundary identified by project/type evidence. Registry state, ActiveX instantiation, `CreateObject`/`GetObject` members, and binary internals are not traversed.

## Suggested first vertical slice

The first runnable fixture should contain one pre-MSBuild-shaped WinForms project and no compiled artifacts:

```text
SaveButton.Click
  -> Handles or AddHandler evidence
OrderForm#SaveButton_Click
  -> direct typed call
OrderService#Save
```

The change target remains `Class#SubOrFunction`. The report should show the reverse UI binding, direct callers/callees, exact source evidence, confidence, and unresolved items. Include these counterexamples:

- Same handler-shaped name with no event binding: no event edge.
- `Dim service As Object : service.Save()`: unresolved late-bound call, while typed calls in the same file remain resolvable.
- Two classes with the same method name: ambiguous, never name-matched.
- A form with generated code in the same `.vb` file and another fixture with a `.Designer.vb` split.
- A CP950/ANSI fixture with Chinese control text and identifiers, plus UTF-8-with-BOM and UTF-16 fixtures.
- A conditional-compilation block whose alternatives bind different handlers.

Configuration, ADO.NET, COM, and process-launch fixtures can follow as separate complete vertical slices using the same parser/project model. This keeps the first parser decision measurable without prematurely claiming full VB.NET semantic analysis.

## Open research gaps to close with real repositories

- Exact Visual Studio .NET 2003 `.vbproj` variants used by the organization, including project-to-project references, COM/ActiveX reference attributes, per-configuration compiler settings, and source/resource membership.
- Whether forms keep designer code in the form file, use migrated `.Designer.vb` files, or contain hand-edited/generated hybrids.
- Actual source encodings and whether repositories contain mixed encodings.
- Frequency of default properties, unqualified module members, `CallByName`, `CreateObject`, typed interop assemblies, custom controls, and third-party designer code.
- Test framework conventions, if any, for .NET 1.1-era applications.

These gaps should be answered with sanitized fixtures or a read-only corpus inventory. None requires installing or running Visual Studio, .NET Framework 1.1, the applications, or COM components.
