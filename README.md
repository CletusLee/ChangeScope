# ChangeScope

Local-first, evidence-backed impact analysis for Java, Spring Boot, WildFly EJB, WildFly & JBoss SOAP, Quarkus, and legacy VB.NET 2003 WinForms repositories.

## The problem

Before changing legacy code, an engineer needs to know more than where a name appears in a text search. The useful question is:

> If this method, component, configuration key, WSDL operation, WinForms event, or interface contract changes, what local code, services, configuration, and tests are affected—and what source evidence proves it?

ChangeScope answers a deliberately smaller version of that question for checked-out repositories. It builds a local SQLite index, analyzes Java source structurally with Tree-sitter, parses legacy VB.NET 2003 WinForms source runtime-free, parses WSDL/XSD contracts and framework descriptors, and produces a reviewable impact report containing:

- the interpreted change target (Java `Class#method`, VB.NET `Class#method`, or SOAP WSDL operation);
- affected relationships grouped by their evidence-backed kind;
- source paths and line ranges;
- confidence levels (high, medium, low);
- assumptions and unresolved items (including late-bound calls, process boundaries, and COM interop); and
- the local Git snapshot and Workspace Catalog provenance used for the analysis.

ChangeScope is not a generic code-search, code-memory, or full semantic call-graph product. Its primary design rule is conservative: an unsupported or ambiguous relationship is reported as unresolved instead of being guessed.

## Current status

The current release is a JRE-free local-analysis capability covering Java, Spring/Spring Boot, annotation- and descriptor-backed WildFly EJB, Quarkus, WildFly & JBoss SOAP Web Services (JAX-WS / WSDL / XSD), legacy VB.NET 2003 WinForms applications without requiring Visual Studio or the .NET runtime, and cross-repository analysis via the Workspace Catalog. The same application-service seam is exposed through the CLI and a local stdio MCP server.

### Validation benchmarks

- **Spring / Spring Boot**: Tested against four public Spring Boot applications: [Spring Petclinic](https://github.com/spring-projects/spring-petclinic), [Spring Petclinic REST](https://github.com/spring-spring-petclinic-rest), [Spring Petclinic Modulith](https://github.com/spring-petclinic/spring-petclinic-modulith), and [RealWorld Spring Boot](https://github.com/gothinkster/spring-boot-realworld-example-app).
- **WildFly EJB**: Tested against five official WildFly EJB quickstarts: [`ejb-remote`](https://github.com/wildfly/quickstart/tree/main/ejb-remote), [`ejb-throws-exception`](https://github.com/wildfly/quickstart/tree/main/ejb-throws-exception), [`ejb-security-context-propagation`](https://github.com/wildfly/quickstart/tree/main/ejb-security-context-propagation), [`helloworld-mdb`](https://github.com/wildfly/quickstart/tree/main/helloworld-mdb), and [`ejb-timer`](https://github.com/wildfly/quickstart/tree/main/ejb-timer).
- **Quarkus**: Tested against four official Quarkus quickstarts: [`getting-started`](https://github.com/quarkusio/quarkus-quickstarts/tree/main/getting-started), [`rest-client-quickstart`](https://github.com/quarkusio/quarkus-quickstarts/tree/main/rest-client-quickstart), [`hibernate-orm-panache-quickstart`](https://github.com/quarkusio/quarkus-quickstarts/tree/main/hibernate-orm-panache-quickstart), and [`security-jpa-quickstart`](https://github.com/quarkusio/quarkus-quickstarts/tree/main/security-jpa-quickstart).
- **WildFly & JBoss SOAP**: Validated against three representative official [WildFly quickstarts](https://github.com/wildfly/quickstart): [`helloworld-ws`](https://github.com/wildfly/quickstart/tree/main/helloworld-ws) (POJO Web Service), [`jaxws-ejb`](https://github.com/wildfly/quickstart/tree/main/jaxws-ejb) (EJB Session Bean SOAP Web Service with Remote Business Interfaces), and [`jaxws-retail`](https://github.com/wildfly/quickstart/tree/main/jaxws-retail) (Complex WSDL contract, XSD payload binding, and evidence navigation).
- **VB.NET 2003 WinForms**: Validated against the open-source GitHub application [Fast-Food-Ordering-System-VB.Net](https://github.com/gauravpatil-06/Fast-Food-Ordering-System-VB.Net) with 23 source/project files, 34 declarations, 525 invocations, 141 facts, zero parse failures, and full WinForms event handler, direct call, and manual verification surface resolution.

## Key capabilities

### Repository indexing

`changescope index` analyzes the current working directory as one repository root. It can:

- discover Maven, Gradle, Eclipse, and conventional `src/main/java` / `src/test/java` layouts;
- fall back to a constrained recursive Java scan for irregular repositories;
- exclude common metadata, build, dependency, vendor, generated-output, and local-index directories;
- index Java declarations, method invocations, annotations, source locations, and test-source classification;
- discover local `.properties`, `.yml`, `.yaml`, `.xml`, `.wsdl`, and `.xsd` configuration and contract files;
- store the local Repository Index in `.changescope/index.sqlite`; and
- record available Git commit and working-tree provenance in the Index Snapshot.

### Target resolution

ChangeScope supports two change target formats:

1. **Java Target**: `changescope impact Class#method`
2. **SOAP Target**: `changescope impact --soap-wsdl <path> --soap-port-type <portType> --soap-operation <operation>`

- One matching target produces a `resolved` outcome with signature/evidence.
- Multiple candidates produce an `ambiguous` result; overloaded methods are not silently selected.
- No match produces a `not_found` result.

### Local Spring and Spring Boot evidence

Recognizes and connects:
- `@Component`, `@Service`, `@Repository`, `@Controller`, `@RestController`, `@Configuration`;
- Java `@Bean` factory methods;
- `@Autowired`, `@Inject`, `@Resource` field/constructor injection;
- `@Value` and `@ConfigurationProperties` property consumers;
- Spring XML `<bean>` definitions and `<property ref="...">` references; and
- Spring-aware test loading (`@SpringBootTest`, etc.).

### WildFly EJB evidence

Recognizes and connects:
- `javax.ejb` and `jakarta.ejb` `@Local` and `@Remote` business interfaces;
- `@Stateless`, `@Stateful`, and `@Singleton` Session Beans;
- local and remote view metadata;
- unique `@EJB` field and setter Injection Points with medium-confidence container dispatch;
- descriptor-backed `ejb-jar.xml` and `jboss-ejb3.xml` Session Beans and business views; and
- EJB-aware test wiring.

### Local Quarkus evidence

Recognizes and connects:
- CDI-managed beans (`@ApplicationScoped`, `@RequestScoped`, `@Singleton`), qualifiers, and `@Inject` points;
- Quarkus REST / RESTEasy endpoints (`@Path`, `@GET`, `@POST`, `@Consumes`, `@Produces`, `Uni`/`Multi`);
- Reactive Routes (`@Route`);
- MicroProfile REST Clients (`@RegisterRestClient`);
- Security policies (`@RolesAllowed`, `@Authenticated`);
- Test wiring (`@QuarkusTest`, `@QuarkusIntegrationTest`, `@InjectMock`, `@TestHTTPEndpoint`); and
- GraalVM Native Image evidence and Panache persistence boundaries.

### WildFly & JBoss SOAP Web Services evidence

Recognizes and connects:
- WSDL 1.1 operations, messages, portTypes, and schema element/complexType payload graphs;
- Recursive repository-local WSDL/XSD import traversal with cyclic path safety and remote reference detection;
- Portable `javax.jws.*`, `jakarta.jws.*`, `javax.xml.ws.*`, `jakarta.xml.ws.*` annotations (`@WebService`, `@WebMethod`, `@Oneway`, `@WebServiceClient`, `@WebEndpoint`, `@WebServiceRef`, `@WebServiceProvider`, `@WebFault`);
- XML binding annotations (`@XmlType`, `@XmlRootElement`, `@XmlElement`, `@RequestWrapper`, `@ResponseWrapper`);
- Code-first endpoints reported as derived contracts at medium Confidence;
- Unified impact neighborhoods for SOAP-exposed EJB Session Beans (`@Stateless` + `@WebService`);
- Web-service descriptors (`webservices.xml`, `jboss-webservices.xml`, `jbossws-cxf.xml`);
- `@HandlerChain` XML resolution (`handler-chain.xml`, `handlers.xml`) and JBossWS/CXF interceptors; and
- WS-Policy attachments (`wsp:Policy`), WS-Security, WS-Addressing (`@Addressing`), MTOM (`@MTOM`), and SOAP 1.1/1.2 binding styles.

### Legacy VB.NET 2003 WinForms evidence

Recognizes and connects:
- Multi-encoding file discovery (`.vb`, `.vbproj`, `.sln`, `.resx`, `.config`, `app.config`, `web.config`, `.sql`);
- Line continuation (`_`), case-insensitive keyword parsing, and designer region `#region ... #end region` provenance tracking;
- Local variable typing (`Dim dao As New OrderDAO()`) and `VB.NET Late-Bound Call` reporting on untyped `Object` / `Option Strict Off`;
- WinForms control event wiring (`Handles Clause`, `AddHandler`, `RemoveHandler`) and `ManualVerificationSurface` reporting for un-automated form logic;
- Multi-project solution assembly linking and `Process.Start(...)` / `Shell(...)` process launches (`Local Process Boundary` / `External Process Boundary`);
- `ConfigurationSettings.AppSettings` reads and ADO.NET `CommandText` / `CommandType = StoredProcedure` data access boundaries linked to `.sql` definitions; and
- COM interop references (`<Reference Name="..." GUID="...">`) and `CreateObject(...)` dynamic COM calls (`COM Interop Boundary`).

### Workspace Catalog and cross-repository continuation

`changescope catalog` manages explicit repository registrations and typed contract mappings across repositories in `.changescope/catalog.sqlite`:

- `changescope catalog register-repo --id <id> --path <path>`: registers repository identity and tracks index commit SHA staleness.
- `changescope catalog register-mapping --source-repo <id> --kind <rest|soap|ejb> --key <key> --target-repo <id> --target-key <key>`: registers explicit cross-repository contract mapping.
- `changescope catalog resolve --source-repo <id> --kind <kind> --key <key>`: resolves exact mapping.
- **Cross-repository continuation**: When a registered target repository or contract mapping is present, ChangeScope continues impact analysis from a client in repository A to the matching endpoint implementation in repository B (e.g. across container migration from `javax` to `jakarta`).

### Profiles, reports, and evidence navigation

- Profile selection: `--profile <spring>` `--build-profile <quarkus>` `--runtime-profile <quarkus>`.
- Format parity: `--format text` and `--format json` output equivalent facts, evidence handles, and provenance.
- Evidence navigation:
  - `changescope evidence <handle>`: context window view.
  - `changescope source <path> <start-line> <end-line>`: explicit line range view.

### Local stdio MCP

Start the model-facing adapter against one explicitly configured repository:

```powershell
changescope-mcp --repository-root .
```

For a registered Workspace Catalog, use Workspace Mode instead:

```powershell
changescope-mcp --workspace-root .
```

The server publishes five tools—`index_repository`, `discover_contracts`, `analyze_impact`, `get_evidence`, and `read_source_range`—and read-only catalog, Repository Index status, and Index Snapshot resources. Indexing is explicit; discovery and impact refresh an existing index when local inputs change. Impact supports `handles_only`, `primary`, and `context_bundle` evidence modes with deterministic item and character limits. Verified workspace traversal follows only explicit catalog mappings and reports stale or unverified links as structured unresolved items.

The server uses JSON-RPC over stdio only. It does not open a network listener, scan outside the configured repository or Registered Workspace, mutate the Workspace Catalog, fetch remote source, or run a background indexer. Normal analysis outcomes such as `resolved`, `partial`, `ambiguous`, `not_found`, `index_missing`, `stale_target`, and `unsupported` are returned as structured results; malformed requests and filesystem-boundary violations are MCP errors.

## Installation

Requirements:
- Python 3.10 or newer;
- No local JRE or JDK requirement for ChangeScope; and
- A checked-out repository to analyze.

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

Dependencies are pinned: `tree-sitter==0.25.2` is paired with `tree-sitter-java==0.23.5`.

## CLI quick start

```bash
# Build or refresh the local SQLite index.
changescope index

# Ask for a Java method target impact report.
changescope impact GreetingResource#hello

# Ask for a VB.NET method target impact report.
changescope impact Form1#Button1_Click

# Ask for a SOAP WSDL operation target impact report.
changescope impact --soap-wsdl wsdl/order_service.wsdl --soap-port-type OrderPortType --soap-operation placeOrder

# Register repository in Workspace Catalog.
changescope catalog register-repo --id order-service --path ./order-service

# Register explicit contract mapping in Workspace Catalog.
changescope catalog register-mapping \
  --source-repo order-service \
  --kind soap \
  --key "{http://example.org/orders}OrderPortType#placeOrder" \
  --target-repo payment-service \
  --target-key "{http://example.org/orders}OrderPortType#placeOrder"

# Retrieve source evidence from handle.
changescope evidence "soap_wsdl:wsdl/order_service.wsdl:15-20" --enclosing-symbol --format json
```

## Development and verification

Run the full repository test suite with:

```bash
python -m unittest discover -s tests -v
```

The test suite contains **248 unit and integration tests (100% passing)** covering:
- Repository discovery & Java AST parsing;
- Spring Boot beans, properties, and XML;
- WildFly EJB session beans, descriptors, and interfaces;
- Quarkus CDI, REST, REST Client, Security, and Native Image boundaries;
- Workspace Catalog repository registration and mapping resolution;
- local stdio MCP tool/resource schemas, progress, bounded impact response modes, and workspace traversal;
- WildFly & JBoss SOAP WSDL/XSD payload graphs, portable endpoints, EJB SOAP beans, handlers, policies, cross-repo continuation, and report parity; and
- Runtime-free VB.NET 2003 WinForms parser facade, case-insensitive symbol resolution, late-bound call reporting, WinForms event wiring, multi-project process launches, ADO.NET & appSettings config boundaries, COM interop boundaries, and affected tests.

## Design principles

- **Evidence before breadth**: every asserted relationship has inspectable local source evidence.
- **Conservative confidence**: uncertainty is reported as unresolved, never guessed.
- **Local-only by default**: no telemetry, cloud calls, remote fetching, embeddings, vector databases, daemons, or Docker daemon access.
- **JRE-free static analysis**: pure Python and Tree-sitter parser without JVM dependencies.
- **Small vertical slices**: runnable, tested report behaviors built in incremental stages.
