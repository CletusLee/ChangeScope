from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from typing import Iterable
from xml.etree import ElementTree

from tree_sitter import Language, Parser
import tree_sitter_java

EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".changescope",
        ".git",
        ".gradle",
        ".hg",
        ".idea",
        ".m2",
        ".mvn",
        ".settings",
        ".svn",
        ".venv",
        "__pycache__",
        "build",
        "dependencies",
        "deps",
        "generated",
        "generated-sources",
        "generated-test-sources",
        "lib",
        "libs",
        "node_modules",
        "out",
        "target",
        "third-party",
        "third_party",
        "vendor",
    }
)

_INDEX_SCHEMA_VERSION = "1"
_INDEX_SCHEMA_COLUMNS = {
    "metadata": frozenset({"key", "value"}),
    "source_files": frozenset({"path", "status", "content_hash"}),
    "java_declarations": frozenset(
        {"kind", "name", "qualified_name", "signature", "path", "start_line", "end_line", "is_test", "is_private"}
    ),
    "java_invocations": frozenset(
        {"name", "receiver", "caller", "path", "start_line", "end_line", "is_test", "argument_count"}
    ),
    "parse_failures": frozenset({"path", "start_line", "start_column", "message"}),
    "spring_facts": frozenset({"kind", "subject", "target", "value", "path", "start_line", "end_line", "profile"}),
    "ejb_facts": frozenset({"kind", "subject", "target", "value", "path", "start_line", "end_line"}),
    "quarkus_build_facts": frozenset({"kind", "subject", "target", "value", "path", "start_line", "end_line", "profile"}),
    "quarkus_config_facts": frozenset(
        {"kind", "subject", "target", "value", "path", "start_line", "end_line", "profile", "is_build_time"}
    ),
    "quarkus_cdi_facts": frozenset({"kind", "subject", "target", "value", "path", "start_line", "end_line", "scope"}),
    "quarkus_rest_facts": frozenset({"kind", "subject", "target", "value", "path", "start_line", "end_line", "flavor"}),
    "quarkus_route_facts": frozenset({"kind", "subject", "target", "value", "path", "start_line", "end_line", "flavor"}),
    "quarkus_security_facts": frozenset({"kind", "subject", "target", "value", "path", "start_line", "end_line", "policy"}),
    "quarkus_test_facts": frozenset({"kind", "subject", "target", "value", "path", "start_line", "end_line", "flavor"}),
    "quarkus_native_facts": frozenset({"kind", "subject", "target", "value", "path", "start_line", "end_line", "scope"}),
    "quarkus_boundary_facts": frozenset({"kind", "subject", "target", "value", "path", "start_line", "end_line", "category"}),
    "soap_facts": frozenset({"kind", "subject", "target", "value", "path", "start_line", "end_line", "namespace"}),
    "vbnet_declarations": frozenset(
        {"kind", "name", "qualified_name", "signature", "path", "start_line", "end_line", "is_test", "is_private", "language"}
    ),
    "vbnet_invocations": frozenset(
        {"name", "receiver", "caller", "path", "start_line", "end_line", "is_test", "argument_count", "language"}
    ),
    "vbnet_facts": frozenset({"kind", "subject", "target", "value", "path", "start_line", "end_line", "extra_info"}),
}


@dataclass(frozen=True)
class IndexSnapshot:
    repository_root: Path
    git_commit: str | None
    working_tree_state: str


@dataclass(frozen=True)
class JavaDeclaration:
    kind: str
    name: str
    qualified_name: str
    signature: str
    path: Path
    start_line: int
    end_line: int
    is_test: bool
    is_private: bool
    language: str = "java"


@dataclass(frozen=True)
class JavaInvocation:
    name: str
    receiver: str | None
    caller: str | None
    path: Path
    start_line: int
    end_line: int
    is_test: bool
    argument_count: int | None = None
    language: str = "java"


@dataclass(frozen=True)
class VBNETDeclaration:
    kind: str
    name: str
    qualified_name: str
    signature: str
    path: Path
    start_line: int
    end_line: int
    is_test: bool
    is_private: bool
    language: str = "vbnet"


@dataclass(frozen=True)
class VBNETInvocation:
    name: str
    receiver: str | None
    caller: str | None
    path: Path
    start_line: int
    end_line: int
    is_test: bool
    argument_count: int | None = None
    language: str = "vbnet"


@dataclass(frozen=True)
class VBNETFact:
    kind: str
    subject: str
    target: str | None
    value: str | None
    path: Path
    start_line: int
    end_line: int
    extra_info: str | None = None


@dataclass(frozen=True)
class ManualVerificationSurface:
    kind: str
    description: str
    path: Path
    start_line: int
    end_line: int
    evidence_handle: str


@dataclass(frozen=True)
class SpringFact:
    kind: str
    subject: str
    target: str | None
    value: str | None
    path: Path
    start_line: int
    end_line: int
    profile: str | None = None


@dataclass(frozen=True)
class EJBFact:
    kind: str
    subject: str
    target: str | None
    value: str | None
    path: Path
    start_line: int
    end_line: int


@dataclass(frozen=True)
class QuarkusBuildFact:
    kind: str
    subject: str
    target: str | None
    value: str | None
    path: Path
    start_line: int
    end_line: int
    profile: str | None = None


@dataclass(frozen=True)
class QuarkusConfigFact:
    kind: str
    subject: str
    target: str | None
    value: str | None
    path: Path
    start_line: int
    end_line: int
    profile: str | None = None
    is_build_time: bool = False


@dataclass(frozen=True)
class QuarkusCDIFact:
    kind: str
    subject: str
    target: str | None
    value: str | None
    path: Path
    start_line: int
    end_line: int
    scope: str | None = None


@dataclass(frozen=True)
class QuarkusRESTFact:
    kind: str
    subject: str
    target: str | None
    value: str | None
    path: Path
    start_line: int
    end_line: int
    flavor: str | None = None


@dataclass(frozen=True)
class QuarkusRouteFact:
    kind: str
    subject: str
    target: str | None
    value: str | None
    path: Path
    start_line: int
    end_line: int
    flavor: str | None = None


@dataclass(frozen=True)
class ParseFailure:
    path: Path
    start_line: int
    start_column: int
    message: str


@dataclass(frozen=True)
class QuarkusSecurityFact:
    kind: str
    subject: str
    target: str | None
    value: str | None
    path: Path
    start_line: int
    end_line: int
    policy: str | None = None


@dataclass(frozen=True)
class QuarkusTestFact:
    kind: str
    subject: str
    target: str | None
    value: str | None
    path: Path
    start_line: int
    end_line: int
    flavor: str | None = None


@dataclass(frozen=True)
class QuarkusNativeFact:
    kind: str
    subject: str
    target: str | None
    value: str | None
    path: Path
    start_line: int
    end_line: int
    scope: str | None = None


@dataclass(frozen=True)
class QuarkusBoundaryFact:
    kind: str
    subject: str
    target: str | None
    value: str | None
    path: Path
    start_line: int
    end_line: int
    category: str | None = None


@dataclass(frozen=True)
class SOAPFact:
    kind: str
    subject: str
    target: str | None
    value: str | None
    path: Path
    start_line: int
    end_line: int
    namespace: str | None = None


RESTFact = QuarkusRESTFact


@dataclass(frozen=True)
class RESTChangeTarget:
    http_method: str
    path: str
    consumes: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    params: tuple[str, ...] = ()
    headers: tuple[str, ...] = ()
    route_shape: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'http_method', self.http_method.strip().upper())
        object.__setattr__(self, 'path', _normalize_rest_path(self.path))
        object.__setattr__(self, 'consumes', _string_tuple(self.consumes))
        object.__setattr__(self, 'produces', _string_tuple(self.produces))
        object.__setattr__(self, 'params', _string_tuple(self.params))
        object.__setattr__(self, 'headers', _string_tuple(self.headers))
        object.__setattr__(self, 'route_shape', _rest_route_shape(self.path))

    @property
    def signature(self) -> str:
        return f'{self.http_method} {self.path}'

    @property
    def contract_key(self) -> str:
        conditions = []
        if self.consumes:
            conditions.append('consumes=' + ','.join(self.consumes))
        if self.produces:
            conditions.append('produces=' + ','.join(self.produces))
        if self.params:
            conditions.append('params=' + ','.join(self.params))
        if self.headers:
            conditions.append('headers=' + ','.join(self.headers))
        return self.signature + ' [' + '; '.join(conditions) + ']' if conditions else self.signature

    @property
    def method(self) -> str:
        return self.http_method

    @property
    def route(self) -> str:
        return self.path


@dataclass(frozen=True)
class RESTContractProvenance:
    application_paths: tuple[str, ...] = ()
    class_paths: tuple[str, ...] = ()
    method_path: str = ''
    http_method: str = ''
    consumes: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    headers: tuple[str, ...] = ()
    route_shape: str = '/'
    flavors: tuple[str, ...] = ()
    evidence_handles: tuple[str, ...] = ()

@dataclass(frozen=True)
class SOAPChangeTarget:
    """Structured SOAP Change Target whose identity is PortType QName plus operation."""

    wsdl: Path
    port_type: str
    operation: str

    @property
    def signature(self) -> str:
        return f"{self.port_type}#{self.operation}"

    @property
    def contract_key(self) -> str:
        return f"soap:{self.signature}"

    @property
    def wsdl_path(self) -> Path:
        return self.wsdl

    @property
    def port_type_qname(self) -> str:
        return self.port_type

    @property
    def operation_name(self) -> str:
        return self.operation


@dataclass(frozen=True)
class SOAPContractProvenance:
    wsdl: Path
    namespace: str | None = None
    services: tuple[str, ...] = ()
    ports: tuple[str, ...] = ()
    bindings: tuple[str, ...] = ()
    endpoint_addresses: tuple[str, ...] = ()
    input_messages: tuple[str, ...] = ()
    output_messages: tuple[str, ...] = ()
    fault_messages: tuple[str, ...] = ()
    evidence_handles: tuple[str, ...] = ()


@dataclass(frozen=True)
class IndexResult:
    source_roots: tuple[Path, ...]
    indexed_files: tuple[Path, ...]
    excluded_directories: tuple[Path, ...]
    read_failures: tuple[Path, ...]
    declarations: tuple[JavaDeclaration, ...]
    invocations: tuple[JavaInvocation, ...]
    parse_failures: tuple[ParseFailure, ...]
    snapshot: IndexSnapshot
    spring_facts: tuple[SpringFact, ...] = ()
    ejb_facts: tuple[EJBFact, ...] = ()
    configuration_files: tuple[Path, ...] = ()
    quarkus_build_facts: tuple[QuarkusBuildFact, ...] = ()
    quarkus_config_facts: tuple[QuarkusConfigFact, ...] = ()
    quarkus_cdi_facts: tuple[QuarkusCDIFact, ...] = ()
    quarkus_rest_facts: tuple[QuarkusRESTFact, ...] = ()
    quarkus_route_facts: tuple[QuarkusRouteFact, ...] = ()
    quarkus_security_facts: tuple[QuarkusSecurityFact, ...] = ()
    quarkus_test_facts: tuple[QuarkusTestFact, ...] = ()
    quarkus_native_facts: tuple[QuarkusNativeFact, ...] = ()
    quarkus_boundary_facts: tuple[QuarkusBoundaryFact, ...] = ()
    soap_facts: tuple[SOAPFact, ...] = ()
    vbnet_declarations: tuple[VBNETDeclaration, ...] = ()
    vbnet_invocations: tuple[VBNETInvocation, ...] = ()
    vbnet_facts: tuple[VBNETFact, ...] = ()
    vbnet_files: tuple[Path, ...] = ()
    indexed_file_hashes: tuple[tuple[Path, str], ...] = ()

    @property
    def rest_facts(self) -> tuple[RESTFact, ...]:
        return tuple(
            RESTFact(
                fact.kind, fact.subject, fact.target, fact.value, fact.path,
                fact.start_line, fact.end_line,
                fact.flavor if fact.flavor and fact.flavor != 'unknown' else 'jaxrs',
            )
            for fact in self.quarkus_rest_facts
        )


@dataclass(frozen=True)
class IndexRequest:
    repository_root: Path


@dataclass(frozen=True)
class ImpactRequest:
    repository_root: Path
    target: str | None = None
    profiles: tuple[str, ...] = ()
    build_profiles: tuple[str, ...] = ()
    runtime_profiles: tuple[str, ...] = ()
    soap_wsdl: Path | None = None
    soap_port_type: str | None = None
    soap_operation: str | None = None
    soap_target: SOAPChangeTarget | None = None
    rest_target: RESTChangeTarget | None = None


@dataclass(frozen=True)
class ContractDiscoveryRequest:
    repository_root: Path
    terms: tuple[str, ...] = ()
    soap_wsdl: Path | None = None
    soap_port_type: str | None = None
    soap_operation: str | None = None
    limit: int = 50
    offset: int = 0
    rest_http_method: str | None = None
    rest_path: str | None = None
    rest_consumes: tuple[str, ...] = ()
    rest_produces: tuple[str, ...] = ()
    rest_params: tuple[str, ...] = ()
    rest_headers: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceRequest:
    repository_root: Path
    evidence_handle: str
    context_lines: int = 2
    max_characters: int = 4_000
    enclosing_symbol: bool = False


@dataclass(frozen=True)
class SourceRequest:
    repository_root: Path
    path: Path
    start_line: int
    end_line: int
    max_characters: int = 4_000
    start_column: int = 0


@dataclass(frozen=True)
class SourceNavigation:
    evidence_handle: str
    path: Path
    start_line: int
    end_line: int
    content: str
    truncated: bool
    continuation_start_line: int | None
    continuation_start_column: int | None


@dataclass(frozen=True)
class ImpactTarget:
    signature: str
    path: Path
    start_line: int
    end_line: int
    evidence_handle: str
    language: str = "java"


@dataclass(frozen=True)
class ImpactRelationship:
    kind: str
    caller: str
    path: Path
    start_line: int
    end_line: int
    evidence_handle: str
    confidence: str
    conditional: bool = False
    profile: str | None = None
    evidence_chain: tuple[str, ...] = ()
    business_view: str | None = None
    language: str = "java"

    def __post_init__(self) -> None:
        if not self.evidence_chain:
            object.__setattr__(self, "evidence_chain", (self.evidence_handle,))


@dataclass(frozen=True)
class UnresolvedItem:
    message: str
    path: Path | None = None
    start_line: int | None = None
    end_line: int | None = None
    evidence_handle: str | None = None


@dataclass(frozen=True)
class SOAPContractCandidate:
    contract_key: str
    target: SOAPChangeTarget
    match_reasons: tuple[str, ...]
    source_resolution: str
    source_entry_points: tuple[ImpactTarget, ...]
    evidence_handles: tuple[str, ...]
    provenance: SOAPContractProvenance
    unresolved_items: tuple[UnresolvedItem, ...] = ()

    @property
    def source_entry_point(self) -> ImpactTarget | None:
        return self.source_entry_points[0] if len(self.source_entry_points) == 1 else None


@dataclass(frozen=True)
class RESTContractCandidate:
    contract_key: str
    target: RESTChangeTarget
    match_reasons: tuple[str, ...]
    source_resolution: str
    source_entry_points: tuple[ImpactTarget, ...]
    evidence_handles: tuple[str, ...]
    provenance: RESTContractProvenance
    unresolved_items: tuple[UnresolvedItem, ...] = ()

    @property
    def source_entry_point(self) -> ImpactTarget | None:
        return self.source_entry_points[0] if len(self.source_entry_points) == 1 else None


@dataclass(frozen=True)
class ContractDiscoveryResult:
    outcome: str
    candidates: tuple[SOAPContractCandidate | RESTContractCandidate, ...]
    requested_terms: tuple[str, ...]
    unresolved_terms: tuple[str, ...]
    unresolved_items: tuple[UnresolvedItem, ...]
    snapshot: IndexSnapshot | None
    total_count: int
    limit: int
    offset: int
    has_more: bool
    next_offset: int | None

    @property
    def contracts(self) -> tuple[SOAPContractCandidate, ...]:
        return self.candidates


@dataclass(frozen=True)
class ImpactResult:
    outcome: str
    requested_target: str
    target: ImpactTarget | None
    candidates: tuple[ImpactTarget, ...]
    relationships: tuple[ImpactRelationship, ...]
    assumptions: tuple[str, ...]
    unresolved_items: tuple[UnresolvedItem, ...]
    snapshot: IndexSnapshot | None
    manual_verification_surfaces: tuple[ManualVerificationSurface, ...] = ()


@dataclass(frozen=True)
class CatalogRepository:
    repository_id: str
    repository_path: Path
    git_commit: str | None
    working_tree_state: str


@dataclass(frozen=True)
class CatalogMapping:
    source_repository_id: str
    contract_kind: str
    contract_key: str
    target_repository_id: str
    target_contract_key: str
    provenance: str = ""


@dataclass(frozen=True)
class CatalogRegisterRepositoryRequest:
    catalog_root: Path
    repository_id: str
    repository_path: Path


@dataclass(frozen=True)
class CatalogRegisterMappingRequest:
    catalog_root: Path
    source_repository_id: str
    contract_kind: str
    contract_key: str
    target_repository_id: str
    target_contract_key: str
    provenance: str = ""


@dataclass(frozen=True)
class CatalogResolveMappingRequest:
    catalog_root: Path
    source_repository_id: str
    contract_kind: str
    contract_key: str


@dataclass(frozen=True)
class CatalogResult:
    outcome: str
    repository: CatalogRepository | None = None
    mapping: CatalogMapping | None = None
    candidates: tuple[CatalogMapping, ...] = ()
    unresolved_items: tuple[UnresolvedItem, ...] = ()
    snapshot: IndexSnapshot | None = None


@dataclass(frozen=True)
class RepositoryStatusRequest:
    repository_root: Path


@dataclass(frozen=True)
class RepositoryIndexStatus:
    outcome: str
    repository_root: Path
    index_exists: bool
    schema_version: str | None
    indexed_file_count: int
    declaration_count: int
    invocation_count: int
    soap_fact_count: int
    snapshot: IndexSnapshot | None


@dataclass(frozen=True)
class CatalogSummaryRequest:
    catalog_root: Path


@dataclass(frozen=True)
class WorkspaceCatalogSummary:
    outcome: str
    catalog_root: Path
    catalog_exists: bool
    repositories: tuple[CatalogRepository, ...] = ()
    mappings: tuple[CatalogMapping, ...] = ()


class ChangeScopeApplication:
    """The application-service seam shared by CLI, tests, and future adapters."""

    def execute(
        self,
        request: IndexRequest
        | ImpactRequest
        | ContractDiscoveryRequest
        | EvidenceRequest
        | SourceRequest
        | CatalogRegisterRepositoryRequest
        | CatalogRegisterMappingRequest
        | CatalogResolveMappingRequest
        | RepositoryStatusRequest
        | CatalogSummaryRequest,
    ) -> IndexResult | ImpactResult | ContractDiscoveryResult | SourceNavigation | CatalogResult | RepositoryIndexStatus | WorkspaceCatalogSummary:
        if isinstance(request, IndexRequest):
            return _index_repository(request.repository_root)
        if isinstance(request, ContractDiscoveryRequest):
            return _discover_contracts(request)
        if isinstance(request, EvidenceRequest):
            return _evidence_context(request)
        if isinstance(request, SourceRequest):
            return _source_range(request)
        if isinstance(request, CatalogRegisterRepositoryRequest):
            return _catalog_register_repository(request)
        if isinstance(request, CatalogRegisterMappingRequest):
            return _catalog_register_mapping(request)
        if isinstance(request, CatalogResolveMappingRequest):
            return _catalog_resolve_mapping(request)
        if isinstance(request, RepositoryStatusRequest):
            return _repository_index_status(request.repository_root)
        if isinstance(request, CatalogSummaryRequest):
            return _workspace_catalog_summary(request.catalog_root)
        return _impact_repository(request)


def _evidence_context(request: EvidenceRequest) -> SourceNavigation:
    match = re.fullmatch(r"(?:declaration|invocation|spring|ejb|source|quarkus_build|quarkus_config|quarkus_cdi|quarkus_rest|quarkus_route|quarkus_security|quarkus_test|quarkus_native|quarkus_boundary|soap_wsdl|vbnet_declaration|vbnet_invocation|vbnet_fact|vbnet_surface):(.+):(\d+)-(\d+)", request.evidence_handle)
    if match is None:
        raise ValueError("Evidence handles must use kind:path:start-end form.")
    path = _validate_relative_path(Path(match.group(1)))
    evidence_start_line = int(match.group(2))
    evidence_end_line = int(match.group(3))
    if evidence_start_line > evidence_end_line or request.context_lines < 0:
        raise ValueError("Evidence line ranges must be ordered and context lines cannot be negative.")
    start_line = max(1, evidence_start_line - request.context_lines)
    end_line = evidence_end_line + request.context_lines
    if request.enclosing_symbol:
        enclosing_range = _enclosing_symbol_range(request.repository_root.resolve(), path, evidence_start_line, evidence_end_line)
        if enclosing_range is not None:
            start_line, end_line = enclosing_range
    return _read_bounded_source(
        request.repository_root.resolve(), path, start_line, end_line,
        request.evidence_handle, request.max_characters,
    )


def _source_range(request: SourceRequest) -> SourceNavigation:
    path = _validate_relative_path(request.path)
    if request.start_line < 1 or request.end_line < request.start_line:
        raise ValueError("Source line ranges must start at line 1 and be ordered.")
    if request.start_column < 0:
        raise ValueError("Source start columns cannot be negative.")
    evidence_handle = f"source:{path.as_posix()}:{request.start_line}-{request.end_line}"
    return _read_bounded_source(
        request.repository_root.resolve(), path, request.start_line, request.end_line,
        evidence_handle, request.max_characters, request.start_column,
    )


def _validate_relative_path(path: Path) -> Path:
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Source paths must be relative to the repository root.")
    return path


def _string_tuple(value: Iterable[str] | str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(item for item in value if item)


def _normalize_rest_path(*parts: str) -> str:
    segments: list[str] = []
    for part in parts:
        if not part:
            continue
        segments.extend(piece for piece in re.split(r'/+', str(part).replace('\\', '/').strip()) if piece)
    return '/' + '/'.join(segments) if segments else '/'


def _rest_route_shape(path: str) -> str:
    return re.sub(r'\{[^}/]+\}', '{}', _normalize_rest_path(path))


def _enclosing_symbol_range(root: Path, path: Path, start_line: int, end_line: int) -> tuple[int, int] | None:
    database_path = root / ".changescope" / "index.sqlite"
    if not database_path.is_file():
        return None
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            """SELECT start_line, end_line FROM java_declarations
            WHERE path = ? AND kind IN ('method', 'constructor', 'class', 'interface', 'enum', 'annotation', 'record')
            AND start_line <= ? AND end_line >= ?
            ORDER BY end_line - start_line, start_line LIMIT 1""",
            (str(path), start_line, end_line),
        ).fetchone()
        if not row:
            vb_has_table = connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'vbnet_declarations'").fetchone() is not None
            if vb_has_table:
                row = connection.execute(
                    """SELECT start_line, end_line FROM vbnet_declarations
                    WHERE path = ? AND start_line <= ? AND end_line >= ?
                    ORDER BY end_line - start_line, start_line LIMIT 1""",
                    (str(path), start_line, end_line),
                ).fetchone()
    finally:
        connection.close()
    return (row[0], row[1]) if row else None


def _read_bounded_source(
    root: Path, path: Path, start_line: int, end_line: int, evidence_handle: str,
    max_characters: int, start_column: int = 0,
) -> SourceNavigation:
    if max_characters < 1:
        raise ValueError("The source size budget must be at least one character.")
    source_path = _resolve_indexed_source(root, path)
    text_content, _ = _read_file_text_with_encoding(source_path)
    lines = text_content.splitlines(keepends=True)
    if start_line > len(lines):
        raise ValueError("The source range starts beyond the end of the file.")
    end_line = min(end_line, len(lines))
    selected = lines[start_line - 1 : end_line]
    content = ""
    final_line = start_line - 1
    for line_number, line in enumerate(selected, start_line):
        column = start_column if line_number == start_line else 0
        line_fragment = line[column:]
        remaining = max_characters - len(content)
        if remaining == 0:
            return SourceNavigation(
                evidence_handle, path, start_line, final_line, content, True, line_number, column
            )
        if len(line_fragment) > remaining:
            content += line_fragment[:remaining]
            return SourceNavigation(
                evidence_handle, path, start_line, line_number, content, True,
                line_number, column + remaining,
            )
        content += line_fragment
        final_line = line_number
    return SourceNavigation(evidence_handle, path, start_line, final_line, content, False, None, None)


def _resolve_indexed_source(root: Path, path: Path) -> Path:
    source_path = (root / path).resolve()
    if not source_path.is_relative_to(root):
        raise ValueError("Source paths must resolve inside the repository root.")
    database_path = root / ".changescope" / "index.sqlite"
    if not database_path.is_file():
        raise ValueError("Source navigation requires a local Repository Index. Run `changescope index` first.")
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT 1 FROM source_files WHERE (path = ? OR path = ? OR path = ?) AND status = 'indexed'",
            (str(path), path.as_posix(), str(path).replace("/", "\\")),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError("Source navigation is limited to files in the local Repository Index.")
    return source_path


def _discover_contracts(request: ContractDiscoveryRequest) -> ContractDiscoveryResult:
    rest_filter = any((
        request.rest_http_method,
        request.rest_path,
        request.rest_consumes,
        request.rest_produces,
        request.rest_params,
        request.rest_headers,
    ))
    soap_filter = any((request.soap_wsdl, request.soap_port_type, request.soap_operation))
    if rest_filter and soap_filter:
        raise ValueError('Specify either REST or SOAP discovery filters, not both.')
    if rest_filter:
        return _discover_rest_contracts(request)
    if soap_filter:
        return _discover_soap_contracts(request)
    inventory_request = replace(request, limit=100, offset=0)
    return _merge_contract_discovery(
        _discover_soap_contracts(inventory_request),
        _discover_rest_contracts(inventory_request),
        request,
    )


def _discover_rest_contracts(request: ContractDiscoveryRequest) -> ContractDiscoveryResult:
    root = request.repository_root.resolve()
    database_path = root / '.changescope' / 'index.sqlite'
    if request.limit < 1 or request.limit > 100:
        raise ValueError('Contract discovery limit must be between 1 and 100.')
    if request.offset < 0:
        raise ValueError('Contract discovery offset cannot be negative.')
    terms = tuple(term.strip() for term in request.terms if term.strip())
    has_exact = any((
        request.rest_http_method,
        request.rest_path,
        request.rest_consumes,
        request.rest_produces,
        request.rest_params,
        request.rest_headers,
    ))
    if has_exact and terms:
        raise ValueError('Use either lexical search terms or exact REST filters, not both.')
    if request.rest_path is not None:
        _normalize_rest_path(request.rest_path)
    if not database_path.is_file():
        return ContractDiscoveryResult(
            'index_missing', (), terms, terms,
            (_unresolved('No local Repository Index exists. Run changescope index first.'),),
            None, 0, request.limit, request.offset, False, None,
        )

    _refresh_index_if_needed(root)
    connection = sqlite3.connect(database_path)
    try:
        snapshot = _read_index_snapshot(connection, root)
        rows = connection.execute(
            '''SELECT kind, subject, target, value, path, start_line, end_line, flavor
               FROM quarkus_rest_facts WHERE kind = 'rest_endpoint'
               ORDER BY path, start_line, subject'''
        ).fetchall()
        app_paths = tuple(dict.fromkeys(
            row[0] for row in connection.execute(
                '''SELECT target FROM quarkus_rest_facts
                   WHERE kind = 'rest_application' AND target IS NOT NULL
                   ORDER BY path, start_line'''
            ).fetchall()
        ))
        class_rows = connection.execute(
            '''SELECT subject, target, value, path, start_line, end_line, flavor
               FROM quarkus_rest_facts WHERE kind = 'rest_resource'
               ORDER BY path, start_line, subject'''
        ).fetchall()
        class_facts = {row[0]: row for row in class_rows}
        selected: list[tuple[tuple, RESTChangeTarget, tuple[str, ...]]] = []
        for row in rows:
            meta = _rest_json_object(row[3])
            method = str(meta.get('http_method') or (row[2] or 'GET').split(' ', 1)[0]).upper()
            subject_class = row[1].rsplit('#', 1)[0]
            class_row = class_facts.get(subject_class)
            class_path = class_row[1] if class_row and class_row[1] else ''
            if not class_path and class_row and class_row[2]:
                class_meta = _rest_json_object(class_row[2])
                class_path = class_meta.get('path', '') or ''
            method_path = meta.get('method_path', '') or ''
            target = RESTChangeTarget(
                method,
                _normalize_rest_path(*app_paths, class_path, method_path),
                _rest_metadata_values(meta.get('consumes')),
                _rest_metadata_values(meta.get('produces')),
                _rest_parameter_keys(meta.get('parameters')),
                _rest_header_keys(meta.get('parameters')),
            )
            if request.rest_http_method and target.http_method != request.rest_http_method.strip().upper():
                continue
            if request.rest_path and target.path != _normalize_rest_path(request.rest_path):
                continue
            if request.rest_consumes and not set(_string_tuple(request.rest_consumes)).issubset(target.consumes):
                continue
            if request.rest_produces and not set(_string_tuple(request.rest_produces)).issubset(target.produces):
                continue
            if request.rest_params and not set(_string_tuple(request.rest_params)).issubset(target.params):
                continue
            if request.rest_headers and not set(_string_tuple(request.rest_headers)).issubset(target.headers):
                continue
            reasons = _rest_discovery_match_reasons(
                row, target, class_row, app_paths, terms,
            ) if terms else ()
            if terms and not reasons:
                continue
            if has_exact:
                reasons = _rest_exact_match_reasons(target, request)
            selected.append((row, target, reasons))

        unique: list[tuple[tuple, RESTChangeTarget, tuple[str, ...]]] = []
        seen: set[tuple[str, str, str, int]] = set()
        for item in selected:
            row, target, _ = item
            key = (target.contract_key, row[1], str(row[4]), row[5])
            if key not in seen:
                seen.add(key)
                unique.append(item)
        unique.sort(key=lambda item: (item[1].contract_key, str(item[0][4]), item[0][5]))
        page = unique[request.offset:request.offset + request.limit]
        candidates = tuple(
            _rest_discovery_candidate(connection, row, target, reasons, app_paths, class_facts)
            for row, target, reasons in page
        )
        unresolved_terms = tuple(
            term for term in terms
            if not any(term.lower() in ' '.join(reasons).lower() for _, _, reasons in unique)
        )
        unresolved_items = [
            _unresolved('No REST Contract Inventory item matched search term ' + repr(term) + '.')
            for term in unresolved_terms
        ]
        for candidate in candidates:
            unresolved_items.extend(candidate.unresolved_items)
        has_more = request.offset + len(page) < len(unique)
        next_offset = request.offset + len(page) if has_more else None
        if not unique:
            outcome = 'not_found'
            if has_exact:
                unresolved_items.insert(
                    0,
                    _unresolved(
                        'REST Contract Identity was not found for '
                        + (request.rest_http_method or '*') + ' '
                        + str(request.rest_path or '*') + '.'
                    ),
                )
        elif has_exact and len(unique) > 1:
            outcome = 'ambiguous'
        elif has_more:
            outcome = 'partial'
        else:
            outcome = 'resolved'
        return ContractDiscoveryResult(
            outcome, candidates, terms, unresolved_terms, tuple(unresolved_items),
            snapshot, len(unique), request.limit, request.offset, has_more, next_offset,
        )
    finally:
        connection.close()


def _rest_json_object(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _rest_metadata_values(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if item)
    if isinstance(value, str) and value:
        return (value,)
    return ()


def _rest_parameter_keys(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        str(item.get('key') or item.get('name'))
        for item in value
        if isinstance(item, dict) and (item.get('key') or item.get('name'))
    )


def _rest_header_keys(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        str(item.get('key') or item.get('name'))
        for item in value
        if (
            isinstance(item, dict)
            and item.get('role') == 'HeaderParam'
            and (item.get('key') or item.get('name'))
        )
    )


def _rest_exact_match_reasons(
    target: RESTChangeTarget,
    request: ContractDiscoveryRequest,
) -> tuple[str, ...]:
    reasons = []
    if request.rest_http_method:
        reasons.append('exact HTTP method matched: ' + target.http_method)
    if request.rest_path:
        reasons.append('exact normalized route matched: ' + target.path)
    if request.rest_consumes:
        reasons.append('exact consumes condition matched')
    if request.rest_produces:
        reasons.append('exact produces condition matched')
    if request.rest_params:
        reasons.append('exact request-parameter condition matched')
    if request.rest_headers:
        reasons.append('exact request-header condition matched')
    return tuple(reasons)


def _rest_discovery_match_reasons(
    row: tuple,
    target: RESTChangeTarget,
    class_row: tuple | None,
    app_paths: tuple[str, ...],
    terms: tuple[str, ...],
) -> tuple[str, ...]:
    meta = _rest_json_object(row[3])
    searchable = ' '.join(str(part) for part in (
        target.http_method, target.path, target.route_shape,
        target.contract_key, row[1], row[4], row[7],
        meta.get('consumes'), meta.get('produces'),
    ) if part)
    if class_row:
        searchable += ' ' + ' '.join(str(part) for part in class_row[:4] if part)
    searchable += ' ' + ' '.join(app_paths)
    reasons = []
    for term in terms:
        if term.lower() not in searchable.lower():
            continue
        lower = term.lower()
        if lower in target.path.lower():
            reason = 'term ' + repr(term) + ' matched REST route ' + repr(target.path)
        elif lower in target.http_method.lower():
            reason = 'term ' + repr(term) + ' matched HTTP method ' + target.http_method
        elif lower in row[1].lower():
            reason = 'term ' + repr(term) + ' matched Java handler ' + row[1]
        else:
            reason = 'term ' + repr(term) + ' matched REST contract metadata'
        reasons.append(reason)
    return tuple(dict.fromkeys(reasons))


def _rest_discovery_candidate(
    connection: sqlite3.Connection,
    row: tuple,
    target: RESTChangeTarget,
    match_reasons: tuple[str, ...],
    app_paths: tuple[str, ...],
    class_facts: dict[str, tuple],
) -> RESTContractCandidate:
    subject = row[1]
    subject_class, _, method_name = subject.partition('#')
    class_row = class_facts.get(subject_class)
    class_path_value = class_row[1] if class_row else ''
    method_meta = _rest_json_object(row[3])
    rest_flavor = row[7] if row[7] and row[7] != 'unknown' else 'jaxrs'
    method_path = method_meta.get('method_path', '') or ''
    entry_rows = connection.execute(
        '''SELECT kind, qualified_name, signature, path, start_line, end_line
           FROM java_declarations WHERE signature LIKE ? OR qualified_name = ?
           ORDER BY path, start_line''',
        (subject + '(%', subject),
    ).fetchall()
    if class_row:
        class_meta = _rest_json_object(class_row[2])
        if class_meta.get('kind') == 'interface':
            implementation_classes = []
            for candidate_class, candidate_row in class_facts.items():
                candidate_meta = _rest_json_object(candidate_row[2])
                interfaces = candidate_meta.get('implements', [])
                if subject_class in interfaces or subject_class.rsplit('.', 1)[-1] in interfaces:
                    implementation_classes.append(candidate_class)
            implementation_rows = []
            for implementation_class in implementation_classes:
                implementation_rows.extend(connection.execute(
                    '''SELECT kind, qualified_name, signature, path, start_line, end_line
                       FROM java_declarations WHERE qualified_name LIKE ?
                       AND name = ? ORDER BY path, start_line''',
                    (implementation_class + '#%', method_name),
                ).fetchall())
            if implementation_rows:
                entry_rows = implementation_rows

    entry_points = tuple(
        ImpactTarget(
            decl[2], Path(decl[3]), decl[4], decl[5],
            _evidence_handle('declaration', Path(decl[3]), decl[4], decl[5]),
        )
        for decl in entry_rows
    )
    endpoint_handle = _evidence_handle('quarkus_rest', Path(row[4]), row[5], row[6])
    evidence_handles = [endpoint_handle]
    if class_row:
        evidence_handles.append(_evidence_handle('quarkus_rest', Path(class_row[3]), class_row[4], class_row[5]))
    application_rows = connection.execute(
        '''SELECT path, start_line, end_line FROM quarkus_rest_facts
           WHERE kind = 'rest_application' ORDER BY path, start_line'''
    ).fetchall()
    evidence_handles.extend(
        _evidence_handle('quarkus_rest', Path(path), start, end)
        for path, start, end in application_rows
    )
    evidence_handles.extend(entry.evidence_handle for entry in entry_points)
    if not entry_points:
        source_resolution = 'unresolved'
        unresolved = (_unresolved(
            'REST operation ' + target.signature + ' has no repository-local implementation evidence.',
            Path(row[4]), row[5], row[6], 'quarkus_rest',
        ),)
    elif len(entry_points) > 1:
        source_resolution = 'ambiguous'
        unresolved = (_unresolved(
            'REST operation ' + target.signature + ' has multiple repository-local source entry points.',
            Path(row[4]), row[5], row[6], 'quarkus_rest',
        ),)
    else:
        source_resolution = 'resolved'
        unresolved = ()
    provenance = RESTContractProvenance(
        tuple(app_paths),
        (class_row[1],) if class_row and class_row[1] else (),
        method_path,
        target.http_method,
        target.consumes,
        target.produces,
        target.headers,
        target.route_shape or '/',
        (rest_flavor,),
        tuple(dict.fromkeys(evidence_handles)),
    )
    return RESTContractCandidate(
        target.contract_key, target, match_reasons, source_resolution,
        entry_points, tuple(dict.fromkeys(evidence_handles)), provenance, unresolved,
    )


def _merge_contract_discovery(
    soap: ContractDiscoveryResult,
    rest: ContractDiscoveryResult,
    request: ContractDiscoveryRequest,
) -> ContractDiscoveryResult:
    if soap.outcome == 'index_missing' and rest.outcome == 'index_missing':
        return ContractDiscoveryResult(
            'index_missing', (), request.terms, request.terms,
            soap.unresolved_items or rest.unresolved_items, None, 0,
            request.limit, request.offset, False, None,
        )
    candidates = tuple(sorted(
        (*soap.candidates, *rest.candidates),
        key=lambda candidate: (candidate.contract_key, candidate.target.signature),
    ))
    page = candidates[request.offset:request.offset + request.limit]
    unresolved_terms = tuple(
        term for term in request.terms
        if not any(term.lower() in ' '.join(candidate.match_reasons).lower() for candidate in candidates)
    )
    unresolved_items = [
        _unresolved('No Contract Inventory item matched search term ' + repr(term) + '.')
        for term in unresolved_terms
    ]
    for candidate in page:
        unresolved_items.extend(candidate.unresolved_items)
    has_more = request.offset + len(page) < len(candidates)
    next_offset = request.offset + len(page) if has_more else None
    outcome = 'partial' if has_more else 'resolved'
    if not candidates:
        outcome = 'not_found'
    return ContractDiscoveryResult(
        outcome, page, request.terms, unresolved_terms, tuple(unresolved_items),
        soap.snapshot or rest.snapshot, len(candidates), request.limit,
        request.offset, has_more, next_offset,
    )

def _discover_soap_contracts(request: ContractDiscoveryRequest) -> ContractDiscoveryResult:
    root = request.repository_root.resolve()
    database_path = root / ".changescope" / "index.sqlite"
    if request.limit < 1 or request.limit > 100:
        raise ValueError("Contract discovery limit must be between 1 and 100.")
    if request.offset < 0:
        raise ValueError("Contract discovery offset cannot be negative.")
    terms = tuple(term.strip() for term in request.terms if term.strip())
    exact_values = (request.soap_wsdl, request.soap_port_type, request.soap_operation)
    has_exact = any(value is not None for value in exact_values)
    if has_exact and not (request.soap_port_type and request.soap_operation):
        raise ValueError("SOAP discovery filters require both port_type and operation.")
    if has_exact and terms:
        raise ValueError("Use either lexical search terms or exact SOAP filters, not both.")
    if request.soap_wsdl is not None:
        _validate_relative_path(request.soap_wsdl)

    if not database_path.is_file():
        return ContractDiscoveryResult(
            "index_missing",
            (),
            terms,
            terms,
            (_unresolved("No local Repository Index exists. Run changescope index first."),),
            None,
            0,
            request.limit,
            request.offset,
            False,
            None,
        )

    _refresh_index_if_needed(root)
    connection = sqlite3.connect(database_path)
    try:
        snapshot = _read_index_snapshot(connection, root)
        operation_rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line, namespace
            FROM soap_facts WHERE kind = 'operation'
            ORDER BY target, subject, path, start_line"""
        ).fetchall()
        selected_rows: list[tuple] = []
        reasons_by_identity: dict[tuple[str, str, str], tuple[str, ...]] = {}
        for row in operation_rows:
            _, operation, port_type, _, path_value, _, _, _ = row
            path = Path(path_value)
            if request.soap_wsdl is not None and path.as_posix() != request.soap_wsdl.as_posix():
                continue
            if has_exact:
                if operation != request.soap_operation:
                    continue
                if not _matches_port_type(port_type or "", request.soap_port_type or ""):
                    continue
                selected_rows.append(row)
                reasons_by_identity[(port_type, operation, path.as_posix())] = (
                    f"exact PortType QName matched: {port_type}",
                    f"exact operation matched: {operation}",
                )
                continue

            if terms:
                reasons = _soap_discovery_match_reasons(connection, row, terms)
                if not reasons:
                    continue
                selected_rows.append(row)
                reasons_by_identity[(port_type, operation, path.as_posix())] = reasons
            else:
                selected_rows.append(row)
                reasons_by_identity[(port_type, operation, path.as_posix())] = ()

        unique_rows: list[tuple] = []
        seen_rows: set[tuple[str, str, str]] = set()
        for row in selected_rows:
            identity = (row[2], row[1], Path(row[4]).as_posix())
            if identity in seen_rows:
                continue
            seen_rows.add(identity)
            unique_rows.append(row)

        unresolved_terms = tuple(
            term for term in terms
            if not any(term.lower() in " ".join(reasons).lower() for reasons in reasons_by_identity.values())
        )
        total_count = len(unique_rows)
        page_rows = unique_rows[request.offset:request.offset + request.limit]
        candidates = tuple(
            _soap_discovery_candidate(
                connection,
                row,
                reasons_by_identity[(row[2], row[1], Path(row[4]).as_posix())],
            )
            for row in page_rows
        )
        has_more = request.offset + len(page_rows) < total_count
        next_offset = request.offset + len(page_rows) if has_more else None
        unresolved_items = [
            _unresolved(
                f"No SOAP Contract Inventory item matched search term '{term}'."
            )
            for term in unresolved_terms
        ]
        for candidate in candidates:
            unresolved_items.extend(candidate.unresolved_items)

        if not unique_rows:
            outcome = "not_found"
            if has_exact:
                unresolved_items.insert(
                    0,
                    _unresolved(
                        f"SOAP Contract Identity was not found for PortType '{request.soap_port_type}' and operation '{request.soap_operation}'."
                    ),
                )
        elif has_exact and len(unique_rows) > 1:
            outcome = "ambiguous"
        elif has_more:
            outcome = "partial"
        else:
            outcome = "resolved"

        return ContractDiscoveryResult(
            outcome,
            candidates,
            terms,
            unresolved_terms,
            tuple(unresolved_items),
            snapshot,
            total_count,
            request.limit,
            request.offset,
            has_more,
            next_offset,
        )
    finally:
        connection.close()


def _soap_discovery_match_reasons(
    connection: sqlite3.Connection,
    operation_row: tuple,
    terms: tuple[str, ...],
) -> tuple[str, ...]:
    _, operation, port_type, value, path, _, _, namespace = operation_row
    related_rows = connection.execute(
        """SELECT kind, subject, target, value FROM soap_facts
        WHERE path = ? AND kind IN ('port_type', 'binding', 'binding_operation', 'service', 'port',
                                   'message', 'operation_input', 'operation_output', 'operation_fault')""",
        (path,),
    ).fetchall()
    searchable = " ".join(
        str(part)
        for part in (operation, port_type, value, namespace, path)
        if part
    )
    searchable += " " + " ".join(
        str(part)
        for row in related_rows
        for part in row
        if part
    )
    reasons: list[str] = []
    for term in terms:
        if term.lower() not in searchable.lower():
            continue
        lower_term = term.lower()
        if lower_term in str(operation).lower():
            reason = f"term '{term}' matched SOAP operation '{operation}'"
        elif lower_term in str(port_type).lower():
            reason = f"term '{term}' matched SOAP PortType QName '{port_type}'"
        elif lower_term in str(path).lower():
            reason = f"term '{term}' matched WSDL provenance '{path}'"
        else:
            reason = f"term '{term}' matched SOAP contract metadata"
        reasons.append(reason)
    return tuple(dict.fromkeys(reasons))


def _soap_discovery_candidate(
    connection: sqlite3.Connection,
    operation_row: tuple,
    match_reasons: tuple[str, ...],
) -> SOAPContractCandidate:
    _, operation, port_type, _, wsdl_path_value, start_line, end_line, namespace = operation_row
    wsdl_path = Path(wsdl_path_value)
    operation_handle = f"soap_wsdl:{wsdl_path.as_posix()}:{start_line}-{end_line}"
    bindings_rows = connection.execute(
        """SELECT subject, target, path, start_line, end_line
        FROM soap_facts WHERE kind = 'binding' AND target = ? ORDER BY subject, path""",
        (port_type,),
    ).fetchall()
    binding_names = tuple(dict.fromkeys(row[0] for row in bindings_rows))
    service_rows = connection.execute(
        """SELECT subject, target, value, path, start_line, end_line
        FROM soap_facts WHERE kind = 'port' ORDER BY subject, value, path"""
    ).fetchall()
    service_names: list[str] = []
    port_names: list[str] = []
    addresses: list[str] = []
    provenance_handles: list[str] = [operation_handle]
    for binding in bindings_rows:
        provenance_handles.append(f"soap_wsdl:{Path(binding[2]).as_posix()}:{binding[3]}-{binding[4]}")
    for service, binding, port_value, path_value, fact_start, fact_end in service_rows:
        if binding not in binding_names:
            continue
        service_names.append(service)
        port_name, _, address = (port_value or "").partition("|")
        if port_name:
            port_names.append(port_name)
        if address:
            addresses.append(address)
        provenance_handles.append(f"soap_wsdl:{Path(path_value).as_posix()}:{fact_start}-{fact_end}")

    message_rows = connection.execute(
        """SELECT kind, value, path, start_line, end_line
        FROM soap_facts WHERE subject = ? AND target = ?
          AND kind IN ('operation_input', 'operation_output', 'operation_fault')
        ORDER BY kind, path""",
        (operation, port_type),
    ).fetchall()
    input_messages = tuple(dict.fromkeys(row[1] for row in message_rows if row[0] == "operation_input" and row[1]))
    output_messages = tuple(dict.fromkeys(row[1] for row in message_rows if row[0] == "operation_output" and row[1]))
    fault_messages = tuple(dict.fromkeys(row[1] for row in message_rows if row[0] == "operation_fault" and row[1]))
    for _, _, path_value, fact_start, fact_end in message_rows:
        provenance_handles.append(f"soap_wsdl:{Path(path_value).as_posix()}:{fact_start}-{fact_end}")

    all_method_rows = connection.execute(
        """SELECT subject, path, start_line, end_line, namespace
        FROM soap_facts WHERE kind = 'java_method' AND target = ?
        ORDER BY subject, path, start_line""",
        (operation,),
    ).fetchall()
    endpoint_subjects = {
        row[0]
        for row in connection.execute(
            "SELECT subject FROM soap_facts WHERE kind = 'java_endpoint'"
        ).fetchall()
    }
    concrete_endpoint_subjects = {
        row[0]
        for row in connection.execute(
            "SELECT qualified_name FROM java_declarations WHERE kind = 'class'"
        ).fetchall()
        if row[0] in endpoint_subjects
    }
    method_rows = [
        row for row in all_method_rows
        if row[0].rsplit('#', 1)[0] in concrete_endpoint_subjects
        and (not namespace or not row[4] or row[4] == namespace)
    ] or [
        row for row in all_method_rows
        if not namespace or not row[4] or row[4] == namespace
    ]
    entry_points: list[ImpactTarget] = []
    seen_entries: set[tuple[str, str, int, int]] = set()
    for subject, method_path, method_start, method_end, _method_namespace in method_rows:
        declaration_rows = connection.execute(
            """SELECT signature, path, start_line, end_line
            FROM java_declarations
            WHERE signature = ? OR signature LIKE ?
            ORDER BY path, start_line""",
            (subject, subject + "(%"),
        ).fetchall()
        if not declaration_rows:
            declaration_rows = ((subject + "()", method_path, method_start, method_end),)
        for signature, source_path, source_start, source_end in declaration_rows:
            entry_key = (signature, source_path, source_start, source_end)
            if entry_key in seen_entries:
                continue
            seen_entries.add(entry_key)
            entry_points.append(
                ImpactTarget(
                    signature,
                    Path(source_path),
                    source_start,
                    source_end,
                    f"declaration:{Path(source_path).as_posix()}:{source_start}-{source_end}",
                )
            )

    if not entry_points:
        source_resolution = "unresolved"
        unresolved_items = (
            _unresolved(
                f"SOAP operation '{operation}' has no repository-local implementation evidence.",
                path=wsdl_path,
                start_line=start_line,
                end_line=end_line,
                evidence_kind="soap_wsdl",
            ),
        )
    elif len(entry_points) > 1:
        source_resolution = "ambiguous"
        unresolved_items = (
            _unresolved(
                f"SOAP operation '{operation}' has {len(entry_points)} possible repository-local source entry points.",
                path=wsdl_path,
                start_line=start_line,
                end_line=end_line,
                evidence_kind="soap_wsdl",
            ),
        )
    else:
        source_resolution = "resolved"
        unresolved_items = ()

    evidence_handles = tuple(dict.fromkeys((
        *provenance_handles,
        *(entry.evidence_handle for entry in entry_points),
    )))
    provenance = SOAPContractProvenance(
        wsdl_path,
        namespace,
        tuple(dict.fromkeys(service_names)),
        tuple(dict.fromkeys(port_names)),
        binding_names,
        tuple(dict.fromkeys(addresses)),
        input_messages,
        output_messages,
        fault_messages,
        evidence_handles,
    )
    target = SOAPChangeTarget(wsdl_path, port_type, operation)
    return SOAPContractCandidate(
        target.contract_key,
        target,
        match_reasons,
        source_resolution,
        tuple(entry_points),
        evidence_handles,
        provenance,
        unresolved_items,
    )


def _impact_repository(request: ImpactRequest) -> ImpactResult:
    root = request.repository_root.resolve()
    database_path = root / ".changescope" / "index.sqlite"
    if not database_path.is_file():
        return ImpactResult(
            "index_missing", request.target or "soap_target", None, (), (), (),
            (_unresolved("No local Repository Index exists. Run `changescope index` first."),), None,
        )
    _refresh_index_if_needed(root)

    has_flat_soap_args = any(
        arg is not None
        for arg in (request.soap_wsdl, request.soap_port_type, request.soap_operation)
    )
    has_soap_args = request.soap_target is not None or has_flat_soap_args
    has_rest_args = request.rest_target is not None
    if has_rest_args and (request.target is not None or has_soap_args):
        snapshot = _read_index_snapshot(database_path, root)
        return ImpactResult(
            'invalid_target', request.rest_target.signature, None, (), (), (),
            (_unresolved('Cannot mix a REST Change Target with a Java or SOAP target.'),), snapshot,
        )
    if has_rest_args:
        return _impact_rest_repository(request, root, database_path)
    if request.target is not None and has_soap_args:
        conn = sqlite3.connect(database_path)
        try:
            snapshot = _read_index_snapshot(conn, root)
        finally:
            conn.close()
        return ImpactResult(
            "invalid_target",
            request.target,
            None,
            (),
            (),
            (),
            (_unresolved("Cannot mix Java target ('Class#method') and SOAP target arguments (--soap-wsdl, --soap-port-type, --soap-operation)."),),
            snapshot,
        )

    if request.soap_target is not None:
        if has_flat_soap_args:
            conn = sqlite3.connect(database_path)
            try:
                snapshot = _read_index_snapshot(conn, root)
            finally:
                conn.close()
            return ImpactResult(
                "invalid_target",
                request.soap_target.signature,
                None,
                (),
                (),
                (),
                (_unresolved("Cannot mix a structured SOAP Change Target with flat SOAP target arguments."),),
                snapshot,
            )
        return _impact_soap_repository(request, root, database_path)

    if has_soap_args or request.target is None:
        if not (request.soap_wsdl and request.soap_port_type and request.soap_operation):
            conn = sqlite3.connect(database_path)
            try:
                snapshot = _read_index_snapshot(conn, root)
            finally:
                conn.close()
            req_target = request.target or "soap_target"
            return ImpactResult(
                "invalid_target",
                req_target,
                None,
                (),
                (),
                (),
                (_unresolved("Must specify all SOAP target arguments (--soap-wsdl, --soap-port-type, --soap-operation)."),),
                snapshot,
            )
        return _impact_soap_repository(request, root, database_path)

    connection = sqlite3.connect(database_path)
    try:
        vb_has_table = connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'vbnet_declarations'").fetchone() is not None
        if vb_has_table:
            target_lower = request.target.lower()
            vb_decls = connection.execute(
                "SELECT signature FROM vbnet_declarations WHERE LOWER(signature) = ? OR LOWER(name) = ? OR LOWER(qualified_name) = ?",
                (target_lower, target_lower, target_lower),
            ).fetchall()
            if vb_decls:
                snapshot = _read_index_snapshot(connection, root)
                return _analyze_vbnet_impact(request, connection, snapshot)
    finally:
        connection.close()

    target_parts = request.target.split("#")
    if len(target_parts) != 2 or not all(target_parts):
        connection = sqlite3.connect(database_path)
        try:
            class_rows = connection.execute(
                """SELECT qualified_name, path, start_line, end_line
                FROM java_declarations WHERE kind IN ('class', 'interface') AND (name = ? OR qualified_name = ?)
                ORDER BY path, start_line""",
                (request.target, request.target),
            ).fetchall()
            if class_rows:
                q_name, c_path, c_sl, c_el = class_rows[0]
                cand = _impact_target(q_name, c_path, c_sl, c_el)
                snapshot = _read_index_snapshot(connection, root)
                relationships, unresolved_items = _direct_relationships(
                    database_path, cand, request.profiles, request.build_profiles, request.runtime_profiles
                )
                return ImpactResult(
                    "resolved", request.target, cand, (), relationships, (), unresolved_items, snapshot
                )
        finally:
            connection.close()
        return ImpactResult(
            "invalid_target", request.target, None, (), (), (),
            (_unresolved("Use the target form Class#method or a valid Class target."),), _read_index_snapshot(database_path, root),
        )
    class_name, method_name = target_parts
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            """SELECT qualified_name, signature, path, start_line, end_line
            FROM java_declarations WHERE kind = 'method' AND name = ?
            ORDER BY path, start_line""",
            (method_name,),
        ).fetchall()
        candidates = tuple(
            _impact_target(signature, path, start_line, end_line)
            for qualified_name, signature, path, start_line, end_line in rows
            if _matches_class_name(qualified_name, class_name)
        )
        inherited_candidates = _inherited_impact_targets(connection, class_name, method_name)
        candidates_by_signature = {candidate.signature: candidate for candidate in candidates}
        for candidate in inherited_candidates:
            candidates_by_signature.setdefault(candidate.signature, candidate)
        candidates = tuple(candidates_by_signature.values())
        snapshot = _read_index_snapshot(connection, root)
    finally:
        connection.close()
    if not candidates:
        return ImpactResult("not_found", request.target, None, (), (), (), (), snapshot)
    if len(candidates) > 1:
        return ImpactResult("ambiguous", request.target, None, candidates, (), (), (), snapshot)
    relationships, unresolved_items = _direct_relationships(
        database_path, candidates[0], request.profiles, request.build_profiles, request.runtime_profiles
    )
    assumptions = [
        "Structural analysis asserts only explicit invocation syntax tied to the resolved target.",
    ]

    connection = sqlite3.connect(database_path)
    try:
        class_qname = candidates[0].signature.split("#")[0]
        soap_class_facts = connection.execute(
            "SELECT kind, subject, target, value, path, start_line, end_line FROM soap_facts WHERE (kind IN ('java_endpoint', 'java_provider') AND subject = ?) OR (kind = 'java_method' AND subject = ?)",
            (class_qname, candidates[0].signature),
        ).fetchall()
        if soap_class_facts:
            m_simple = candidates[0].signature.split("#")[1].split("(")[0]
            op_facts = connection.execute(
                "SELECT subject, target, value, path, start_line, end_line FROM soap_facts WHERE kind = 'operation' AND subject = ?",
                (m_simple,),
            ).fetchall()
            if op_facts:
                w_op, w_pt, w_in, w_path, w_sl, w_el = op_facts[0]
                w_handle = f"soap_wsdl:{w_path}:{w_sl}-{w_el}"
                relationships = (
                    ImpactRelationship(
                        kind="soap_endpoint_implementation",
                        caller=f"{w_pt}#{w_op}",
                        path=Path(w_path),
                        start_line=w_sl,
                        end_line=w_el,
                        evidence_handle=w_handle,
                        evidence_chain=(w_handle, candidates[0].evidence_handle),
                        confidence="high",
                    ),
                ) + relationships
            else:
                assumptions.append("Code-first Java endpoint metadata specifies a derived contract; deployed WSDL remains unverified.")
                relationships = (
                    ImpactRelationship(
                        kind="soap_endpoint_implementation",
                        caller=f"derived:{candidates[0].signature}",
                        path=candidates[0].path,
                        start_line=candidates[0].start_line,
                        end_line=candidates[0].end_line,
                        evidence_handle=candidates[0].evidence_handle,
                        evidence_chain=(candidates[0].evidence_handle,),
                        confidence="medium",
                    ),
                ) + relationships
            if any(f[0] == "java_provider" for f in soap_class_facts):
                unresolved_items.append(
                    _unresolved(
                        "WebServiceProvider endpoint payload-to-operation dispatch remains unresolved without WSDL contract evidence.",
                        path=candidates[0].path,
                        start_line=candidates[0].start_line,
                        end_line=candidates[0].end_line,
                        evidence_kind="declaration",
                    )
                )
    finally:
        connection.close()
    if request.profiles:
        assumptions.append(
            "Active Spring profiles: " + ", ".join(request.profiles) + "."
        )
    else:
        assumptions.append(
            "No Spring profile was selected; profile-specific configuration remains conditional."
        )

    if request.build_profiles or request.runtime_profiles:
        parts = []
        if request.build_profiles:
            parts.append("Quarkus build profiles: " + ", ".join(request.build_profiles))
        if request.runtime_profiles:
            parts.append("Quarkus runtime profiles: " + ", ".join(request.runtime_profiles))
        assumptions.append(". ".join(parts) + ".")
    else:
        conn = sqlite3.connect(database_path)
        try:
            config_count = conn.execute("SELECT COUNT(*) FROM quarkus_config_facts").fetchone()[0]
        except sqlite3.OperationalError:
            config_count = 0
        finally:
            conn.close()
        if config_count > 0:
            assumptions.append("No Quarkus profile was selected; profile-specific configuration remains conditional.")

    quarkus_assumptions, quarkus_unresolved = _quarkus_build_impact_evidence(
        database_path, candidates[0].path
    )
    assumptions.extend(quarkus_assumptions)
    unresolved_items = (*unresolved_items, *quarkus_unresolved)
    return ImpactResult(
        "resolved",
        request.target,
        candidates[0],
        (),
        relationships,
        tuple(assumptions),
        unresolved_items,
        snapshot,
    )


def _matches_class_name(qualified_name: str, class_name: str) -> bool:
    owner = qualified_name.rsplit("#", maxsplit=1)[0]
    return owner == class_name or owner.rsplit(".", maxsplit=1)[-1] == class_name


def _inherited_impact_targets(
    connection: sqlite3.Connection, class_name: str, method_name: str
) -> tuple[ImpactTarget, ...]:
    fact_rows = connection.execute(
        "SELECT kind, subject, target FROM ejb_facts"
    ).fetchall()
    session_subjects = {
        subject
        for kind, subject, target in fact_rows
        if kind == "session_bean"
    }
    relevant_types = {
        subject
        for kind, subject, target in fact_rows
        if kind == "interface_view"
    }
    relevant_types.update(
        target
        for kind, subject, target in fact_rows
        if kind == "type_implements" and subject in session_subjects and target
    )
    inheritance_rows = [
        (subject, target)
        for kind, subject, target in fact_rows
        if kind == "type_extends" and target is not None
    ]
    while True:
        inherited_types = {
            target for subject, target in inheritance_rows if subject in relevant_types
        }
        new_types = inherited_types - relevant_types
        if not new_types:
            break
        relevant_types.update(new_types)
    parents_by_child: dict[str, list[str]] = {}
    for subject, target in inheritance_rows:
        if subject in relevant_types and target in relevant_types:
            parents_by_child.setdefault(subject, []).append(target)
    roots = [subject for subject in parents_by_child if _matches_class_name(subject, class_name)]
    if not roots:
        return ()
    method_rows = connection.execute(
        """SELECT qualified_name, signature, path, start_line, end_line
        FROM java_declarations WHERE kind = 'method' AND name = ?""",
        (method_name,),
    ).fetchall()
    queue: list[tuple[str, str, frozenset[str]]] = [
        (root, root, frozenset({root})) for root in roots
    ]
    aliases: list[ImpactTarget] = []
    seen: set[tuple[str, str]] = set()
    while queue:
        root, current, visited = queue.pop(0)
        for parent in parents_by_child.get(current, ()):
            if parent in visited:
                continue
            queue.append((root, parent, visited | {parent}))
            for qualified_name, signature, path, start_line, end_line in method_rows:
                if not qualified_name.startswith(parent + "#"):
                    continue
                method_part = signature.split("#", maxsplit=1)[1]
                key = (root, method_part)
                if key in seen:
                    continue
                seen.add(key)
                aliases.append(
                    _impact_target(
                        f"{root}#{method_part}",
                        path,
                        start_line,
                        end_line,
                    )
                )
    return tuple(aliases)


def _impact_target(signature: str, path: str, start_line: int, end_line: int) -> ImpactTarget:
    source_path = Path(path)
    return ImpactTarget(
        signature,
        source_path,
        start_line,
        end_line,
        _evidence_handle("declaration", source_path, start_line, end_line),
    )


def _evidence_handle(kind: str, path: Path, start_line: int, end_line: int) -> str:
    return f"{kind}:{path.as_posix()}:{start_line}-{end_line}"


def _spring_evidence_handle(path: Path, start_line: int, end_line: int) -> str:
    return _evidence_handle("spring", path, start_line, end_line)


def _ejb_evidence_handle(path: Path, start_line: int, end_line: int) -> str:
    return _evidence_handle("ejb", path, start_line, end_line)


def _quarkus_build_evidence_handle(path: Path, start_line: int, end_line: int) -> str:
    return _evidence_handle("quarkus_build", path, start_line, end_line)


def _unresolved(
    message: str,
    path: Path | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    evidence_kind: str = "invocation",
) -> UnresolvedItem:
    evidence_handle = (
        _evidence_handle(evidence_kind, path, start_line, end_line)
        if path is not None and start_line is not None and end_line is not None
        else None
    )
    return UnresolvedItem(message, path, start_line, end_line, evidence_handle)


def _direct_relationships(
    database_path: Path,
    target: ImpactTarget,
    profiles: tuple[str, ...] = (),
    build_profiles: tuple[str, ...] = (),
    runtime_profiles: tuple[str, ...] = (),
) -> tuple[tuple[ImpactRelationship, ...], tuple[UnresolvedItem, ...]]:
    if "#" in target.signature:
        owner, method_with_parameters = target.signature.split("#", maxsplit=1)
        method_name, _, _ = method_with_parameters.partition("(")
    else:
        owner = target.signature
        method_name = ""
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            """SELECT receiver, caller, path, start_line, end_line, is_test, argument_count
            FROM java_invocations WHERE name = ? ORDER BY path, start_line""",
            (method_name,),
        ).fetchall()
        callee_rows = connection.execute(
            """SELECT name, receiver, path, start_line, end_line
            FROM java_invocations WHERE caller = ? ORDER BY path, start_line""",
            (f"{owner}#{method_name}",),
        ).fetchall()
        declarations = connection.execute(
            """SELECT qualified_name, name, signature, is_private FROM java_declarations
            WHERE kind = 'method'"""
        ).fetchall()
        ejb_facts = tuple(
            EJBFact(kind, subject, fact_target, value, Path(path), start_line, end_line)
            for kind, subject, fact_target, value, path, start_line, end_line in connection.execute(
                """SELECT kind, subject, target, value, path, start_line, end_line
                FROM ejb_facts"""
            ).fetchall()
        )
    finally:
        connection.close()
    relationships: list[ImpactRelationship] = []
    unresolved_items = [
        _unresolved(
            "Structural analysis does not resolve receiver types, overload dispatch, inheritance, reflection, or unsupported framework dispatch."
        )
    ]
    for receiver, caller, path, start_line, end_line, is_test, _argument_count in rows:
        source_path = Path(path)
        relationship_kind = None
        confidence = ""
        if receiver and (receiver == owner or _is_direct_construction(receiver, owner)):
            relationship_kind = "direct_test" if is_test else "direct_caller"
            confidence = "high"
        elif receiver is None and caller and caller.rsplit("#", maxsplit=1)[0] == owner:
            relationship_kind = "possible_caller"
            confidence = "medium"
        else:
            if caller and any(
                _ejb_injection_receiver_matches(injection, receiver, caller)
                and _ejb_injection_targets_owner(injection, owner, ejb_facts)
                for injection in ejb_facts
                if injection.kind == "ejb_injection"
            ):
                continue
            unresolved_items.append(
                _unresolved(
                    f"Invocation named {method_name} was not asserted because its receiver type is unresolved.",
                    source_path,
                    start_line,
                    end_line,
                )
            )
            continue
        relationships.append(
            ImpactRelationship(
                relationship_kind,
                caller or "<initializer>",
                source_path,
                start_line,
                end_line,
                _evidence_handle("invocation", source_path, start_line, end_line),
                confidence,
            )
        )
    declarations_by_name: dict[str, list[tuple[str, str, bool]]] = {}
    for declaration_owner, declaration_name, declaration_signature, is_private in declarations:
        declarations_by_name.setdefault(declaration_name, []).append(
            (declaration_owner.rsplit("#", maxsplit=1)[0], declaration_signature, bool(is_private))
        )
    for callee_name, receiver, path, start_line, end_line in callee_rows:
        source_path = Path(path)
        candidates = declarations_by_name.get(callee_name, [])
        if receiver in {None, "this"}:
            candidates = [
                candidate for candidate in candidates if candidate[0] == owner and candidate[2]
            ]
        else:
            candidates = [
                candidate
                for candidate in candidates
                if receiver == candidate[0] or _is_direct_construction(receiver, candidate[0])
            ]
        if len(candidates) == 1:
            relationships.append(
                ImpactRelationship(
                    "direct_callee",
                    candidates[0][1].split("(", maxsplit=1)[0],
                    source_path,
                    start_line,
                    end_line,
                    _evidence_handle("invocation", source_path, start_line, end_line),
                    "high",
                )
            )
            continue
        unresolved_items.append(
            _unresolved(
                f"Invocation named {callee_name} was not asserted as a direct callee because its target is unresolved.",
                source_path,
                start_line,
                end_line,
            )
        )
    spring_relationships, spring_unresolved = _spring_relationships(
        connection_data_path=database_path,
        owner=owner,
        profiles=profiles,
    )
    relationships.extend(spring_relationships)
    unresolved_items.extend(spring_unresolved)
    ejb_relationships, ejb_unresolved = _ejb_relationships(
        connection_data_path=database_path,
        target=target,
    )
    relationships.extend(ejb_relationships)
    unresolved_items.extend(ejb_unresolved)
    qc_relationships, qc_unresolved = _quarkus_config_relationships(
        connection_data_path=database_path,
        owner=owner,
        build_profiles=build_profiles,
        runtime_profiles=runtime_profiles,
    )
    relationships.extend(qc_relationships)
    unresolved_items.extend(qc_unresolved)
    cdi_relationships, cdi_unresolved = _quarkus_cdi_relationships(
        connection_data_path=database_path,
        owner=owner,
        target=target,
        build_profiles=build_profiles,
    )
    relationships.extend(cdi_relationships)
    unresolved_items.extend(cdi_unresolved)
    rest_relationships, rest_unresolved = _quarkus_rest_relationships(
        connection_data_path=database_path,
        owner=owner,
        target=target,
    )
    relationships.extend(rest_relationships)
    unresolved_items.extend(rest_unresolved)
    route_relationships, route_unresolved = _quarkus_route_relationships(
        connection_data_path=database_path,
        owner=owner,
        target=target,
    )
    relationships.extend(route_relationships)
    unresolved_items.extend(route_unresolved)
    sec_relationships, sec_unresolved = _quarkus_security_relationships(
        connection_data_path=database_path,
        owner=owner,
        target=target,
        build_profiles=build_profiles,
        runtime_profiles=runtime_profiles,
    )
    relationships.extend(sec_relationships)
    unresolved_items.extend(sec_unresolved)
    test_relationships, test_unresolved = _quarkus_test_relationships(
        connection_data_path=database_path,
        owner=owner,
        target=target,
    )
    relationships.extend(test_relationships)
    unresolved_items.extend(test_unresolved)
    nat_relationships, nat_unresolved = _quarkus_native_relationships(
        connection_data_path=database_path,
        owner=owner,
        target=target,
        build_profiles=build_profiles,
        runtime_profiles=runtime_profiles,
    )
    relationships.extend(nat_relationships)
    unresolved_items.extend(nat_unresolved)
    b_relationships, b_unresolved = _quarkus_boundary_relationships(
        connection_data_path=database_path,
        owner=owner,
        target=target,
    )
    relationships.extend(b_relationships)
    unresolved_items.extend(b_unresolved)
    return tuple(relationships), tuple(unresolved_items)


def _is_direct_construction(receiver: str, owner: str) -> bool:
    return bool(re.fullmatch(rf"new\s+{re.escape(owner)}\s*\([^)]*\)", receiver))


def _ejb_relationships(
    connection_data_path: Path, target: ImpactTarget
) -> tuple[tuple[ImpactRelationship, ...], tuple[UnresolvedItem, ...]]:
    if "#" in target.signature:
        owner, method_with_parameters = target.signature.split("#", maxsplit=1)
        method_name, _, parameters = method_with_parameters.partition("(")
        parameters = parameters.removesuffix(")")
    else:
        owner = target.signature
        method_name = ""
        parameters = ""
    connection = sqlite3.connect(connection_data_path)
    try:
        facts = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line
            FROM ejb_facts ORDER BY path, start_line, kind, subject"""
        ).fetchall()
        declarations = connection.execute(
            """SELECT kind, qualified_name, signature, path, start_line, end_line
            FROM java_declarations WHERE kind IN ('class', 'interface', 'method')"""
        ).fetchall()
        invocations = connection.execute(
            """SELECT name, receiver, caller, path, start_line, end_line, argument_count
            FROM java_invocations WHERE name = ? ORDER BY path, start_line""",
            (method_name,),
        ).fetchall()
        test_source_roots_row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'test_source_roots'"
        ).fetchone()
    finally:
        connection.close()
    if not facts:
        return (), ()
    test_source_roots = tuple(
        Path(value)
        for value in (test_source_roots_row[0].splitlines() if test_source_roots_row else ())
        if value
    )
    parsed_facts = tuple(
        EJBFact(kind, subject, fact_target, value, Path(path), start_line, end_line)
        for kind, subject, fact_target, value, path, start_line, end_line in facts
    )
    conflicting_views = _ejb_conflicting_subjects(parsed_facts, "interface_view")
    conflicting_sessions = _ejb_conflicting_subjects(parsed_facts, "session_bean")
    views = {
        fact.subject: fact
        for fact in parsed_facts
        if fact.kind == "interface_view" and fact.subject not in conflicting_views
    }
    sessions = {
        fact.subject: fact
        for fact in parsed_facts
        if fact.kind == "session_bean" and fact.subject not in conflicting_sessions
    }
    implements = [
        fact for fact in parsed_facts if fact.kind == "type_implements" and fact.target
    ]
    extends = [
        fact for fact in parsed_facts if fact.kind == "type_extends" and fact.target
    ]
    bean_views = [fact for fact in parsed_facts if fact.kind == "bean_view"]
    mappings: list[tuple[str, str, str, tuple[EJBFact, ...]]] = []
    for implementation in implements:
        if implementation.subject not in sessions:
            continue
        direct_view = views.get(implementation.target or "")
        implementation_facts = (implementation, sessions[implementation.subject])
        if direct_view is not None:
            mappings.append(
                (
                    implementation.target,
                    implementation.subject,
                    direct_view.value or "local",
                    (direct_view, *implementation_facts),
                )
            )
            for ancestor, inheritance_facts in _ejb_interface_ancestors(
                implementation.target, extends
            ):
                mappings.append(
                    (
                        ancestor,
                        implementation.subject,
                        direct_view.value or "local",
                        (direct_view, *inheritance_facts, *implementation_facts),
                    )
                )
        else:
            for ancestor, inheritance_facts in _ejb_interface_ancestors(
                implementation.target, extends
            ):
                ancestor_view = views.get(ancestor)
                if ancestor_view is None:
                    continue
                mappings.append(
                    (
                        ancestor,
                        implementation.subject,
                        ancestor_view.value or "local",
                        (ancestor_view, *inheritance_facts, *implementation_facts),
                    )
                )
                mappings.append(
                    (
                        implementation.target,
                        implementation.subject,
                        ancestor_view.value or "local",
                        (ancestor_view, *inheritance_facts, *implementation_facts),
                    )
                )
        for bean_view in bean_views:
            if bean_view.subject != implementation.subject:
                continue
            if bean_view.target is None or bean_view.target == implementation.target:
                view = bean_view.value or "local"
                mappings.append(
                    (
                        implementation.target,
                        implementation.subject,
                        view,
                        (bean_view, *implementation_facts),
                    )
                )
                for ancestor, inheritance_facts in _ejb_interface_ancestors(
                    implementation.target, extends
                ):
                    mappings.append(
                        (
                            ancestor,
                            implementation.subject,
                            view,
                            (bean_view, *inheritance_facts, *implementation_facts),
                        )
                    )
    unique_mappings = _unique_ejb_mappings(mappings)
    relevant_mappings = tuple(
        mapping
        for mapping in unique_mappings
        if owner in {mapping[0], mapping[1]}
        or _ejb_interface_related(mapping[0], owner, extends)
    )
    relevant_types = {owner}
    relevant_types.update(mapping[0] for mapping in relevant_mappings)
    relevant_types.update(mapping[1] for mapping in relevant_mappings)
    relevant_types.update(
        fact.target
        for fact in parsed_facts
        if fact.kind in {"type_implements", "type_extends"}
        and fact.subject == owner
        and fact.target
    )
    relevant_types.update(
        fact.subject
        for fact in parsed_facts
        if fact.kind == "type_implements" and fact.target == owner
    )
    declaration_rows = [
        (qualified_name, signature, Path(path), start_line, end_line)
        for kind, qualified_name, signature, path, start_line, end_line in declarations
        if kind == "method"
    ]
    relationships: list[ImpactRelationship] = []
    unresolved: list[UnresolvedItem] = []
    seen_business_relationships: set[tuple[str, tuple[str, ...], str | None]] = set()
    resolved_mapping_keys: set[tuple[str, str, str]] = set()
    resolved_mapping_facts: dict[tuple[str, str, str], tuple[EJBFact, ...]] = {}
    for fact in parsed_facts:
        if fact.kind != "ejb_unresolved":
            continue
        subject_owner = fact.subject.rsplit("#", maxsplit=1)[0]
        if fact.target in relevant_types or subject_owner in relevant_types:
            unresolved.append(
                _unresolved(
                    fact.value or "EJB behavior remains unresolved.",
                    fact.path,
                    fact.start_line,
                    fact.end_line,
                    evidence_kind="ejb",
                )
            )
    for interface_name, bean_name, view, mapping_facts in unique_mappings:
        if owner not in {interface_name, bean_name}:
            continue
        counterpart = bean_name if owner == interface_name else interface_name
        counterpart_declaration = _matching_ejb_method(
            declaration_rows,
            counterpart,
            method_name,
            parameters,
            connection_data_path.parent.parent,
            target.path,
        )
        if counterpart_declaration is None and owner == bean_name:
            for ancestor, _ in _ejb_interface_ancestors(interface_name, extends):
                counterpart_declaration = _matching_ejb_method(
                    declaration_rows,
                    ancestor,
                    method_name,
                    parameters,
                    connection_data_path.parent.parent,
                    target.path,
                )
                if counterpart_declaration is not None:
                    break
        if counterpart_declaration is None:
            unresolved.append(
                _unresolved(
                    f"EJB method {owner}#{method_name} was not connected because the matching Session Bean or EJB Business Interface method is unresolved."
                )
            )
            continue
        effective_mapping_facts = mapping_facts
        target_declared = _matching_ejb_method(
            declaration_rows,
            owner,
            method_name,
            parameters,
            connection_data_path.parent.parent,
            target.path,
        )
        if owner == interface_name and target_declared is None:
            for ancestor, inheritance_facts in _ejb_interface_ancestors(owner, extends):
                if _matching_ejb_method(
                    declaration_rows,
                    ancestor,
                    method_name,
                    parameters,
                    connection_data_path.parent.parent,
                    target.path,
                ) is None:
                    continue
                existing_edges = tuple(
                    fact for fact in mapping_facts if fact.kind == "type_extends"
                )
                combined_edges = list(existing_edges)
                for edge in inheritance_facts:
                    if edge not in combined_edges:
                        combined_edges.append(edge)
                non_inheritance_facts = tuple(
                    fact for fact in mapping_facts if fact.kind != "type_extends"
                )
                effective_mapping_facts = (
                    non_inheritance_facts[0],
                    *combined_edges,
                    *non_inheritance_facts[1:],
                )
                break
        evidence_chain = tuple(
            _ejb_evidence_handle(fact.path, fact.start_line, fact.end_line)
            for fact in effective_mapping_facts
        )
        mapping_key = (interface_name, bean_name, view)
        resolved_mapping_keys.add(mapping_key)
        resolved_mapping_facts[mapping_key] = effective_mapping_facts
        primary_fact = effective_mapping_facts[0]
        relationship_key = (counterpart_declaration[1], evidence_chain, view)
        if relationship_key not in seen_business_relationships:
            relationships.append(
                ImpactRelationship(
                    "ejb_business_implementation",
                    counterpart_declaration[1],
                    primary_fact.path,
                    primary_fact.start_line,
                    primary_fact.end_line,
                    evidence_chain[0],
                    "high",
                    evidence_chain=evidence_chain,
                    business_view=view,
                )
            )
            seen_business_relationships.add(relationship_key)
        if view == "remote":
            unresolved.append(
                _unresolved(
                    f"Remote EJB Business Interface {interface_name} may have consumers outside the local Repository Index."
                )
            )
    for interface_name, bean_name, view, mapping_facts in relevant_mappings:
        mapping_key = (interface_name, bean_name, view)
        if mapping_key in resolved_mapping_keys:
            continue
        if not _ejb_interface_related(interface_name, owner, extends):
            continue
        counterpart_declaration = _matching_ejb_method(
            declaration_rows,
            bean_name,
            method_name,
            parameters,
            connection_data_path.parent.parent,
            target.path,
        )
        if counterpart_declaration is None:
            continue
        resolved_mapping_keys.add(mapping_key)
        resolved_mapping_facts[mapping_key] = mapping_facts
    injections = tuple(fact for fact in parsed_facts if fact.kind == "ejb_injection")
    view_facts = {
        fact.subject: fact
        for fact in parsed_facts
        if fact.kind == "interface_view" and fact.subject not in conflicting_views
    }
    seen_injection_points: set[tuple[str, str | None]] = set()
    for injection in injections:
        injection_key = (injection.subject, injection.target)
        if injection_key in seen_injection_points:
            continue
        seen_injection_points.add(injection_key)
        if (
            injection.path.suffix.lower() == ".xml"
            and not _ejb_descriptor_target_exists(
                connection_data_path.parent.parent,
                injection,
                declarations,
            )
        ):
            unresolved.append(
                _unresolved(
                    f"EJB descriptor injection target {injection.subject} is not present in the indexed Java source.",
                    injection.path,
                    injection.start_line,
                    injection.end_line,
                    evidence_kind="ejb",
                )
            )
            continue
        matching_mappings = tuple(
            mapping
            for mapping in relevant_mappings
            if mapping[0] == injection.target
            and (mapping[0], mapping[1], mapping[2]) in resolved_mapping_keys
        )
        if not matching_mappings and injection.target not in relevant_types:
            continue
        candidate_beans = tuple(sorted({candidate[1] for candidate in matching_mappings}))
        mapping = matching_mappings[0] if len(candidate_beans) == 1 else None
        mapping_facts = (
            resolved_mapping_facts.get((mapping[0], mapping[1], mapping[2]), mapping[3])
            if mapping is not None
            else (
                view_facts[injection.target],
            ) if injection.target in view_facts else ()
        )
        if not mapping_facts:
            unresolved.append(
                _unresolved(
                    f"EJB Injection Point {injection.subject} does not identify a source-supported EJB Business Interface.",
                    injection.path,
                    injection.start_line,
                    injection.end_line,
                    evidence_kind="ejb",
                )
            )
            continue
        injection_handle = _ejb_evidence_handle(
            injection.path, injection.start_line, injection.end_line
        )
        evidence_chain = (
            injection_handle,
            *tuple(
                _ejb_evidence_handle(fact.path, fact.start_line, fact.end_line)
                for fact in mapping_facts
            ),
        )
        view = mapping[2] if mapping is not None else (mapping_facts[0].value or "local")
        relationships.append(
            ImpactRelationship(
                "ejb_injection",
                injection.subject,
                injection.path,
                injection.start_line,
                injection.end_line,
                injection_handle,
                "high",
                evidence_chain=evidence_chain,
                business_view=view,
            )
        )
        if any(
            injection.path == test_root or injection.path.is_relative_to(test_root)
            for test_root in test_source_roots
        ):
            relationships.append(
                ImpactRelationship(
                    "ejb_test",
                    injection.subject,
                    injection.path,
                    injection.start_line,
                    injection.end_line,
                    injection_handle,
                    "medium",
                    evidence_chain=evidence_chain,
                    business_view=view,
                )
            )
        if len(candidate_beans) > 1:
            unresolved.append(
                _unresolved(
                    f"Multiple eligible Session Beans remain for EJB Injection Point {injection.subject}; container dispatch was not selected by name similarity.",
                    injection.path,
                    injection.start_line,
                    injection.end_line,
                    evidence_kind="ejb",
                )
            )
            continue
        if not candidate_beans:
            unresolved.append(
                _unresolved(
                    f"No source-supported Session Bean was found for EJB Injection Point {injection.subject}.",
                    injection.path,
                    injection.start_line,
                    injection.end_line,
                    evidence_kind="ejb",
                )
            )
            continue
        dispatch_mapping = next(
            candidate for candidate in matching_mappings if candidate[1] == candidate_beans[0]
        )
        dispatch_facts = resolved_mapping_facts.get(
            (dispatch_mapping[0], dispatch_mapping[1], dispatch_mapping[2]),
            dispatch_mapping[3],
        )
        expected_argument_count = len(_split_java_parameters(parameters))
        matched_invocations = []
        invalid_invocations = []
        for _name, receiver, caller, path, start_line, end_line, argument_count in invocations:
            if not caller or not _ejb_injection_receiver_matches(injection, receiver, caller):
                continue
            if _ejb_receiver_is_shadowed(
                connection_data_path.parent.parent,
                injection,
                Path(path),
                caller,
                receiver,
                start_line,
            ):
                invalid_invocations.append((path, start_line, end_line, argument_count, "scope"))
                continue
            if argument_count != expected_argument_count:
                invalid_invocations.append((path, start_line, end_line, argument_count, "arity"))
                continue
            matched_invocations.append((caller, path, start_line, end_line))
            invocation_handle = _evidence_handle("invocation", Path(path), start_line, end_line)
            dispatch_chain = (
                injection_handle,
                invocation_handle,
                *tuple(
                    _ejb_evidence_handle(fact.path, fact.start_line, fact.end_line)
                    for fact in dispatch_facts
                ),
            )
            relationships.append(
                ImpactRelationship(
                    "ejb_container_dispatch",
                    caller,
                    Path(path),
                    start_line,
                    end_line,
                    invocation_handle,
                    "medium",
                    evidence_chain=dispatch_chain,
                    business_view=dispatch_mapping[2],
                )
            )
        for path, start_line, end_line, argument_count, reason in invalid_invocations:
            expected = "unknown" if argument_count is None else str(expected_argument_count)
            detail = (
                "receiver scope is unresolved"
                if reason == "scope"
                else f"argument count does not match the indexed EJB method (expected {expected})"
            )
            unresolved.append(
                _unresolved(
                    f"Invocation through EJB Injection Point {injection.subject} was not connected because its {detail}.",
                    Path(path),
                    start_line,
                    end_line,
                    evidence_kind="invocation",
                )
            )
        if not matched_invocations and not invalid_invocations:
            relationships.append(
                ImpactRelationship(
                    "ejb_container_dispatch",
                    injection.subject,
                    injection.path,
                    injection.start_line,
                    injection.end_line,
                    injection_handle,
                    "medium",
                    evidence_chain=evidence_chain,
                    business_view=dispatch_mapping[2],
                )
            )
    return tuple(relationships), tuple(unresolved)


def _unique_ejb_mappings(
    mappings: Iterable[tuple[str, str, str, tuple[EJBFact, ...]]]
) -> tuple[tuple[str, str, str, tuple[EJBFact, ...]], ...]:
    unique: list[tuple[str, str, str, tuple[EJBFact, ...]]] = []
    seen: set[tuple[str, str, str]] = set()
    for mapping in mappings:
        key = mapping[:3]
        if key in seen:
            continue
        seen.add(key)
        unique.append(mapping)
    return tuple(unique)


def _ejb_interface_ancestors(
    interface_name: str, extends: Iterable[EJBFact]
) -> tuple[tuple[str, tuple[EJBFact, ...]], ...]:
    parents_by_child: dict[str, list[EJBFact]] = {}
    for relationship in extends:
        if relationship.target:
            parents_by_child.setdefault(relationship.subject, []).append(relationship)
    ancestors: list[tuple[str, tuple[EJBFact, ...]]] = []
    queue: list[tuple[str, tuple[EJBFact, ...], frozenset[str]]] = [
        (interface_name, (), frozenset({interface_name}))
    ]
    while queue:
        current, path, visited = queue.pop(0)
        for relationship in parents_by_child.get(current, ()):
            parent = relationship.target
            if parent in visited:
                continue
            next_path = (*path, relationship)
            ancestors.append((parent, next_path))
            queue.append((parent, next_path, visited | {parent}))
    return tuple(ancestors)


def _ejb_interface_related(
    interface_name: str, target_name: str, extends: Iterable[EJBFact]
) -> bool:
    return interface_name == target_name or any(
        ancestor == target_name
        for ancestor, _ in _ejb_interface_ancestors(interface_name, extends)
    )


def _ejb_injection_receiver_matches(
    injection: EJBFact, receiver: str | None, caller: str
) -> bool:
    if not receiver or not injection.value:
        return False
    injection_owner = injection.subject.rsplit("#", maxsplit=1)[0]
    caller_owner = caller.rsplit("#", maxsplit=1)[0]
    if injection_owner != caller_owner:
        return False
    _, _, property_name = injection.value.partition(":")
    normalized_receiver = receiver.strip()
    if normalized_receiver.startswith("this."):
        normalized_receiver = normalized_receiver[5:]
    return normalized_receiver == property_name


def _ejb_descriptor_target_exists(
    repository_root: Path,
    injection: EJBFact,
    declarations: Iterable[tuple[str, str, str, str, int, int]],
) -> bool:
    owner, _, member = injection.subject.partition("#")
    if not owner or not member:
        return False
    class_paths = {
        Path(path)
        for kind, qualified_name, _signature, path, _start_line, _end_line in declarations
        if kind == "class" and qualified_name == owner
    }
    if not class_paths:
        return False
    for class_path in class_paths:
        try:
            source = (repository_root / class_path).read_bytes()
        except OSError:
            continue
        parser = Parser(Language(tree_sitter_java.language()))
        tree = parser.parse(source)
        for node in _tree_nodes(tree.root_node):
            if node.type != "class_declaration":
                continue
            if _node_text(node.child_by_field_name("name"), source) != owner.rsplit(".", 1)[-1]:
                continue
            for descendant in _tree_nodes(node):
                if descendant.type == "variable_declarator" and _node_text(
                    descendant.child_by_field_name("name"), source
                ) == member:
                    return True
                if descendant.type == "method_declaration":
                    method_name = _node_text(descendant.child_by_field_name("name"), source)
                    if method_name == member or method_name == _ejb_setter_name(member):
                        return True
    return False


def _ejb_setter_name(property_name: str) -> str:
    return "set" + (property_name[:1].upper() + property_name[1:] if property_name else "")


def _ejb_receiver_is_shadowed(
    repository_root: Path,
    injection: EJBFact,
    path: Path,
    caller: str,
    receiver: str | None,
    line: int,
) -> bool:
    if receiver is None or receiver.strip().startswith("this.") or not injection.value:
        return False
    _, _, property_name = injection.value.partition(":")
    try:
        source = (repository_root / path).read_bytes()
    except OSError:
        return True
    parser = Parser(Language(tree_sitter_java.language()))
    tree = parser.parse(source)
    method_name = caller.rsplit("#", maxsplit=1)[-1]
    for node in _tree_nodes(tree.root_node):
        if node.type != "method_declaration":
            continue
        if _node_text(node.child_by_field_name("name"), source) != method_name:
            continue
        if not (node.start_point.row + 1 <= line <= node.end_point.row + 1):
            continue
        parameters = node.child_by_field_name("parameters")
        parameter_names = {
            _node_text(parameter.child_by_field_name("name"), source)
            for parameter in parameters.named_children
            if parameter.child_by_field_name("name") is not None
        } if parameters is not None else set()
        local_names = {
            _node_text(declarator.child_by_field_name("name"), source)
            for descendant in _tree_nodes(node)
            if descendant.type == "local_variable_declaration"
            for declarator in descendant.named_children
            if declarator.type == "variable_declarator"
            and declarator.child_by_field_name("name") is not None
        }
        return property_name in parameter_names or property_name in local_names
    return True


def _tree_nodes(root) -> Iterable:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.named_children))


def _ejb_injection_targets_owner(
    injection: EJBFact, owner: str, facts: Iterable[EJBFact]
) -> bool:
    if injection.target == owner:
        return True
    related_types = {
        fact.target
        for fact in facts
        if fact.kind == "type_implements" and fact.subject == owner and fact.target
    }
    related_types.update(
        fact.target
        for fact in facts
        if fact.kind == "type_extends" and fact.subject == owner and fact.target
    )
    if injection.target in related_types:
        return True
    parents_by_child: dict[str, set[str]] = {}
    for fact in facts:
        if fact.kind in {"type_implements", "type_extends"} and fact.subject and fact.target:
            parents_by_child.setdefault(fact.subject, set()).add(fact.target)
    queue = [owner]
    visited: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        if current == injection.target:
            return True
        queue.extend(parents_by_child.get(current, ()))
    queue = [injection.target] if injection.target else []
    visited.clear()
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        if current == owner:
            return True
        queue.extend(parents_by_child.get(current, ()))
    return False


def _matching_ejb_method(
    declarations: Iterable[tuple[str, str, Path, int, int]],
    owner: str,
    name: str,
    parameters: str,
    repository_root: Path,
    requested_path: Path | None = None,
) -> tuple[str, str, Path, int, int] | None:
    expected = _normalize_java_parameters(parameters)
    matches = [
        (qualified_name, signature, path, start_line, end_line)
        for qualified_name, signature, path, start_line, end_line in declarations
        if qualified_name.startswith(owner + "#")
        and signature.split("#", maxsplit=1)[1].split("(", maxsplit=1)[0] == name
        and _normalize_java_parameters(signature.split("(", maxsplit=1)[1].removesuffix(")")) == expected
    ]
    if len(matches) > 1:
        return None
    if len(matches) == 1 and _conflicting_ejb_parameter_imports(
        parameters,
        matches[0][1].split("(", maxsplit=1)[1].removesuffix(")"),
        requested_path,
        matches[0][2],
        repository_root,
    ):
        return None
    return matches[0] if len(matches) == 1 else None


def _normalize_java_parameters(parameters: str) -> str:
    return re.sub(r"\s+", "", parameters)


def _conflicting_ejb_parameter_imports(
    requested_parameters: str,
    candidate_parameters: str,
    requested_path: Path | None,
    candidate_path: Path,
    repository_root: Path,
) -> bool:
    requested_types = _split_java_parameters(requested_parameters)
    candidate_types = _split_java_parameters(candidate_parameters)
    if len(requested_types) != len(candidate_types):
        return True
    candidate_imports = _java_imports_for_path(repository_root, candidate_path)
    for requested_type, candidate_type in zip(requested_types, candidate_types):
        if _contains_generic_type_variable(requested_type) or _contains_generic_type_variable(
            candidate_type
        ):
            return True
        requested_names = _java_type_references(requested_type)
        candidate_names = _java_type_references(candidate_type)
        if requested_names != candidate_names:
            return True
        requested_imports = _java_imports_for_path(repository_root, requested_path)
        for requested_name, candidate_name in zip(requested_names, candidate_names):
            if "." in requested_name or "." in candidate_name:
                if requested_name != candidate_name:
                    return True
                continue
            if re.fullmatch(r"[A-Z]", requested_name):
                return True
            requested_candidates = _java_type_import_candidates(requested_imports, requested_name)
            candidate_candidates = _java_type_import_candidates(candidate_imports, candidate_name)
            if not requested_candidates and not candidate_candidates:
                requested_package = _java_package_for_path(repository_root, requested_path)
                candidate_package = _java_package_for_path(repository_root, candidate_path)
                if (
                    requested_package != candidate_package
                    and requested_name not in _JAVA_LANG_TYPES
                    and not re.fullmatch(
                        r"(?:boolean|byte|char|double|float|int|long|short|void)", requested_name
                    )
                ):
                    return True
            if requested_candidates != candidate_candidates and (
                requested_candidates or candidate_candidates
            ):
                return True
    return False


def _contains_generic_type_variable(value: str) -> bool:
    return "<" in value and bool(re.search(r"\b[A-Z]\b", value))


def _java_type_references(value: str) -> tuple[str, ...]:
    return tuple(
        re.findall(r"(?:[A-Za-z_$][\w$]*\.)*[A-Z_$][\w$]*", value)
    )


def _split_java_parameters(parameters: str) -> tuple[str, ...]:
    values: list[str] = []
    start = 0
    nesting = 0
    for index, character in enumerate(parameters):
        if character in "<[(":
            nesting += 1
        elif character in ">)]":
            nesting = max(0, nesting - 1)
        elif character == "," and nesting == 0:
            values.append(parameters[start:index].strip())
            start = index + 1
    if parameters.strip():
        values.append(parameters[start:].strip())
    return tuple(values)


def _java_type_import_candidates(imports: dict[str, str], simple_name: str) -> set[str]:
    explicit = imports.get(simple_name)
    if explicit and not explicit.endswith(".*"):
        return {explicit}
    return {
        f"{package}.{simple_name}"
        for package in imports
        if imports[package].endswith(".*")
    }


def _java_imports_for_path(repository_root: Path, path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    try:
        source = (repository_root / path).read_bytes()
    except OSError:
        return {}
    return _java_imports(source)


_JAVA_LANG_TYPES = frozenset(
    {"Boolean", "Byte", "Character", "Double", "Float", "Integer", "Long", "Object", "Short", "String"}
)


def _java_package_for_path(repository_root: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        source = (repository_root / path).read_bytes()
    except OSError:
        return ""
    parser = Parser(Language(tree_sitter_java.language()))
    tree = parser.parse(source)
    return _package_name(tree.root_node, source)


def _spring_relationships(
    connection_data_path: Path, owner: str, profiles: tuple[str, ...]
) -> tuple[tuple[ImpactRelationship, ...], tuple[UnresolvedItem, ...]]:
    connection = sqlite3.connect(connection_data_path)
    try:
        rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line, profile
            FROM spring_facts ORDER BY path, start_line, kind, subject"""
        ).fetchall()
        test_owners = {
            row[0]
            for row in connection.execute(
                "SELECT qualified_name FROM java_declarations WHERE is_test = 1 AND kind IN ('class', 'interface', 'enum', 'record')"
            )
        }
        declared_owners = {
            row[0]
            for row in connection.execute(
                "SELECT qualified_name FROM java_declarations WHERE kind IN ('class', 'interface', 'enum', 'record')"
            )
        }
    except sqlite3.OperationalError:
        return (), (_unresolved("Spring evidence is unavailable because the local index is missing Spring facts."),)
    finally:
        connection.close()

    facts = tuple(SpringFact(kind, subject, target, value, Path(path), start, end, profile)
                  for kind, subject, target, value, path, start, end, profile in rows)
    relationships: list[ImpactRelationship] = []
    unresolved: list[UnresolvedItem] = []

    def active(fact: SpringFact) -> tuple[bool, bool]:
        if fact.profile is None:
            return True, False
        if _spring_profile_is_expression(fact.profile):
            return False, False
        if profiles:
            return fact.profile in profiles, False
        return True, True

    def add_relationship(
        kind: str, caller: str, fact: SpringFact, confidence: str = "medium",
    ) -> None:
        applies, conditional = active(fact)
        if not applies:
            return
        relationships.append(
            ImpactRelationship(
                kind,
                caller,
                fact.path,
                fact.start_line,
                fact.end_line,
                _spring_evidence_handle(fact.path, fact.start_line, fact.end_line),
                confidence,
                conditional,
                fact.profile,
            )
        )

    def matching_type(candidate: str | None, requested: str | None) -> bool:
        if not candidate or not requested:
            return False
        if candidate == requested:
            return True
        candidate_name = candidate.rsplit(".", maxsplit=1)[-1]
        requested_name = requested.rsplit(".", maxsplit=1)[-1]
        if candidate_name != requested_name:
            return False
        return sum(
            1 for owner_name in declared_owners
            if owner_name.rsplit(".", maxsplit=1)[-1] == requested_name
        ) <= 1

    owner_facts = [
        fact for fact in facts
        if fact.subject == owner or matching_type(fact.target, owner)
    ]
    for fact in owner_facts:
        if fact.kind == "spring_component" and fact.subject == owner:
            add_relationship("spring_configuration_boundary", owner, fact)
        elif fact.kind in {"spring_bean", "spring_xml_bean"} and matching_type(fact.target, owner):
            add_relationship("spring_configuration_boundary", fact.subject, fact)

    bean_facts = [
        fact for fact in facts
        if fact.kind in {"spring_component", "spring_bean", "spring_xml_bean"}
    ]
    xml_beans = [fact for fact in facts if fact.kind == "spring_xml_bean"]
    for fact in facts:
        if fact.kind != "spring_xml_ref":
            continue
        referenced_beans = [
            bean for bean in xml_beans
            if bean.subject == fact.target and matching_type(bean.target, owner)
            and active(bean)[0]
        ]
        consumer_beans = [bean for bean in xml_beans if bean.subject == fact.subject and active(bean)[0]]
        if len(referenced_beans) == 1 and len(consumer_beans) == 1:
            add_relationship("bean_consumer", consumer_beans[0].target or fact.subject, fact, "high")
        elif referenced_beans and not consumer_beans:
            unresolved.append(
                _unresolved(
                    f"Spring XML reference {fact.target} has no proven consumer bean.",
                    fact.path,
                    fact.start_line,
                    fact.end_line,
                    "spring",
                )
            )
    for fact in facts:
        if fact.kind != "spring_injection" or not matching_type(fact.target, owner):
            continue
        applies, _ = active(fact)
        if not applies:
            continue
        candidates = [
            candidate for candidate in bean_facts
            if matching_type(candidate.subject if candidate.kind == "spring_component" else candidate.target, owner)
            and active(candidate)[0]
        ]
        if len(candidates) == 1:
            kind = "spring_test" if fact.subject in test_owners else "bean_consumer"
            add_relationship(kind, fact.subject, fact, "medium" if kind == "spring_test" else "high")
        elif len(candidates) > 1:
            unresolved.append(
                _unresolved(
                    f"Spring injection of {owner} in {fact.subject} has multiple local bean candidates.",
                    fact.path,
                    fact.start_line,
                    fact.end_line,
                    "spring",
                )
            )
        else:
            unresolved.append(
                _unresolved(
                    f"Spring injection of {owner} in {fact.subject} has no proven local bean candidate.",
                    fact.path,
                    fact.start_line,
                    fact.end_line,
                    "spring",
                )
            )

    property_consumers: list[SpringFact] = []
    for fact in facts:
        if fact.kind != "spring_property_consumer":
            continue
        if fact.subject == owner:
            property_consumers.append(fact)
            continue
        if any(
            bean.subject == fact.subject and matching_type(bean.target, owner)
            for bean in xml_beans
        ):
            property_consumers.append(fact)
    property_sources = [fact for fact in facts if fact.kind == "spring_property_source"]
    property_placeholders = [
        fact for fact in facts if fact.kind == "spring_property_placeholder"
    ]
    for consumer in property_consumers:
        applies, _ = active(consumer)
        if not applies:
            continue
        if consumer.target:
            add_relationship("property_consumer", consumer.target, consumer, "high")
        matches = [
            source for source in property_sources
            if source.subject == consumer.target
            or (consumer.target.endswith(".") and source.subject.startswith(consumer.target))
            or (consumer.target and source.subject.startswith(consumer.target + "."))
        ]
        if consumer.path.suffix.lower() == ".xml":
            placeholders = [
                placeholder
                for placeholder in property_placeholders
                if placeholder.path == consumer.path and active(placeholder)[0]
            ]
            if placeholders:
                matches = [
                    source for source in matches
                    if any(
                        _spring_placeholder_matches(placeholder.target, source.path)
                        for placeholder in placeholders
                    )
                ]
                if not matches:
                    placeholder = placeholders[0]
                    unresolved.append(
                        _unresolved(
                            "Spring XML property placeholder did not match a local property source.",
                            placeholder.path,
                            placeholder.start_line,
                            placeholder.end_line,
                            "spring",
                        )
                    )
            else:
                matches = []
        for source in matches:
            add_relationship("property_source", source.subject, source, "medium" if active(source)[1] else "high")

    for fact in facts:
        if fact.kind == "spring_test" and matching_type(fact.target, owner):
            add_relationship("spring_test", fact.subject, fact, "medium")
        elif fact.kind == "spring_unresolved" and (
            not fact.subject or matching_type(fact.subject, owner)
        ):
            applies, _ = active(fact)
            if applies:
                unresolved.append(
                    _unresolved(
                        fact.value or "Spring behavior remains unresolved.",
                        fact.path,
                        fact.start_line,
                        fact.end_line,
                        "spring",
                    )
                )

    unique_relationships: list[ImpactRelationship] = []
    seen_relationships: set[tuple[object, ...]] = set()
    for relationship in relationships:
        key = (
            relationship.kind,
            relationship.caller,
            relationship.path,
            relationship.start_line,
            relationship.end_line,
            relationship.conditional,
        )
        if key not in seen_relationships:
            seen_relationships.add(key)
            unique_relationships.append(relationship)
    return tuple(unique_relationships), tuple(unresolved)


def _read_index_snapshot(
    connection_or_path: sqlite3.Connection | Path, root: Path
) -> IndexSnapshot:
    close_when_done = isinstance(connection_or_path, Path)
    connection = (
        sqlite3.connect(connection_or_path) if close_when_done else connection_or_path
    )
    try:
        values = dict(connection.execute("SELECT key, value FROM metadata"))
        return IndexSnapshot(
            root,
            values.get("git_commit") or None,
            values.get("working_tree_state", "unknown"),
        )
    finally:
        if close_when_done:
            connection.close()


def _index_repository(repository_root: Path) -> IndexResult:
    """Build a local repository index and describe exactly what it contains."""
    root = repository_root.resolve()
    source_roots = _discover_source_roots(root)
    excluded_directories = _excluded_directories(root)
    indexed_files, read_failures, contents_by_path = _java_files(root, source_roots)
    configuration_files, configuration_read_failures, configuration_contents = _configuration_files(
        root, source_roots
    )
    declarations, invocations, parse_failures = _analyze_java_files(
        contents_by_path, _test_source_roots(root, source_roots)
    )
    vbnet_files, vbnet_read_failures, vbnet_contents, vbnet_file_hashes = _discover_vbnet_files(root)
    vbnet_declarations, vbnet_invocations, vbnet_facts, vbnet_parse_failures = _analyze_vbnet_files(
        root, vbnet_files, vbnet_contents
    )
    spring_facts = _analyze_spring_files(
        {**contents_by_path, **configuration_contents}, declarations
    )
    ejb_facts = _analyze_ejb_files({**contents_by_path, **configuration_contents})
    quarkus_build_facts = _analyze_quarkus_build_files(
        {**contents_by_path, **configuration_contents}
    )
    quarkus_config_facts = _analyze_quarkus_config_files(
        {**contents_by_path, **configuration_contents}, declarations
    )
    quarkus_cdi_facts = _analyze_quarkus_cdi_files(contents_by_path, declarations)
    quarkus_rest_facts = _analyze_quarkus_rest_files(
        contents_by_path, declarations, quarkus_build_facts, quarkus_config_facts
    )
    quarkus_route_facts = _analyze_quarkus_route_files(
        contents_by_path, declarations, quarkus_build_facts
    )
    quarkus_security_facts = _analyze_quarkus_security_files(
        contents_by_path, declarations, quarkus_config_facts
    )
    quarkus_test_facts = _analyze_quarkus_test_files(
        contents_by_path, declarations
    )
    quarkus_native_facts = _analyze_quarkus_native_files(
        {**contents_by_path, **configuration_contents}, declarations
    )
    quarkus_boundary_facts = _analyze_quarkus_boundary_files(
        {**contents_by_path, **configuration_contents}, declarations
    )
    soap_facts = _analyze_soap_files(
        {**contents_by_path, **configuration_contents}, root
    )
    indexed_file_hashes = {
        **{
            path: _content_hash(content)
            for path, content in {**contents_by_path, **configuration_contents}.items()
        },
        **vbnet_file_hashes,
    }
    snapshot = _snapshot(root)
    result = IndexResult(
        source_roots=source_roots,
        indexed_files=tuple(sorted(dict.fromkeys((*indexed_files, *vbnet_files)), key=str)),
        excluded_directories=excluded_directories,
        read_failures=tuple(sorted(set(read_failures) | set(configuration_read_failures) | set(vbnet_read_failures), key=str)),
        declarations=declarations,
        invocations=invocations,
        parse_failures=tuple(sorted(set(parse_failures) | set(vbnet_parse_failures), key=lambda p: (str(p.path), p.start_line))),
        snapshot=snapshot,
        spring_facts=spring_facts,
        ejb_facts=ejb_facts,
        configuration_files=configuration_files,
        quarkus_build_facts=quarkus_build_facts,
        quarkus_config_facts=quarkus_config_facts,
        quarkus_cdi_facts=quarkus_cdi_facts,
        quarkus_rest_facts=quarkus_rest_facts,
        quarkus_route_facts=quarkus_route_facts,
        quarkus_security_facts=quarkus_security_facts,
        quarkus_test_facts=quarkus_test_facts,
        quarkus_native_facts=quarkus_native_facts,
        quarkus_boundary_facts=quarkus_boundary_facts,
        soap_facts=soap_facts,
        vbnet_declarations=vbnet_declarations,
        vbnet_invocations=vbnet_invocations,
        vbnet_facts=vbnet_facts,
        vbnet_files=vbnet_files,
        indexed_file_hashes=tuple(sorted(indexed_file_hashes.items(), key=lambda item: str(item[0]))),
    )
    _write_index(result)
    return result


def _analyze_quarkus_config_files(
    contents_by_path: dict[Path, bytes],
    declarations: tuple[JavaDeclaration, ...],
) -> tuple[QuarkusConfigFact, ...]:
    facts: list[QuarkusConfigFact] = []
    for path, content in sorted(contents_by_path.items(), key=lambda item: str(item[0])):
        if path.suffix.lower() == ".java":
            facts.extend(_quarkus_java_facts(path, content, declarations))
        elif path.suffix.lower() in {".properties", ".yml", ".yaml"}:
            facts.extend(_quarkus_configuration_facts(path, content))
    return tuple(facts)


def _refresh_index_if_needed(root: Path) -> None:
    """Plan a complete replacement before applying a new Repository Index snapshot."""
    database_path = root / ".changescope" / "index.sqlite"
    source_roots, current_files = _current_index_inputs(root)
    current_test_roots = _test_source_roots(root, source_roots)
    refresh_required = False
    connection = sqlite3.connect(database_path)
    try:
        if not _index_schema_is_current(connection):
            refresh_required = True
            previous_files: dict[str, tuple[str, str]] = {}
            previous_metadata: dict[str, str] = {}
        else:
            previous_files = {
                path: (status, content_hash)
                for path, status, content_hash in connection.execute(
                    "SELECT path, status, content_hash FROM source_files"
                )
            }
            previous_metadata = dict(connection.execute("SELECT key, value FROM metadata"))
            refresh_required = (
                previous_metadata.get("schema_version") != _INDEX_SCHEMA_VERSION
                or previous_files != current_files
                or previous_metadata.get("source_roots") != _root_list_value(source_roots)
                or previous_metadata.get("test_source_roots") != _root_list_value(current_test_roots)
            )

        if not refresh_required:
            current_snapshot = _snapshot(root)
            stored_snapshot = IndexSnapshot(
                root,
                previous_metadata.get("git_commit") or None,
                previous_metadata.get("working_tree_state", "unknown"),
            )
            if current_snapshot != stored_snapshot:
                with connection:
                    _write_metadata(connection, current_snapshot, source_roots)
    finally:
        connection.close()

    if refresh_required:
        # _index_repository builds every fact family before opening the write
        # transaction. A failed analysis therefore leaves the prior snapshot
        # untouched, and _write_index replaces the complete snapshot atomically.
        _index_repository(root)


def _current_index_inputs(
    root: Path,
) -> tuple[tuple[Path, ...], dict[str, tuple[str, str]]]:
    source_roots = _discover_source_roots(root)
    indexed_java_files, java_read_failures, _ = _java_files(root, source_roots)
    configuration_files, configuration_read_failures, _ = _configuration_files(root, source_roots)
    vbnet_files, vbnet_read_failures, _, _ = _discover_vbnet_files(root)
    indexed_files = (*indexed_java_files, *configuration_files, *vbnet_files)
    current_files = {
        str(path): ("indexed", _file_content_hash(root / path))
        for path in indexed_files
    }
    current_files.update({
        str(path): ("unreadable", "")
        for path in (*java_read_failures, *configuration_read_failures, *vbnet_read_failures)
    })
    return source_roots, current_files


def _index_schema_is_current(connection: sqlite3.Connection) -> bool:
    for table, required_columns in _INDEX_SCHEMA_COLUMNS.items():
        columns = {
            row[1]
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if not required_columns.issubset(columns):
            return False
    return True


def _discover_source_roots(root: Path) -> tuple[Path, ...]:
    declared_build_roots = _declared_build_source_roots(root)
    if declared_build_roots:
        return declared_build_roots

    conventional_roots = tuple(
        candidate
        for candidate in (
            Path("src/main/java"),
            Path("src/test/java"),
            Path("src/main/kotlin"),
            Path("src/test/kotlin"),
            Path("src/main/scala"),
            Path("src/test/scala"),
        )
        if (root / candidate).is_dir()
    )
    eclipse_roots = _eclipse_source_roots(root)
    if conventional_roots or eclipse_roots:
        return tuple(dict.fromkeys((*conventional_roots, *eclipse_roots)))

    if (root / "src").is_dir():
        return (Path("src"),)
    return (Path("."),)


def _declared_build_source_roots(root: Path) -> tuple[Path, ...]:
    candidates = [*_maven_source_roots(root), *_gradle_source_roots(root)]
    existing_roots = filter(None, (_repository_relative_root(root, path) for path in candidates))
    return tuple(dict.fromkeys(existing_roots))


def _maven_source_roots(root: Path) -> tuple[Path, ...]:
    return _maven_source_roots_named(root, {"sourceDirectory", "testSourceDirectory"})


def _maven_test_source_roots(root: Path) -> tuple[Path, ...]:
    return _maven_source_roots_named(root, {"testSourceDirectory"})


def _maven_source_roots_named(root: Path, names: set[str]) -> tuple[Path, ...]:
    pom = root / "pom.xml"
    if not pom.is_file():
        return ()
    try:
        elements = ElementTree.parse(pom).getroot().iter()
    except (ElementTree.ParseError, OSError):
        return ()
    roots = []
    for element in elements:
        name = element.tag.rsplit("}", maxsplit=1)[-1]
        if name not in names or not element.text:
            continue
        candidate = Path(element.text.strip())
        if "$" not in str(candidate):
            roots.append(candidate)
    return tuple(roots)


def _gradle_source_roots(root: Path) -> tuple[Path, ...]:
    roots = []
    for filename in ("build.gradle", "build.gradle.kts"):
        try:
            contents = (root / filename).read_text(encoding="utf-8")
        except OSError:
            continue
        for match in re.finditer(
            r"srcDirs?\s*(?:=)?\s*(?:\[\s*)?['\"]([^'\"]+)", contents
        ):
            roots.append(Path(match.group(1)))
    return tuple(roots)


def _gradle_test_source_roots(root: Path) -> tuple[Path, ...]:
    roots = []
    for filename in ("build.gradle", "build.gradle.kts"):
        try:
            contents = (root / filename).read_text(encoding="utf-8")
        except OSError:
            continue
        for block in re.finditer(r"\btest\s*\{(?P<body>.*?\})", contents, re.DOTALL):
            for match in re.finditer(
                r"srcDirs?\s*(?:=)?\s*(?:\[\s*)?['\"]([^'\"]+)",
                block.group("body"),
            ):
                roots.append(Path(match.group(1)))
    return tuple(roots)


def _eclipse_source_roots(root: Path) -> tuple[Path, ...]:
    return _eclipse_source_roots_matching(root, lambda entry: True)


def _eclipse_test_source_roots(root: Path) -> tuple[Path, ...]:
    return _eclipse_source_roots_matching(root, _is_eclipse_test_source_entry)


def _is_eclipse_test_source_entry(entry: ElementTree.Element) -> bool:
    if entry.get("test") == "true":
        return True
    return any(
        attribute.get("name") == "test" and attribute.get("value") == "true"
        for attribute in entry.findall("attributes/attribute")
    )


def _eclipse_source_roots_matching(root: Path, predicate) -> tuple[Path, ...]:
    classpath = root / ".classpath"
    if not classpath.is_file():
        return ()
    try:
        entries = ElementTree.parse(classpath).getroot().findall("classpathentry")
    except (ElementTree.ParseError, OSError):
        return ()
    roots = []
    for entry in entries:
        if entry.get("kind") != "src" or not predicate(entry):
            continue
        path = entry.get("path")
        if not path:
            continue
        candidate = _repository_relative_root(root, Path(path))
        if candidate is None:
            continue
        roots.append(candidate)
    return tuple(dict.fromkeys(roots))


def _repository_relative_root(root: Path, candidate: Path) -> Path | None:
    if candidate.is_absolute():
        return None
    try:
        relative_path = (root / candidate).resolve().relative_to(root)
    except ValueError:
        return None
    return relative_path if (root / relative_path).is_dir() else None


def _excluded_directories(root: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (
                path.relative_to(root)
                for path in root.rglob("*")
                if path.is_dir() and path.name in EXCLUDED_DIRECTORY_NAMES
            ),
            key=str,
        )
    )


def _java_files(
    root: Path, source_roots: Iterable[Path]
) -> tuple[tuple[Path, ...], tuple[Path, ...], dict[Path, bytes]]:
    indexed_files: list[Path] = []
    read_failures: list[Path] = []
    contents_by_path: dict[Path, bytes] = {}

    def record_walk_failure(error: OSError) -> None:
        if not error.filename:
            return
        try:
            read_failures.append(Path(error.filename).relative_to(root))
        except ValueError:
            return

    for source_root in source_roots:
        for directory, directories, filenames in os.walk(
            root / source_root, onerror=record_walk_failure
        ):
            directories[:] = [
                name for name in directories if name not in EXCLUDED_DIRECTORY_NAMES
            ]
            for filename in filenames:
                ext = Path(filename).suffix.lower()
                if ext not in (".java", ".kt", ".scala"):
                    continue
                candidate = Path(directory) / filename
                relative_path = candidate.relative_to(root)
                try:
                    contents_by_path[relative_path] = candidate.read_bytes()
                except OSError:
                    read_failures.append(relative_path)
                    continue
                indexed_files.append(relative_path)
    return (
        tuple(sorted(set(indexed_files), key=str)),
        tuple(sorted(read_failures, key=str)),
        contents_by_path,
    )


def _analyze_java_files(
    contents_by_path: dict[Path, bytes], test_roots: tuple[Path, ...]
) -> tuple[
    tuple[JavaDeclaration, ...], tuple[JavaInvocation, ...], tuple[ParseFailure, ...]
]:
    declarations: list[JavaDeclaration] = []
    invocations: list[JavaInvocation] = []
    parse_failures: list[ParseFailure] = []

    for relative_path, content in contents_by_path.items():
        if not relative_path.name.endswith(".java"):
            continue
    for path, source in sorted(contents_by_path.items(), key=lambda item: str(item[0])):
        is_test = any(path.is_relative_to(root) for root in test_roots)
        # Keep native grammar and parser state scoped to one source file. This avoids
        # retaining parser state across heterogeneous legacy source trees.
        parser = Parser(Language(tree_sitter_java.language()))
        tree = parser.parse(source)
        package_name = _package_name(tree.root_node, source)
        _collect_java_facts(
            tree.root_node,
            source,
            path,
            package_name,
            is_test,
            (),
            None,
            declarations,
            invocations,
        )
        issue = _first_parse_failure(tree.root_node, path)
        if issue is not None:
            parse_failures.append(issue)
    return (
        tuple(sorted(declarations, key=lambda item: (str(item.path), item.start_line, item.kind))),
        tuple(sorted(invocations, key=lambda item: (str(item.path), item.start_line, item.name))),
        tuple(sorted(parse_failures, key=lambda item: (str(item.path), item.start_line))),
    )


def _test_source_roots(root: Path, source_roots: tuple[Path, ...]) -> tuple[Path, ...]:
    declared_test_roots = set(_maven_test_source_roots(root)) | set(
        _gradle_test_source_roots(root)
    ) | set(_eclipse_test_source_roots(root))
    return tuple(
        source_root
        for source_root in source_roots
        if source_root in declared_test_roots
        or any(part.lower() in {"test", "tests"} for part in source_root.parts)
    )


def _configuration_files(
    root: Path, source_roots: tuple[Path, ...]
) -> tuple[tuple[Path, ...], tuple[Path, ...], dict[Path, bytes]]:
    """Discover bounded local configuration files used by Spring applications."""
    resource_roots = [
        Path("src/main/resources"),
        Path("src/test/resources"),
        Path("resources"),
        Path("config"),
        Path("wsdl"),
        Path("schema"),
        Path("schemas"),
        Path("."),
        *source_roots,
    ]
    indexed: dict[Path, bytes] = {}
    read_failures: set[Path] = set()
    visited_roots: set[Path] = set()
    for relative_root in resource_roots:
        candidate_root = root / relative_root
        if not candidate_root.is_dir():
            continue
        resolved_root = candidate_root.resolve()
        if resolved_root in visited_roots:
            continue
        visited_roots.add(resolved_root)
        for directory, directories, filenames in os.walk(candidate_root):
            directories[:] = [
                name for name in directories if name not in EXCLUDED_DIRECTORY_NAMES
            ]
            for filename in filenames:
                path = Path(directory) / filename
                path_posix = path.as_posix()
                is_config = (
                    path.suffix.lower() in {".properties", ".yml", ".yaml", ".xml", ".json", ".wsdl", ".xsd"}
                    or "META-INF/services" in path_posix
                    or "META-INF/native-image" in path_posix
                )
                if not is_config:
                    continue
                relative_path = path.relative_to(root)
                try:
                    indexed[relative_path] = path.read_bytes()
                except OSError:
                    read_failures.add(relative_path)

    for path in root.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".properties", ".yml", ".yaml", ".xml", ".wsdl", ".xsd"}:
            continue
        name = path.name.lower()
        if not (
            name.startswith("application")
            or "spring" in name
            or "context" in name
            or "beans" in name
        ):
            continue
        relative_path = path.relative_to(root)
        try:
            indexed[relative_path] = path.read_bytes()
        except OSError:
            read_failures.add(relative_path)

    for directory, directories, filenames in os.walk(root):
        directories[:] = [
            name for name in directories if name not in EXCLUDED_DIRECTORY_NAMES
        ]
        for filename in filenames:
            if filename in {
                "pom.xml",
                "build.gradle",
                "build.gradle.kts",
                "settings.gradle",
                "settings.gradle.kts",
            }:
                path = Path(directory) / filename
                relative_path = path.relative_to(root)
                try:
                    indexed[relative_path] = path.read_bytes()
                except OSError:
                    read_failures.add(relative_path)
    return (
        tuple(sorted(indexed, key=str)),
        tuple(sorted(read_failures, key=str)),
        indexed,
    )


def _analyze_spring_files(
    contents_by_path: dict[Path, bytes],
    declarations: tuple[JavaDeclaration, ...] = (),
) -> tuple[SpringFact, ...]:
    facts: list[SpringFact] = []
    java_by_path: dict[Path, tuple[JavaDeclaration, ...]] = {}
    for declaration in declarations:
        java_by_path.setdefault(declaration.path, ())
        java_by_path[declaration.path] = (*java_by_path[declaration.path], declaration)
    for path, source in sorted(contents_by_path.items(), key=lambda item: str(item[0])):
        if path.suffix.lower() == ".java":
            facts.extend(_spring_java_facts(path, source, java_by_path.get(path, ())))
        else:
            facts.extend(_spring_configuration_facts(path, source))
    return tuple(sorted(facts, key=lambda fact: (str(fact.path), fact.start_line, fact.kind, fact.subject)))


def _analyze_ejb_files(contents_by_path: dict[Path, bytes]) -> tuple[EJBFact, ...]:
    facts: list[EJBFact] = []
    for path, source in sorted(contents_by_path.items(), key=lambda item: str(item[0])):
        if path.suffix.lower() == ".java":
            facts.extend(_ejb_java_facts(path, source))
        elif path.name.lower() in {"ejb-jar.xml", "jboss-ejb3.xml"}:
            facts.extend(_ejb_descriptor_facts(path, source))
    facts.extend(_ejb_descriptor_inheritance_facts(contents_by_path, facts))
    facts.extend(_ejb_implicit_no_interface_facts(facts))
    facts.extend(_ejb_descriptor_conflicts(facts))
    return tuple(
        sorted(facts, key=lambda fact: (str(fact.path), fact.start_line, fact.kind, fact.subject, fact.target or ""))
    )


def _ejb_descriptor_inheritance_facts(
    contents_by_path: dict[Path, bytes], facts: Iterable[EJBFact]
) -> tuple[EJBFact, ...]:
    descriptor_sessions = {
        fact.subject
        for fact in facts
        if fact.kind == "session_bean" and fact.path.suffix.lower() == ".xml"
    }
    if not descriptor_sessions:
        return ()
    existing = {
        (fact.subject, fact.value)
        for fact in facts
        if fact.kind == "ejb_unresolved"
    }
    inheritance: list[EJBFact] = []
    type_kinds = {"class_declaration", "record_declaration"}
    for path, source in sorted(contents_by_path.items(), key=lambda item: str(item[0])):
        if path.suffix.lower() != ".java":
            continue
        parser = Parser(Language(tree_sitter_java.language()))
        tree = parser.parse(source)
        package_name = _package_name(tree.root_node, source)

        def visit(node, enclosing_types: tuple[str, ...]) -> None:
            if node.type in type_kinds:
                name = _node_text(node.child_by_field_name("name"), source)
                qualified_name = _qualified_type_name(
                    package_name, (*enclosing_types, name)
                )
                if (
                    qualified_name in descriptor_sessions
                    and _ejb_type_clause(node, "superclass", source)
                    and (
                        qualified_name,
                        "Session Bean class inheritance is not resolved by this local EJB slice.",
                    )
                    not in existing
                ):
                    declaration_line = node.start_point.row + 1
                    inheritance.append(
                        EJBFact(
                            "ejb_unresolved",
                            qualified_name,
                            None,
                            "Session Bean class inheritance is not resolved by this local EJB slice.",
                            path,
                            declaration_line,
                            declaration_line,
                        )
                    )
                next_types = (*enclosing_types, name)
            else:
                next_types = enclosing_types
            for child in node.named_children:
                visit(child, next_types)

        visit(tree.root_node, ())
    return tuple(inheritance)


def _ejb_implicit_no_interface_facts(facts: Iterable[EJBFact]) -> tuple[EJBFact, ...]:
    session_facts = tuple(fact for fact in facts if fact.kind == "session_bean")
    interface_views = {
        fact.subject for fact in facts if fact.kind == "interface_view"
    }
    declared_contracts = {
        fact.subject
        for fact in facts
        if fact.kind == "type_implements" and fact.target in interface_views
    }
    declared_contracts.update(
        fact.subject
        for fact in facts
        if fact.kind == "bean_view" and fact.target
    )
    explicit_unresolved = tuple(fact for fact in facts if fact.kind == "ejb_unresolved")
    implicit: list[EJBFact] = []
    for session in session_facts:
        if session.path.suffix.lower() != ".java" or session.subject in declared_contracts:
            continue
        if any(
            fact.subject == session.subject
            and fact.value
            and ("LocalBean" in fact.value or "no-interface" in fact.value)
            for fact in explicit_unresolved
        ):
            continue
        implicit.append(
            EJBFact(
                "ejb_unresolved",
                session.subject,
                None,
                "Implicit no-interface EJB view is not resolved by this local EJB slice.",
                session.path,
                session.start_line,
                session.end_line,
            )
        )
    return tuple(implicit)


def _ejb_descriptor_facts(path: Path, source: bytes) -> tuple[EJBFact, ...]:
    text = source.decode("utf-8", errors="replace")
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError as error:
        return (
            EJBFact(
                "ejb_unresolved",
                "",
                None,
                f"Malformed EJB deployment descriptor: {error}.",
                path,
                1,
                max(1, len(text.splitlines())),
            ),
        )
    facts: list[EJBFact] = []
    sessions: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {}
    for session in (element for element in root.iter() if _xml_local_name(element.tag) == "session"):
        ejb_name = _xml_child_text(session, "ejb-name")
        bean_class = _xml_child_text(session, "ejb-class")
        session_type = (_xml_child_text(session, "session-type") or "").lower()
        session_line = _xml_descriptor_line(text, "session", ejb_name)
        subject = bean_class or ejb_name or path.stem
        if not ejb_name or not bean_class or not session_type:
            incomplete_target = (
                _xml_child_text(session, "business-local")
                or _xml_child_text(session, "business-remote")
                or _xml_child_text(session, "local")
                or _xml_child_text(session, "remote")
            )
            facts.append(
                EJBFact(
                    "ejb_unresolved",
                    subject,
                    incomplete_target or bean_class,
                    "EJB descriptor Session Bean is incomplete; ejb-name, ejb-class, and session-type are required.",
                    path,
                    session_line,
                    session_line,
                )
            )
            continue
        if session_type not in {"stateless", "stateful", "singleton"}:
            facts.append(
                EJBFact(
                    "ejb_unresolved",
                    subject,
                    _xml_child_text(session, "business-local")
                    or _xml_child_text(session, "business-remote")
                    or bean_class,
                    f"EJB descriptor session-type {session_type!r} is not supported by this local EJB slice.",
                    path,
                    session_line,
                    session_line,
                )
            )
            continue
        interface_entries: list[tuple[str, str]] = []
        for tag, view in (
            ("business-local", "local"),
            ("business-remote", "remote"),
            ("local", "local"),
            ("remote", "remote"),
        ):
            interface_name = _xml_child_text(session, tag)
            if interface_name:
                interface_entries.append((interface_name, view))
        if not interface_entries:
            facts.append(
                EJBFact(
                    "ejb_unresolved",
                    subject,
                    bean_class,
                    "EJB descriptor Session Bean does not declare a supported local or remote business interface.",
                    path,
                    session_line,
                    session_line,
                )
            )
        sessions[ejb_name or subject] = (bean_class, tuple(interface_entries))
        facts.append(
            EJBFact(
                "session_bean",
                bean_class,
                None,
                session_type,
                path,
                session_line,
                session_line,
            )
        )
        for interface_name, view in interface_entries:
            interface_line = _xml_descriptor_line(text, "business-" + view, interface_name)
            if interface_line == 1:
                interface_line = _xml_descriptor_line(text, view, interface_name)
            facts.append(
                EJBFact(
                    "interface_view",
                    interface_name,
                    None,
                    view,
                    path,
                    interface_line,
                    interface_line,
                )
            )
            facts.append(
                EJBFact(
                    "type_implements",
                    bean_class,
                    interface_name,
                    None,
                    path,
                    interface_line,
                    interface_line,
                )
            )

    for reference in (
        element
        for element in root.iter()
        if _xml_local_name(element.tag) in {"ejb-ref", "ejb-local-ref", "ejb-remote-ref"}
    ):
        reference_tag = _xml_local_name(reference.tag)
        reference_name = _xml_child_text(reference, "ejb-ref-name")
        link = _xml_child_text(reference, "ejb-link")
        interfaces: list[tuple[str, str]] = []
        for tag, view in (
            ("business-local", "local"),
            ("business-remote", "remote"),
            ("local", "local"),
            ("remote", "remote"),
        ):
            interface_name = _xml_child_text(reference, tag)
            if interface_name:
                interfaces.append((interface_name, view))
        linked_session = sessions.get(link or "")
        if not interfaces and linked_session is not None:
            interfaces.extend(linked_session[1])
        reference_line = _xml_descriptor_line(text, reference_tag, reference_name or link)
        if not link:
            facts.append(
                EJBFact(
                    "ejb_unresolved",
                    reference_name or path.stem,
                    interfaces[0][0] if interfaces else None,
                    "EJB descriptor reference has no ejb-link; naming indirection remains unresolved.",
                    path,
                    reference_line,
                    reference_line,
                )
            )
            continue
        if linked_session is None:
            facts.append(
                EJBFact(
                    "ejb_unresolved",
                    reference_name or link,
                    interfaces[0][0] if interfaces else None,
                    f"EJB descriptor reference link {link} does not identify a local Session Bean.",
                    path,
                    reference_line,
                    reference_line,
                )
            )
            continue
        if interfaces and any(
            (interface_name, view) not in set(linked_session[1])
            for interface_name, view in interfaces
        ):
            facts.append(
                EJBFact(
                    "ejb_unresolved",
                    reference_name or link,
                    interfaces[0][0] if interfaces else None,
                    "EJB descriptor reference business interface conflicts with its ejb-link.",
                    path,
                    reference_line,
                    reference_line,
                )
            )
            continue
        if not reference_name or not interfaces:
            facts.append(
                EJBFact(
                    "ejb_unresolved",
                    reference_name or link or path.stem,
                    None,
                    "EJB descriptor reference is incomplete; a reference name and business interface are required.",
                    path,
                    reference_line,
                    reference_line,
                )
            )
            continue
        injection_targets = [
            target
            for target in reference.iter()
            if _xml_local_name(target.tag) == "injection-target"
        ]
        if not injection_targets:
            facts.append(
                EJBFact(
                    "ejb_unresolved",
                    reference_name,
                    interfaces[0][0],
                    "EJB descriptor reference has no explicit injection target; runtime naming resolution remains unresolved.",
                    path,
                    reference_line,
                    reference_line,
                )
            )
            continue
        for injection_target in injection_targets:
            target_class = _xml_child_text(injection_target, "injection-target-class")
            target_name = _xml_child_text(injection_target, "injection-target-name")
            target_line = _xml_descriptor_line(text, "injection-target-name", target_name) or reference_line
            if not target_class or not target_name:
                facts.append(
                    EJBFact(
                        "ejb_unresolved",
                        reference_name,
                        interfaces[0][0],
                        "EJB descriptor injection target is incomplete.",
                        path,
                        target_line,
                        target_line,
                    )
                )
                continue
            for interface_name, _view in interfaces:
                facts.append(
                    EJBFact(
                        "ejb_injection",
                        f"{target_class}#{target_name}",
                        interface_name,
                        f"field:{target_name}",
                        path,
                        target_line,
                        target_line,
                    )
                )
    return tuple(facts)


def _ejb_descriptor_conflicts(facts: Iterable[EJBFact]) -> tuple[EJBFact, ...]:
    grouped: dict[tuple[str, str], list[EJBFact]] = {}
    for fact in facts:
        if fact.kind not in {"session_bean", "interface_view"}:
            continue
        grouped.setdefault((fact.kind, fact.subject), []).append(fact)
    conflicts: list[EJBFact] = []
    for (kind, subject), entries in grouped.items():
        values = {entry.value for entry in entries}
        if len(values) < 2:
            continue
        first = entries[0]
        conflicts.append(
            EJBFact(
                "ejb_unresolved",
                subject,
                None,
                f"Conflicting annotation and descriptor EJB {kind} evidence remains unresolved.",
                first.path,
                first.start_line,
                first.end_line,
            )
        )
    return tuple(conflicts)


def _ejb_conflicting_subjects(facts: Iterable[EJBFact], kind: str) -> set[str]:
    values_by_subject: dict[str, set[str | None]] = {}
    for fact in facts:
        if fact.kind == kind:
            values_by_subject.setdefault(fact.subject, set()).add(fact.value)
    return {
        subject for subject, values in values_by_subject.items() if len(values) > 1
    }


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1].rsplit(":", maxsplit=1)[-1]


def _xml_child_text(element, name: str) -> str | None:
    for child in element:
        if _xml_local_name(child.tag) == name:
            value = "".join(child.itertext()).strip()
            return value or None
    return None


def _xml_descriptor_line(text: str, tag: str, value: str | None = None) -> int:
    if value:
        pattern = re.compile(rf"<(?:(?:[A-Za-z_$][\w$.-]*):)?{re.escape(tag)}\b[^>]*>[^<]*{re.escape(value)}", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            return text.count("\n", 0, match.start()) + 1
    pattern = re.compile(rf"<(?:(?:[A-Za-z_$][\w$.-]*):)?{re.escape(tag)}\b", re.IGNORECASE)
    match = pattern.search(text)
    return text.count("\n", 0, match.start()) + 1 if match else 1


def _ejb_java_facts(path: Path, source: bytes) -> tuple[EJBFact, ...]:
    parser = Parser(Language(tree_sitter_java.language()))
    tree = parser.parse(source)
    package_name = _package_name(tree.root_node, source)
    imports = _java_imports(source, tree.root_node)
    facts: list[EJBFact] = []
    type_kinds = {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "record_declaration": "record",
    }

    def visit(node, enclosing_types: tuple[str, ...]) -> None:
        current_types = enclosing_types
        if node.type in type_kinds:
            name = _node_text(node.child_by_field_name("name"), source)
            qualified_name = _qualified_type_name(package_name, (*enclosing_types, name))
            current_types = (*enclosing_types, name)
            modifiers = next(
                (child for child in node.named_children if child.type == "modifiers"),
                None,
            )
            annotations = _ejb_annotations(modifiers, source, imports)
            declaration_line = node.start_point.row + 1
            if any(annotation[0] in {"Local", "Remote"} for annotation in annotations):
                for annotation_name, annotation_node, arguments in annotations:
                    if annotation_name not in {"Local", "Remote"}:
                        continue
                    view = annotation_name.lower()
                    targets = _ejb_annotation_types(arguments, package_name, imports)
                    if type_kinds[node.type] == "interface":
                        facts.append(
                            EJBFact(
                                "interface_view",
                                qualified_name,
                                None,
                                view,
                                path,
                                annotation_node.start_point.row + 1,
                                annotation_node.end_point.row + 1,
                            )
                        )
                    elif targets:
                        for target in targets:
                            facts.append(
                                EJBFact(
                                    "bean_view",
                                    qualified_name,
                                    target,
                                    view,
                                    path,
                                    annotation_node.start_point.row + 1,
                                    annotation_node.end_point.row + 1,
                                )
                            )
                    else:
                        facts.append(
                            EJBFact(
                                "bean_view",
                                qualified_name,
                                None,
                                view,
                                path,
                                annotation_node.start_point.row + 1,
                                annotation_node.end_point.row + 1,
                            )
                        )
            for annotation_name, annotation_node, _ in annotations:
                if annotation_name in {"Stateless", "Stateful", "Singleton"}:
                    facts.append(
                        EJBFact(
                            "session_bean",
                            qualified_name,
                            None,
                            annotation_name.lower(),
                            path,
                            annotation_node.start_point.row + 1,
                            annotation_node.end_point.row + 1,
                        )
                    )
                elif annotation_name in {"MessageDriven", "LocalBean"}:
                    facts.append(
                        EJBFact(
                            "ejb_unresolved",
                            qualified_name,
                            None,
                            f"WildFly {annotation_name} behavior is not resolved in this EJB slice.",
                            path,
                            annotation_node.start_point.row + 1,
                            annotation_node.end_point.row + 1,
                        )
                    )
            if any(
                annotation_name in {"Stateless", "Stateful", "Singleton"}
                for annotation_name, _annotation_node, _arguments in annotations
            ) and _ejb_type_clause(node, "superclass", source):
                facts.append(
                    EJBFact(
                        "ejb_unresolved",
                        qualified_name,
                        None,
                        "Session Bean class inheritance is not resolved by this local EJB slice.",
                        path,
                        declaration_line,
                        declaration_line,
                    )
                )
            for interface_name in _ejb_type_clause(node, "super_interfaces", source):
                facts.append(
                    EJBFact(
                        "type_implements",
                        qualified_name,
                        _resolve_java_type(interface_name, package_name, imports),
                        None,
                        path,
                        declaration_line,
                        declaration_line,
                    )
                )
            if type_kinds[node.type] == "interface":
                for interface_name in _ejb_type_clause(node, "extends_interfaces", source):
                    facts.append(
                        EJBFact(
                            "type_extends",
                            qualified_name,
                            _resolve_java_type(interface_name, package_name, imports),
                            None,
                            path,
                            declaration_line,
                            declaration_line,
                        )
                    )
        if current_types and node.type == "field_declaration":
            owner = _qualified_type_name(package_name, current_types)
            modifiers = next(
                (child for child in node.named_children if child.type == "modifiers"),
                None,
            )
            field_type = _node_text(node.child_by_field_name("type"), source)
            declarators = tuple(
                child for child in node.named_children if child.type == "variable_declarator"
            )
            for annotation_name, annotation_node, arguments in _ejb_annotations(
                modifiers, source, imports
            ):
                if annotation_name != "EJB":
                    continue
                targets = _ejb_annotation_types(arguments, package_name, imports)
                target = targets[0] if targets else _resolve_java_type(field_type, package_name, imports)
                unsupported = _ejb_unsupported_injection_attributes(arguments)
                for declarator in declarators:
                    field_name = _node_text(declarator.child_by_field_name("name"), source)
                    subject = f"{owner}#{field_name}"
                    if unsupported:
                        facts.append(
                            EJBFact(
                                "ejb_unresolved",
                                subject,
                                target,
                                "EJB Injection Point uses unsupported naming attributes: "
                                + ", ".join(unsupported),
                                path,
                                annotation_node.start_point.row + 1,
                                annotation_node.end_point.row + 1,
                            )
                        )
                    else:
                        facts.append(
                            EJBFact(
                                "ejb_injection",
                                subject,
                                target,
                                f"field:{field_name}",
                                path,
                                annotation_node.start_point.row + 1,
                                annotation_node.end_point.row + 1,
                            )
                        )
        if current_types and node.type == "method_declaration":
            owner = _qualified_type_name(package_name, current_types)
            modifiers = next(
                (child for child in node.named_children if child.type == "modifiers"),
                None,
            )
            method_name = _node_text(node.child_by_field_name("name"), source)
            parameters_node = node.child_by_field_name("parameters")
            parameter_nodes = (
                tuple(parameters_node.named_children)
                if parameters_node is not None
                else ()
            )
            for annotation_name, annotation_node, arguments in _ejb_annotations(
                modifiers, source, imports
            ):
                if annotation_name != "EJB":
                    continue
                unsupported = _ejb_unsupported_injection_attributes(arguments)
                if not method_name.startswith("set") or len(parameter_nodes) != 1:
                    message = "EJB Injection Point method is not a single-argument setter."
                    if unsupported:
                        message += " Unsupported naming attributes: " + ", ".join(unsupported) + "."
                    facts.append(
                        EJBFact(
                            "ejb_unresolved",
                            f"{owner}#{method_name}",
                            None,
                            message,
                            path,
                            annotation_node.start_point.row + 1,
                            annotation_node.end_point.row + 1,
                        )
                    )
                    continue
                parameter_type = _node_text(parameter_nodes[0].child_by_field_name("type"), source)
                targets = _ejb_annotation_types(arguments, package_name, imports)
                target = targets[0] if targets else _resolve_java_type(
                    parameter_type, package_name, imports
                )
                subject = f"{owner}#{method_name}"
                if unsupported:
                    facts.append(
                        EJBFact(
                            "ejb_unresolved",
                            subject,
                            target,
                            "EJB Injection Point uses unsupported naming attributes: "
                            + ", ".join(unsupported),
                            path,
                            annotation_node.start_point.row + 1,
                            annotation_node.end_point.row + 1,
                        )
                    )
                else:
                    property_name = method_name[3:]
                    property_name = property_name[:1].lower() + property_name[1:]
                    facts.append(
                        EJBFact(
                            "ejb_injection",
                            subject,
                            target,
                            f"setter:{property_name}",
                            path,
                            annotation_node.start_point.row + 1,
                            annotation_node.end_point.row + 1,
                        )
                    )
        if current_types and node.type == "method_invocation":
            invocation_name = _node_text(node.child_by_field_name("name"), source)
            if invocation_name in {"lookup", "lookupLink", "doLookup"}:
                facts.append(
                    EJBFact(
                        "ejb_unresolved",
                        _qualified_type_name(package_name, current_types),
                        None,
                        "Arbitrary JNDI lookup is not resolved by this local EJB slice.",
                        path,
                        node.start_point.row + 1,
                        node.end_point.row + 1,
                    )
                )
        for child in node.named_children:
            visit(child, current_types)

    visit(tree.root_node, ())
    return tuple(facts)


def _java_imports(source: bytes, root_node=None) -> dict[str, str]:
    imports: dict[str, str] = {}
    if root_node is None:
        parser = Parser(Language(tree_sitter_java.language()))
        root_node = parser.parse(source).root_node
    for import_node in (
        child for child in root_node.named_children if child.type == "import_declaration"
    ):
        value = _node_text(import_node, source).strip()
        value = re.sub(r"^import\s+(?:static\s+)?", "", value).removesuffix(";").strip()
        if value.endswith(".*"):
            imports[value.removesuffix(".*")] = value
            continue
        simple_name = value.rsplit(".", 1)[-1]
        imports[simple_name] = value
    return imports


def _ejb_annotations(modifiers, source: bytes, imports: dict[str, str]):
    if modifiers is None:
        return ()
    annotations = []
    for annotation in modifiers.named_children:
        if annotation.type not in {"marker_annotation", "annotation"}:
            continue
        name_node = annotation.child_by_field_name("name")
        name = _node_text(name_node, source)
        simple_name = name.rsplit(".", 1)[-1]
        imported = imports.get(simple_name)
        if name.startswith(("javax.ejb.", "jakarta.ejb.")):
            allowed = True
        elif imported is not None:
            allowed = imported in {f"javax.ejb.{simple_name}", f"jakarta.ejb.{simple_name}"}
        else:
            allowed = (
                imports.get("javax.ejb") == "javax.ejb.*"
                or imports.get("jakarta.ejb") == "jakarta.ejb.*"
            )
        if not allowed:
            continue
        text = _node_text(annotation, source)
        opening = text.find("(")
        arguments = text[opening + 1 : -1] if opening >= 0 and text.endswith(")") else ""
        annotations.append((simple_name, annotation, arguments))
    return tuple(annotations)


def _ejb_annotation_types(
    arguments: str, package_name: str, imports: dict[str, str]
) -> tuple[str, ...]:
    values = []
    for value in re.findall(r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\.class", arguments):
        values.append(_resolve_java_type(value, package_name, imports))
    return tuple(values)


def _ejb_unsupported_injection_attributes(arguments: str) -> tuple[str, ...]:
    names = set(re.findall(r"\b([A-Za-z_$][\w$]*)\s*=", arguments))
    return tuple(sorted(names & {"beanName", "mappedName", "lookup", "name"}))


def _ejb_type_clause(node, clause_type: str, source: bytes) -> tuple[str, ...]:
    clause = next((child for child in node.named_children if child.type == clause_type), None)
    if clause is None:
        return ()
    type_list = next((child for child in clause.named_children if child.type == "type_list"), clause)
    return tuple(_node_text(child, source) for child in type_list.named_children)


def _resolve_java_type(value: str, package_name: str, imports: dict[str, str]) -> str:
    value = re.sub(r"\s+", "", value).replace(".class", "")
    value = value.split("<", 1)[0].rstrip("[]")
    if value in imports and not imports[value].endswith(".*"):
        return imports[value]
    if "." in value:
        return value
    return f"{package_name}.{value}" if package_name else value


def _spring_java_facts(
    path: Path, source: bytes, declarations: tuple[JavaDeclaration, ...]
) -> tuple[SpringFact, ...]:
    text = source.decode("utf-8", errors="replace")
    lines = text.splitlines()
    facts: list[SpringFact] = []
    ast_annotations = _spring_ast_annotations(source)
    type_declarations = [
        declaration for declaration in declarations
        if declaration.kind in {"class", "interface", "enum", "record"}
    ]
    def add(
        kind: str,
        subject: str,
        target: str | None,
        value: str | None,
        start_line: int,
        end_line: int | None = None,
        profile: str | None = None,
    ) -> None:
        facts.append(SpringFact(kind, subject, target, value, path, start_line, end_line or start_line, profile))

    for declaration in type_declarations:
        type_line_index = _spring_type_line_index(lines, declaration)
        annotation_start, context = _spring_annotation_context(lines, type_line_index)
        annotation_lines = lines[annotation_start:type_line_index]
        class_annotations = ast_annotations.get(
            ("class_declaration", declaration.start_line, declaration.end_line), ()
        )
        if class_annotations:
            annotation_start = class_annotations[0][1] - 1
            annotation_lines = [annotation for annotation, _, _ in class_annotations]
            context = "\n".join(annotation_lines)
        component_annotation = next(
            (
                (annotation, line, end_line)
                for annotation, line, end_line in class_annotations
                if _simple_annotation_name(annotation)
                in {"Component", "Service", "Repository", "Controller", "RestController", "Configuration"}
            ),
            None,
        )
        component_match = re.search(
            r"@(Component|Service|Repository|Controller|RestController|Configuration)\b",
            context,
        )
        profile = _spring_annotation_profile(context)
        if component_annotation is not None:
            add(
                "spring_component",
                declaration.qualified_name,
                declaration.qualified_name,
                _simple_annotation_name(component_annotation[0]),
                component_annotation[1],
                profile=profile,
            )
        elif component_match:
            add(
                "spring_component",
                declaration.qualified_name,
                declaration.qualified_name,
                component_match.group(1),
                annotation_start + context[:component_match.start()].count("\n") + 1,
                profile=profile,
            )
        configuration_properties = re.search(
            r"@ConfigurationProperties\s*\(\s*(?:(?:prefix|value)\s*=\s*)?['\"]([^'\"]+)['\"]",
            context,
        )
        if configuration_properties:
            line = annotation_start + context[:configuration_properties.start()].count("\n") + 1
            add("spring_property_consumer", declaration.qualified_name, configuration_properties.group(1), "prefix", line, profile=profile)
        unsupported_annotation = next(
            (
                (annotation, line, end_line)
                for annotation, line, end_line in class_annotations
                if _simple_annotation_name(annotation) in {"ComponentScan", "Import"}
                or _simple_annotation_name(annotation).startswith("Conditional")
            ),
            None,
        )
        if unsupported_annotation is None:
            unsupported_match = re.search(
                r"@ComponentScan\b|@Import\b|@Conditional\w*\b", context
            )
            if unsupported_match:
                unsupported_annotation = (
                    "",
                    annotation_start + context[:unsupported_match.start()].count("\n") + 1,
                    annotation_start + context[:unsupported_match.start()].count("\n") + 1,
                )
        if unsupported_annotation is not None:
            line = unsupported_annotation[1]
            add("spring_unresolved", "", None,
                "Spring component scanning, imports, or conditional configuration was not resolved.", line, profile=profile)
        if profile and _spring_profile_is_expression(profile):
            profile_match = re.search(r"@(?:[A-Za-z_$][\w$.]*\.)?Profile\b", context)
            line = annotation_start + context[:profile_match.start()].count("\n") + 1 if profile_match else annotation_start + 1
            add("spring_unresolved", declaration.qualified_name, None,
                "Spring profile expression was not resolved.", line)

        for annotation, line, _ in class_annotations:
            annotation_name = _simple_annotation_name(annotation)
            if annotation_name == "Primary":
                add(
                    "spring_unresolved",
                    declaration.qualified_name,
                    None,
                    "Spring @Primary bean selection was not resolved.",
                    line,
                    profile=profile,
                )
            elif annotation_name in _SPRING_PROXY_ANNOTATIONS:
                configuration_level = annotation_name.startswith("Enable")
                add(
                    "spring_unresolved",
                    "" if configuration_level else declaration.qualified_name,
                    None,
                    (
                        f"Spring proxy or advice behavior declared by {declaration.qualified_name} was not resolved."
                        if configuration_level
                        else "Spring proxy or advice behavior was not resolved."
                    ),
                    line,
                    profile=profile,
                )

        class_lines = lines[declaration.start_line - 1:declaration.end_line]
        for offset, line in enumerate(class_lines, declaration.start_line):
            value_match = re.search(r"@Value\s*\(\s*['\"]\$\{([^}\s]+)", line)
            if value_match:
                property_key = value_match.group(1).split(":", maxsplit=1)[0]
                add("spring_property_consumer", declaration.qualified_name, property_key, "value", offset, profile=profile)
            if "@Value" in line and "#{" in line:
                add("spring_unresolved", declaration.qualified_name, None,
                    "Spring SpEL property expression was not resolved.", offset, profile=profile)
            if "System.getenv(" in line:
                add("spring_unresolved", "", None,
                    "Environment-variable configuration override was not resolved.", offset)

        methods = [
            method for method in declarations
            if method.kind == "method" and method.path == path and method.start_line >= declaration.start_line and method.end_line <= declaration.end_line
        ]
        for method in methods:
            method_type_line_index = method.start_line - 1
            for candidate_line in range(method_type_line_index, min(len(lines), method_type_line_index + 8)):
                if re.search(rf"\b{re.escape(method.name)}\s*\(", lines[candidate_line]):
                    method_type_line_index = candidate_line
                    break
            method_annotations = ast_annotations.get(
                ("method_declaration", method.start_line, method.end_line), ()
            )
            context_start = max(declaration.start_line - 1, method_type_line_index - 6)
            method_context = "\n".join(lines[context_start:method_type_line_index])
            if method_annotations:
                method_context = "\n".join(annotation for annotation, _, _ in method_annotations)
            bean_annotation = next(
                (
                    (annotation, line, end_line)
                    for annotation, line, end_line in method_annotations
                    if _simple_annotation_name(annotation) == "Bean"
                ),
                None,
            )
            if bean_annotation is not None or (not method_annotations and "@Bean" in method_context):
                method_line = lines[method_type_line_index] if method_type_line_index < len(lines) else ""
                return_match = re.search(
                    rf"(?:public|protected|private)?\s*(?:static\s+)?((?:[A-Za-z_$][\w$]*\.)*[A-Za-z_$][\w$]*(?:\s*<[^>]+>)?(?:\[\])?)\s+{re.escape(method.name)}\s*\(",
                    method_line,
                )
                target = _simple_java_type(return_match.group(1)) if return_match else None
                if target:
                    annotation_line = bean_annotation[1] if bean_annotation else context_start + method_context.count("\n") + 1
                    add("spring_bean", method.qualified_name, target, "@Bean", annotation_line,
                        profile=_spring_annotation_profile(method_context))
                    if any(
                        _simple_annotation_name(annotation) == "Primary"
                        for annotation, _, _ in method_annotations
                    ):
                        add(
                            "spring_unresolved",
                            target,
                            None,
                            "Spring @Primary bean selection was not resolved.",
                            next(
                                line
                                for annotation, line, _ in method_annotations
                                if _simple_annotation_name(annotation) == "Primary"
                            ),
                            profile=_spring_annotation_profile(method_context),
                        )
            for annotation, line, _ in method_annotations:
                annotation_name = _simple_annotation_name(annotation)
                if annotation_name in _SPRING_PROXY_ANNOTATIONS:
                    configuration_level = annotation_name.startswith("Enable")
                    add(
                        "spring_unresolved",
                        "" if configuration_level else declaration.qualified_name,
                        None,
                        (
                            f"Spring proxy or advice behavior declared by {declaration.qualified_name} was not resolved."
                            if configuration_level
                            else "Spring proxy or advice behavior was not resolved."
                        ),
                        line,
                        profile=profile,
                    )

    for declaration in type_declarations:
        class_text = "\n".join(lines[declaration.start_line - 1:declaration.end_line])
        managed = declaration.is_test or any(
            fact.kind == "spring_component" and fact.subject == declaration.qualified_name
            for fact in facts
        )
        owner_profile = _spring_annotation_profile(
            "\n".join(annotation for annotation, _, _ in ast_annotations.get(
                ("class_declaration", declaration.start_line, declaration.end_line), ()
            ))
            or _spring_annotation_context(lines, _spring_type_line_index(lines, declaration))[1]
        )
        if managed:
            for (region_type, start_line, end_line), field_annotations in ast_annotations.items():
                if region_type != "field_declaration" or not (
                    declaration.start_line <= start_line <= declaration.end_line
                ):
                    continue
                annotation_names = {
                    _simple_annotation_name(annotation)
                    for annotation, _, _ in field_annotations
                }
                injection_annotations = {"Autowired", "Inject", "Resource"}
                if not annotation_names & injection_annotations:
                    continue
                field_text = "\n".join(lines[start_line - 1:end_line])
                type_match = re.search(
                    r"(?:private|protected|public)?\s*(?:final\s+)?"
                    r"(?P<type>[A-Za-z_$][\w$.]*(?:\s*<[^;=]+>)?(?:\[\])?)\s+"
                    r"[A-Za-z_$][\w$]*\s*(?:=|;)",
                    field_text,
                )
                if type_match is None:
                    continue
                raw_type = type_match.group("type")
                target = _simple_java_type(raw_type)
                injection_line = next(
                    line
                    for annotation, line, _ in field_annotations
                    if _simple_annotation_name(annotation) in injection_annotations
                )
                resource_name = any(
                    _simple_annotation_name(annotation) == "Resource"
                    and re.search(r"\b(?:name|mappedName)\s*=", annotation)
                    for annotation, _, _ in field_annotations
                )
                if "Qualifier" in annotation_names or resource_name:
                    selection = "qualifier" if "Qualifier" in annotation_names else "named @Resource"
                    add(
                        "spring_unresolved",
                        target,
                        None,
                        f"Spring {selection} bean selection was not resolved.",
                        injection_line,
                        profile=owner_profile,
                    )
                if _is_spring_collection_type(raw_type):
                    add(
                        "spring_unresolved",
                        _spring_generic_argument(raw_type) or target,
                        None,
                        "Spring collection injection was not resolved.",
                        injection_line,
                        profile=owner_profile,
                    )
                if "Qualifier" not in annotation_names and not resource_name and not _is_spring_collection_type(raw_type):
                    add("spring_injection", declaration.qualified_name, target, "field", injection_line, profile=owner_profile)
        constructor_declarations = [
            constructor for constructor in declarations
            if constructor.kind == "constructor" and constructor.path == path
            and constructor.qualified_name.startswith(declaration.qualified_name + "#")
        ]
        if managed and len(constructor_declarations) == 1:
            constructor = constructor_declarations[0]
            constructor_text = "\n".join(
                lines[constructor.start_line - 1:min(len(lines), constructor.start_line + 8)]
            )
            constructor_match = re.search(
                rf"\b{re.escape(declaration.name)}\s*\(", constructor_text
            )
            parameter_text = (
                _parenthesized_content(constructor_text, constructor_match.end() - 1)
                if constructor_match
                else None
            )
            if parameter_text is not None:
                for parameter_type, parameter_annotations in _spring_constructor_parameters(
                    parameter_text
                ):
                    annotation_names = {
                        _simple_annotation_name(annotation)
                        for annotation in parameter_annotations
                    }
                    resource_name = any(
                        _simple_annotation_name(annotation) == "Resource"
                        and re.search(r"\b(?:name|mappedName)\s*=", annotation)
                        for annotation in parameter_annotations
                    )
                    if "Qualifier" in annotation_names or resource_name:
                        selection = "qualifier" if "Qualifier" in annotation_names else "named @Resource"
                        add(
                            "spring_unresolved",
                            parameter_type,
                            None,
                            f"Spring constructor {selection} bean selection was not resolved.",
                            constructor.start_line,
                            profile=owner_profile,
                        )
                    elif _is_spring_collection_type(parameter_type):
                        add(
                            "spring_unresolved",
                            _spring_generic_argument(parameter_type) or parameter_type,
                            None,
                            "Spring collection injection was not resolved.",
                            constructor.start_line,
                            profile=owner_profile,
                        )
                    else:
                        add(
                            "spring_injection",
                            declaration.qualified_name,
                            _simple_java_type(parameter_type),
                            "constructor",
                            constructor.start_line,
                            profile=owner_profile,
                        )

        if declaration.is_test:
            class_annotations = ast_annotations.get(
                ("class_declaration", declaration.start_line, declaration.end_line), ()
            )
            for annotation, line, _ in class_annotations:
                if _simple_annotation_name(annotation) not in {
                    "SpringBootTest",
                    "ContextConfiguration",
                    "DataJpaTest",
                    "SpringJUnitConfig",
                    "SpringJUnitWebConfig",
                    "WebMvcTest",
                }:
                    continue
                classes = re.findall(r"([A-Za-z_$][\w$]*)\.class", annotation)
                for target in classes:
                    add("spring_test", declaration.qualified_name, target, "test context", line, profile=owner_profile)

    return tuple(facts)


def _spring_configuration_facts(path: Path, source: bytes) -> tuple[SpringFact, ...]:
    text = source.decode("utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".properties":
        return _spring_properties_facts(path, text.splitlines())
    if suffix in {".yml", ".yaml"}:
        return _spring_yaml_facts(path, text.splitlines())
    return _spring_xml_facts(path, text.splitlines())


def _spring_properties_facts(path: Path, lines: list[str]) -> tuple[SpringFact, ...]:
    profile = _spring_file_profile(path)
    facts: list[SpringFact] = []
    for line_number, line in enumerate(lines, 1):
        match = re.match(r"\s*([^#!\s][^:=\s]*)\s*[:=]\s*(.*?)\s*$", line)
        if match:
            facts.append(SpringFact("spring_property_source", match.group(1), None, match.group(2), path, line_number, line_number, profile))
            if re.search(r"\$\{[A-Z][A-Z0-9_]*(?::[^}]*)?\}", match.group(2)):
                facts.append(SpringFact("spring_unresolved", "", None, "Environment-variable configuration override was not resolved.", path, line_number, line_number, profile))
    return tuple(facts)


def _spring_yaml_facts(path: Path, lines: list[str]) -> tuple[SpringFact, ...]:
    profile = _spring_file_profile(path)
    document_profile = profile
    facts: list[SpringFact] = []
    parents: list[tuple[int, str]] = []
    for line_number, line in enumerate(lines, 1):
        if line.strip() == "---":
            parents.clear()
            document_profile = None
            continue
        if not line.strip() or line.lstrip().startswith("#") or line.lstrip().startswith("-"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        match = re.match(r"\s*([^:#]+):(?:\s*(.*?))?\s*$", line)
        if not match:
            continue
        key, value = match.group(1).strip(), (match.group(2) or "").strip()
        while parents and parents[-1][0] >= indent:
            parents.pop()
        full_key = ".".join([parent[1] for parent in parents] + [key])
        if value:
            value = value.strip("'\"")
            if full_key == "spring.config.activate.on-profile":
                document_profile = value
                continue
            facts.append(SpringFact("spring_property_source", full_key, None, value, path, line_number, line_number, document_profile))
            if re.search(r"\$\{[A-Z][A-Z0-9_]*(?::[^}]*)?\}", value):
                facts.append(SpringFact("spring_unresolved", "", None, "Environment-variable configuration override was not resolved.", path, line_number, line_number, document_profile))
        else:
            parents.append((indent, key))
    return tuple(facts)


def _spring_xml_facts(path: Path, lines: list[str]) -> tuple[SpringFact, ...]:
    text = "\n".join(lines)
    facts: list[SpringFact] = []
    def line_number(position: int) -> int:
        return text.count("\n", 0, position) + 1

    bean_ranges: list[tuple[int, int, str]] = []
    for match in re.finditer(r"<bean\b(?P<attributes>[^>]*?)(?P<close>/?>)", text, re.DOTALL):
        attributes = match.group("attributes")
        class_match = re.search(r"\bclass\s*=\s*['\"]([^'\"]+)['\"]", attributes)
        id_match = re.search(r"\b(?:id|name)\s*=\s*['\"]([^'\"]+)['\"]", attributes)
        if class_match is None:
            continue
        subject = id_match.group(1) if id_match else class_match.group(1)
        end = match.end()
        if match.group("close") != "/>":
            closing = re.search(r"</bean\s*>", text[match.end():], re.DOTALL)
            end = match.end() + closing.end() if closing else len(text)
        bean_ranges.append((match.start(), end, subject))
        profile = _spring_xml_profile_at(text, match.start())
        start_line = line_number(match.start())
        facts.append(SpringFact("spring_xml_bean", subject, class_match.group(1), "xml bean", path, start_line, start_line, profile))

    for match in re.finditer(r"<property\b(?P<attributes>[^>]*?)(?:/?>)", text, re.DOTALL):
        container = next(
            (bean for bean_start, bean_end, bean in bean_ranges if bean_start < match.start() < bean_end),
            None,
        )
        if container is None:
            continue
        attributes = match.group("attributes")
        start_line = line_number(match.start())
        profile = _spring_xml_profile_at(text, match.start())
        ref_match = re.search(r"\bref\s*=\s*['\"]([^'\"]+)['\"]", attributes)
        value_match = re.search(r"\bvalue\s*=\s*['\"]\$\{([^}]+)\}", attributes)
        if ref_match:
            facts.append(SpringFact("spring_xml_ref", container, ref_match.group(1), "xml ref", path, start_line, start_line, profile))
        if value_match:
            facts.append(SpringFact("spring_property_consumer", container, value_match.group(1), "xml value", path, start_line, start_line, profile))

    for match in re.finditer(
        r"<[^>]*property-placeholder\b[^>]*\blocation\s*=\s*['\"]([^'\"]+)['\"]",
        text,
        re.DOTALL,
    ):
        start_line = line_number(match.start())
        facts.append(SpringFact("spring_property_placeholder", "", match.group(1), "xml placeholder", path, start_line, start_line, _spring_xml_profile_at(text, match.start())))
    for match in re.finditer(r"<[^>]*(?:component-scan|import)\b", text, re.DOTALL):
        start_line = line_number(match.start())
        facts.append(SpringFact("spring_unresolved", "", None, "Spring XML scan or import indirection was not resolved.", path, start_line, start_line, _spring_xml_profile_at(text, match.start())))
    for match in re.finditer(
        r"<beans\b[^>]*\bprofile\s*=\s*['\"]([^'\"]+)['\"]",
        text,
        re.DOTALL,
    ):
        profile = match.group(1).strip()
        if _spring_profile_is_expression(profile):
            start_line = line_number(match.start())
            facts.append(
                SpringFact(
                    "spring_unresolved",
                    "",
                    None,
                    "Spring XML profile expression was not resolved.",
                    path,
                    start_line,
                    start_line,
                )
            )
    try:
        ElementTree.fromstring(text)
    except ElementTree.ParseError:
        facts.append(SpringFact("spring_unresolved", "", None, "Malformed Spring XML was not fully indexed.", path, 1, 1))
    return tuple(facts)


def _spring_xml_profile_at(text: str, position: int) -> str | None:
    profiles: list[str] = []
    for match in re.finditer(r"<beans\b[^>]*\bprofile\s*=\s*['\"]([^'\"]+)['\"]", text[:position], re.DOTALL):
        profiles.append(match.group(1).strip())
    profile = profiles[-1] if profiles else None
    return profile


def _spring_annotation_profile(context: str) -> str | None:
    match = re.search(
        r"@(?:[A-Za-z_$][\w$.]*\.)?Profile\s*\((?P<arguments>[^)]*)\)",
        context,
        re.DOTALL,
    )
    if match is None:
        return None
    arguments = match.group("arguments").strip()
    values = re.findall(r"[\"']([^\"']+)[\"']", arguments)
    if len(values) == 1 and not _spring_profile_is_expression(arguments):
        return values[0].strip()
    return arguments


def _spring_profile_is_expression(profile: str) -> bool:
    return any(operator in profile for operator in ("!", "&", "|", "{", "}", ","))


def _spring_placeholder_matches(location: str | None, path: Path) -> bool:
    if not location:
        return False
    for candidate in re.split(r"\s*,\s*", location):
        normalized = re.sub(r"^(?:classpath\*:|classpath:|file:)", "", candidate.strip())
        normalized = normalized.lstrip("/")
        if normalized and path.as_posix().endswith(normalized):
            return True
    return False


def _spring_type_line_index(lines: list[str], declaration: JavaDeclaration) -> int:
    type_line_index = declaration.start_line - 1
    for candidate_line in range(type_line_index, min(len(lines), type_line_index + 8)):
        if re.search(
            rf"\b(?:class|interface|enum|record)\s+{re.escape(declaration.name)}\b",
            lines[candidate_line],
        ):
            return candidate_line
    return type_line_index


def _spring_annotation_context(lines: list[str], type_line_index: int) -> tuple[int, str]:
    index = type_line_index - 1
    saw_annotation = False
    while index >= 0:
        stripped = lines[index].strip()
        if not stripped:
            if saw_annotation:
                index -= 1
                continue
            break
        if stripped.startswith("@"):
            saw_annotation = True
            index -= 1
            continue
        if saw_annotation and (
            stripped.endswith(")")
            or stripped.startswith(("{", "}", "value", "prefix"))
        ):
            index -= 1
            continue
        break
    start = index + 1
    return start, "\n".join(lines[start:type_line_index])


def _spring_ast_annotations(
    source: bytes,
) -> dict[tuple[str, int, int], tuple[tuple[str, int, int], ...]]:
    parser = Parser(Language(tree_sitter_java.language()))
    tree = parser.parse(source)
    regions: dict[tuple[str, int, int], tuple[tuple[str, int, int], ...]] = {}
    region_types = {"class_declaration", "method_declaration", "constructor_declaration", "field_declaration"}

    def visit(node) -> None:
        if node.type in region_types:
            annotations: list[tuple[str, int, int]] = []
            for child in node.named_children:
                annotation_nodes = (
                    child.named_children
                    if child.type == "modifiers"
                    else (child,) if child.type in {"marker_annotation", "annotation"} else ()
                )
                for annotation_node in annotation_nodes:
                    if annotation_node.type not in {"marker_annotation", "annotation"}:
                        continue
                    annotation = _node_text(annotation_node, source)
                    annotations.append(
                        (annotation, annotation_node.start_point.row + 1, annotation_node.end_point.row + 1)
                    )
            if annotations:
                regions[(node.type, node.start_point.row + 1, node.end_point.row + 1)] = tuple(annotations)
        for child in node.named_children:
            visit(child)

    visit(tree.root_node)
    return regions


def _simple_annotation_name(annotation: str) -> str:
    match = re.match(r"@([A-Za-z_$][\w$.]*)", annotation.strip())
    return match.group(1).rsplit(".", maxsplit=1)[-1] if match else ""


_SPRING_PROXY_ANNOTATIONS = frozenset(
    {
        "After",
        "AfterReturning",
        "AfterThrowing",
        "Around",
        "Async",
        "Before",
        "CacheEvict",
        "CachePut",
        "Cacheable",
        "EnableAspectJAutoProxy",
        "EnableCaching",
        "EnableTransactionManagement",
        "Retryable",
        "Transactional",
    }
)


def _spring_file_profile(path: Path) -> str | None:
    match = re.match(r"application-([^./]+)\.(?:properties|ya?ml)$", path.name, re.IGNORECASE)
    return match.group(1) if match else None


def _simple_java_type(value: str) -> str:
    return re.sub(r"\s+", "", value).split("<", maxsplit=1)[0].replace("[]", "")


def _is_spring_collection_type(value: str) -> bool:
    base_type = _simple_java_type(value).rsplit(".", maxsplit=1)[-1]
    return (
        "<" in value
        or "[]" in value
        or base_type in {"Collection", "Iterable", "List", "Map", "Set", "Stream"}
    )


def _spring_generic_argument(value: str) -> str | None:
    match = re.search(r"<\s*([A-Za-z_$][\w$.]*)", value)
    return _simple_java_type(match.group(1)) if match else None


def _spring_parameter_types(value: str) -> tuple[str, ...]:
    types: list[str] = []
    for parameter in _split_spring_parameters(value):
        tokens = parameter.strip().split()
        if len(tokens) >= 2:
            types.append(_simple_java_type(" ".join(tokens[:-1])))
    return tuple(types)


def _spring_constructor_parameters(value: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    parameters: list[tuple[str, tuple[str, ...]]] = []
    for parameter in _split_spring_parameters(value):
        match = re.fullmatch(
            r"(?P<annotations>(?:@[A-Za-z_$][\w$.]*(?:\([^)]*\))?\s*)*)"
            r"(?P<type>[A-Za-z_$][\w$.]*(?:\s*<[^>]+>)?(?:\[\])?)\s+"
            r"[A-Za-z_$][\w$]*",
            parameter.strip(),
        )
        if match is None:
            continue
        annotations = tuple(
            re.findall(r"@[A-Za-z_$][\w$.]*(?:\([^)]*\))?", match.group("annotations"))
        )
        parameters.append((match.group("type"), annotations))
    if parameters:
        return tuple(parameters)
    return tuple((parameter_type, ()) for parameter_type in _spring_parameter_types(value))


def _split_spring_parameters(value: str) -> tuple[str, ...]:
    parameters: list[str] = []
    start = 0
    nesting = 0
    for index, character in enumerate(value):
        if character in "(<[":
            nesting += 1
        elif character in ")>]":
            nesting = max(0, nesting - 1)
        elif character == "," and nesting == 0:
            parameters.append(value[start:index])
            start = index + 1
    parameters.append(value[start:])
    return tuple(parameter for parameter in parameters if parameter.strip())


def _parenthesized_content(value: str, opening_index: int) -> str | None:
    nesting = 0
    for index in range(opening_index, len(value)):
        character = value[index]
        if character == "(":
            nesting += 1
        elif character == ")":
            nesting -= 1
            if nesting == 0:
                return value[opening_index + 1:index]
    return None


def _package_name(root_node, source: bytes) -> str:
    for child in root_node.children:
        if child.type != "package_declaration":
            continue
        declaration = _node_text(child, source)
        match = re.match(r"package\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*;", declaration)
        return match.group(1) if match else ""
    return ""


def _collect_java_facts(
    node,
    source: bytes,
    path: Path,
    package_name: str,
    is_test: bool,
    enclosing_types: tuple[str, ...],
    caller: str | None,
    declarations: list[JavaDeclaration],
    invocations: list[JavaInvocation],
) -> None:
    type_kinds = {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "annotation_type_declaration": "annotation",
        "record_declaration": "record",
    }
    cursor = node.walk()
    contexts = [(enclosing_types, caller)]
    while True:
        current = cursor.node
        current_types, current_caller = contexts[-1]
        child_types = current_types
        child_caller = current_caller
        if current.type in type_kinds:
            name = _node_text(current.child_by_field_name("name"), source)
            qualified_name = _qualified_type_name(package_name, (*current_types, name))
            declarations.append(
                JavaDeclaration(
                    type_kinds[current.type],
                    name,
                    qualified_name,
                    qualified_name,
                    path,
                    current.start_point.row + 1,
                    current.end_point.row + 1,
                    is_test,
                    False,
                )
            )
            child_types = (*current_types, name)
        elif current.type in {"method_declaration", "constructor_declaration"}:
            name = _node_text(current.child_by_field_name("name"), source)
            kind = "constructor" if current.type == "constructor_declaration" else "method"
            owner = _qualified_type_name(package_name, current_types)
            qualified_name = f"{owner}#{name}" if owner else name
            signature = f"{qualified_name}({_parameter_types(current, source)})"
            declarations.append(
                JavaDeclaration(
                    kind,
                    name,
                    qualified_name,
                    signature,
                    path,
                    current.start_point.row + 1,
                    current.end_point.row + 1,
                    is_test,
                    _has_private_modifier(current, source),
                )
            )
            child_caller = qualified_name
        elif current.type == "method_invocation":
            name = _node_text(current.child_by_field_name("name"), source)
            receiver_node = current.child_by_field_name("object")
            receiver = _node_text(receiver_node, source) if receiver_node is not None else None
            arguments_node = current.child_by_field_name("arguments")
            invocations.append(
                JavaInvocation(
                    name,
                    receiver,
                    current_caller,
                    path,
                    current.start_point.row + 1,
                    current.end_point.row + 1,
                    is_test,
                    len(arguments_node.named_children) if arguments_node is not None else None,
                )
            )
        if cursor.goto_first_child():
            contexts.append((child_types, child_caller))
            continue
        while not cursor.goto_next_sibling():
            if not cursor.goto_parent():
                return
            contexts.pop()


def _qualified_type_name(package_name: str, type_names: tuple[str, ...]) -> str:
    segments = (*((package_name,) if package_name else ()), *type_names)
    return ".".join(segments)


def _node_text(node, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _parameter_types(node, source: bytes) -> str:
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        return ""
    types = []
    for parameter in parameters.named_children:
        parameter_type = parameter.child_by_field_name("type")
        if parameter_type is not None:
            types.append(_node_text(parameter_type, source))
    return ", ".join(types)


def _has_private_modifier(node, source: bytes) -> bool:
    return any(
        child.type == "modifiers" and re.search(r"\bprivate\b", _node_text(child, source))
        for child in node.children
    )


def _first_parse_failure(root_node, path: Path) -> ParseFailure | None:
    if not root_node.has_error:
        return None
    stack = [root_node]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            return ParseFailure(
                path, node.start_point.row + 1, node.start_point.column + 1,
                "Java syntax error",
            )
        stack.extend(reversed(node.children))
    return ParseFailure(path, root_node.start_point.row + 1, root_node.start_point.column + 1, "Java syntax error")


def _is_excluded(relative_path: Path) -> bool:
    return any(part in EXCLUDED_DIRECTORY_NAMES for part in relative_path.parts[:-1])


def _snapshot(root: Path) -> IndexSnapshot:
    commit = _git_output(root, "rev-parse", "HEAD")
    status = _git_output(
        root, "status", "--porcelain", "--", ".", ":(exclude).changescope"
    )
    if commit is None:
        commit = _head_commit(root)
    if commit is None:
        return IndexSnapshot(root, None, "unavailable")
    if status is None:
        return IndexSnapshot(root, commit, "unknown")
    return IndexSnapshot(root, commit, "dirty" if status else "clean")


def _git_output(root: Path, *arguments: str) -> str | None:
    try:
        completed = subprocess.run(
            ("git", "-c", "safe.directory=*", "-C", str(root), *arguments),
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _head_commit(root: Path) -> str | None:
    git_directory = _git_directory(root)
    if git_directory is None:
        return None
    try:
        head = (git_directory / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head.startswith("ref: "):
        return head or None
    try:
        return (git_directory / head.removeprefix("ref: ")).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _git_directory(root: Path) -> Path | None:
    git_entry = root / ".git"
    if git_entry.is_dir():
        return git_entry
    try:
        reference = git_entry.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not reference.startswith("gitdir: "):
        return None
    return (root / reference.removeprefix("gitdir: ")).resolve()


def _write_index(result: IndexResult) -> None:
    database_directory = result.snapshot.repository_root / ".changescope"
    database_directory.mkdir(exist_ok=True)
    connection = sqlite3.connect(database_directory / "index.sqlite")
    try:
        with connection:
            _replace_index_contents(connection, result)
    finally:
        connection.close()


def _replace_index_contents(
    connection: sqlite3.Connection,
    result: IndexResult,
) -> None:
    _initialize_index_schema(connection)
    connection.execute("DELETE FROM metadata")
    connection.execute("DELETE FROM source_files")
    connection.execute("DELETE FROM java_declarations")
    connection.execute("DELETE FROM java_invocations")
    connection.execute("DELETE FROM parse_failures")
    connection.execute("DELETE FROM spring_facts")
    connection.execute("DELETE FROM ejb_facts")
    connection.execute("DELETE FROM quarkus_build_facts")
    connection.execute("DELETE FROM quarkus_config_facts")
    connection.execute("DELETE FROM quarkus_cdi_facts")
    connection.execute("DELETE FROM quarkus_rest_facts")
    connection.execute("DELETE FROM quarkus_security_facts")
    connection.execute("DELETE FROM quarkus_test_facts")
    connection.execute("DELETE FROM quarkus_native_facts")
    connection.execute("DELETE FROM quarkus_boundary_facts")
    connection.execute("DELETE FROM soap_facts")
    connection.execute("DELETE FROM vbnet_declarations")
    connection.execute("DELETE FROM vbnet_invocations")
    connection.execute("DELETE FROM vbnet_facts")
    _write_metadata(connection, result.snapshot, result.source_roots)
    indexed_paths = tuple(dict.fromkeys((*result.indexed_files, *getattr(result, "vbnet_files", ()), *result.configuration_files)))
    indexed_file_hashes = dict(getattr(result, "indexed_file_hashes", ()))
    connection.executemany(
        "INSERT INTO source_files(path, status, content_hash) VALUES (?, ?, ?)",
        (
            (
                str(path),
                "indexed",
                indexed_file_hashes[path]
                if path in indexed_file_hashes
                else _file_content_hash(result.snapshot.repository_root / path),
            )
            for path in indexed_paths
        ),
    )
    connection.executemany(
        "INSERT INTO source_files(path, status, content_hash) VALUES (?, ?, ?)",
        ((str(path), "unreadable", "") for path in result.read_failures),
    )
    _insert_java_facts(connection, result.declarations, result.invocations, result.parse_failures)
    _insert_spring_facts(connection, result.spring_facts)
    _insert_ejb_facts(connection, result.ejb_facts)
    _insert_quarkus_build_facts(connection, result.quarkus_build_facts)
    _insert_quarkus_config_facts(connection, result.quarkus_config_facts)
    _insert_quarkus_cdi_facts(connection, result.quarkus_cdi_facts)
    _insert_quarkus_rest_facts(connection, result.quarkus_rest_facts)
    _insert_quarkus_route_facts(connection, result.quarkus_route_facts)
    _insert_quarkus_security_facts(connection, result.quarkus_security_facts)
    _insert_quarkus_test_facts(connection, result.quarkus_test_facts)
    _insert_quarkus_native_facts(connection, result.quarkus_native_facts)
    _insert_quarkus_boundary_facts(connection, result.quarkus_boundary_facts)
    _insert_soap_facts(connection, result.soap_facts)
    _insert_vbnet_facts(
        connection,
        getattr(result, "vbnet_declarations", ()),
        getattr(result, "vbnet_invocations", ()),
        getattr(result, "vbnet_facts", ()),
    )


def _initialize_index_schema(connection: sqlite3.Connection) -> bool:
    schema_was_current = _index_schema_is_current(connection)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS source_files (path TEXT PRIMARY KEY, status TEXT NOT NULL, content_hash TEXT NOT NULL DEFAULT '')"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS java_declarations (
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        qualified_name TEXT NOT NULL,
        signature TEXT NOT NULL,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        is_test INTEGER NOT NULL,
        is_private INTEGER NOT NULL DEFAULT 0
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS java_invocations (
        name TEXT NOT NULL,
        receiver TEXT,
        caller TEXT,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        is_test INTEGER NOT NULL,
        argument_count INTEGER
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS parse_failures (
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        start_column INTEGER NOT NULL,
        message TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS spring_facts (
        kind TEXT NOT NULL,
        subject TEXT NOT NULL,
        target TEXT,
        value TEXT,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        profile TEXT
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS ejb_facts (
        kind TEXT NOT NULL,
        subject TEXT NOT NULL,
        target TEXT,
        value TEXT,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS quarkus_build_facts (
        kind TEXT NOT NULL,
        subject TEXT NOT NULL,
        target TEXT,
        value TEXT,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        profile TEXT
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS quarkus_config_facts (
        kind TEXT NOT NULL,
        subject TEXT NOT NULL,
        target TEXT,
        value TEXT,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        profile TEXT,
        is_build_time INTEGER NOT NULL DEFAULT 0
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS quarkus_cdi_facts (
        kind TEXT NOT NULL,
        subject TEXT NOT NULL,
        target TEXT,
        value TEXT,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        scope TEXT
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS quarkus_rest_facts (
        kind TEXT NOT NULL,
        subject TEXT NOT NULL,
        target TEXT,
        value TEXT,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        flavor TEXT
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS quarkus_route_facts (
        kind TEXT NOT NULL,
        subject TEXT NOT NULL,
        target TEXT,
        value TEXT,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        flavor TEXT
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS quarkus_security_facts (
        kind TEXT NOT NULL,
        subject TEXT NOT NULL,
        target TEXT,
        value TEXT,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        policy TEXT
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS quarkus_test_facts (
        kind TEXT NOT NULL,
        subject TEXT NOT NULL,
        target TEXT,
        value TEXT,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        flavor TEXT
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS quarkus_native_facts (
        kind TEXT NOT NULL,
        subject TEXT NOT NULL,
        target TEXT,
        value TEXT,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        scope TEXT
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS quarkus_boundary_facts (
        kind TEXT NOT NULL,
        subject TEXT NOT NULL,
        target TEXT,
        value TEXT,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        category TEXT
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS soap_facts (
        kind TEXT NOT NULL,
        subject TEXT NOT NULL,
        target TEXT,
        value TEXT,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        namespace TEXT
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS vbnet_declarations (
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        qualified_name TEXT NOT NULL,
        signature TEXT NOT NULL,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        is_test INTEGER NOT NULL,
        is_private INTEGER NOT NULL DEFAULT 0,
        language TEXT NOT NULL DEFAULT 'vbnet'
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS vbnet_invocations (
        name TEXT NOT NULL,
        receiver TEXT,
        caller TEXT,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        is_test INTEGER NOT NULL,
        argument_count INTEGER,
        language TEXT NOT NULL DEFAULT 'vbnet'
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS vbnet_facts (
        kind TEXT NOT NULL,
        subject TEXT NOT NULL,
        target TEXT,
        value TEXT,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        extra_info TEXT
        )"""
    )
    for table, required_columns in _INDEX_SCHEMA_COLUMNS.items():
        existing_columns = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        for column in sorted(required_columns - existing_columns):
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
    return not schema_was_current


def _write_metadata(
    connection: sqlite3.Connection, snapshot: IndexSnapshot, source_roots: tuple[Path, ...]
) -> None:
    connection.execute("DELETE FROM metadata")
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        (
            ("schema_version", _INDEX_SCHEMA_VERSION),
            ("repository_root", str(snapshot.repository_root)),
            ("git_commit", snapshot.git_commit or ""),
            ("working_tree_state", snapshot.working_tree_state),
            ("source_roots", _root_list_value(source_roots)),
            ("test_source_roots", _root_list_value(_test_source_roots(snapshot.repository_root, source_roots))),
        ),
    )


def _root_list_value(roots: tuple[Path, ...]) -> str:
    return "\n".join(map(str, roots))


def _replace_changed_source_records(
    connection: sqlite3.Connection,
    changed_paths: set[str],
    current_files: dict[str, tuple[str, str]],
    declarations: tuple[JavaDeclaration, ...],
    invocations: tuple[JavaInvocation, ...],
    parse_failures: tuple[ParseFailure, ...],
    spring_facts: tuple[SpringFact, ...],
    ejb_facts: tuple[EJBFact, ...],
    quarkus_build_facts: tuple[QuarkusBuildFact, ...] = (),
    quarkus_config_facts: tuple[QuarkusConfigFact, ...] = (),
    quarkus_cdi_facts: tuple[QuarkusCDIFact, ...] = (),
    quarkus_rest_facts: tuple[QuarkusRESTFact, ...] = (),
    quarkus_route_facts: tuple[QuarkusRouteFact, ...] = (),
    quarkus_security_facts: tuple[QuarkusSecurityFact, ...] = (),
    quarkus_test_facts: tuple[QuarkusTestFact, ...] = (),
    quarkus_native_facts: tuple[QuarkusNativeFact, ...] = (),
    quarkus_boundary_facts: tuple[QuarkusBoundaryFact, ...] = (),
    soap_facts: tuple[SOAPFact, ...] = (),
    replace_all_ejb_facts: bool = False,
) -> None:
    if replace_all_ejb_facts:
        connection.execute("DELETE FROM ejb_facts")
        connection.execute("DELETE FROM quarkus_build_facts")
        connection.execute("DELETE FROM quarkus_config_facts")
        connection.execute("DELETE FROM quarkus_cdi_facts")
        connection.execute("DELETE FROM quarkus_rest_facts")
        connection.execute("DELETE FROM quarkus_route_facts")
        connection.execute("DELETE FROM quarkus_security_facts")
        connection.execute("DELETE FROM quarkus_test_facts")
        connection.execute("DELETE FROM quarkus_native_facts")
        connection.execute("DELETE FROM quarkus_boundary_facts")
        connection.execute("DELETE FROM soap_facts")
    for path in changed_paths:
        connection.execute("DELETE FROM source_files WHERE path = ?", (path,))
        connection.execute("DELETE FROM java_declarations WHERE path = ?", (path,))
        connection.execute("DELETE FROM java_invocations WHERE path = ?", (path,))
        connection.execute("DELETE FROM parse_failures WHERE path = ?", (path,))
        connection.execute("DELETE FROM spring_facts WHERE path = ?", (path,))
        if not replace_all_ejb_facts:
            connection.execute("DELETE FROM ejb_facts WHERE path = ?", (path,))
        connection.execute("DELETE FROM quarkus_build_facts WHERE path = ?", (path,))
        connection.execute("DELETE FROM quarkus_config_facts WHERE path = ?", (path,))
        connection.execute("DELETE FROM quarkus_cdi_facts WHERE path = ?", (path,))
        connection.execute("DELETE FROM quarkus_rest_facts WHERE path = ?", (path,))
        connection.execute("DELETE FROM quarkus_route_facts WHERE path = ?", (path,))
        connection.execute("DELETE FROM quarkus_security_facts WHERE path = ?", (path,))
        connection.execute("DELETE FROM quarkus_test_facts WHERE path = ?", (path,))
        connection.execute("DELETE FROM quarkus_native_facts WHERE path = ?", (path,))
        connection.execute("DELETE FROM quarkus_boundary_facts WHERE path = ?", (path,))
        connection.execute("DELETE FROM soap_facts WHERE path = ?", (path,))
    connection.executemany(
        "INSERT INTO source_files(path, status, content_hash) VALUES (?, ?, ?)",
        ((path, status, content_hash) for path, (status, content_hash) in current_files.items() if path in changed_paths),
    )
    _insert_java_facts(connection, declarations, invocations, parse_failures)
    _insert_spring_facts(connection, spring_facts)
    _insert_ejb_facts(connection, ejb_facts)
    _insert_quarkus_build_facts(connection, quarkus_build_facts)
    _insert_quarkus_config_facts(connection, quarkus_config_facts)
    _insert_quarkus_cdi_facts(connection, quarkus_cdi_facts)
    _insert_quarkus_rest_facts(connection, quarkus_rest_facts)
    _insert_quarkus_route_facts(connection, quarkus_route_facts)
    _insert_quarkus_security_facts(connection, quarkus_security_facts)
    _insert_quarkus_test_facts(connection, quarkus_test_facts)
    _insert_quarkus_native_facts(connection, quarkus_native_facts)
    _insert_quarkus_boundary_facts(connection, quarkus_boundary_facts)
    _insert_soap_facts(connection, soap_facts)


def _insert_java_facts(
    connection: sqlite3.Connection,
    declarations: Iterable[JavaDeclaration],
    invocations: Iterable[JavaInvocation],
    parse_failures: Iterable[ParseFailure],
) -> None:
    connection.executemany(
        """INSERT INTO java_declarations(
        kind, name, qualified_name, signature, path, start_line, end_line, is_test, is_private
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                declaration.kind,
                declaration.name,
                declaration.qualified_name,
                declaration.signature,
                str(declaration.path),
                declaration.start_line,
                declaration.end_line,
                int(declaration.is_test),
                int(declaration.is_private),
            )
            for declaration in declarations
        ),
    )
    connection.executemany(
        """INSERT INTO java_invocations(
        name, receiver, caller, path, start_line, end_line, is_test, argument_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                invocation.name,
                invocation.receiver,
                invocation.caller,
                str(invocation.path),
                invocation.start_line,
                invocation.end_line,
                int(invocation.is_test),
                invocation.argument_count,
            )
            for invocation in invocations
        ),
    )
    connection.executemany(
        "INSERT INTO parse_failures(path, start_line, start_column, message) VALUES (?, ?, ?, ?)",
        (
            (str(failure.path), failure.start_line, failure.start_column, failure.message)
            for failure in parse_failures
        ),
    )


def _insert_spring_facts(
    connection: sqlite3.Connection, facts: Iterable[SpringFact]
) -> None:
    connection.executemany(
        """INSERT INTO spring_facts(
        kind, subject, target, value, path, start_line, end_line, profile
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                fact.kind,
                fact.subject,
                fact.target,
                fact.value,
                str(fact.path),
                fact.start_line,
                fact.end_line,
                fact.profile,
            )
            for fact in facts
        ),
    )


def _insert_ejb_facts(
    connection: sqlite3.Connection, facts: Iterable[EJBFact]
) -> None:
    connection.executemany(
        """INSERT INTO ejb_facts(
        kind, subject, target, value, path, start_line, end_line
        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                fact.kind,
                fact.subject,
                fact.target,
                fact.value,
                str(fact.path),
                fact.start_line,
                fact.end_line,
            )
            for fact in facts
        ),
    )


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _file_content_hash(path: Path) -> str:
    try:
        return _content_hash(path.read_bytes())
    except OSError:
        return ""


def _insert_quarkus_build_facts(
    connection: sqlite3.Connection, facts: Iterable[QuarkusBuildFact]
) -> None:
    connection.executemany(
        """INSERT INTO quarkus_build_facts(
        kind, subject, target, value, path, start_line, end_line, profile
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                fact.kind,
                fact.subject,
                fact.target,
                fact.value,
                str(fact.path),
                fact.start_line,
                fact.end_line,
                fact.profile,
            )
            for fact in facts
        ),
    )


def _insert_quarkus_config_facts(
    connection: sqlite3.Connection, facts: Iterable[QuarkusConfigFact]
) -> None:
    connection.executemany(
        """INSERT INTO quarkus_config_facts(
        kind, subject, target, value, path, start_line, end_line, profile, is_build_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                fact.kind,
                fact.subject,
                fact.target,
                fact.value,
                str(fact.path),
                fact.start_line,
                fact.end_line,
                fact.profile,
                int(fact.is_build_time),
            )
            for fact in facts
        ),
    )


def _insert_quarkus_cdi_facts(
    connection: sqlite3.Connection, facts: Iterable[QuarkusCDIFact]
) -> None:
    connection.executemany(
        """INSERT INTO quarkus_cdi_facts(
        kind, subject, target, value, path, start_line, end_line, scope
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                fact.kind,
                fact.subject,
                fact.target,
                fact.value,
                str(fact.path),
                fact.start_line,
                fact.end_line,
                fact.scope,
            )
            for fact in facts
        ),
    )


def _insert_quarkus_rest_facts(
    connection: sqlite3.Connection, facts: Iterable[QuarkusRESTFact]
) -> None:
    connection.executemany(
        """INSERT INTO quarkus_rest_facts(
        kind, subject, target, value, path, start_line, end_line, flavor
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                fact.kind,
                fact.subject,
                fact.target,
                fact.value,
                str(fact.path),
                fact.start_line,
                fact.end_line,
                fact.flavor,
            )
            for fact in facts
        ),
    )


def _insert_quarkus_route_facts(
    connection: sqlite3.Connection, facts: Iterable[QuarkusRouteFact]
) -> None:
    connection.executemany(
        """INSERT INTO quarkus_route_facts(
        kind, subject, target, value, path, start_line, end_line, flavor
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                fact.kind,
                fact.subject,
                fact.target,
                fact.value,
                str(fact.path),
                fact.start_line,
                fact.end_line,
                fact.flavor,
            )
            for fact in facts
        ),
    )


def _insert_quarkus_security_facts(
    connection: sqlite3.Connection, facts: Iterable[QuarkusSecurityFact]
) -> None:
    connection.executemany(
        """INSERT INTO quarkus_security_facts(
        kind, subject, target, value, path, start_line, end_line, policy
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                fact.kind,
                fact.subject,
                fact.target,
                fact.value,
                str(fact.path),
                fact.start_line,
                fact.end_line,
                fact.policy,
            )
            for fact in facts
        ),
    )


def _insert_quarkus_test_facts(
    connection: sqlite3.Connection, facts: Iterable[QuarkusTestFact]
) -> None:
    connection.executemany(
        """INSERT INTO quarkus_test_facts(
        kind, subject, target, value, path, start_line, end_line, flavor
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                fact.kind,
                fact.subject,
                fact.target,
                fact.value,
                str(fact.path),
                fact.start_line,
                fact.end_line,
                fact.flavor,
            )
            for fact in facts
        ),
    )


def _insert_quarkus_native_facts(
    connection: sqlite3.Connection, facts: Iterable[QuarkusNativeFact]
) -> None:
    connection.executemany(
        """INSERT INTO quarkus_native_facts(
        kind, subject, target, value, path, start_line, end_line, scope
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                fact.kind,
                fact.subject,
                fact.target,
                fact.value,
                str(fact.path),
                fact.start_line,
                fact.end_line,
                fact.scope,
            )
            for fact in facts
        ),
    )


def _insert_quarkus_boundary_facts(
    connection: sqlite3.Connection, facts: Iterable[QuarkusBoundaryFact]
) -> None:
    connection.executemany(
        """INSERT INTO quarkus_boundary_facts(
        kind, subject, target, value, path, start_line, end_line, category
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                fact.kind,
                fact.subject,
                fact.target,
                fact.value,
                str(fact.path),
                fact.start_line,
                fact.end_line,
                fact.category,
            )
            for fact in facts
        ),
    )


def _insert_soap_facts(
    connection: sqlite3.Connection, facts: Iterable[SOAPFact]
) -> None:
    connection.executemany(
        """INSERT INTO soap_facts(
        kind, subject, target, value, path, start_line, end_line, namespace
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                fact.kind,
                fact.subject,
                fact.target,
                fact.value,
                fact.path.as_posix(),
                fact.start_line,
                fact.end_line,
                fact.namespace,
            )
            for fact in facts
        ),
    )


def _analyze_soap_files(contents: dict[Path, bytes], root: Path) -> tuple[SOAPFact, ...]:
    facts: list[SOAPFact] = []
    visited_imports: set[Path] = set()
    for rel_path, raw_bytes in sorted(contents.items(), key=lambda pair: pair[0].as_posix()):
        if rel_path.suffix.lower() not in {".wsdl", ".xsd"}:
            continue
        try:
            text = raw_bytes.decode("utf-8", errors="replace")
        except Exception:
            continue
        file_facts = _parse_soap_wsdl_or_xsd(rel_path, text, root, contents, visited_imports)
        facts.extend(file_facts)
    facts.extend(_analyze_java_soap_files(contents))
    facts.extend(_analyze_soap_descriptor_files(contents))
    return tuple(facts)


def _analyze_java_soap_files(contents: dict[Path, bytes]) -> list[SOAPFact]:
    facts: list[SOAPFact] = []
    parser = Parser(Language(tree_sitter_java.language()))
    for rel_path, raw_bytes in contents.items():
        if rel_path.suffix.lower() != ".java":
            continue
        try:
            tree = parser.parse(raw_bytes)
            imports = _java_imports(raw_bytes, tree.root_node)
            pkg_name = _package_name(tree.root_node, raw_bytes)
            file_facts = _extract_java_soap_facts(rel_path, raw_bytes, tree.root_node, imports, pkg_name)
            facts.extend(file_facts)
        except Exception:
            continue
    return facts


def _extract_java_soap_facts(
    rel_path: Path,
    source: bytes,
    root_node,
    imports: dict[str, str],
    pkg_name: str,
) -> list[SOAPFact]:
    facts: list[SOAPFact] = []
    type_kinds = {"class_declaration", "interface_declaration"}

    def visit(node, enclosing_types: tuple[str, ...]) -> None:
        if node.type in type_kinds:
            name_node = node.child_by_field_name("name")
            if name_node:
                type_name = _node_text(name_node, source)
                current_types = enclosing_types + (type_name,)
                qualified_name = f"{pkg_name}.{'.'.join(current_types)}" if pkg_name else ".".join(current_types)
                modifiers = next((c for c in node.children if c.type == "modifiers"), None)

                ws_anno = _find_soap_annotation(modifiers, source, imports, "WebService")
                wsp_anno = _find_soap_annotation(modifiers, source, imports, "WebServiceProvider")
                wsc_anno = _find_soap_annotation(modifiers, source, imports, "WebServiceClient")
                wf_anno = _find_soap_annotation(modifiers, source, imports, "WebFault")
                hc_anno = _find_soap_annotation(modifiers, source, imports, "HandlerChain")
                xt_anno = _find_soap_annotation(modifiers, source, imports, "XmlType")
                xr_anno = _find_soap_annotation(modifiers, source, imports, "XmlRootElement")
                addr_anno = _find_soap_annotation(modifiers, source, imports, "Addressing")
                mtom_anno = _find_soap_annotation(modifiers, source, imports, "MTOM")
                sec_anno = _find_soap_annotation(modifiers, source, imports, "SecurityDomain")
                arq_anno = _find_soap_annotation(modifiers, source, imports, "Deployment") or _find_soap_annotation(modifiers, source, imports, "RunWith")

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                if ws_anno is not None:
                    sei = ws_anno.get("endpointInterface")
                    ws_name = ws_anno.get("name") or type_name
                    target_ns = ws_anno.get("targetNamespace") or (f"http://{pkg_name.split('.')[0]}.example.org/" if pkg_name else "")
                    svc_name = ws_anno.get("serviceName") or f"{type_name}Service"
                    port_name = ws_anno.get("portName") or f"{ws_name}Port"
                    wsdl_loc = ws_anno.get("wsdlLocation")

                    if sei and not (sei.startswith("javax.") or sei.startswith("jakarta.")):
                        if "." not in sei and pkg_name:
                            sei = f"{pkg_name}.{sei}"

                    val_str = f"{ws_name}|{svc_name}|{port_name}|{wsdl_loc or ''}"
                    facts.append(SOAPFact("java_endpoint", qualified_name, sei, val_str, rel_path, start_line, end_line, target_ns))

                if wsp_anno is not None:
                    target_ns = wsp_anno.get("targetNamespace") or ""
                    svc_name = wsp_anno.get("serviceName") or ""
                    port_name = wsp_anno.get("portName") or ""
                    wsdl_loc = wsp_anno.get("wsdlLocation") or ""
                    val_str = f"{svc_name}|{port_name}|{wsdl_loc}"
                    facts.append(SOAPFact("java_provider", qualified_name, None, val_str, rel_path, start_line, end_line, target_ns))

                if wsc_anno is not None:
                    wsc_name = wsc_anno.get("name") or type_name
                    target_ns = wsc_anno.get("targetNamespace") or ""
                    wsdl_loc = wsc_anno.get("wsdlLocation") or ""
                    val_str = f"{wsc_name}|{target_ns}"
                    facts.append(SOAPFact("java_client", qualified_name, wsdl_loc, val_str, rel_path, start_line, end_line, target_ns))

                if wf_anno is not None:
                    wf_name = wf_anno.get("name") or type_name
                    target_ns = wf_anno.get("targetNamespace") or ""
                    facts.append(SOAPFact("java_fault", qualified_name, wf_name, target_ns, rel_path, start_line, end_line, target_ns))

                if hc_anno is not None:
                    hc_file = hc_anno.get("file") or hc_anno.get("value") or ""
                    facts.append(SOAPFact("soap_handler", qualified_name, hc_file, "handler_chain_xml", rel_path, start_line, end_line))

                if xt_anno is not None or xr_anno is not None:
                    bind_name = (xr_anno.get("name") if xr_anno else None) or (xt_anno.get("name") if xt_anno else None) or type_name
                    bind_ns = (xr_anno.get("namespace") if xr_anno else None) or (xt_anno.get("namespace") if xt_anno else None) or ""
                    facts.append(SOAPFact("java_xml_binding", qualified_name, bind_name, bind_ns, rel_path, start_line, end_line, bind_ns))

                if addr_anno is not None or mtom_anno is not None or sec_anno is not None:
                    p_name = "Addressing" if addr_anno is not None else ("MTOM" if mtom_anno is not None else "SecurityDomain")
                    p_val = sec_anno.get("value") if sec_anno else "enabled"
                    facts.append(SOAPFact("policy_attachment", qualified_name, p_name, p_val, rel_path, start_line, end_line))

                is_test_class = type_name.endswith("Test") or type_name.endswith("Tests") or arq_anno is not None
                if is_test_class:
                    flav = "arquillian" if arq_anno is not None else "unit_test"
                    facts.append(SOAPFact("soap_test", qualified_name, type_name, flav, rel_path, start_line, end_line))

                body_node = node.child_by_field_name("body")
                if body_node:
                    for child in body_node.named_children:
                        if child.type == "method_declaration":
                            m_name_node = child.child_by_field_name("name")
                            if m_name_node:
                                m_name = _node_text(m_name_node, source)
                                m_modifiers = next((c for c in child.children if c.type == "modifiers"), None)
                                wm_anno = _find_soap_annotation(m_modifiers, source, imports, "WebMethod")
                                we_anno = _find_soap_annotation(m_modifiers, source, imports, "WebEndpoint")
                                oneway_anno = _find_soap_annotation(m_modifiers, source, imports, "Oneway")
                                m_sl = child.start_point[0] + 1
                                m_el = child.end_point[0] + 1

                                if wm_anno and wm_anno.get("exclude") == "true":
                                    continue

                                op_name = (wm_anno.get("operationName") if wm_anno else None) or m_name
                                action = wm_anno.get("action") if wm_anno else None
                                target_ns = ws_anno.get("targetNamespace") if ws_anno else None

                                if ws_anno is not None or wm_anno is not None or oneway_anno is not None:
                                    facts.append(
                                        SOAPFact(
                                            "java_method",
                                            f"{qualified_name}#{m_name}",
                                            op_name,
                                            f"{action or ''}|{'oneway' if oneway_anno else 'twoway'}",
                                            rel_path,
                                            m_sl,
                                            m_el,
                                            target_ns,
                                        )
                                    )

                                if we_anno is not None:
                                    ep_port = we_anno.get("name") or m_name
                                    ret_type_node = child.child_by_field_name("type")
                                    ret_type = _node_text(ret_type_node, source) if ret_type_node else ""
                                    facts.append(
                                        SOAPFact(
                                            "java_client_port",
                                            f"{qualified_name}#{m_name}",
                                            ret_type,
                                            ep_port,
                                            rel_path,
                                            m_sl,
                                            m_el,
                                        )
                                    )

                        elif child.type == "field_declaration":
                            f_modifiers = next((c for c in child.children if c.type == "modifiers"), None)
                            wsr_anno = _find_soap_annotation(f_modifiers, source, imports, "WebServiceRef")
                            if wsr_anno is not None:
                                decl = child.child_by_field_name("declarator")
                                f_name_node = decl.child_by_field_name("name") if decl else None
                                if f_name_node:
                                    f_name = _node_text(f_name_node, source)
                                    f_sl = child.start_point[0] + 1
                                    f_el = child.end_point[0] + 1
                                    ref_type = wsr_anno.get("type") or wsr_anno.get("value") or ""
                                    facts.append(
                                        SOAPFact(
                                            "java_client_ref",
                                            f"{qualified_name}#{f_name}",
                                            ref_type,
                                            wsr_anno.get("name") or f_name,
                                            rel_path,
                                            f_sl,
                                            f_el,
                                        )
                                    )

        for child in node.named_children:
            visit(child, enclosing_types if node.type not in type_kinds else current_types)

    visit(root_node, ())
    return facts


def _find_soap_annotation(modifiers, source: bytes, imports: dict[str, str], target_name: str) -> dict[str, str] | None:
    if modifiers is None:
        return None
    for annotation in modifiers.children:
        if annotation.type not in {"marker_annotation", "annotation"}:
            continue
        name_node = annotation.child_by_field_name("name") or (annotation.named_children[0] if annotation.named_children else None)
        if not name_node:
            continue
        name = _node_text(name_node, source)
        simple_name = name.rsplit(".", 1)[-1]
        if simple_name != target_name:
            continue

        imported = imports.get(simple_name)
        if name.startswith(("javax.jws.", "jakarta.jws.", "javax.xml.ws.", "jakarta.xml.ws.", "javax.xml.bind.", "jakarta.xml.bind.", "org.jboss.", "org.junit.")):
            allowed = True
        elif imported is not None:
            allowed = imported in {
                f"javax.jws.{target_name}",
                f"jakarta.jws.{target_name}",
                f"javax.jws.soap.{target_name}",
                f"jakarta.jws.soap.{target_name}",
                f"javax.xml.ws.{target_name}",
                f"jakarta.xml.ws.{target_name}",
                f"javax.xml.ws.soap.{target_name}",
                f"jakarta.xml.ws.soap.{target_name}",
                f"javax.xml.bind.annotation.{target_name}",
                f"jakarta.xml.bind.annotation.{target_name}",
                f"org.jboss.annotation.security.{target_name}",
                f"org.jboss.arquillian.container.test.api.{target_name}",
                f"org.junit.runner.{target_name}",
            }
        else:
            allowed = (
                imports.get("javax.jws") == "javax.jws.*"
                or imports.get("jakarta.jws") == "jakarta.jws.*"
                or imports.get("javax.xml.ws") == "javax.xml.ws.*"
                or imports.get("jakarta.xml.ws") == "jakarta.xml.ws.*"
                or imports.get("javax.xml.bind.annotation") == "javax.xml.bind.annotation.*"
                or imports.get("jakarta.xml.bind.annotation") == "jakarta.xml.bind.annotation.*"
                or target_name in ("Addressing", "MTOM", "SecurityDomain", "Deployment", "RunWith")
            )
        if not allowed:
            continue

        params: dict[str, str] = {}
        if annotation.type == "annotation":
            for child in annotation.named_children:
                if child.type == "annotation_argument_list":
                    for arg in child.named_children:
                        if arg.type == "element_value_pair":
                            k_node = arg.child_by_field_name("key") or (arg.named_children[0] if arg.named_children else None)
                            v_node = arg.child_by_field_name("value") or (arg.named_children[1] if len(arg.named_children) > 1 else None)
                            if k_node and v_node:
                                key = _node_text(k_node, source).strip()
                                val = _node_text(v_node, source).strip().strip('"').strip("'")
                                params[key] = val
                        elif arg.type in ("string_literal", "identifier"):
                            params["value"] = _node_text(arg, source).strip().strip('"').strip("'")
        return params
    return None


def _analyze_soap_descriptor_files(contents: dict[Path, bytes]) -> list[SOAPFact]:
    facts: list[SOAPFact] = []
    for rel_path, raw_bytes in contents.items():
        name_lower = rel_path.name.lower()
        if name_lower not in {"webservices.xml", "jboss-webservices.xml", "jbossws-cxf.xml"}:
            continue
        try:
            text = raw_bytes.decode("utf-8", errors="replace")
            root_elem = ElementTree.fromstring(text)
        except Exception:
            continue

        if name_lower in {"webservices.xml", "jboss-webservices.xml"}:
            for pc in root_elem.iter():
                if _xml_local_name(pc.tag) == "port-component":
                    pc_name = _xml_child_text(pc, "port-component-name")
                    sei = _xml_child_text(pc, "service-endpoint-interface")
                    impl_bean = None
                    for sib in pc:
                        if _xml_local_name(sib.tag) == "service-impl-bean":
                            impl_bean = _xml_child_text(sib, "servlet-link") or _xml_child_text(sib, "ejb-link")
                    line = _xml_descriptor_line(text, "port-component-name", pc_name) if pc_name else 1
                    if impl_bean:
                        facts.append(SOAPFact("descriptor_endpoint", impl_bean, pc_name, f"{sei or ''}|{pc_name or ''}", rel_path, line, line))

        elif name_lower == "jbossws-cxf.xml":
            for feat in root_elem.iter():
                tag_local = _xml_local_name(feat.tag)
                if tag_local in ("feature", "interceptor", "inInterceptors", "outInterceptors"):
                    cls_name = feat.get("class") or feat.text or tag_local
                    line = _xml_descriptor_line(text, tag_local, cls_name)
                    facts.append(SOAPFact("jboss_config", rel_path.as_posix(), str(cls_name).strip(), tag_local, rel_path, line, line))
    return facts


def _parse_soap_wsdl_or_xsd(
    rel_path: Path,
    text: str,
    root: Path,
    contents: dict[Path, bytes],
    visited_imports: set[Path],
) -> list[SOAPFact]:
    facts: list[SOAPFact] = []
    if rel_path in visited_imports:
        return facts
    visited_imports.add(rel_path)

    try:
        elem_tree = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return facts

    namespaces = _extract_namespaces(text)
    target_namespace = elem_tree.get("targetNamespace") or elem_tree.get("namespace") or ""
    if target_namespace:
        namespaces[""] = target_namespace

    def resolve_qname(qname_str: str | None) -> str:
        if not qname_str:
            return ""
        if qname_str.startswith("{"):
            return qname_str
        if ":" in qname_str:
            prefix, local = qname_str.split(":", 1)
            ns_uri = namespaces.get(prefix, target_namespace)
            return f"{{{ns_uri}}}{local}" if ns_uri else local
        ns_uri = namespaces.get("", target_namespace)
        return f"{{{ns_uri}}}{qname_str}" if ns_uri else qname_str

    for child in elem_tree.iter():
        tag_local = _xml_local_name(child.tag)

        if tag_local in ("import", "include"):
            location = child.get("location") or child.get("schemaLocation")
            imp_ns = child.get("namespace") or target_namespace
            line = _xml_descriptor_line(text, tag_local, location)
            if location:
                if location.startswith("http://") or location.startswith("https://"):
                    facts.append(SOAPFact("import", location, imp_ns, "remote_import", rel_path, line, line, target_namespace))
                else:
                    target_rel = (rel_path.parent / location)
                    try:
                        normalized_rel = Path(os.path.normpath(target_rel))
                        if str(normalized_rel).startswith(".."):
                            facts.append(SOAPFact("import", location, imp_ns, "unresolvable_import", rel_path, line, line, target_namespace))
                        elif normalized_rel in contents or (root / normalized_rel).is_file():
                            facts.append(SOAPFact("import", location, imp_ns, "local_import", rel_path, line, line, target_namespace))
                            if normalized_rel not in visited_imports:
                                imp_bytes = contents.get(normalized_rel)
                                if imp_bytes is None and (root / normalized_rel).is_file():
                                    try:
                                        imp_bytes = (root / normalized_rel).read_bytes()
                                    except Exception:
                                        imp_bytes = None
                                if imp_bytes:
                                    imp_text = imp_bytes.decode("utf-8", errors="replace")
                                    nested_facts = _parse_soap_wsdl_or_xsd(normalized_rel, imp_text, root, contents, visited_imports)
                                    facts.extend(nested_facts)
                        else:
                            facts.append(SOAPFact("import", location, imp_ns, "unresolvable_import", rel_path, line, line, target_namespace))
                    except Exception:
                        facts.append(SOAPFact("import", location, imp_ns, "unresolvable_import", rel_path, line, line, target_namespace))

        elif tag_local == "portType":
            pt_name = child.get("name")
            if pt_name:
                pt_qname = f"{{{target_namespace}}}{pt_name}" if target_namespace else pt_name
                pt_line = _xml_descriptor_line(text, "portType", pt_name)
                facts.append(SOAPFact("port_type", pt_qname, None, pt_name, rel_path, pt_line, pt_line, target_namespace))
                for op in child:
                    if _xml_local_name(op.tag) == "operation":
                        op_name = op.get("name")
                        op_line = _xml_descriptor_line(text, "operation", op_name)
                        if op_name:
                            facts.append(SOAPFact("operation", op_name, pt_qname, op_name, rel_path, op_line, op_line, target_namespace))
                            for sub in op:
                                sub_kind = _xml_local_name(sub.tag)
                                if sub_kind in ("input", "output", "fault"):
                                    msg_ref = resolve_qname(sub.get("message"))
                                    facts.append(SOAPFact(f"operation_{sub_kind}", op_name, pt_qname, msg_ref, rel_path, op_line, op_line, target_namespace))

        elif tag_local == "message":
            msg_name = child.get("name")
            if msg_name:
                msg_qname = f"{{{target_namespace}}}{msg_name}" if target_namespace else msg_name
                m_line = _xml_descriptor_line(text, "message", msg_name)
                facts.append(SOAPFact("message", msg_qname, None, msg_name, rel_path, m_line, m_line, target_namespace))
                for part in child:
                    if _xml_local_name(part.tag) == "part":
                        p_name = part.get("name")
                        e_ref = resolve_qname(part.get("element") or part.get("type"))
                        facts.append(SOAPFact("message_part", msg_qname, e_ref, p_name, rel_path, m_line, m_line, target_namespace))

        elif tag_local == "binding":
            binding_name = child.get("name")
            pt_ref = resolve_qname(child.get("type"))
            if binding_name:
                binding_qname = f"{{{target_namespace}}}{binding_name}" if target_namespace else binding_name
                b_line = _xml_descriptor_line(text, "binding", binding_name)
                facts.append(SOAPFact("binding", binding_qname, pt_ref, binding_name, rel_path, b_line, b_line, target_namespace))
                for b_op in child:
                    if _xml_local_name(b_op.tag) == "operation":
                        b_op_name = b_op.get("name")
                        if b_op_name:
                            action = None
                            for sub in b_op:
                                if _xml_local_name(sub.tag) == "operation":
                                    action = sub.get("soapAction")
                            facts.append(SOAPFact("binding_operation", binding_qname, b_op_name, action, rel_path, b_line, b_line, target_namespace))

        elif tag_local == "service":
            service_name = child.get("name")
            if service_name:
                service_qname = f"{{{target_namespace}}}{service_name}" if target_namespace else service_name
                s_line = _xml_descriptor_line(text, "service", service_name)
                facts.append(SOAPFact("service", service_qname, None, service_name, rel_path, s_line, s_line, target_namespace))
                for port in child:
                    if _xml_local_name(port.tag) == "port":
                        port_name = port.get("name")
                        b_ref = resolve_qname(port.get("binding"))
                        addr = None
                        for sub in port:
                            if _xml_local_name(sub.tag) == "address":
                                addr = sub.get("location")
                        facts.append(SOAPFact("port", service_qname, b_ref, f"{port_name}|{addr or ''}", rel_path, s_line, s_line, target_namespace))

        elif tag_local == "element":
            elem_name = child.get("name")
            if elem_name:
                elem_qname = f"{{{target_namespace}}}{elem_name}" if target_namespace else elem_name
                type_ref = resolve_qname(child.get("type"))
                e_line = _xml_descriptor_line(text, "element", elem_name)
                facts.append(SOAPFact("schema_element", elem_qname, type_ref, elem_name, rel_path, e_line, e_line, target_namespace))

        elif tag_local in ("complexType", "simpleType"):
            t_name = child.get("name")
            if t_name:
                t_qname = f"{{{target_namespace}}}{t_name}" if target_namespace else t_name
                t_line = _xml_descriptor_line(text, tag_local, t_name)
                facts.append(SOAPFact("schema_type", t_qname, None, t_name, rel_path, t_line, t_line, target_namespace))

    return facts


def _extract_namespaces(text: str) -> dict[str, str]:
    namespaces: dict[str, str] = {}
    for match in re.finditer(r'xmlns(?::([A-Za-z0-9_-]+))?\s*=\s*["\']([^"\']+)["\']', text):
        prefix = match.group(1) or ""
        uri = match.group(2)
        namespaces[prefix] = uri
    return namespaces


def _soap_payload_relationships(
    connection: sqlite3.Connection,
    wsdl_path: str,
    port_type_qname: str,
    operation_name: str,
) -> tuple[list[ImpactRelationship], list[UnresolvedItem]]:
    relationships: list[ImpactRelationship] = []
    unresolved_items: list[UnresolvedItem] = []

    op_facts = connection.execute(
        """SELECT kind, subject, target, value, path, start_line, end_line, namespace
        FROM soap_facts WHERE kind IN ('operation_input', 'operation_output', 'operation_fault')
        AND subject = ?""",
        (operation_name,),
    ).fetchall()

    for row in op_facts:
        f_kind, op_name, pt_ref, msg_qname, f_path, sl, el, ns = row
        if not msg_qname:
            continue
        op_handle = f"soap_wsdl:{f_path}:{sl}-{el}"

        msg_rows = connection.execute(
            """SELECT subject, target, value, path, start_line, end_line
            FROM soap_facts WHERE kind IN ('message', 'message_part') AND subject = ?""",
            (msg_qname,),
        ).fetchall()

        for m_row in msg_rows:
            m_subj, m_target, m_val, m_path, m_sl, m_el = m_row
            m_handle = f"soap_wsdl:{m_path}:{m_sl}-{m_el}"
            rel_kind = "soap_fault" if f_kind == "operation_fault" else "soap_payload"

            relationships.append(
                ImpactRelationship(
                    kind=rel_kind,
                    caller=m_subj,
                    path=Path(m_path),
                    start_line=m_sl,
                    end_line=m_el,
                    evidence_handle=m_handle,
                    evidence_chain=(op_handle, m_handle),
                    confidence="high",
                )
            )

            if m_target:
                elem_rows = connection.execute(
                    """SELECT subject, target, value, path, start_line, end_line
                    FROM soap_facts WHERE kind IN ('schema_element', 'schema_type') AND subject = ?""",
                    (m_target,),
                ).fetchall()

                for e_row in elem_rows:
                    e_subj, e_target, e_val, e_path, e_sl, e_el = e_row
                    e_handle = f"soap_wsdl:{e_path}:{e_sl}-{e_el}"
                    relationships.append(
                        ImpactRelationship(
                            kind=rel_kind,
                            caller=e_subj,
                            path=Path(e_path),
                            start_line=e_sl,
                            end_line=e_el,
                            evidence_handle=e_handle,
                            evidence_chain=(op_handle, m_handle, e_handle),
                            confidence="high",
                        )
                    )

    import_rows = connection.execute(
        "SELECT subject, value, path, start_line, end_line FROM soap_facts WHERE kind = 'import' AND value IN ('remote_import', 'unresolvable_import')"
    ).fetchall()
    for imp_subj, imp_val, imp_path, imp_sl, imp_el in import_rows:
        msg = f"Remote or unresolvable WSDL/XSD import location: {imp_subj}"
        unresolved_items.append(
            _unresolved(msg, path=Path(imp_path), start_line=imp_sl, end_line=imp_el, evidence_kind="soap_wsdl")
        )

    return relationships, unresolved_items


def _soap_endpoint_relationships(
    connection: sqlite3.Connection,
    target_port_type: str,
    target_operation: str,
    resolved_op_row: tuple,
    root: Path,
    database_path: Path,
) -> tuple[list[ImpactRelationship], list[UnresolvedItem]]:
    relationships: list[ImpactRelationship] = []
    unresolved_items: list[UnresolvedItem] = []

    f_kind, op_name, pt_qname, in_msg, f_path, sl, el, ns = resolved_op_row
    op_handle = f"soap_wsdl:{f_path}:{sl}-{el}"

    java_methods = connection.execute(
        "SELECT subject, target, value, path, start_line, end_line, namespace FROM soap_facts WHERE kind = 'java_method' AND target = ?",
        (target_operation,),
    ).fetchall()

    for m_subj, m_target, m_val, m_path, m_sl, m_el, m_ns in java_methods:
        class_qname, method_name = m_subj.rsplit("#", 1)

        ep_rows = connection.execute(
            "SELECT subject, target, value, path, start_line, end_line, namespace FROM soap_facts WHERE kind = 'java_endpoint' AND subject = ?",
            (class_qname,),
        ).fetchall()

        sei_qname = ep_rows[0][1] if ep_rows else None
        m_handle = f"declaration:{m_path}:{m_sl}-{m_el}"

        relationships.append(
            ImpactRelationship(
                kind="soap_endpoint_implementation",
                caller=m_subj,
                path=Path(m_path),
                start_line=m_sl,
                end_line=m_el,
                evidence_handle=m_handle,
                evidence_chain=(op_handle, m_handle),
                confidence="high",
            )
        )

        if sei_qname:
            sei_methods = connection.execute(
                "SELECT path, start_line, end_line FROM soap_facts WHERE kind = 'java_method' AND subject LIKE ? AND target = ?",
                (f"{sei_qname}#%", target_operation),
            ).fetchall()
            for s_path, s_sl, s_el in sei_methods:
                s_handle = f"declaration:{s_path}:{s_sl}-{s_el}"
                relationships.append(
                    ImpactRelationship(
                        kind="soap_container_dispatch",
                        caller=f"{sei_qname}#{method_name}",
                        path=Path(s_path),
                        start_line=s_sl,
                        end_line=s_el,
                        evidence_handle=s_handle,
                        evidence_chain=(op_handle, s_handle, m_handle),
                        confidence="high",
                    )
                )

        ejb_rows = connection.execute(
            "SELECT kind, subject, target, value, path, start_line, end_line FROM ejb_facts WHERE subject = ? AND kind IN ('session_bean', 'stateless_bean')",
            (class_qname,),
        ).fetchall()

        if ejb_rows:
            target_item = ImpactTarget(m_subj, Path(m_path), m_sl, m_el, m_handle)
            ejb_rels, ejb_unres = _direct_relationships(database_path, target_item)
            for r in ejb_rels:
                conf = "medium" if "caller" in r.kind or "callee" in r.kind else r.confidence
                relationships.append(
                    ImpactRelationship(
                        kind=r.kind,
                        caller=r.caller,
                        path=r.path,
                        start_line=r.start_line,
                        end_line=r.end_line,
                        evidence_handle=r.evidence_handle,
                        evidence_chain=(op_handle, *r.evidence_chain),
                        confidence=conf,
                        profile=r.profile,
                        business_view=r.business_view,
                    )
                )
            unresolved_items.extend(ejb_unres)

    desc_rows = connection.execute(
        "SELECT subject, target, value, path, start_line, end_line FROM soap_facts WHERE kind = 'descriptor_endpoint'"
    ).fetchall()
    for d_subj, d_target, d_val, d_path, d_sl, d_el in desc_rows:
        d_handle = f"soap_wsdl:{d_path}:{d_sl}-{d_el}"
        relationships.append(
            ImpactRelationship(
                kind="soap_configuration",
                caller=d_subj,
                path=Path(d_path),
                start_line=d_sl,
                end_line=d_el,
                evidence_handle=d_handle,
                evidence_chain=(op_handle, d_handle),
                confidence="medium",
            )
        )

    return relationships, unresolved_items


def _impact_rest_repository(request: ImpactRequest, root: Path, database_path: Path) -> ImpactResult:
    rest_target = request.rest_target
    if rest_target is None:
        raise ValueError('A REST impact request requires a REST Change Target.')
    connection = sqlite3.connect(database_path)
    try:
        snapshot = _read_index_snapshot(connection, root)
        app_paths = tuple(dict.fromkeys(
            row[0] for row in connection.execute(
                '''SELECT target FROM quarkus_rest_facts
                   WHERE kind = 'rest_application' AND target IS NOT NULL
                   ORDER BY path, start_line'''
            ).fetchall()
        ))
        class_rows = connection.execute(
            '''SELECT subject, target, value, path, start_line, end_line, flavor
               FROM quarkus_rest_facts WHERE kind = 'rest_resource'
               ORDER BY path, start_line, subject'''
        ).fetchall()
        class_facts = {row[0]: row for row in class_rows}
        endpoint_rows = connection.execute(
            '''SELECT kind, subject, target, value, path, start_line, end_line, flavor
               FROM quarkus_rest_facts WHERE kind = 'rest_endpoint'
               ORDER BY path, start_line, subject'''
        ).fetchall()
        matched_rows = []
        for row in endpoint_rows:
            meta = _rest_json_object(row[3])
            method = str(meta.get('http_method') or (row[2] or 'GET').split(' ', 1)[0]).upper()
            subject_class = row[1].rsplit('#', 1)[0]
            class_row = class_facts.get(subject_class)
            class_path = class_row[1] if class_row and class_row[1] else ''
            method_path = meta.get('method_path', '') or ''
            candidate_target = RESTChangeTarget(
                method,
                _normalize_rest_path(*app_paths, class_path, method_path),
                _rest_metadata_values(meta.get('consumes')),
                _rest_metadata_values(meta.get('produces')),
                _rest_parameter_keys(meta.get('parameters')),
                _rest_header_keys(meta.get('parameters')),
            )
            if _rest_target_matches(rest_target, candidate_target):
                matched_rows.append((row, candidate_target))
    finally:
        connection.close()

    if not matched_rows:
        return ImpactResult(
            'not_found', rest_target.contract_key, None, (), (), (),
            (_unresolved('REST target ' + rest_target.contract_key + ' was not found.'),), snapshot,
        )
    candidates = tuple(
        ImpactTarget(
            candidate_target.signature, Path(row[4]), row[5], row[6],
            _evidence_handle('quarkus_rest', Path(row[4]), row[5], row[6]),
        )
        for row, candidate_target in matched_rows
    )
    if len(matched_rows) > 1:
        return ImpactResult('ambiguous', rest_target.contract_key, None, candidates, (), (), (), snapshot)

    row, matched_target = matched_rows[0]
    connection = sqlite3.connect(database_path)
    try:
        class_rows = connection.execute(
            '''SELECT subject, target, value, path, start_line, end_line, flavor
               FROM quarkus_rest_facts WHERE kind = 'rest_resource'
               ORDER BY path, start_line, subject'''
        ).fetchall()
        class_facts = {class_row[0]: class_row for class_row in class_rows}
        app_paths = tuple(dict.fromkeys(
            app_row[0] for app_row in connection.execute(
                '''SELECT target FROM quarkus_rest_facts
                   WHERE kind = 'rest_application' AND target IS NOT NULL
                   ORDER BY path, start_line'''
            ).fetchall()
        ))
        contract = _rest_discovery_candidate(
            connection, row, matched_target, (), app_paths, class_facts,
        )
    finally:
        connection.close()
    if contract.source_resolution == 'ambiguous':
        return ImpactResult(
            'ambiguous', rest_target.contract_key, None,
            contract.source_entry_points, (), (), contract.unresolved_items, snapshot,
        )
    route_target = candidates[0]
    relationships = [
        ImpactRelationship(
            'rest_contract', rest_target.signature, route_target.path,
            route_target.start_line, route_target.end_line, route_target.evidence_handle,
            'high' if row[7] and row[7] != 'unknown' else 'medium',
            evidence_chain=tuple(contract.evidence_handles),
            business_view=json.dumps({
                'http_method': matched_target.http_method,
                'route': matched_target.path,
                'route_shape': matched_target.route_shape,
                'consumes': matched_target.consumes,
                'produces': matched_target.produces,
                'headers': matched_target.headers,
                'flavor': row[7],
            }),
        )
    ]
    unresolved_items = list(contract.unresolved_items)
    source = contract.source_entry_point
    if source is not None:
        direct, direct_unresolved = _direct_relationships(
            database_path, source, request.profiles, request.build_profiles, request.runtime_profiles,
        )
        relationships.extend(direct)
        unresolved_items.extend(direct_unresolved)
        rest_relationships, rest_unresolved = _quarkus_rest_relationships(
            database_path, source.signature.split('#', 1)[0], source,
        )
        relationships.extend(rest_relationships)
        unresolved_items.extend(rest_unresolved)
    return ImpactResult(
        'resolved', rest_target.contract_key, route_target, (), tuple(relationships),
        ('REST Contract Identity resolved from local JAX-RS route and handler evidence.',),
        tuple(unresolved_items), snapshot,
    )


def _rest_target_matches(requested: RESTChangeTarget, candidate: RESTChangeTarget) -> bool:
    if requested.http_method and requested.http_method != candidate.http_method:
        return False
    if requested.path and requested.path != candidate.path:
        return False
    if requested.consumes and requested.consumes != candidate.consumes:
        return False
    if requested.produces and requested.produces != candidate.produces:
        return False
    if requested.params and requested.params != candidate.params:
        return False
    if requested.headers and requested.headers != candidate.headers:
        return False
    return True

def _impact_soap_repository(request: ImpactRequest, root: Path, database_path: Path) -> ImpactResult:
    connection = sqlite3.connect(database_path)
    try:
        snapshot = _read_index_snapshot(connection, root)
        soap_target = request.soap_target
        wsdl_rel_path = (
            soap_target.wsdl.as_posix()
            if soap_target is not None
            else request.soap_wsdl.as_posix() if request.soap_wsdl else ""
        )
        target_port_type = soap_target.port_type if soap_target is not None else request.soap_port_type or ""
        target_operation = soap_target.operation if soap_target is not None else request.soap_operation or ""

        rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line, namespace
            FROM soap_facts WHERE kind = 'operation' AND subject = ?""",
            (target_operation,),
        ).fetchall()

        matched_rows = []
        for r in rows:
            f_kind, f_subject, f_target, f_value, f_path, f_start_line, f_end_line, f_namespace = r
            f_path_posix = Path(f_path).as_posix()
            wsdl_posix = Path(wsdl_rel_path).as_posix()
            if f_path_posix != wsdl_posix and not f_path_posix.endswith("/" + wsdl_posix) and not wsdl_posix.endswith("/" + f_path_posix):
                continue
            if _matches_port_type(f_target or "", target_port_type):
                matched_rows.append(r)

        target_name_str = f"soap:{wsdl_rel_path}#{target_port_type}#{target_operation}"

        if not matched_rows:
            return ImpactResult("not_found", target_name_str, None, (), (), (), (_unresolved(f"SOAP target operation '{target_operation}' was not found in WSDL '{wsdl_rel_path}'."),), snapshot)

        candidates = tuple(
            ImpactTarget(
                f"{r[2]}#{r[1]}",
                Path(r[4]),
                r[5],
                r[6],
                f"soap_wsdl:{r[4]}:{r[5]}-{r[6]}",
            )
            for r in matched_rows
        )

        if len(matched_rows) > 1:
            return ImpactResult("ambiguous", target_name_str, None, candidates, (), (), (), snapshot)

        resolved_row = matched_rows[0]

        payload_rels, payload_unresolved = _soap_payload_relationships(connection, wsdl_rel_path, target_port_type, target_operation)
        endpoint_rels, endpoint_unresolved = _soap_endpoint_relationships(connection, target_port_type, target_operation, resolved_row, root, database_path)

        all_rels = payload_rels + endpoint_rels
        all_unresolved = payload_unresolved + endpoint_unresolved

        assumptions = (
            "SOAP analysis ground truth is established by repository-local WSDL 1.1 and XML Schema contract evidence.",
            "No network or server-side dynamic compilation was performed.",
        )

        return ImpactResult(
            "resolved",
            target_name_str,
            candidates[0],
            (),
            tuple(all_rels),
            assumptions,
            tuple(all_unresolved),
            snapshot,
        )
    finally:
        connection.close()


def _matches_port_type(port_type_qname: str, target_port_type: str) -> bool:
    if target_port_type.startswith("{"):
        return port_type_qname == target_port_type
    if "}" in port_type_qname:
        local_name = port_type_qname.rsplit("}", maxsplit=1)[-1]
        return local_name == target_port_type
    return port_type_qname == target_port_type


def _analyze_quarkus_boundary_files(
    contents_by_path: dict[Path, bytes],
    declarations: tuple[JavaDeclaration, ...],
) -> tuple[QuarkusBoundaryFact, ...]:
    facts: list[QuarkusBoundaryFact] = []

    for path in contents_by_path.keys():
        if any(part in ("target", "build", ".quarkus", "node_modules") for part in path.parts):
            continue
        ext = path.suffix.lower()
        if ext in (".kt", ".scala"):
            facts.append(
                QuarkusBoundaryFact(
                    "kotlin_scala_gap",
                    path.name,
                    None,
                    f"Kotlin (.kt) or Scala (.scala) source file '{path.as_posix()}' present in repository cannot be statically parsed; analysis is restricted to Java source files and Gradle Kotlin DSL.",
                    path,
                    1,
                    1,
                    category="coverage_gap",
                )
            )

    for path, content in sorted(contents_by_path.items(), key=lambda item: str(item[0])):
        if any(part in ("target", "build", ".quarkus") for part in path.parts):
            continue

        if path.suffix.lower() != ".java":
            continue

        text = content.decode("utf-8", errors="replace")
        lines = text.splitlines()

        for decl in declarations:
            if decl.path != path:
                continue

            if decl.kind in ("class", "interface"):
                decl_snippet = _get_class_snippet(lines, decl)
                full_class_text = "\n".join(lines[max(0, decl.start_line - 1) : decl.end_line])

                if "@QuarkusMain" in decl_snippet or "QuarkusApplication" in decl_snippet:
                    facts.append(
                        QuarkusBoundaryFact(
                            "quarkus_main",
                            decl.qualified_name,
                            None,
                            json.dumps({"class": decl.qualified_name}),
                            path,
                            decl.start_line,
                            decl.end_line,
                            category="entry_point",
                        )
                    )

                if "PanacheRepository" in decl_snippet or "PanacheEntity" in decl_snippet or "@Entity" in decl_snippet:
                    facts.append(
                        QuarkusBoundaryFact(
                            "panache_repository",
                            decl.qualified_name,
                            None,
                            json.dumps({"class": decl.qualified_name}),
                            path,
                            decl.start_line,
                            decl.end_line,
                            category="persistence",
                        )
                    )
                    facts.append(
                        QuarkusBoundaryFact(
                            "persistence_unresolved",
                            decl.qualified_name,
                            None,
                            f"Generated Panache CRUD methods (persist, delete, find, listAll), query interpretation, and database dispatch for '{decl.name}' cannot be statically resolved to database schema.",
                            path,
                            decl.start_line,
                            decl.end_line,
                            category="persistence",
                        )
                    )

            elif decl.kind == "method":
                method_snippet = _get_method_snippet(lines, decl)

                if decl.name == "main" and "String" in method_snippet:
                    facts.append(
                        QuarkusBoundaryFact(
                            "quarkus_main",
                            decl.qualified_name,
                            None,
                            json.dumps({"method": decl.qualified_name}),
                            path,
                            decl.start_line,
                            decl.end_line,
                            category="entry_point",
                        )
                    )

                if "@Startup" in method_snippet or ("@Observes" in method_snippet and ("StartupEvent" in method_snippet or "ShutdownEvent" in method_snippet)):
                    facts.append(
                        QuarkusBoundaryFact(
                            "startup_event",
                            decl.qualified_name,
                            None,
                            json.dumps({"method": decl.qualified_name}),
                            path,
                            decl.start_line,
                            decl.end_line,
                            category="lifecycle",
                        )
                    )

                if any(ann in method_snippet for ann in ("@Incoming", "@Outgoing", "@Channel")):
                    facts.append(
                        QuarkusBoundaryFact(
                            "messaging",
                            decl.qualified_name,
                            None,
                            f"Quarkus Reactive Messaging boundary (@Incoming/@Outgoing/@Channel) on '{decl.name}' cannot be statically linked to message broker topic.",
                            path,
                            decl.start_line,
                            decl.end_line,
                            category="messaging",
                        )
                    )

                if "@Scheduled" in method_snippet:
                    facts.append(
                        QuarkusBoundaryFact(
                            "scheduler",
                            decl.qualified_name,
                            None,
                            f"Quarkus Scheduler boundary (@Scheduled) on '{decl.name}' execution flow cannot be statically linked to runtime timer trigger.",
                            path,
                            decl.start_line,
                            decl.end_line,
                            category="scheduler",
                        )
                    )

                if any(ann in method_snippet for ann in ("@GraphQLApi", "@Query", "@Mutation")):
                    facts.append(
                        QuarkusBoundaryFact(
                            "graphql",
                            decl.qualified_name,
                            None,
                            f"Quarkus GraphQL boundary (@GraphQLApi/@Query/@Mutation) on '{decl.name}' cannot be statically mapped to client GraphQL query.",
                            path,
                            decl.start_line,
                            decl.end_line,
                            category="graphql",
                        )
                    )

                if "@GrpcService" in method_snippet or "BindableService" in method_snippet:
                    facts.append(
                        QuarkusBoundaryFact(
                            "grpc",
                            decl.qualified_name,
                            None,
                            f"Quarkus gRPC boundary (@GrpcService) on '{decl.name}' cannot be statically mapped to gRPC client stub.",
                            path,
                            decl.start_line,
                            decl.end_line,
                            category="grpc",
                        )
                    )

                if "@WebServlet" in method_snippet:
                    facts.append(
                        QuarkusBoundaryFact(
                            "servlet",
                            decl.qualified_name,
                            None,
                            f"Quarkus Servlet boundary (@WebServlet) on '{decl.name}' cannot be statically mapped to HTTP container routes.",
                            path,
                            decl.start_line,
                            decl.end_line,
                            category="servlet",
                        )
                    )

                if "@Observes" in method_snippet and "StartupEvent" not in method_snippet and "ShutdownEvent" not in method_snippet:
                    facts.append(
                        QuarkusBoundaryFact(
                            "cdi_event",
                            decl.qualified_name,
                            None,
                            f"Dynamic CDI event dispatch (@Observes) on '{decl.name}' cannot be statically bound to event producers.",
                            path,
                            decl.start_line,
                            decl.end_line,
                            category="cdi_event",
                        )
                    )

    return tuple(facts)


def _quarkus_boundary_relationships(
    connection_data_path: Path,
    owner: str,
    target: ImpactTarget,
) -> tuple[tuple[ImpactRelationship, ...], tuple[UnresolvedItem, ...]]:
    connection = sqlite3.connect(connection_data_path)
    try:
        b_rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line, category
            FROM quarkus_boundary_facts ORDER BY path, start_line"""
        ).fetchall()
    except sqlite3.OperationalError:
        return (), ()
    finally:
        connection.close()

    relationships: list[ImpactRelationship] = []
    unresolved: list[UnresolvedItem] = []
    seen_keys: set[tuple[str, str, Path, int, int]] = set()

    target_sig = target.signature
    target_class = target_sig.rsplit("#", 1)[0]
    target_method = target_sig.split("#")[-1].split("(")[0] if "#" in target_sig else None
    clean_target_class = target_class.rsplit(".", 1)[-1]

    def matches_target(f_subject: str) -> bool:
        if f_subject == target_sig or f_subject == owner:
            return True
        f_c, _, f_m = f_subject.partition("#")
        clean_fm = f_m.split("(")[0] if f_m else ""
        clean_fc = f_c.rsplit(".", 1)[-1]
        if f_c == target_class or clean_fc == clean_target_class:
            if not target_method or not clean_fm or clean_fm == target_method:
                return True
        return False

    b_facts = [
        QuarkusBoundaryFact(kind, subject, fact_target, value, Path(path), start_line, end_line, category)
        for kind, subject, fact_target, value, path, start_line, end_line, category in b_rows
    ]

    for bf in b_facts:
        if bf.kind == "kotlin_scala_gap":
            unresolved.append(
                UnresolvedItem(
                    bf.value or "Kotlin/Scala source file present in repository.",
                    path=bf.path,
                    start_line=bf.start_line,
                    end_line=bf.end_line,
                    evidence_handle=f"quarkus_boundary:{bf.path.as_posix()}:{bf.start_line}-{bf.end_line}",
                )
            )

    for bf in b_facts:
        if matches_target(bf.subject):
            handle = f"quarkus_boundary:{bf.path.as_posix()}:{bf.start_line}-{bf.end_line}"
            if bf.kind == "quarkus_main":
                rel_key = ("quarkus_main_entry", bf.subject, bf.path, bf.start_line, bf.end_line)
                if rel_key not in seen_keys:
                    seen_keys.add(rel_key)
                    relationships.append(
                        ImpactRelationship(
                            "quarkus_main_entry",
                            f"Quarkus Main Entry ({bf.subject}) -> {target_sig}",
                            bf.path,
                            bf.start_line,
                            bf.end_line,
                            handle,
                            "high",
                            False,
                            None,
                            evidence_chain=(handle,),
                            business_view=bf.value,
                        )
                    )

            elif bf.kind == "startup_event":
                rel_key = ("quarkus_lifecycle", bf.subject, bf.path, bf.start_line, bf.end_line)
                if rel_key not in seen_keys:
                    seen_keys.add(rel_key)
                    relationships.append(
                        ImpactRelationship(
                            "quarkus_lifecycle",
                            f"Quarkus Lifecycle ({bf.subject}) -> {target_sig}",
                            bf.path,
                            bf.start_line,
                            bf.end_line,
                            handle,
                            "high",
                            False,
                            None,
                            evidence_chain=(handle,),
                            business_view=bf.value,
                        )
                    )

            elif bf.kind == "panache_repository":
                rel_key = ("quarkus_persistence", bf.subject, bf.path, bf.start_line, bf.end_line)
                if rel_key not in seen_keys:
                    seen_keys.add(rel_key)
                    relationships.append(
                        ImpactRelationship(
                            "quarkus_persistence",
                            f"Panache Persistence ({bf.subject}) -> {target_sig}",
                            bf.path,
                            bf.start_line,
                            bf.end_line,
                            handle,
                            "high",
                            False,
                            None,
                            evidence_chain=(handle,),
                            business_view=bf.value,
                        )
                    )

            elif bf.kind in ("persistence_unresolved", "messaging", "scheduler", "graphql", "grpc", "servlet", "cdi_event"):
                unresolved.append(
                    UnresolvedItem(
                        bf.value or f"Unsupported Quarkus boundary '{bf.kind}' on {bf.subject}",
                        path=bf.path,
                        start_line=bf.start_line,
                        end_line=bf.end_line,
                        evidence_handle=handle,
                    )
                )

    return tuple(relationships), tuple(unresolved)
    connection.executemany(
        """INSERT INTO quarkus_native_facts(
        kind, subject, target, value, path, start_line, end_line, scope
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                fact.kind,
                fact.subject,
                fact.target,
                fact.value,
                str(fact.path),
                fact.start_line,
                fact.end_line,
                fact.scope,
            )
            for fact in facts
        ),
    )


def _analyze_quarkus_native_files(
    contents_by_path: dict[Path, bytes],
    declarations: tuple[JavaDeclaration, ...],
) -> tuple[QuarkusNativeFact, ...]:
    facts: list[QuarkusNativeFact] = []

    for path, content in sorted(contents_by_path.items(), key=lambda item: str(item[0])):
        path_str = path.as_posix()

        if any(part in ("target", "build", ".quarkus") for part in path.parts):
            continue

        text = content.decode("utf-8", errors="replace")
        lines = text.splitlines()

        if "META-INF/services/" in path_str:
            spi_interface = path.name.strip()
            for idx, line in enumerate(lines, 1):
                clean_line = line.strip()
                if clean_line and not clean_line.startswith("#"):
                    facts.append(
                        QuarkusNativeFact(
                            "meta_inf_service",
                            spi_interface,
                            clean_line,
                            json.dumps({"spi_interface": spi_interface, "provider": clean_line}),
                            path,
                            idx,
                            idx,
                            scope="spi",
                        )
                    )

        elif "META-INF/native-image/" in path_str and path.suffix == ".json":
            try:
                data = json.loads(text)
                if isinstance(data, list):
                    for idx, entry in enumerate(data, 1):
                        if isinstance(entry, dict) and "name" in entry:
                            class_name = entry["name"]
                            facts.append(
                                QuarkusNativeFact(
                                    "native_json_config",
                                    class_name,
                                    path.name,
                                    json.dumps(entry),
                                    path,
                                    1,
                                    len(lines) if lines else 1,
                                    scope="reflection" if "reflection" in path.name else "native_json",
                                )
                            )
                        elif isinstance(entry, list):
                            for iface in entry:
                                if isinstance(iface, str):
                                    facts.append(
                                        QuarkusNativeFact(
                                            "native_json_config",
                                            iface,
                                            path.name,
                                            json.dumps({"interface": iface}),
                                            path,
                                            1,
                                            len(lines) if lines else 1,
                                            scope="proxy",
                                        )
                                    )
            except Exception:
                pass

        elif path.suffix.lower() == ".java":
            for decl in declarations:
                if decl.path != path:
                    continue

                if decl.kind in ("class", "interface"):
                    decl_snippet = _get_class_snippet(lines, decl)
                    full_class_text = "\n".join(lines[max(0, decl.start_line - 1) : decl.end_line])

                    if "@RegisterForReflection" in decl_snippet or "@RegisterForReflection" in full_class_text:
                        targets: list[str] = []
                        ref_match = re.search(r'@RegisterForReflection\s*\(\s*(?:targets|classNames)\s*=\s*(?:\{([^}]+)\}|["\']([^"\']+)["\'])\s*\)', full_class_text)
                        if ref_match:
                            raw_t = ref_match.group(1) or ref_match.group(2) or ""
                            for item in raw_t.split(","):
                                clean_t = item.strip().replace(".class", "").strip('"').strip("'").strip()
                                if clean_t:
                                    targets.append(clean_t)

                        if not targets:
                            targets.append(decl.qualified_name)

                        for tgt in targets:
                            facts.append(
                                QuarkusNativeFact(
                                    "register_reflection",
                                    tgt,
                                    decl.qualified_name,
                                    json.dumps({"target": tgt, "annotated_class": decl.qualified_name}),
                                    path,
                                    decl.start_line,
                                    decl.end_line,
                                    scope="reflection",
                                )
                            )

                    if "@RegisterForProxy" in decl_snippet or "@RegisterForProxy" in full_class_text:
                        targets: list[str] = []
                        proxy_match = re.search(r'@RegisterForProxy\s*\(\s*targets\s*=\s*\{([^}]+)\}\s*\)', full_class_text)
                        if proxy_match:
                            raw_t = proxy_match.group(1)
                            for item in raw_t.split(","):
                                clean_t = item.strip().replace(".class", "").strip()
                                if clean_t:
                                    targets.append(clean_t)

                        for tgt in targets:
                            facts.append(
                                QuarkusNativeFact(
                                    "register_proxy",
                                    tgt,
                                    decl.qualified_name,
                                    json.dumps({"target_interface": tgt, "annotated_class": decl.qualified_name}),
                                    path,
                                    decl.start_line,
                                    decl.end_line,
                                    scope="proxy",
                                )
                            )

                elif decl.kind == "method":
                    method_snippet = _get_method_snippet(lines, decl)

                    for forname_match in re.finditer(r'\bClass\.forName\s*\(\s*["\']([^"\']+)["\']\s*\)', method_snippet):
                        ref_class = forname_match.group(1)
                        call_line = decl.start_line + method_snippet[:forname_match.start()].count("\n")
                        facts.append(
                            QuarkusNativeFact(
                                "reflection_usage",
                                decl.qualified_name,
                                ref_class,
                                json.dumps({"target_class": ref_class}),
                                path,
                                call_line,
                                call_line,
                                scope="reflection",
                            )
                        )

    return tuple(facts)


def _quarkus_native_relationships(
    connection_data_path: Path,
    owner: str,
    target: ImpactTarget,
    build_profiles: tuple[str, ...] = (),
    runtime_profiles: tuple[str, ...] = (),
) -> tuple[tuple[ImpactRelationship, ...], tuple[UnresolvedItem, ...]]:
    connection = sqlite3.connect(connection_data_path)
    try:
        nat_rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line, scope
            FROM quarkus_native_facts ORDER BY path, start_line"""
        ).fetchall()
        rest_rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line FROM quarkus_rest_facts"""
        ).fetchall()
        config_rows = connection.execute(
            """SELECT subject, target, value, path, start_line, end_line, profile FROM quarkus_config_facts"""
        ).fetchall()
    except sqlite3.OperationalError:
        return (), ()
    finally:
        connection.close()

    relationships: list[ImpactRelationship] = []
    unresolved: list[UnresolvedItem] = []
    seen_keys: set[tuple[str, str, Path, int, int]] = set()

    target_sig = target.signature
    target_class = target_sig.rsplit("#", 1)[0]
    target_method = target_sig.split("#")[-1].split("(")[0] if "#" in target_sig else None
    clean_target_class = target_class.rsplit(".", 1)[-1]

    nat_facts = [
        QuarkusNativeFact(kind, subject, fact_target, value, Path(path), start_line, end_line, scope)
        for kind, subject, fact_target, value, path, start_line, end_line, scope in nat_rows
    ]

    for nf in nat_facts:
        if nf.kind in ("register_reflection", "native_json_config") and nf.scope == "reflection":
            clean_sub = nf.subject.rsplit(".", 1)[-1]
            if nf.subject == target_class or clean_sub == clean_target_class:
                handle = f"quarkus_native:{nf.path.as_posix()}:{nf.start_line}-{nf.end_line}"
                rel_key = ("quarkus_native_reflection", nf.subject, nf.path, nf.start_line, nf.end_line)
                if rel_key not in seen_keys:
                    seen_keys.add(rel_key)
                    relationships.append(
                        ImpactRelationship(
                            "quarkus_native_reflection",
                            f"Native Reflection ({nf.subject}) -> {target_sig}",
                            nf.path,
                            nf.start_line,
                            nf.end_line,
                            handle,
                            "high",
                            False,
                            None,
                            evidence_chain=(handle,),
                            business_view=nf.value,
                        )
                    )

    for nf in nat_facts:
        if nf.kind in ("register_proxy", "native_json_config") and nf.scope == "proxy":
            clean_sub = nf.subject.rsplit(".", 1)[-1]
            if nf.subject == target_class or clean_sub == clean_target_class:
                handle = f"quarkus_native:{nf.path.as_posix()}:{nf.start_line}-{nf.end_line}"
                rel_key = ("quarkus_native_proxy", nf.subject, nf.path, nf.start_line, nf.end_line)
                if rel_key not in seen_keys:
                    seen_keys.add(rel_key)
                    relationships.append(
                        ImpactRelationship(
                            "quarkus_native_proxy",
                            f"Native Proxy ({nf.subject}) -> {target_sig}",
                            nf.path,
                            nf.start_line,
                            nf.end_line,
                            handle,
                            "high",
                            False,
                            None,
                            evidence_chain=(handle,),
                            business_view=nf.value,
                        )
                    )

    for nf in nat_facts:
        if nf.kind == "meta_inf_service":
            clean_sub = nf.subject.rsplit(".", 1)[-1]
            clean_tgt = nf.target.rsplit(".", 1)[-1] if nf.target else ""
            if nf.subject == target_class or clean_sub == clean_target_class or (nf.target and (nf.target == target_class or clean_tgt == clean_target_class)):
                handle = f"quarkus_native:{nf.path.as_posix()}:{nf.start_line}-{nf.end_line}"
                rel_key = ("quarkus_native_spi", nf.subject, nf.path, nf.start_line, nf.end_line)
                if rel_key not in seen_keys:
                    seen_keys.add(rel_key)
                    relationships.append(
                        ImpactRelationship(
                            "quarkus_native_spi",
                            f"META-INF/services ({nf.subject}) -> {nf.target}",
                            nf.path,
                            nf.start_line,
                            nf.end_line,
                            handle,
                            "high",
                            False,
                            None,
                            evidence_chain=(handle,),
                            business_view=nf.value,
                        )
                    )

    for nf in nat_facts:
        if nf.kind == "reflection_usage" and nf.target:
            clean_tgt = nf.target.rsplit(".", 1)[-1]
            if nf.target == target_class or clean_tgt == clean_target_class:
                usage_handle = f"quarkus_native:{nf.path.as_posix()}:{nf.start_line}-{nf.end_line}"
                chain = [usage_handle]
                reg_fact = next((r for r in nat_facts if r.kind in ("register_reflection", "native_json_config") and (r.subject == target_class or r.subject.rsplit(".", 1)[-1] == clean_target_class)), None)
                if reg_fact:
                    reg_handle = f"quarkus_native:{reg_fact.path.as_posix()}:{reg_fact.start_line}-{reg_fact.end_line}"
                    chain.append(reg_handle)

                rel_key = ("quarkus_native_reflection_usage", nf.subject, nf.path, nf.start_line, nf.end_line)
                if rel_key not in seen_keys:
                    seen_keys.add(rel_key)
                    relationships.append(
                        ImpactRelationship(
                            "quarkus_native_reflection_usage",
                            f"Class.forName({nf.target}) in {nf.subject} -> {target_sig}",
                            nf.path,
                            nf.start_line,
                            nf.end_line,
                            usage_handle,
                            "high",
                            False,
                            None,
                            evidence_chain=tuple(chain),
                            business_view=nf.value,
                        )
                    )

    dto_types_found: set[str] = set()
    target_rest_handles: list[tuple[str, Path, int, int]] = []

    for r_kind, r_subj, r_tgt, r_val, r_path, r_sl, r_el in rest_rows:
        if r_kind in ("rest_endpoint", "rest_client_method", "rest_client_interface") and (r_subj == target_sig or r_subj.partition("#")[0] == target_class or r_subj.partition("#")[0].rsplit(".", 1)[-1] == clean_target_class):
            rest_h = f"quarkus_rest:{Path(r_path).as_posix()}:{r_sl}-{r_el}"
            target_rest_handles.append((rest_h, Path(r_path), r_sl, r_el))
            if r_val:
                try:
                    meta = json.loads(r_val)
                    if isinstance(meta, dict) and "dto_types" in meta:
                        for dt in meta["dto_types"]:
                            dto_types_found.add(dt)
                except Exception:
                    pass

    if not dto_types_found and "#" in target_sig:
        ret_part = target_sig.split("#")[-1]
        m_dto = re.search(r'([A-Za-z0-9_$]+DTO)', ret_part)
        if m_dto:
            dto_types_found.add(m_dto.group(1))

    for dto_name in sorted(dto_types_found):
        reg_fact = next((r for r in nat_facts if r.kind in ("register_reflection", "native_json_config") and (r.subject == dto_name or r.subject.rsplit(".", 1)[-1] == dto_name)), None)
        if reg_fact:
            reg_h = f"quarkus_native:{reg_fact.path.as_posix()}:{reg_fact.start_line}-{reg_fact.end_line}"
            chain = []
            if target_rest_handles:
                chain.append(target_rest_handles[0][0])
            chain.append(reg_h)

            rel_key = ("quarkus_native_dto", dto_name, reg_fact.path, reg_fact.start_line, reg_fact.end_line)
            if rel_key not in seen_keys:
                seen_keys.add(rel_key)
                relationships.append(
                    ImpactRelationship(
                        "quarkus_native_dto",
                        f"REST DTO Native Reflection ({dto_name}) -> {target_sig}",
                        reg_fact.path,
                        reg_fact.start_line,
                        reg_fact.end_line,
                        reg_h,
                        "high",
                        False,
                        None,
                        evidence_chain=tuple(chain),
                        business_view=json.dumps({"dto_type": dto_name, "disclaimer": "GraalVM/Mandrel compilation was not executed; complete closed-world reachability was not reconstructed."}),
                    )
                )
        else:
            unresolved.append(
                UnresolvedItem(
                    f"DTO type '{dto_name}' used in REST contract signature lacks explicit @RegisterForReflection or native-image reflection metadata.",
                    path=target_rest_handles[0][1] if target_rest_handles else None,
                    start_line=target_rest_handles[0][2] if target_rest_handles else 1,
                    end_line=target_rest_handles[0][3] if target_rest_handles else 1,
                    evidence_handle=target_rest_handles[0][0] if target_rest_handles else f"quarkus_native:unresolved:{dto_name}",
                )
            )

    has_native_config = "native" in build_profiles or "native" in runtime_profiles
    for c_subj, c_tgt, c_val, c_path, c_sl, c_el, c_prof in config_rows:
        if c_subj == "quarkus.package.type" and c_val == "native":
            has_native_config = True

    if has_native_config:
        for c_subj, c_tgt, c_val, c_path, c_sl, c_el, c_prof in config_rows:
            if c_subj in ("quarkus.package.type", "quarkus.native.additional-build-args") or c_subj.startswith("quarkus.native."):
                cfg_h = f"quarkus_config:{Path(c_path).as_posix()}:{c_sl}-{c_el}"
                rel_key = ("quarkus_native_config", c_subj, Path(c_path), c_sl, c_el)
                if rel_key not in seen_keys:
                    seen_keys.add(rel_key)
                    relationships.append(
                        ImpactRelationship(
                            "quarkus_native_config",
                            f"Native Build Config ({c_subj}={c_val}) -> {target_sig}",
                            Path(c_path),
                            c_sl,
                            c_el,
                            cfg_h,
                            "high",
                            False,
                            c_prof,
                            evidence_chain=(cfg_h,),
                            business_view=json.dumps({"key": c_subj, "value": c_val, "profile": c_prof}),
                        )
                    )

    return tuple(relationships), tuple(unresolved)
    connection = sqlite3.connect(connection_data_path)
    try:
        nat_rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line, scope
            FROM quarkus_native_facts ORDER BY path, start_line"""
        ).fetchall()
    except sqlite3.OperationalError:
        return (), ()
    finally:
        connection.close()

    if not nat_rows:
        return (), ()

    nat_facts = [
        QuarkusNativeFact(kind, subject, fact_target, value, Path(path), start_line, end_line, scope)
        for kind, subject, fact_target, value, path, start_line, end_line, scope in nat_rows
    ]

    relationships: list[ImpactRelationship] = []
    unresolved: list[UnresolvedItem] = []
    seen_keys: set[tuple[str, str, Path, int, int]] = set()

    target_sig = target.signature
    target_class = target_sig.rsplit("#", 1)[0]
    target_method = target_sig.split("#")[-1].split("(")[0] if "#" in target_sig else None
    clean_target_class = target_class.rsplit(".", 1)[-1]

    for nf in nat_facts:
        if nf.kind in ("register_reflection", "native_json_config") and nf.scope == "reflection":
            clean_sub = nf.subject.rsplit(".", 1)[-1]
            if nf.subject == target_class or clean_sub == clean_target_class:
                handle = f"quarkus_native:{nf.path.as_posix()}:{nf.start_line}-{nf.end_line}"
                rel_key = ("quarkus_native_reflection", nf.subject, nf.path, nf.start_line, nf.end_line)
                if rel_key not in seen_keys:
                    seen_keys.add(rel_key)
                    relationships.append(
                        ImpactRelationship(
                            "quarkus_native_reflection",
                            f"Native Reflection ({nf.subject}) -> {target_sig}",
                            nf.path,
                            nf.start_line,
                            nf.end_line,
                            handle,
                            "high",
                            False,
                            None,
                            evidence_chain=(handle,),
                            business_view=nf.value,
                        )
                    )

    for nf in nat_facts:
        if nf.kind in ("register_proxy", "native_json_config") and nf.scope == "proxy":
            clean_sub = nf.subject.rsplit(".", 1)[-1]
            if nf.subject == target_class or clean_sub == clean_target_class:
                handle = f"quarkus_native:{nf.path.as_posix()}:{nf.start_line}-{nf.end_line}"
                rel_key = ("quarkus_native_proxy", nf.subject, nf.path, nf.start_line, nf.end_line)
                if rel_key not in seen_keys:
                    seen_keys.add(rel_key)
                    relationships.append(
                        ImpactRelationship(
                            "quarkus_native_proxy",
                            f"Native Proxy ({nf.subject}) -> {target_sig}",
                            nf.path,
                            nf.start_line,
                            nf.end_line,
                            handle,
                            "high",
                            False,
                            None,
                            evidence_chain=(handle,),
                            business_view=nf.value,
                        )
                    )

    for nf in nat_facts:
        if nf.kind == "meta_inf_service":
            clean_sub = nf.subject.rsplit(".", 1)[-1]
            clean_tgt = nf.target.rsplit(".", 1)[-1] if nf.target else ""
            if nf.subject == target_class or clean_sub == clean_target_class or (nf.target and (nf.target == target_class or clean_tgt == clean_target_class)):
                handle = f"quarkus_native:{nf.path.as_posix()}:{nf.start_line}-{nf.end_line}"
                rel_key = ("quarkus_native_spi", nf.subject, nf.path, nf.start_line, nf.end_line)
                if rel_key not in seen_keys:
                    seen_keys.add(rel_key)
                    relationships.append(
                        ImpactRelationship(
                            "quarkus_native_spi",
                            f"META-INF/services ({nf.subject}) -> {nf.target}",
                            nf.path,
                            nf.start_line,
                            nf.end_line,
                            handle,
                            "high",
                            False,
                            None,
                            evidence_chain=(handle,),
                            business_view=nf.value,
                        )
                    )

    for nf in nat_facts:
        if nf.kind == "reflection_usage" and nf.target:
            clean_tgt = nf.target.rsplit(".", 1)[-1]
            if nf.target == target_class or clean_tgt == clean_target_class:
                usage_handle = f"quarkus_native:{nf.path.as_posix()}:{nf.start_line}-{nf.end_line}"
                chain = [usage_handle]
                reg_fact = next((r for r in nat_facts if r.kind in ("register_reflection", "native_json_config") and (r.subject == target_class or r.subject.rsplit(".", 1)[-1] == clean_target_class)), None)
                if reg_fact:
                    reg_handle = f"quarkus_native:{reg_fact.path.as_posix()}:{reg_fact.start_line}-{reg_fact.end_line}"
                    chain.append(reg_handle)

                rel_key = ("quarkus_native_reflection_usage", nf.subject, nf.path, nf.start_line, nf.end_line)
                if rel_key not in seen_keys:
                    seen_keys.add(rel_key)
                    relationships.append(
                        ImpactRelationship(
                            "quarkus_native_reflection_usage",
                            f"Class.forName({nf.target}) in {nf.subject} -> {target_sig}",
                            nf.path,
                            nf.start_line,
                            nf.end_line,
                            usage_handle,
                            "high",
                            False,
                            None,
                            evidence_chain=tuple(chain),
                            business_view=nf.value,
                        )
                    )

    return tuple(relationships), tuple(unresolved)
    connection.executemany(
        """INSERT INTO quarkus_test_facts(
        kind, subject, target, value, path, start_line, end_line, flavor
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                fact.kind,
                fact.subject,
                fact.target,
                fact.value,
                str(fact.path),
                fact.start_line,
                fact.end_line,
                fact.flavor,
            )
            for fact in facts
        ),
    )


def _analyze_quarkus_test_files(
    contents_by_path: dict[Path, bytes],
    declarations: tuple[JavaDeclaration, ...],
) -> tuple[QuarkusTestFact, ...]:
    facts: list[QuarkusTestFact] = []

    for path, content in sorted(contents_by_path.items(), key=lambda item: str(item[0])):
        if path.suffix.lower() == ".java":
            text = content.decode("utf-8", errors="replace")
            lines = text.splitlines()

            if "QuarkusMock." in text:
                for decl in declarations:
                    if decl.path == path:
                        facts.append(
                            QuarkusTestFact(
                                "test_unresolved",
                                decl.qualified_name,
                                "dynamic_mock",
                                "Dynamic mock installation via QuarkusMock at runtime.",
                                path,
                                decl.start_line,
                                decl.end_line,
                                flavor=None,
                            )
                        )

            for decl in declarations:
                if decl.path != path:
                    continue

                if decl.kind in ("class", "interface"):
                    decl_snippet = _get_class_snippet(lines, decl)

                    flavor = None
                    if "@QuarkusTest" in decl_snippet:
                        flavor = "quarkus_test"
                    elif "@QuarkusIntegrationTest" in decl_snippet:
                        flavor = "quarkus_integration_test"
                    elif "@QuarkusComponentTest" in decl_snippet:
                        flavor = "quarkus_component_test"
                    elif "@NativeImageTest" in decl_snippet:
                        flavor = "quarkus_native_test"

                    if flavor:
                        facts.append(
                            QuarkusTestFact(
                                "test_class",
                                decl.qualified_name,
                                None,
                                None,
                                path,
                                decl.start_line,
                                decl.end_line,
                                flavor=flavor,
                            )
                        )

                    ep_match = re.search(r'@TestHTTPEndpoint\s*\(\s*(?:value\s*=\s*)?([A-Za-z0-9_$.]+)(?:\.class)?\s*\)', decl_snippet)
                    if ep_match:
                        res_name = ep_match.group(1).replace(".class", "").strip()
                        facts.append(
                            QuarkusTestFact(
                                "test_http_endpoint",
                                decl.qualified_name,
                                res_name,
                                json.dumps({"target_resource": res_name}),
                                path,
                                decl.start_line,
                                decl.end_line,
                                flavor=flavor,
                            )
                        )

                    prof_match = re.search(r'@TestProfile\s*\(\s*(?:value\s*=\s*)?([A-Za-z0-9_$.]+)(?:\.class)?\s*\)', decl_snippet)
                    if prof_match:
                        prof_name = prof_match.group(1).replace(".class", "").strip()
                        facts.append(
                            QuarkusTestFact(
                                "test_profile",
                                decl.qualified_name,
                                prof_name,
                                json.dumps({"test_profile": prof_name}),
                                path,
                                decl.start_line,
                                decl.end_line,
                                flavor=flavor,
                            )
                        )

                    full_class_text = "\n".join(lines[max(0, decl.start_line - 1) : decl.end_line])
                    for f_match in re.finditer(r'(@InjectMock|@InjectSpy)[\s\n]+(?:private|protected|public)?[\s\n]*([A-Za-z0-9_$.<>]+)[\s\n]+([A-Za-z0-9_$]+)\s*;', full_class_text):
                        annot = f_match.group(1).strip("@")
                        fieldType = f_match.group(2).strip()
                        field_line = decl.start_line + full_class_text[:f_match.start()].count("\n")
                        facts.append(
                            QuarkusTestFact(
                                "inject_mock" if annot == "InjectMock" else "inject_spy",
                                decl.qualified_name,
                                fieldType,
                                json.dumps({"annotation": annot, "field_type": fieldType}),
                                path,
                                field_line,
                                field_line,
                                flavor=flavor,
                            )
                        )

                    for http_res_match in re.finditer(r'@TestHTTPResource\s*\(\s*["\']([^"\']+)["\']\s*\)', full_class_text):
                        res_path = http_res_match.group(1)
                        field_line = decl.start_line + full_class_text[:http_res_match.start()].count("\n")
                        facts.append(
                            QuarkusTestFact(
                                "test_http_resource",
                                decl.qualified_name,
                                res_path,
                                json.dumps({"path": res_path}),
                                path,
                                field_line,
                                field_line,
                                flavor=flavor,
                            )
                        )

                elif decl.kind == "method":
                    method_snippet = _get_method_snippet(lines, decl)

                    for ra_match in re.finditer(r'\b(given\(\)\.)?(get|post|put|delete|patch|head|options)\s*\(\s*["\']([^"\']+)["\']\s*\)', method_snippet):
                        http_method = ra_match.group(2).upper()
                        target_path = ra_match.group(3)
                        call_line = decl.start_line + method_snippet[:ra_match.start()].count("\n")
                        facts.append(
                            QuarkusTestFact(
                                "rest_assured_call",
                                decl.qualified_name,
                                target_path,
                                json.dumps({"method": http_method, "path": target_path}),
                                path,
                                call_line,
                                call_line,
                                flavor=None,
                            )
                        )

    return tuple(facts)


def _quarkus_test_relationships(
    connection_data_path: Path,
    owner: str,
    target: ImpactTarget,
) -> tuple[tuple[ImpactRelationship, ...], tuple[UnresolvedItem, ...]]:
    connection = sqlite3.connect(connection_data_path)
    try:
        test_rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line, flavor
            FROM quarkus_test_facts ORDER BY path, start_line"""
        ).fetchall()
        rest_rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line FROM quarkus_rest_facts"""
        ).fetchall()
    except sqlite3.OperationalError:
        return (), ()
    finally:
        connection.close()

    if not test_rows:
        return (), ()

    test_facts = [
        QuarkusTestFact(kind, subject, fact_target, value, Path(path), start_line, end_line, flavor)
        for kind, subject, fact_target, value, path, start_line, end_line, flavor in test_rows
    ]

    relationships: list[ImpactRelationship] = []
    unresolved: list[UnresolvedItem] = []
    seen_keys: set[tuple[str, str, Path, int, int]] = set()

    target_sig = target.signature
    target_class = target_sig.rsplit("#", 1)[0]
    target_method = target_sig.split("#")[-1].split("(")[0] if "#" in target_sig else None
    clean_target_class = target_class.rsplit(".", 1)[-1]

    def matches_target(f_subject: str) -> bool:
        if f_subject == target_sig or f_subject == owner:
            return True
        f_c, _, f_m = f_subject.partition("#")
        clean_fm = f_m.split("(")[0] if f_m else ""
        clean_fc = f_c.rsplit(".", 1)[-1]
        if f_c == target_class or clean_fc == clean_target_class:
            if not target_method or not clean_fm or clean_fm == target_method:
                return True
        return False

    for tf in test_facts:
        if tf.kind == "test_unresolved" and matches_target(tf.subject):
            unresolved.append(
                UnresolvedItem(
                    tf.value or f"Dynamic test mock installation on {tf.subject}",
                    path=tf.path,
                    start_line=tf.start_line,
                    end_line=tf.end_line,
                    evidence_handle=f"quarkus_test:{tf.path.as_posix()}:{tf.start_line}-{tf.end_line}",
                )
            )

    class_flavors: dict[str, str] = {}
    for tf in test_facts:
        if tf.kind == "test_class" and tf.flavor:
            class_flavors[tf.subject] = tf.flavor
            class_flavors[tf.subject.rsplit(".", 1)[-1]] = tf.flavor

    def get_confidence(test_cls: str, explicit_flavor: str | None = None) -> str:
        flv = explicit_flavor or class_flavors.get(test_cls) or class_flavors.get(test_cls.rsplit(".", 1)[-1])
        if flv in ("quarkus_integration_test", "quarkus_native_test"):
            return "medium"
        return "high"

    for tf in test_facts:
        if tf.kind in ("inject_mock", "inject_spy") and tf.target:
            clean_tgt = tf.target.rsplit(".", 1)[-1]
            if tf.target == target_class or clean_tgt == clean_target_class:
                conf = get_confidence(tf.subject, tf.flavor)
                handle = f"quarkus_test:{tf.path.as_posix()}:{tf.start_line}-{tf.end_line}"
                rel_key = ("quarkus_test_mock", tf.subject, tf.path, tf.start_line, tf.end_line)
                if rel_key not in seen_keys:
                    seen_keys.add(rel_key)
                    relationships.append(
                        ImpactRelationship(
                            "quarkus_test_mock",
                            f"{tf.subject} -> {target_sig}",
                            tf.path,
                            tf.start_line,
                            tf.end_line,
                            handle,
                            conf,
                            True,
                            None,
                            evidence_chain=(handle,),
                            business_view=tf.value,
                        )
                    )

    matching_endpoints = [r for r in rest_rows if r[0] == "rest_endpoint" and matches_target(r[1])]
    for ep in matching_endpoints:
        ep_meta = json.loads(ep[3]) if ep[3] else {}
        ep_path = ep_meta.get("method_path", "")
        ep_class = ep[1].rsplit("#", 1)[0]
        ep_class_clean = ep_class.rsplit(".", 1)[-1]
        ep_class_facts = [r for r in rest_rows if r[0] == "rest_resource" and r[1] == ep_class]
        ep_class_path = ep_class_facts[0][2] if ep_class_facts else ""
        full_route = "/" + "/".join(p.strip("/") for p in (ep_class_path, ep_path) if p.strip("/"))

        for tf in test_facts:
            matched = False
            if tf.kind == "test_http_endpoint" and tf.target:
                clean_tf_tgt = tf.target.rsplit(".", 1)[-1]
                if tf.target == ep_class or clean_tf_tgt == ep_class_clean:
                    matched = True
            elif tf.kind == "test_http_resource" and tf.target:
                if full_route.startswith(tf.target.rstrip("*").rstrip("/")) or tf.target == full_route:
                    matched = True
            elif tf.kind == "rest_assured_call" and tf.target:
                if full_route.startswith(tf.target.rstrip("*").rstrip("/")) or tf.target == full_route:
                    matched = True

            if matched:
                conf = get_confidence(tf.subject, tf.flavor)
                handle = f"quarkus_test:{tf.path.as_posix()}:{tf.start_line}-{tf.end_line}"
                rel_key = ("quarkus_test_endpoint", tf.subject, tf.path, tf.start_line, tf.end_line)
                if rel_key not in seen_keys:
                    seen_keys.add(rel_key)
                    relationships.append(
                        ImpactRelationship(
                            "quarkus_test_endpoint",
                            f"{tf.subject} -> {full_route}",
                            tf.path,
                            tf.start_line,
                            tf.end_line,
                            handle,
                            conf,
                            True,
                            None,
                            evidence_chain=(handle,),
                            business_view=tf.value,
                        )
                    )

    return tuple(relationships), tuple(unresolved)


def _extract_security_annotations(snippet: str) -> dict[str, Any] | None:
    roles_match = re.search(r'@RolesAllowed\s*\(\s*(?:\{([^}]+)\}|["\']([^"\']+)["\'])\s*\)', snippet)
    if roles_match:
        raw_roles = roles_match.group(1) or roles_match.group(2) or ""
        roles = [r.strip().strip('"').strip("'") for r in raw_roles.split(",") if r.strip()]
        return {"policy_type": "roles_allowed", "roles": roles, "annotation": "@RolesAllowed"}

    if re.search(r'@PermitAll\b', snippet):
        return {"policy_type": "permit_all", "roles": [], "annotation": "@PermitAll"}

    if re.search(r'@DenyAll\b', snippet):
        return {"policy_type": "deny_all", "roles": [], "annotation": "@DenyAll"}

    if re.search(r'@Authenticated\b', snippet):
        return {"policy_type": "authenticated", "roles": [], "annotation": "@Authenticated"}

    perm_match = re.search(r'@PermissionsAllowed\s*\(\s*(?:\{([^}]+)\}|["\']([^"\']+)["\'])\s*\)', snippet)
    if perm_match:
        raw_perms = perm_match.group(1) or perm_match.group(2) or ""
        perms = [p.strip().strip('"').strip("'") for p in raw_perms.split(",") if p.strip()]
        return {"policy_type": "permissions_allowed", "permissions": perms, "annotation": "@PermissionsAllowed"}

    return None


def _analyze_quarkus_security_files(
    contents_by_path: dict[Path, bytes],
    declarations: tuple[JavaDeclaration, ...],
    quarkus_config_facts: tuple[QuarkusConfigFact, ...] = (),
) -> tuple[QuarkusSecurityFact, ...]:
    facts: list[QuarkusSecurityFact] = []

    perm_rules: dict[str, dict[str, Any]] = {}
    for cfg in quarkus_config_facts:
        if cfg.kind == "quarkus_property_source" and cfg.subject.startswith("quarkus.http.auth.permission."):
            rest_key = cfg.subject[len("quarkus.http.auth.permission."):]
            if "." in rest_key:
                perm_name, attr = rest_key.split(".", 1)
                rule = perm_rules.setdefault(
                    perm_name,
                    {"name": perm_name, "path": cfg.path, "start_line": cfg.start_line, "end_line": cfg.end_line},
                )
                rule[attr] = cfg.value

    for perm_name, rule in perm_rules.items():
        if "paths" in rule or "policy" in rule:
            facts.append(
                QuarkusSecurityFact(
                    "security_config_policy",
                    f"quarkus.http.auth.permission.{perm_name}",
                    rule.get("paths"),
                    json.dumps({
                        "name": perm_name,
                        "paths": rule.get("paths"),
                        "policy": rule.get("policy"),
                        "roles_allowed": rule.get("roles-allowed"),
                        "methods": rule.get("methods"),
                    }),
                    rule["path"],
                    rule["start_line"],
                    rule["end_line"],
                    policy=rule.get("policy"),
                )
            )

    for path, content in sorted(contents_by_path.items(), key=lambda item: str(item[0])):
        if path.suffix.lower() == ".java":
            text = content.decode("utf-8", errors="replace")
            lines = text.splitlines()

            if re.search(r'\bimplements\s+([A-Za-z0-9_$,\s]*\b(SecurityIdentityAugmentor|IdentityProvider|SecurityPolicy)\b[A-Za-z0-9_$,\s]*)', text):
                for decl in declarations:
                    if decl.path == path and decl.kind in ("class", "interface"):
                        facts.append(
                            QuarkusSecurityFact(
                                "security_unresolved",
                                decl.qualified_name,
                                "custom_security",
                                f"Custom security component {decl.qualified_name} implements dynamic authentication or identity augmentation.",
                                path,
                                decl.start_line,
                                decl.end_line,
                                policy="custom",
                            )
                        )

            for decl in declarations:
                if decl.path != path:
                    continue

                if decl.kind in ("class", "interface"):
                    decl_snippet = _get_class_snippet(lines, decl)
                    sec_info = _extract_security_annotations(decl_snippet)
                    if sec_info:
                        facts.append(
                            QuarkusSecurityFact(
                                "security_annotation_class",
                                decl.qualified_name,
                                None,
                                json.dumps(sec_info),
                                path,
                                decl.start_line,
                                decl.end_line,
                                policy=sec_info.get("policy_type"),
                            )
                        )

                elif decl.kind == "method":
                    method_snippet = _get_method_snippet(lines, decl)
                    sec_info = _extract_security_annotations(method_snippet)
                    if sec_info:
                        facts.append(
                            QuarkusSecurityFact(
                                "security_annotation_method",
                                decl.qualified_name,
                                decl.qualified_name.rsplit("#", 1)[0],
                                json.dumps(sec_info),
                                path,
                                decl.start_line,
                                decl.end_line,
                                policy=sec_info.get("policy_type"),
                            )
                        )

    return tuple(facts)


def _quarkus_security_relationships(
    connection_data_path: Path,
    owner: str,
    target: ImpactTarget,
    build_profiles: tuple[str, ...] = (),
    runtime_profiles: tuple[str, ...] = (),
) -> tuple[tuple[ImpactRelationship, ...], tuple[UnresolvedItem, ...]]:
    connection = sqlite3.connect(connection_data_path)
    try:
        sec_rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line, policy
            FROM quarkus_security_facts ORDER BY path, start_line"""
        ).fetchall()
        config_rows = connection.execute(
            """SELECT subject, value, path, start_line, end_line FROM quarkus_config_facts WHERE kind = 'quarkus_property_source'"""
        ).fetchall()
        rest_rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line FROM quarkus_rest_facts"""
        ).fetchall()
    except sqlite3.OperationalError:
        return (), ()
    finally:
        connection.close()

    if not sec_rows:
        return (), ()

    sec_facts = [
        QuarkusSecurityFact(kind, subject, fact_target, value, Path(path), start_line, end_line, policy)
        for kind, subject, fact_target, value, path, start_line, end_line, policy in sec_rows
    ]

    relationships: list[ImpactRelationship] = []
    unresolved: list[UnresolvedItem] = []
    seen_keys: set[tuple[str, str, Path, int, int]] = set()

    target_sig = target.signature
    target_class = target_sig.rsplit("#", 1)[0]
    target_method = target_sig.split("#")[-1].split("(")[0] if "#" in target_sig else None

    def matches_subject(f_subject: str) -> bool:
        if f_subject == target_sig or f_subject == owner:
            return True
        f_class, _, f_m = f_subject.partition("#")
        clean_fm = f_m.split("(")[0] if f_m else ""
        clean_target_class = target_class.rsplit(".", 1)[-1]
        clean_f_class = f_class.rsplit(".", 1)[-1]
        if f_class == target_class or clean_f_class == clean_target_class:
            if not target_method or not clean_fm or clean_fm == target_method:
                return True
        return False

    for sf in sec_facts:
        if sf.kind == "security_unresolved" and matches_subject(sf.subject):
            unresolved.append(
                UnresolvedItem(
                    sf.value or f"Custom security component on {sf.subject}",
                    path=sf.path,
                    start_line=sf.start_line,
                    end_line=sf.end_line,
                    evidence_handle=f"quarkus_security:{sf.path.as_posix()}:{sf.start_line}-{sf.end_line}",
                )
            )

    method_sec = next((sf for sf in sec_facts if sf.kind == "security_annotation_method" and matches_subject(sf.subject)), None)
    active_sec = method_sec
    if active_sec is None:
        active_sec = next((sf for sf in sec_facts if sf.kind == "security_annotation_class" and (sf.subject == target_class or sf.subject.rsplit(".", 1)[-1] == target_class.rsplit(".", 1)[-1])), None)

    if active_sec is not None:
        sec_meta = json.loads(active_sec.value) if active_sec.value else {}
        sec_handle = f"quarkus_security:{active_sec.path.as_posix()}:{active_sec.start_line}-{active_sec.end_line}"
        chain = [sec_handle]

        raw_val = active_sec.value or ""
        if "${" in raw_val:
            for expr_match in re.finditer(r"\$\{([^}:]+)(?::[^}]*)?\}", raw_val):
                prop_key = expr_match.group(1).strip()
                for cfg_sub, cfg_val, cfg_p, cfg_sl, cfg_el in config_rows:
                    if cfg_sub == prop_key:
                        cfg_path = Path(cfg_p)
                        cfg_handle = f"quarkus_config:{cfg_path.as_posix()}:{cfg_sl}-{cfg_el}"
                        if cfg_handle not in chain:
                            chain.append(cfg_handle)

        rel_key = ("quarkus_security_policy", target_sig, active_sec.path, active_sec.start_line, active_sec.end_line)
        if rel_key not in seen_keys:
            seen_keys.add(rel_key)
            relationships.append(
                ImpactRelationship(
                    "quarkus_security_policy",
                    f"{sec_meta.get('annotation', '@Security')} -> {target_sig}",
                    active_sec.path,
                    active_sec.start_line,
                    active_sec.end_line,
                    sec_handle,
                    "high",
                    False,
                    None,
                    evidence_chain=tuple(chain),
                    business_view=json.dumps(sec_meta),
                )
            )

    config_policies = [sf for sf in sec_facts if sf.kind == "security_config_policy"]
    if config_policies:
        matching_endpoints = [r for r in rest_rows if r[0] == "rest_endpoint" and matches_subject(r[1])]
        for ep in matching_endpoints:
            ep_meta = json.loads(ep[3]) if ep[3] else {}
            ep_path = ep_meta.get("method_path", "")
            ep_class_facts = [r for r in rest_rows if r[0] == "rest_resource" and r[1] == ep[1].rsplit("#", 1)[0]]
            ep_class_path = ep_class_facts[0][2] if ep_class_facts else ""
            full_route = "/" + "/".join(p.strip("/") for p in (ep_class_path, ep_path) if p.strip("/"))

            for cp in config_policies:
                cp_meta = json.loads(cp.value) if cp.value else {}
                path_pattern = cp_meta.get("paths", "")
                if path_pattern:
                    clean_pattern = path_pattern.rstrip("*").rstrip("/")
                    if full_route.startswith(clean_pattern) or clean_pattern == "":
                        cp_handle = f"quarkus_security:{cp.path.as_posix()}:{cp.start_line}-{cp.end_line}"
                        rel_key_cp = ("quarkus_security_policy", cp.subject, cp.path, cp.start_line, cp.end_line)
                        if rel_key_cp not in seen_keys:
                            seen_keys.add(rel_key_cp)
                            relationships.append(
                                ImpactRelationship(
                                    "quarkus_security_policy",
                                    f"Config Policy ({cp.subject}) -> {full_route}",
                                    cp.path,
                                    cp.start_line,
                                    cp.end_line,
                                    cp_handle,
                                    "high",
                                    False,
                                    None,
                                    evidence_chain=(cp_handle,),
                                    business_view=json.dumps(cp_meta),
                                )
                            )

    return tuple(relationships), tuple(unresolved)


def _analyze_quarkus_cdi_files(
    contents_by_path: dict[Path, bytes],
    declarations: tuple[JavaDeclaration, ...],
) -> tuple[QuarkusCDIFact, ...]:
    facts: list[QuarkusCDIFact] = []
    for path, content in sorted(contents_by_path.items(), key=lambda item: str(item[0])):
        if path.suffix.lower() == ".java":
            text = content.decode("utf-8", errors="replace")
            lines = text.splitlines()

            has_cdi_import = bool(
                re.search(
                    r"import\s+(?:jakarta|javax|io\.quarkus\.arc)\.",
                    text,
                )
            ) or "@Inject" in text or "@Produces" in text or "@ApplicationScoped" in text

            if has_cdi_import:
                for decl in declarations:
                    if decl.kind == "class" and decl.path == path:
                        decl_snippet = "\n".join(lines[max(0, decl.start_line - 6) : decl.end_line])
                        scope_match = re.search(
                            r"@(ApplicationScoped|RequestScoped|SessionScoped|Dependent|Singleton)",
                            decl_snippet,
                        )
                        named_match = re.search(r'@Named\s*(?:\(\s*["\']([^"\']+)["\']\s*\))?', decl_snippet)
                        named_val = named_match.group(1) if (named_match and named_match.group(1)) else (decl.name if named_match else None)

                        ifBuild_match = re.search(r'@IfBuildProfile\s*\(\s*["\']([^"\']+)["\']\s*\)', decl_snippet)
                        unlessBuild_match = re.search(r'@UnlessBuildProfile\s*\(\s*["\']([^"\']+)["\']\s*\)', decl_snippet)
                        cond = []
                        if ifBuild_match:
                            cond.append(f"if:{ifBuild_match.group(1)}")
                        if unlessBuild_match:
                            cond.append(f"unless:{unlessBuild_match.group(1)}")
                        cond_val = ";".join(cond) if cond else None

                        if scope_match:
                            scope_name = scope_match.group(1)
                            implements_match = re.search(
                                r"\bimplements\s+([A-Za-z0-9_$,\s]+?)(?:\{|implements|extends)",
                                decl_snippet,
                            )
                            interfaces = (
                                implements_match.group(1).strip()
                                if implements_match
                                else None
                            )
                            facts.append(
                                QuarkusCDIFact(
                                    "cdi_bean",
                                    decl.qualified_name,
                                    interfaces,
                                    named_val,
                                    path,
                                    decl.start_line,
                                    decl.end_line,
                                    scope=cond_val or scope_name,
                                )
                            )

                        # Check constructor injection (single constructor or @Inject constructor)
                        constructors = [d for d in declarations if d.kind == "constructor" and d.path == path and d.start_line >= decl.start_line and d.end_line <= decl.end_line]
                        for c in constructors:
                            c_line = lines[c.start_line - 1]
                            param_match = re.search(r"\(([^)]+)\)", c_line)
                            if param_match:
                                params = param_match.group(1).split(",")
                                for p in params:
                                    p_tokens = p.strip().split()
                                    if len(p_tokens) >= 2:
                                        p_type = p_tokens[-2]
                                        p_name = p_tokens[-1]
                                        facts.append(
                                            QuarkusCDIFact(
                                                "cdi_injection",
                                                c.qualified_name,
                                                p_type,
                                                p_name,
                                                path,
                                                c.start_line,
                                                c.start_line,
                                            )
                                        )

                # Check @Produces methods
                if "@Produces" in text:
                    for decl in declarations:
                        if decl.kind == "method" and decl.path == path:
                            method_snippet = "\n".join(lines[max(0, decl.start_line - 6) : decl.end_line])
                            if "@Produces" in method_snippet:
                                ret_match = re.search(
                                    r'([A-Za-z0-9_$.<>]+)\s+' + re.escape(decl.name) + r'\s*\(',
                                    method_snippet,
                                )
                                return_type = ret_match.group(1).strip() if (ret_match and ret_match.group(1) not in {"public", "protected", "private", "static", "final", "synchronized"}) else decl.name
                                named_match = re.search(r'@Named\s*(?:\(\s*["\']([^"\']+)["\']\s*\))?', method_snippet)
                                named_val = named_match.group(1) if (named_match and named_match.group(1)) else None
                                facts.append(
                                    QuarkusCDIFact(
                                        "cdi_producer",
                                        decl.qualified_name,
                                        return_type,
                                        named_val,
                                        path,
                                        decl.start_line,
                                        decl.end_line,
                                    )
                                )

                # Check @Inject / @RestClient fields / initializers
                if "@Inject" in text or "@RestClient" in text:
                    for line_idx, line in enumerate(lines, 1):
                        if "@Inject" in line or "@RestClient" in line:
                            field_snippet = "\n".join(lines[max(0, line_idx - 1) : min(len(lines), line_idx + 4)])
                            named_match = re.search(r'@Named\s*(?:\(\s*["\']([^"\']+)["\']\s*\))?', field_snippet)
                            named_val = named_match.group(1) if (named_match and named_match.group(1)) else None
                            is_rest_client_inj = "@RestClient" in field_snippet

                            field_match = re.search(
                                r"(?:@Inject\s+|@RestClient\s+)+(?:@[A-Za-z0-9_$.()'\"-]+\s+)*([A-Za-z0-9_$.<>]+)\s+([A-Za-z0-9_$]+)\s*;",
                                field_snippet,
                                re.DOTALL,
                            )
                            if field_match:
                                field_type = field_match.group(1).strip()
                                field_name = field_match.group(2).strip()
                                owner_decl = next(
                                    (
                                        d
                                        for d in declarations
                                        if d.path == path and d.start_line <= line_idx <= d.end_line
                                    ),
                                    None,
                                )
                                subject = owner_decl.qualified_name if owner_decl else ""
                                facts.append(
                                    QuarkusCDIFact(
                                        "cdi_injection",
                                        subject,
                                        field_type,
                                        field_name,
                                        path,
                                        line_idx,
                                        line_idx,
                                        scope="RestClient" if is_rest_client_inj else named_val,
                                    )
                                )

                # Check dynamic CDI patterns (Instance<T>, Provider<T>, CDI.current())
                for line_idx, line in enumerate(lines, 1):
                    if re.search(r"\b(?:Instance|Provider)<|CDI\.current\(\)", line):
                        owner_decl = next(
                            (
                                d
                                for d in declarations
                                if d.path == path and d.start_line <= line_idx <= d.end_line
                            ),
                            None,
                        )
                        subject = owner_decl.qualified_name if owner_decl else ""
                        facts.append(
                            QuarkusCDIFact(
                                "cdi_dynamic",
                                subject,
                                "dynamic",
                                line.strip(),
                                path,
                                line_idx,
                                line_idx,
                            )
                        )
    return tuple(facts)


def _extract_build_profile_conditions(snippet: str) -> list[str]:
    conds = []
    if_prof = re.search(r'@IfBuildProfile\s*\(\s*(?:stringValues\s*=\s*)?["\']([^"\']+)["\']\s*\)', snippet)
    if if_prof:
        conds.append(f"if:{if_prof.group(1)}")
    unless_prof = re.search(r'@UnlessBuildProfile\s*\(\s*(?:stringValues\s*=\s*)?["\']([^"\']+)["\']\s*\)', snippet)
    if unless_prof:
        conds.append(f"unless:{unless_prof.group(1)}")
    if_prop = re.search(r'@IfBuildProperty\s*\(\s*name\s*=\s*["\']([^"\']+)["\'](?:\s*,\s*stringValue\s*=\s*["\']([^"\']+)["\'])?\s*\)', snippet)
    if if_prop:
        conds.append(f"if_prop:{if_prop.group(1)}={if_prop.group(2) or 'true'}")
    unless_prop = re.search(r'@UnlessBuildProperty\s*\(\s*name\s*=\s*["\']([^"\']+)["\'](?:\s*,\s*stringValue\s*=\s*["\']([^"\']+)["\'])?\s*\)', snippet)
    if unless_prop:
        conds.append(f"unless_prop:{unless_prop.group(1)}={unless_prop.group(2) or 'true'}")
    prof = re.search(r'@Profile\s*\(\s*["\']([^"\']+)["\']\s*\)', snippet)
    if prof:
        conds.append(f"if:{prof.group(1)}")
    return conds


def _get_class_snippet(lines: list[str], decl: JavaDeclaration) -> str:
    annotation_start = max(0, decl.start_line - 1)
    while annotation_start > 0 and (
        lines[annotation_start - 1].strip().startswith('@')
        or not lines[annotation_start - 1].strip()
    ):
        annotation_start -= 1
    raw_lines = lines[annotation_start : decl.end_line]
    header_lines = []
    for line in raw_lines:
        header_lines.append(line)
        if re.search(r'\b(class|interface|enum|record)\b', line):
            break
    return "\n".join(header_lines)


def _get_method_snippet(lines: list[str], decl: JavaDeclaration) -> str:
    raw_lines = lines[max(0, decl.start_line - 6) : decl.end_line]
    start_idx = 0
    for idx, line in enumerate(raw_lines):
        line_no = max(0, decl.start_line - 6) + idx + 1
        if line_no < decl.start_line:
            if re.search(r'\b(class|interface|enum|record)\b', line) or line.strip() == "}":
                start_idx = idx + 1
    return "\n".join(raw_lines[start_idx:])


def _rest_annotation_value(snippet: str, name: str) -> str | list[str] | None:
    match = re.search(r'@' + re.escape(name) + r'\s*\(\s*([^)]*)\)', snippet, re.DOTALL)
    if not match:
        return None
    quote_chars = chr(34) + chr(39)
    values = re.findall('[' + quote_chars + ']([^' + quote_chars + ']+)[' + quote_chars + ']', match.group(1))
    if not values:
        value = match.group(1).strip()
        return value or None
    return values[0] if len(values) == 1 else values


def _analyze_quarkus_rest_files(
    contents_by_path: dict[Path, bytes],
    declarations: tuple[JavaDeclaration, ...],
    quarkus_build_facts: tuple[QuarkusBuildFact, ...] = (),
    quarkus_config_facts: tuple[QuarkusConfigFact, ...] = (),
) -> tuple[QuarkusRESTFact, ...]:
    facts: list[QuarkusRESTFact] = []

    build_flavor = "unknown"
    build_client_flavor = "unknown"
    for bf in quarkus_build_facts:
        if bf.kind == "extension":
            subj = (bf.subject or "").lower()
            if "resteasy-reactive" in subj or "quarkus-rest" in subj:
                build_flavor = "quarkus_rest"
            elif "resteasy" in subj:
                build_flavor = "resteasy_classic"
            if "quarkus-rest-client" in subj or "quarkus-rest" in subj:
                build_client_flavor = "quarkus_rest_client"
            elif "resteasy-client" in subj or "resteasy" in subj:
                build_client_flavor = "quarkus_resteasy_client"

    for path, content in sorted(contents_by_path.items(), key=lambda item: str(item[0])):
        if path.suffix.lower() == ".java":
            text = content.decode("utf-8", errors="replace")
            lines = text.splitlines()

            file_flavor = build_flavor
            if "org.jboss.resteasy.reactive" in text or "io.quarkus.resteasy.reactive" in text:
                file_flavor = "quarkus_rest"
            elif "org.jboss.resteasy.annotations" in text:
                file_flavor = "resteasy_classic"

            file_client_flavor = build_client_flavor
            if "io.quarkus.rest.client" in text:
                file_client_flavor = "quarkus_rest_client"
            elif "org.jboss.resteasy.client" in text or "io.quarkus.resteasy.client" in text:
                file_client_flavor = "quarkus_resteasy_client"
            elif file_client_flavor == "unknown":
                file_client_flavor = "quarkus_rest_client"

            for decl in declarations:
                if decl.path != path:
                    continue

                if decl.kind in ("class", "interface"):
                    decl_snippet = _get_class_snippet(lines, decl)
                    app_path_match = re.search(r'@ApplicationPath\s*\(\s*["\']([^"\']+)["\']\s*\)', decl_snippet)
                    if app_path_match:
                        facts.append(
                            QuarkusRESTFact(
                                "rest_application",
                                decl.qualified_name,
                                app_path_match.group(1).strip(),
                                None,
                                path,
                                decl.start_line,
                                decl.end_line,
                                flavor=file_flavor,
                            )
                        )

                    class_path_match = re.search(r'@Path\s*\(\s*["\']([^"\']+)["\']\s*\)', decl_snippet)
                    class_path_val = class_path_match.group(1).strip() if class_path_match else ""

                    implements_match = re.search(r'\bimplements\s+([A-Za-z0-9_$,\s]+?)(?:\{|implements|extends)', decl_snippet)
                    implements_list = []
                    if implements_match:
                        implements_list = [i.strip() for i in implements_match.group(1).split(",") if i.strip()]

                    extends_match = re.search(r'\bextends\s+([A-Za-z0-9_$]+)', decl_snippet)
                    extends_val = extends_match.group(1).strip() if extends_match else None

                    prof_conds = _extract_build_profile_conditions(decl_snippet)

                    is_provider = "@Provider" in decl_snippet
                    is_filter_or_mapper = bool(re.search(r'\b(ContainerRequestFilter|ContainerResponseFilter|ExceptionMapper|MessageBodyReader|MessageBodyWriter|ParamConverterProvider)\b', decl_snippet))

                    res_meta = {
                        "kind": decl.kind,
                        "implements": implements_list,
                        "extends": extends_val,
                        "build_profile_conditions": prof_conds,
                        "is_provider": is_provider,
                        "is_filter_or_mapper": is_filter_or_mapper,
                    }

                    facts.append(
                        QuarkusRESTFact(
                            "rest_resource",
                            decl.qualified_name,
                            class_path_val,
                            json.dumps(res_meta),
                            path,
                            decl.start_line,
                            decl.end_line,
                            flavor=file_flavor,
                        )
                    )

                    if is_provider or is_filter_or_mapper:
                        facts.append(
                            QuarkusRESTFact(
                                "rest_provider_filter",
                                decl.qualified_name,
                                "filter_provider",
                                json.dumps({"is_provider": is_provider, "is_filter_or_mapper": is_filter_or_mapper}),
                                path,
                                decl.start_line,
                                decl.end_line,
                                flavor=file_flavor,
                            )
                        )

                    is_rest_client = "@RegisterRestClient" in decl_snippet
                    if is_rest_client:
                        config_key_match = re.search(r'@RegisterRestClient\s*\([^)]*configKey\s*=\s*["\']([^"\']+)["\']', decl_snippet)
                        config_key_val = config_key_match.group(1).strip() if config_key_match else None
                        base_uri_match = re.search(r'@RegisterRestClient\s*\([^)]*baseUri\s*=\s*["\']([^"\']+)["\']', decl_snippet)
                        base_uri_val = base_uri_match.group(1).strip() if base_uri_match else None

                        client_meta = {
                            "kind": decl.kind,
                            "config_key": config_key_val,
                            "base_uri": base_uri_val,
                            "class_path": class_path_val,
                            "build_profile_conditions": prof_conds,
                        }
                        facts.append(
                            QuarkusRESTFact(
                                "rest_client_interface",
                                decl.qualified_name,
                                config_key_val or class_path_val or "",
                                json.dumps(client_meta),
                                path,
                                decl.start_line,
                                decl.end_line,
                                flavor=file_client_flavor,
                            )
                        )
                        if "@RegisterProvider" in decl_snippet or "ClientBuilder" in decl_snippet:
                            facts.append(
                                QuarkusRESTFact(
                                    "rest_client_unresolved",
                                    decl.qualified_name,
                                    "filter_provider",
                                    "Dynamic REST client filter or provider registered via @RegisterProvider or ClientBuilder cannot be statically validated.",
                                    path,
                                    decl.start_line,
                                    decl.end_line,
                                    flavor=file_client_flavor,
                                )
                            )

                elif decl.kind == "method":
                    method_snippet = _get_method_snippet(lines, decl)
                    http_match = re.search(r'@(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b', method_snippet)
                    method_path_match = re.search(r'@Path\s*\(\s*["\']([^"\']+)["\']\s*\)', method_snippet)
                    method_path_val = method_path_match.group(1).strip() if method_path_match else ""

                    prof_conds = _extract_build_profile_conditions(method_snippet)

                    produces_match = re.search(r'@Produces\s*\(\s*(?:\{[^}]*\}|["\']([^"\']+)["\']|([A-Za-z0-9_$.]+))\s*\)', method_snippet)
                    produces_val = _rest_annotation_value(method_snippet, 'Produces')

                    consumes_match = re.search(r'@Consumes\s*\(\s*(?:\{[^}]*\}|["\']([^"\']+)["\']|([A-Za-z0-9_$.]+))\s*\)', method_snippet)
                    consumes_val = _rest_annotation_value(method_snippet, 'Consumes')

                    params = []
                    has_servlet_ctx = False
                    for p_match in re.finditer(r'@(PathParam|QueryParam|HeaderParam|CookieParam|FormParam|MatrixParam|BeanParam|Context)\s*(?:\(\s*["\']([^"\']+)["\']\s*\))?\s*([A-Za-z0-9_$.<>]+)\s+([A-Za-z0-9_$]+)', method_snippet):
                        role = p_match.group(1)
                        param_type = p_match.group(3)
                        params.append({
                            "role": role,
                            "key": p_match.group(2) or "",
                            "type": param_type,
                            "name": p_match.group(4),
                        })
                        if role == "Context" or any(st in param_type for st in ("HttpServletRequest", "HttpServletResponse", "ServletContext", "UriInfo", "HttpHeaders")):
                            has_servlet_ctx = True

                    if not has_servlet_ctx and ("@Context" in method_snippet or "HttpServletRequest" in method_snippet):
                        has_servlet_ctx = True

                    execution_mode = "synchronous"
                    reactive_type = None
                    streaming = None

                    if re.search(r'@Blocking\b', method_snippet):
                        execution_mode = "blocking"
                    elif re.search(r'@NonBlocking\b', method_snippet):
                        execution_mode = "non_blocking"

                    m_ret = re.search(r'\b(Uni|Multi|CompletionStage|Publisher|Single|Observable|Flux|Mono)\s*<', method_snippet)
                    if m_ret:
                        reactive_type = m_ret.group(1)
                        if execution_mode == "synchronous":
                            execution_mode = "reactive"

                    if (produces_val and ("SERVER_SENT_EVENTS" in produces_val or "text/event-stream" in produces_val)) or "SERVER_SENT_EVENTS" in method_snippet or "text/event-stream" in method_snippet or "@RestStreamElementType" in method_snippet or "@SseElementType" in method_snippet:
                        streaming = "server_sent_events"
                    elif reactive_type == "Multi" or "Publisher" in method_snippet:
                        if not streaming:
                            streaming = "stream"

                    ret_type_match = re.search(r'(?:(?:public|protected|private)\s+)?(?:<[^>]+>\s+)?([A-Za-z0-9_$.<>]+)\s+' + re.escape(decl.name) + r'\b', method_snippet)
                    return_type_val = ret_type_match.group(1).strip() if ret_type_match else "void"

                    parent_class = next(
                        (
                            c for c in declarations
                            if c.path == path and c.kind in ("class", "interface")
                            and c.start_line <= decl.start_line <= c.end_line
                        ),
                        None,
                    )
                    is_client_method = False
                    if parent_class is not None:
                        parent_snippet = _get_class_snippet(lines, parent_class)
                        if "@RegisterRestClient" in parent_snippet:
                            is_client_method = True

                    dto_types: list[str] = []
                    def add_dto_type(t_name: str) -> None:
                        raw_t = re.sub(r'^(Uni|Multi|CompletionStage|Publisher|Single|Observable|Flux|Mono|Response|List|Set|Collection)<', '', t_name).rstrip('>')
                        clean_t = raw_t.rsplit('.', 1)[-1].strip()
                        if clean_t and clean_t not in ('void', 'int', 'long', 'boolean', 'double', 'float', 'byte', 'short', 'char', 'String', 'Response', 'Object', 'Uni', 'Multi'):
                            if clean_t not in dto_types:
                                dto_types.append(clean_t)

                    if return_type_val:
                        add_dto_type(return_type_val)

                    sig_params_match = re.search(re.escape(decl.name) + r'\s*\(([^)]*)\)', method_snippet)
                    if sig_params_match:
                        raw_params = sig_params_match.group(1)
                        for param_decl in raw_params.split(','):
                            param_tokens = param_decl.strip().split()
                            if param_tokens:
                                type_idx = -2 if len(param_tokens) >= 2 else -1
                                p_type = param_tokens[type_idx]
                                add_dto_type(p_type)

                    if is_client_method and http_match:
                        http_method = http_match.group(1)
                        client_method_meta = {
                            "http_method": http_method,
                            "method_path": method_path_val,
                            "produces": produces_val,
                            "consumes": consumes_val,
                            "parameters": params,
                            "return_type": return_type_val,
                            "dto_types": dto_types,
                            "interface": parent_class.qualified_name if parent_class else "",
                        }
                        facts.append(
                            QuarkusRESTFact(
                                "rest_client_method",
                                decl.qualified_name,
                                parent_class.qualified_name if parent_class else "",
                                json.dumps(client_method_meta),
                                path,
                                decl.start_line,
                                decl.end_line,
                                flavor=file_client_flavor,
                            )
                        )

                    if "RestClientBuilder" in method_snippet:
                        builder_uri_match = re.search(r'\.(?:baseUri|baseUrl)\s*\(\s*(?:URI\.create\s*\(\s*)?["\']([^"\']+)["\']\s*\)?\s*\)', method_snippet)
                        builder_target_match = re.search(r'\.build\s*\(\s*([A-Za-z0-9_$]+)\.class\s*\)', method_snippet)
                        if builder_uri_match and builder_target_match:
                            base_uri_val = builder_uri_match.group(1).strip()
                            target_iface_val = builder_target_match.group(1).strip()
                            prog_meta = {
                                "base_uri": base_uri_val,
                                "target_interface": target_iface_val,
                                "builder": "RestClientBuilder",
                            }
                            facts.append(
                                QuarkusRESTFact(
                                    "programmatic_rest_client",
                                    decl.qualified_name,
                                    target_iface_val,
                                    json.dumps(prog_meta),
                                    path,
                                    decl.start_line,
                                    decl.end_line,
                                    flavor=file_client_flavor,
                                )
                            )
                        else:
                            facts.append(
                                QuarkusRESTFact(
                                    "programmatic_client_unresolved",
                                    decl.qualified_name,
                                    "builder_unresolved",
                                    "Programmatic RestClientBuilder uses dynamic base URI, dynamic builder flow, or unasserted interface target.",
                                    path,
                                    decl.start_line,
                                    decl.end_line,
                                    flavor=file_client_flavor,
                                )
                            )

                    if "WebClient" in text or "WebClient" in method_snippet:
                        for wc_match in re.finditer(
                            r'\b(?:webClient|client|wc)\s*\.\s*(get|post|put|delete|patch|getAbs|postAbs|request)\s*\(\s*(?:HttpMethod\.([A-Z]+)\s*,\s*)?(?:["\']([^"\']+)["\']|([A-Za-z0-9_$.]+))',
                            method_snippet,
                        ):
                            verb = (wc_match.group(1) or wc_match.group(2) or "GET").upper()
                            if verb in {"GETABS", "POSTABS"}:
                                verb = verb[:-3]
                            path_or_url = wc_match.group(3) or wc_match.group(4) or ""
                            if path_or_url and not path_or_url.startswith("HttpMethod."):
                                if path_or_url.startswith("/") or path_or_url.startswith("http://") or path_or_url.startswith("https://"):
                                    wc_meta = {
                                        "http_method": verb,
                                        "path": path_or_url,
                                    }
                                    facts.append(
                                        QuarkusRESTFact(
                                            "vertx_webclient_call",
                                            decl.qualified_name,
                                            f"{verb} {path_or_url}",
                                            json.dumps(wc_meta),
                                            path,
                                            decl.start_line,
                                            decl.end_line,
                                            flavor="quarkus_vertx_webclient",
                                        )
                                    )
                                else:
                                    facts.append(
                                        QuarkusRESTFact(
                                            "programmatic_client_unresolved",
                                            decl.qualified_name,
                                            "webclient_unresolved",
                                            "Vert.x WebClient call uses dynamic path, redirect, or provider mutation.",
                                            path,
                                            decl.start_line,
                                            decl.end_line,
                                            flavor="quarkus_vertx_webclient",
                                        )
                                    )

                    if http_match:
                        http_method = http_match.group(1)
                        meta = {
                            "http_method": http_method,
                            "method_path": method_path_val,
                            "produces": produces_val,
                            "consumes": consumes_val,
                            "parameters": params,
                            "execution_mode": execution_mode,
                            "reactive_type": reactive_type,
                            "streaming": streaming,
                            "build_profile_conditions": prof_conds,
                            "has_servlet_context": has_servlet_ctx,
                            "return_type": return_type_val,
                            "dto_types": dto_types,
                        }
                        facts.append(
                            QuarkusRESTFact(
                                "rest_endpoint",
                                decl.qualified_name,
                                f"{http_method} {method_path_val}".strip(),
                                json.dumps(meta),
                                path,
                                decl.start_line,
                                decl.end_line,
                                flavor=file_flavor,
                            )
                        )
                    elif method_path_match:
                        meta = {
                            "method_path": method_path_val,
                            "return_type": return_type_val,
                            "parameters": params,
                            "build_profile_conditions": prof_conds,
                            "has_servlet_context": has_servlet_ctx,
                        }
                        facts.append(
                            QuarkusRESTFact(
                                "rest_subresource_locator",
                                decl.qualified_name,
                                method_path_val,
                                json.dumps(meta),
                                path,
                                decl.start_line,
                                decl.end_line,
                                flavor=file_flavor,
                            )
                        )

    return tuple(facts)


def _quarkus_route_has_reactive_extension(quarkus_build_facts: Iterable[QuarkusBuildFact]) -> bool:
    for fact in quarkus_build_facts:
        if fact.kind != "extension":
            continue
        subject = (fact.subject or "").lower()
        if subject in {
            "quarkus-reactive-routes",
            "quarkus-vertx-http",
            "quarkus-rest",
            "quarkus-resteasy-reactive",
        }:
            return True
    return False


def _route_methods_from_snippet(snippet: str) -> list[str]:
    """Return declared route methods for a `@Route` or programmatic registration.

    Accepts ``HttpMethod.GET``, ``GET`` (when nested in a methods array), and the
    common method-name literal forms.
    """
    methods: list[str] = []
    for match in re.finditer(
        r"(?:HttpMethod\.([A-Z]+)|methods\s*=\s*\{?\s*([A-Z][A-Z_]+))",
        snippet,
    ):
        token = match.group(1) or match.group(2)
        if token and token not in methods:
            methods.append(token)
    return methods


def _route_media_types(snippet: str, attribute: str) -> list[str]:
    pattern = (
        rf"{attribute}\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([A-Za-z][\w.]*)|({{\s*[^}}]+\s*}}))"
    )
    match = re.search(pattern, snippet)
    if not match:
        return []
    raw = match.group(4) or match.group(1) or match.group(2) or match.group(3) or ""
    parts: list[str] = []
    for candidate in re.findall(r"\"([^\"]+)\"|'([^']+)'", raw):
        token = candidate[0] or candidate[1]
        if token and token not in parts:
            parts.append(token)
    if not parts and raw:
        parts = [raw.strip()]
    return parts


def _route_order(snippet: str) -> int | None:
    match = re.search(r"order\s*=\s*(\d+)", snippet)
    return int(match.group(1)) if match else None


def _route_handler_type(snippet: str) -> str:
    match = re.search(r"type\s*=\s*Route\.HandlerType\.([A-Z]+)", snippet)
    if match:
        return match.group(1)
    match = re.search(r"type\s*=\s*([A-Z]+)", snippet)
    if match and match.group(1) in {"NORMAL", "BLOCKING", "FAILURE"}:
        return match.group(1)
    if re.search(r"@io\.smallrye\.common\.annotation\.Blocking\b|@Blocking\b", snippet):
        return "BLOCKING"
    return "NORMAL"


def _route_path(snippet: str) -> str:
    match = re.search(r'path\s*=\s*"([^"]+)"', snippet)
    if match:
        return match.group(1)
    match = re.search(r"path\s*=\s*'([^']+)'", snippet)
    return match.group(1) if match else ""


def _route_regex(snippet: str) -> str:
    match = re.search(r'regex\s*=\s*"([^"]+)"', snippet)
    if match:
        return match.group(1)
    return re.search(r"regex\s*=\s*'([^']+)'", snippet).group(1) if re.search(r"regex\s*=\s*'([^']+)'", snippet) else ""


def _route_annotations_on_target(snippet: str, *, multi: bool = False) -> list[str]:
    """Return each literal `@Route(...)` annotation block appearing directly above the method.

    ``multi=True`` accepts repeatable annotations stacked above the method.
    """
    if multi:
        annotations: list[str] = []
        position = 0
        while True:
            match = re.search(r"@Route\s*\((.*?)\)", snippet[position:], re.DOTALL)
            if match is None:
                break
            annotations.append(match.group(1))
            position += match.end()
        return annotations
    match = re.search(r"@Route\s*\((.*?)\)", snippet, re.DOTALL)
    return [match.group(1)] if match else []


def _analyze_quarkus_route_files(
    contents_by_path: dict[Path, bytes],
    declarations: tuple[JavaDeclaration, ...],
    quarkus_build_facts: tuple[QuarkusBuildFact, ...] = (),
) -> tuple[QuarkusRouteFact, ...]:
    facts: list[QuarkusRouteFact] = []
    build_present = _quarkus_route_has_reactive_extension(quarkus_build_facts)
    for path, content in sorted(contents_by_path.items(), key=lambda item: str(item[0])):
        if path.suffix.lower() != ".java":
            continue
        text = content.decode("utf-8", errors="replace")
        has_route_import = bool(
            re.search(r"import\s+(?:io\.quarkus\.vertx\.web\.Route\b|io\.quarkus\.vertx\.web\.RouteBase\b)", text)
        )
        has_router_import = bool(
            re.search(r"import\s+(?:io\.vertx\.ext\.web\.Router\b|io\.vertx\.mutiny\.ext\.web\.Router\b)", text)
        )
        if not (has_route_import or has_router_import):
            continue
        lines = text.splitlines()
        file_flavor = "quarkus_reactive_routes" if build_present or has_route_import else "unknown"
        for decl in declarations:
            if decl.path != path:
                continue
            if decl.kind == "class":
                class_snippet = "\n".join(lines[max(0, decl.start_line - 8): decl.end_line])
                base_match = re.search(
                    r"@RouteBase\s*\(([^)]*)\)", class_snippet, re.DOTALL
                )
                if base_match:
                    base_args = base_match.group(1)
                    base_path = _route_path(base_args)
                    base_produces = _route_media_types(base_args, "produces")
                    facts.append(
                        QuarkusRouteFact(
                            "route_base",
                            decl.qualified_name,
                            base_path,
                            json.dumps({"produces": base_produces}),
                            path,
                            decl.start_line,
                            decl.end_line,
                            flavor=file_flavor,
                        )
                    )
                # Programmatic Vert.x router registration: a method that takes
                # `Router router` and uses literal paths/handler references.
                for method in declarations:
                    if method.path != path or method.kind not in {"method", "constructor"}:
                        continue
                    if method.start_line < decl.start_line or method.end_line > decl.end_line:
                        continue
                    if method.name == decl.name:
                        continue
                    method_text = "\n".join(lines[max(0, method.start_line - 3): method.end_line])
                    if "Router" not in method_text:
                        continue
                    if not re.search(r"@Observes\b", method_text):
                        continue
                    any_literal_registration = False
                    for route_match in re.finditer(
                        r"router\s*\.\s*(get|post|put|delete|patch|head|options)\s*\(\s*(?:\"([^\"]+)\"|'([^']+)')\s*\)\s*\.\s*handler\s*\(\s*([^)]+)\)",
                        method_text,
                    ):
                        verb = route_match.group(1).upper()
                        path_value = route_match.group(2) or route_match.group(3) or ""
                        handler_ref = (route_match.group(4) or "").strip()
                        if not path_value:
                            continue
                        any_literal_registration = True
                        if handler_ref.startswith("this::"):
                            handler_ref = handler_ref[len("this::"):]
                        if "::" in handler_ref or handler_ref.endswith("::") or handler_ref.startswith("::"):
                            facts.append(
                                QuarkusRouteFact(
                                    "router_unresolved",
                                    f"{decl.qualified_name}#{method.name}",
                                    path_value,
                                    "Router handler method reference cannot be tied to a local symbol.",
                                    path,
                                    method.start_line,
                                    method.end_line,
                                    flavor=file_flavor,
                                )
                            )
                            continue
                        if "." in handler_ref:
                            facts.append(
                                QuarkusRouteFact(
                                    "router_unresolved",
                                    f"{decl.qualified_name}#{method.name}",
                                    path_value,
                                    "Router handler reference is not a direct owning-class method.",
                                    path,
                                    method.start_line,
                                    method.end_line,
                                    flavor=file_flavor,
                                )
                            )
                            continue
                        handler_method = next(
                            (
                                m for m in declarations
                                if m.path == path
                                and m.kind == "method"
                                and m.name == handler_ref
                                and m.start_line >= decl.start_line
                                and m.end_line <= decl.end_line
                            ),
                            None,
                        )
                        if handler_method is None:
                            facts.append(
                                QuarkusRouteFact(
                                    "router_unresolved",
                                    f"{decl.qualified_name}#{method.name}",
                                    path_value,
                                    "Router handler reference does not resolve to a local method declaration.",
                                    path,
                                    method.start_line,
                                    method.end_line,
                                    flavor=file_flavor,
                                )
                            )
                            continue
                        meta = {
                            "path": path_value,
                            "methods": [verb],
                            "handler_type": "NORMAL",
                            "produces": [],
                            "consumes": [],
                            "order": None,
                            "build_profile_conditions": _extract_build_profile_conditions(method_text),
                            "source": "router_registration",
                        }
                        facts.append(
                            QuarkusRouteFact(
                                "route_method",
                                handler_method.qualified_name,
                                f"{verb} {path_value}",
                                json.dumps(meta),
                                path,
                                handler_method.start_line,
                                handler_method.end_line,
                                flavor=file_flavor,
                            )
                        )
                    if not any_literal_registration and re.search(
                        r"router\s*\.\s*(get|post|put|delete|patch|head|options)\s*\(", method_text
                    ):
                        # Try to find any handler references even with non-literal paths
                        handler_subjects: list[str] = [f"{decl.qualified_name}#{method.name}"]
                        for handler_match in re.finditer(
                            r"router\s*\.\s*(?:get|post|put|delete|patch|head|options)\s*\([^)]*\)\s*\.\s*handler\s*\(\s*(this::)?([A-Za-z_$][\w$]*)\s*\)",
                            method_text,
                        ):
                            this_prefix = handler_match.group(1) or ""
                            handler_name = handler_match.group(2)
                            if this_prefix:
                                handler_method = next(
                                    (
                                        m for m in declarations
                                        if m.path == path
                                        and m.kind == "method"
                                        and m.name == handler_name
                                        and m.start_line >= decl.start_line
                                        and m.end_line <= decl.end_line
                                    ),
                                    None,
                                )
                                if handler_method is not None:
                                    handler_subjects.append(handler_method.qualified_name)
                        for subject in handler_subjects:
                            facts.append(
                                QuarkusRouteFact(
                                    "router_unresolved",
                                    subject,
                                    None,
                                    "Programmatic router registration uses a dynamic, lambda, or method-reference handler that was not asserted.",
                                    path,
                                    method.start_line,
                                    method.end_line,
                                    flavor=file_flavor,
                                )
                            )
                continue
            if decl.kind != "method":
                continue
            method_snippet = "\n".join(lines[max(0, decl.start_line - 8): decl.end_line])
            class_decl = next(
                (
                    c for c in declarations
                    if c.path == path and c.kind == "class"
                    and c.start_line <= decl.start_line <= c.end_line
                ),
                None,
            )
            base_path = ""
            base_produces: list[str] = []
            if class_decl is not None:
                class_snippet = "\n".join(lines[max(0, class_decl.start_line - 8): class_decl.end_line])
                base_match = re.search(r"@RouteBase\s*\(([^)]*)\)", class_snippet, re.DOTALL)
                if base_match:
                    base_path = _route_path(base_match.group(1))
                    base_produces = _route_media_types(base_match.group(1), "produces")
            route_blocks = _route_annotations_on_target(method_snippet, multi=True)
            if not route_blocks:
                continue
            for args in route_blocks:
                regex_value = _route_regex(args)
                if regex_value:
                    facts.append(
                        QuarkusRouteFact(
                            "route_unresolved",
                            decl.qualified_name,
                            regex_value,
                            "Reactive Route regex path was not asserted as a static route.",
                            path,
                            decl.start_line,
                            decl.end_line,
                            flavor=file_flavor,
                        )
                    )
                    continue
                path_value = _route_path(args)
                if not path_value:
                    path_value = _route_path(method_snippet) or decl.name
                methods_list = _route_methods_from_snippet(args)
                if not methods_list:
                    methods_list = ["GET"]
                produces = _route_media_types(args, "produces") or list(base_produces)
                consumes = _route_media_types(args, "consumes")
                handler_type = _route_handler_type(method_snippet + "\n" + args)
                order_value = _route_order(args)
                full_path = (("/" + base_path.strip("/") + "/" + path_value.lstrip("/")) if base_path else path_value).replace("//", "/")
                meta = {
                    "path": full_path,
                    "methods": methods_list,
                    "handler_type": handler_type,
                    "produces": produces,
                    "consumes": consumes,
                    "order": order_value,
                    "build_profile_conditions": _extract_build_profile_conditions(method_snippet),
                    "source": "annotation",
                }
                facts.append(
                    QuarkusRouteFact(
                        "route_method",
                        decl.qualified_name,
                        f"{'/'.join(methods_list)} {full_path}".strip(),
                        json.dumps(meta),
                        path,
                        decl.start_line,
                        decl.end_line,
                        flavor=file_flavor,
                    )
                )
    return tuple(facts)


def _quarkus_rest_relationships(
    connection_data_path: Path,
    owner: str,
    target: ImpactTarget,
) -> tuple[tuple[ImpactRelationship, ...], tuple[UnresolvedItem, ...]]:
    connection = sqlite3.connect(connection_data_path)
    try:
        rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line, flavor
            FROM quarkus_rest_facts ORDER BY path, start_line"""
        ).fetchall()
        config_rows = connection.execute(
            """SELECT subject, value FROM quarkus_config_facts WHERE kind = 'quarkus_property_source'"""
        ).fetchall()
    except sqlite3.OperationalError:
        return (), ()
    finally:
        connection.close()

    if not rows:
        return (), ()

    rest_facts = [
        QuarkusRESTFact(kind, subject, fact_target, value, Path(path), start_line, end_line, flavor)
        for kind, subject, fact_target, value, path, start_line, end_line, flavor in rows
    ]

    relationships: list[ImpactRelationship] = []
    unresolved: list[UnresolvedItem] = []
    seen_rel_keys: set[tuple[str, str, Path, int, int, str]] = set()

    http_root_path = ""
    for prop_key, prop_val in config_rows:
        if prop_key in {"quarkus.http.root-path", "quarkus.rest.path", "quarkus.resteasy.path"} and prop_val:
            http_root_path = prop_val.strip()
            if not http_root_path.startswith("/"):
                http_root_path = "/" + http_root_path

    app_path = ""
    app_facts = [f for f in rest_facts if f.kind == "rest_application"]
    if app_facts and app_facts[0].target:
        app_path = app_facts[0].target
        if not app_path.startswith("/"):
            app_path = "/" + app_path

    resource_by_class: dict[str, QuarkusRESTFact] = {}
    for f in rest_facts:
        if f.kind == "rest_resource":
            resource_by_class[f.subject] = f

    impls_by_interface: dict[str, list[QuarkusRESTFact]] = {}
    for cls_name, r_fact in resource_by_class.items():
        meta = json.loads(r_fact.value) if r_fact.value else {}
        for iface in meta.get("implements", []):
            impls_by_interface.setdefault(iface, []).append(r_fact)
            simple_iface = iface.rsplit(".", maxsplit=1)[-1]
            if simple_iface != iface:
                impls_by_interface.setdefault(simple_iface, []).append(r_fact)

    target_sig_name = target.signature.split("(")[0].strip()
    target_class_part = None
    target_method_part = None

    if "#" in target_sig_name:
        target_class_part, target_method_part = target_sig_name.split("#", 1)
    elif "#" in owner:
        target_class_part, target_method_part = owner.split("#", 1)
    else:
        target_class_part = owner
        target_method_part = target_sig_name if target_sig_name != owner else None

    def matches_target(f_subject: str) -> bool:
        f_class, _, f_method = f_subject.partition("#")

        if target_method_part and f_method:
            if f_method != target_method_part:
                return False

        if target_class_part:
            if f_class == target_class_part:
                return True
            owner_r_fact = resource_by_class.get(target_class_part)
            if owner_r_fact:
                o_meta = json.loads(owner_r_fact.value) if owner_r_fact.value else {}
                o_impls = o_meta.get("implements", [])
                for imp in o_impls:
                    if imp == f_class or imp.endswith("." + f_class) or f_class.endswith("." + imp):
                        return True
            subj_r_fact = resource_by_class.get(f_class)
            if subj_r_fact:
                s_meta = json.loads(subj_r_fact.value) if subj_r_fact.value else {}
                s_impls = s_meta.get("implements", [])
                for imp in s_impls:
                    if imp == target_class_part or imp.endswith("." + target_class_part) or target_class_part.endswith("." + imp):
                        return True
            return False

        return True

    for f in rest_facts:
        if matches_target(f.subject):
            if f.kind == "rest_provider_filter":
                unresolved.append(
                    UnresolvedItem(
                        f"JAX-RS provider / filter ContainerRequestFilter on {f.subject}",
                        path=f.path,
                        start_line=f.start_line,
                        end_line=f.end_line,
                        evidence_handle=f"quarkus_rest:{f.path.as_posix()}:{f.start_line}-{f.end_line}",
                    )
                )

    endpoint_facts = [f for f in rest_facts if f.kind == "rest_endpoint"]
    locator_facts = [f for f in rest_facts if f.kind == "rest_subresource_locator"]

    subresource_target_classes: set[str] = set()
    for loc in locator_facts:
        loc_meta = json.loads(loc.value) if loc.value else {}
        loc_ret = loc_meta.get("return_type", "Object").split("<")[0].strip()
        if loc_ret not in ("Object", "void"):
            subresource_target_classes.add(loc_ret)

    for f in endpoint_facts:
        subject_class = f.subject.rsplit("#", maxsplit=1)[0]
        if matches_target(f.subject) or any(matches_target(f"{impl.subject}#{f.subject.split('#')[-1]}") for impl in impls_by_interface.get(subject_class, [])):
            meta = json.loads(f.value) if f.value else {}
            http_method = meta.get("http_method", "GET")
            method_path = meta.get("method_path", "")

            class_facts = [cf for cf in rest_facts if cf.kind == "rest_resource" and cf.subject == subject_class]
            class_path = class_facts[0].target if class_facts and class_facts[0].target else ""

            interface_handles = []
            if not class_path and class_facts:
                cf_meta = json.loads(class_facts[0].value) if class_facts[0].value else {}
                for iface in cf_meta.get("implements", []):
                    iface_facts = [cf for cf in rest_facts if cf.kind == "rest_resource" and (cf.subject == iface or cf.subject.endswith("." + iface))]
                    if iface_facts:
                        if not class_path:
                            class_path = iface_facts[0].target or ""
                        interface_handles.append(f"quarkus_rest:{iface_facts[0].path.as_posix()}:{iface_facts[0].start_line}-{iface_facts[0].end_line}")

            # If this class has no class_path and is a subresource target class, skip direct standalone top-level route mapping
            if not class_path and not interface_handles and (subject_class in subresource_target_classes or subject_class.rsplit(".", maxsplit=1)[-1] in subresource_target_classes):
                continue

            parts = [p for p in (http_root_path, app_path, class_path, method_path) if p]
            full_path = "/" + "/".join(p.strip("/") for p in parts if p.strip("/"))
            if not full_path or full_path == "//":
                full_path = "/"

            route_identity = f"{http_method} {full_path}"
            flavor = f.flavor or "unknown"
            confidence = "high" if flavor != "unknown" else "medium"

            handle = f"quarkus_rest:{f.path.as_posix()}:{f.start_line}-{f.end_line}"
            chain = [handle]
            if class_facts:
                chain.append(f"quarkus_rest:{class_facts[0].path.as_posix()}:{class_facts[0].start_line}-{class_facts[0].end_line}")
            for ih in interface_handles:
                if ih not in chain:
                    chain.append(ih)

            prof_conds = meta.get("build_profile_conditions", [])
            if class_facts:
                cf_meta = json.loads(class_facts[0].value) if class_facts[0].value else {}
                for c in cf_meta.get("build_profile_conditions", []):
                    if c not in prof_conds:
                        prof_conds.append(c)

            business_data = {
                "flavor": flavor,
                "route": route_identity,
                "http_method": http_method,
                "produces": meta.get("produces"),
                "consumes": meta.get("consumes"),
                "parameters": meta.get("parameters", []),
                "execution_mode": meta.get("execution_mode", "synchronous"),
            }
            if meta.get("reactive_type"):
                business_data["reactive_type"] = meta.get("reactive_type")
            if meta.get("streaming"):
                business_data["streaming"] = meta.get("streaming")
            if prof_conds:
                business_data["build_profile_conditions"] = prof_conds

            rel_key = ("quarkus_rest_contract", route_identity, f.path, f.start_line, f.end_line, handle)
            if rel_key not in seen_rel_keys:
                seen_rel_keys.add(rel_key)
                relationships.append(
                    ImpactRelationship(
                        "quarkus_rest_contract",
                        route_identity,
                        f.path,
                        f.start_line,
                        f.end_line,
                        handle,
                        confidence,
                        False,
                        None,
                        evidence_chain=tuple(chain),
                        business_view=json.dumps(business_data),
                    )
                )

            if meta.get("has_servlet_context"):
                unresolved.append(
                    UnresolvedItem(
                        f"Servlet coupling via HttpServletRequest on endpoint {f.subject}",
                        path=f.path,
                        start_line=f.start_line,
                        end_line=f.end_line,
                        evidence_handle=handle,
                    )
                )

    for loc in locator_facts:
        loc_class = loc.subject.rsplit("#", maxsplit=1)[0]
        loc_meta = json.loads(loc.value) if loc.value else {}
        loc_return_type = loc_meta.get("return_type", "Object")

        loc_class_facts = [cf for cf in rest_facts if cf.kind == "rest_resource" and cf.subject == loc_class]
        loc_class_path = loc_class_facts[0].target if loc_class_facts and loc_class_facts[0].target else ""
        sub_loc_path = loc_meta.get("method_path", "")

        loc_handle = f"quarkus_rest:{loc.path.as_posix()}:{loc.start_line}-{loc.end_line}"

        clean_ret_type = loc_return_type.split("<")[0].strip()
        matching_sub_resources = [
            rf for rf in rest_facts if rf.kind == "rest_resource" and (rf.subject == clean_ret_type or rf.subject.rsplit(".", maxsplit=1)[-1] == clean_ret_type)
        ]

        if not matching_sub_resources or clean_ret_type in ("Object", "void") or len(matching_sub_resources) > 1:
            if matches_target(loc.subject) or any(matches_target(f"{sub.subject}#{e.subject.split('#')[-1]}") for sub in matching_sub_resources for e in endpoint_facts if e.subject.startswith(sub.subject)):
                unresolved.append(
                    UnresolvedItem(
                        f"Ambiguous subresource locator return type: {loc_return_type} on {loc.subject}",
                        path=loc.path,
                        start_line=loc.start_line,
                        end_line=loc.end_line,
                        evidence_handle=loc_handle,
                    )
                )
        else:
            sub_res_fact = matching_sub_resources[0]
            sub_endpoints = [e for e in endpoint_facts if e.subject.startswith(sub_res_fact.subject)]

            for ep in sub_endpoints:
                if matches_target(loc.subject) or matches_target(ep.subject) or matches_target(sub_res_fact.subject):
                    ep_meta = json.loads(ep.value) if ep.value else {}
                    ep_http_method = ep_meta.get("http_method", "GET")
                    ep_method_path = ep_meta.get("method_path", "")

                    parts = [p for p in (http_root_path, app_path, loc_class_path, sub_loc_path, ep_method_path) if p]
                    composed_path = "/" + "/".join(p.strip("/") for p in parts if p.strip("/"))
                    if not composed_path or composed_path == "//":
                        composed_path = "/"

                    route_identity = f"{ep_http_method} {composed_path}"
                    flavor = ep.flavor or loc.flavor or "unknown"
                    confidence = "high" if flavor != "unknown" else "medium"

                    ep_handle = f"quarkus_rest:{ep.path.as_posix()}:{ep.start_line}-{ep.end_line}"
                    chain = [ep_handle, loc_handle]

                    business_data = {
                        "flavor": flavor,
                        "route": route_identity,
                        "http_method": ep_http_method,
                        "produces": ep_meta.get("produces"),
                        "consumes": ep_meta.get("consumes"),
                        "parameters": ep_meta.get("parameters", []),
                        "execution_mode": ep_meta.get("execution_mode", "synchronous"),
                    }

                    rel_key = ("quarkus_rest_contract", route_identity, ep.path, ep.start_line, ep.end_line, ep_handle)
                    if rel_key not in seen_rel_keys:
                        seen_rel_keys.add(rel_key)
                        relationships.append(
                            ImpactRelationship(
                                "quarkus_rest_contract",
                                route_identity,
                                ep.path,
                                ep.start_line,
                                ep.end_line,
                                ep_handle,
                                confidence,
                                False,
                                None,
                                evidence_chain=tuple(chain),
                                business_view=json.dumps(business_data),
                            )
                        )

    client_iface_facts = [f for f in rest_facts if f.kind == "rest_client_interface"]
    client_method_facts = [f for f in rest_facts if f.kind == "rest_client_method"]
    client_unresolved_facts = [f for f in rest_facts if f.kind == "rest_client_unresolved"]

    for f in client_unresolved_facts:
        if matches_target(f.subject):
            unresolved.append(
                UnresolvedItem(
                    f.value or f"Dynamic REST client element on {f.subject}",
                    path=f.path,
                    start_line=f.start_line,
                    end_line=f.end_line,
                    evidence_handle=f"quarkus_rest:{f.path.as_posix()}:{f.start_line}-{f.end_line}",
                )
            )

    try:
        connection_sub = sqlite3.connect(connection_data_path)
        cdi_facts_rows = connection_sub.execute(
            """SELECT subject, target, value, path, start_line, end_line, scope FROM quarkus_cdi_facts"""
        ).fetchall()
        config_facts_rows = connection_sub.execute(
            """SELECT subject, value, path, start_line, end_line, profile FROM quarkus_config_facts WHERE kind = 'quarkus_property_source'"""
        ).fetchall()
        connection_sub.close()
    except sqlite3.OperationalError:
        cdi_facts_rows = []
        config_facts_rows = []

    prog_client_facts = [f for f in rest_facts if f.kind == "programmatic_rest_client"]
    webclient_facts = [f for f in rest_facts if f.kind == "vertx_webclient_call"]
    prog_unresolved_facts = [f for f in rest_facts if f.kind == "programmatic_client_unresolved"]

    for f in prog_unresolved_facts:
        if matches_target(f.subject):
            unresolved.append(
                UnresolvedItem(
                    f.value or f"Unresolved programmatic client element on {f.subject}",
                    path=f.path,
                    start_line=f.start_line,
                    end_line=f.end_line,
                    evidence_handle=f"quarkus_rest:{f.path.as_posix()}:{f.start_line}-{f.end_line}",
                )
            )

    for pc in prog_client_facts:
        if matches_target(pc.subject) or matches_target(pc.target):
            pc_meta = json.loads(pc.value) if pc.value else {}
            iface_target = pc_meta.get("target_interface", pc.target)
            pc_handle = f"quarkus_rest:{pc.path.as_posix()}:{pc.start_line}-{pc.end_line}"
            rel_key_pc = ("quarkus_programmatic_client", f"RestClientBuilder -> {iface_target}", pc.path, pc.start_line, pc.end_line, pc_handle)
            if rel_key_pc not in seen_rel_keys:
                seen_rel_keys.add(rel_key_pc)
                relationships.append(
                    ImpactRelationship(
                        "quarkus_rest_contract",
                        f"RestClientBuilder -> {iface_target}",
                        pc.path,
                        pc.start_line,
                        pc.end_line,
                        pc_handle,
                        "high",
                        False,
                        None,
                        evidence_chain=(pc_handle,),
                        business_view=json.dumps(pc_meta),
                    )
                )

    for wc in webclient_facts:
        if matches_target(wc.subject) or matches_target(wc.target):
            wc_meta = json.loads(wc.value) if wc.value else {}
            route_str = f"{wc_meta.get('http_method', 'GET')} {wc_meta.get('path', '')}"
            wc_handle = f"quarkus_rest:{wc.path.as_posix()}:{wc.start_line}-{wc.end_line}"
            rel_key_wc = ("quarkus_vertx_webclient", route_str, wc.path, wc.start_line, wc.end_line, wc_handle)
            if rel_key_wc not in seen_rel_keys:
                seen_rel_keys.add(rel_key_wc)
                relationships.append(
                    ImpactRelationship(
                        "quarkus_http_route",
                        route_str,
                        wc.path,
                        wc.start_line,
                        wc.end_line,
                        wc_handle,
                        "high",
                        False,
                        None,
                        evidence_chain=(wc_handle,),
                        business_view=json.dumps(wc_meta),
                    )
                )

    for cm in client_method_facts:
        if matches_target(cm.subject) or matches_target(cm.target):
            cm_meta = json.loads(cm.value) if cm.value else {}
            http_method = cm_meta.get("http_method", "GET")
            method_path = cm_meta.get("method_path", "")
            iface_fqcn = cm.target
            iface_short = iface_fqcn.rsplit(".", 1)[-1] if iface_fqcn else ""

            iface_fact = next((f for f in client_iface_facts if f.subject == iface_fqcn or f.subject.rsplit(".", 1)[-1] == iface_short), None)
            iface_path = iface_fact.target if iface_fact and iface_fact.target else ""
            iface_meta = json.loads(iface_fact.value) if iface_fact and iface_fact.value else {}
            config_key = iface_meta.get("config_key")

            parts = [p for p in (iface_path, method_path) if p]
            full_path = "/" + "/".join(p.strip("/") for p in parts if p.strip("/"))
            if not full_path or full_path == "//":
                full_path = "/"

            # Check negative rule: If client has same path as server resource, check shared interface or proven local mapping
            matching_server_impls = [
                impl for impl_list in impls_by_interface.values() for impl in impl_list
                if impl.subject == iface_fqcn or iface_short in json.loads(impl.value).get("implements", [])
            ]
            has_shared_interface = len(matching_server_impls) > 0

            candidate_keys = [
                f"{iface_fqcn}/mp-rest/url",
                f"{iface_fqcn}/mp-rest/uri",
                f"{iface_short}/mp-rest/url",
                f"{iface_short}/mp-rest/uri",
            ]
            if config_key:
                candidate_keys.extend([
                    f'quarkus.rest-client."{config_key}".url',
                    f'quarkus.rest-client.{config_key}.url',
                    f'quarkus.rest-client."{config_key}".uri',
                    f'quarkus.rest-client.{config_key}.uri',
                    f'{config_key}/mp-rest/url',
                    f'{config_key}/mp-rest/uri',
                ])
            proven_config_matches = [row for row in config_facts_rows if row[0] in candidate_keys]
            has_proven_mapping = len(proven_config_matches) > 0

            if not has_shared_interface and not has_proven_mapping:
                # Same path/name alone without contract identity or proven mapping: report UnresolvedItem
                unresolved.append(
                    UnresolvedItem(
                        f"Identical path '{full_path}' or name '{iface_short}' without shared interface contract identity or proven local mapping cannot establish a local client/server relationship.",
                        path=cm.path,
                        start_line=cm.start_line,
                        end_line=cm.end_line,
                        evidence_handle=f"quarkus_rest:{cm.path.as_posix()}:{cm.start_line}-{cm.end_line}",
                    )
                )

            route_identity = f"{http_method} {full_path}"
            flavor = cm.flavor or (iface_fact.flavor if iface_fact else None) or "quarkus_rest_client"
            confidence = "high"

            cm_handle = f"quarkus_rest:{cm.path.as_posix()}:{cm.start_line}-{cm.end_line}"
            chain = [cm_handle]
            if iface_fact:
                iface_handle = f"quarkus_rest:{iface_fact.path.as_posix()}:{iface_fact.start_line}-{iface_fact.end_line}"
                if iface_handle not in chain:
                    chain.append(iface_handle)

            business_data = {
                "flavor": flavor,
                "route": route_identity,
                "http_method": http_method,
                "produces": cm_meta.get("produces"),
                "consumes": cm_meta.get("consumes"),
                "parameters": cm_meta.get("parameters", []),
                "client_interface": iface_fqcn,
            }

            rel_key = ("quarkus_rest_contract", route_identity, cm.path, cm.start_line, cm.end_line, cm_handle)
            if rel_key not in seen_rel_keys:
                seen_rel_keys.add(rel_key)
                relationships.append(
                    ImpactRelationship(
                        "quarkus_rest_contract",
                        route_identity,
                        cm.path,
                        cm.start_line,
                        cm.end_line,
                        cm_handle,
                        confidence,
                        False,
                        None,
                        evidence_chain=tuple(chain),
                        business_view=json.dumps(business_data),
                    )
                )

            matching_cdi_injections = [
                row for row in cdi_facts_rows
                if row[1] == iface_fqcn or row[1] == iface_short or (row[1] and row[1].endswith("." + iface_short))
            ]

            for subj, tgt, val, cdi_p, cdi_sl, cdi_el, scope in matching_cdi_injections:
                cdi_path = Path(cdi_p)
                cdi_handle = f"quarkus_cdi:{cdi_path.as_posix()}:{cdi_sl}-{cdi_el}"
                inj_chain = (*chain, cdi_handle)
                rel_key_inj = ("quarkus_cdi_injection", route_identity, cdi_path, cdi_sl, cdi_el, cdi_handle)
                if rel_key_inj not in seen_rel_keys:
                    seen_rel_keys.add(rel_key_inj)
                    relationships.append(
                        ImpactRelationship(
                            "quarkus_cdi_injection",
                            route_identity,
                            cdi_path,
                            cdi_sl,
                            cdi_el,
                            cdi_handle,
                            "high",
                            False,
                            None,
                            evidence_chain=inj_chain,
                            business_view=json.dumps(business_data),
                        )
                    )

            for cand_key in candidate_keys:
                cfg_matches = [row for row in config_facts_rows if row[0] == cand_key]
                for cfg_sub, cfg_val, cfg_p, cfg_sl, cfg_el, cfg_prof in cfg_matches:
                    cfg_path = Path(cfg_p)
                    cfg_handle = f"quarkus_config:{cfg_path.as_posix()}:{cfg_sl}-{cfg_el}"
                    cfg_chain = (*chain, cfg_handle)
                    rel_key_cfg = ("quarkus_config", cand_key, cfg_path, cfg_sl, cfg_el, cfg_handle)
                    if rel_key_cfg not in seen_rel_keys:
                        seen_rel_keys.add(rel_key_cfg)
                        relationships.append(
                            ImpactRelationship(
                                "quarkus_config",
                                cand_key,
                                cfg_path,
                                cfg_sl,
                                cfg_el,
                                cfg_handle,
                                "high",
                                False,
                                cfg_prof,
                                evidence_chain=cfg_chain,
                            )
                        )

    return tuple(relationships), tuple(unresolved)


def _quarkus_route_relationships(
    connection_data_path: Path,
    owner: str,
    target: ImpactTarget,
) -> tuple[tuple[ImpactRelationship, ...], tuple[UnresolvedItem, ...]]:
    connection = sqlite3.connect(connection_data_path)
    try:
        rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line, flavor
            FROM quarkus_route_facts ORDER BY path, start_line"""
        ).fetchall()
    except sqlite3.OperationalError:
        return (), ()
    finally:
        connection.close()

    if not rows:
        return (), ()

    facts = [
        QuarkusRouteFact(
            kind, subject, fact_target, value, Path(path), start_line, end_line, flavor
        )
        for kind, subject, fact_target, value, path, start_line, end_line, flavor in rows
    ]

    target_class, _, target_method = target.signature.partition("#")
    target_method_name = target_method.split("(", maxsplit=1)[0]

    relationships: list[ImpactRelationship] = []
    unresolved: list[UnresolvedItem] = []
    seen: set[tuple[str, Path, int, int, str]] = set()

    for fact in facts:
        if fact.kind == "route_base":
            continue
        fact_class, _, fact_method = fact.subject.partition("#")
        if fact_method:
            if fact_class != target_class or fact_method != target_method_name:
                continue
        elif fact_class != target_class:
            continue
        if fact.kind == "route_unresolved":
            unresolved.append(
                UnresolvedItem(
                    fact.value or "Reactive Route could not be proven.",
                    fact.path,
                    fact.start_line,
                    fact.end_line,
                    evidence_handle=f"quarkus_route:{fact.path.as_posix()}:{fact.start_line}-{fact.end_line}",
                )
            )
            continue
        if fact.kind != "route_method":
            continue
        meta = json.loads(fact.value or "{}")
        flavor = fact.flavor or "unknown"
        confidence = "high" if flavor != "unknown" else "medium"
        caller = fact.target or (f"{'/'.join(meta.get('methods', []))} {meta.get('path', '')}".strip())
        handle = f"quarkus_route:{fact.path.as_posix()}:{fact.start_line}-{fact.end_line}"
        chain = (handle,)
        business_data = {
            "flavor": flavor,
            "route": caller,
            "methods": meta.get("methods", []),
            "path": meta.get("path"),
            "handler_type": meta.get("handler_type"),
            "produces": meta.get("produces", []),
            "consumes": meta.get("consumes", []),
            "order": meta.get("order"),
            "source": meta.get("source"),
        }
        if meta.get("build_profile_conditions"):
            business_data["build_profile_conditions"] = meta.get("build_profile_conditions")
        key = ("quarkus_http_route", caller, fact.path, fact.start_line, fact.end_line, handle)
        if key in seen:
            continue
        seen.add(key)
        relationships.append(
            ImpactRelationship(
                "quarkus_http_route",
                caller,
                fact.path,
                fact.start_line,
                fact.end_line,
                handle,
                confidence,
                False,
                None,
                evidence_chain=chain,
                business_view=json.dumps(business_data),
            )
        )

    for fact in facts:
        if fact.kind != "router_unresolved":
            continue
        fact_class, _, fact_method = fact.subject.partition("#")
        if fact_method:
            if fact_class != target_class or fact_method != target_method_name:
                continue
        elif fact_class != target_class:
            continue
        unresolved.append(
            UnresolvedItem(
                fact.value or "Programmatic router registration remained unresolved.",
                fact.path,
                fact.start_line,
                fact.end_line,
                evidence_handle=f"quarkus_route:{fact.path.as_posix()}:{fact.start_line}-{fact.end_line}",
            )
        )

    return tuple(relationships), tuple(unresolved)


def _quarkus_cdi_relationships(
    connection_data_path: Path,
    owner: str,
    target: ImpactTarget,
) -> tuple[tuple[ImpactRelationship, ...], tuple[UnresolvedItem, ...]]:
    connection = sqlite3.connect(connection_data_path)
    try:
        rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line, flavor
            FROM quarkus_route_facts ORDER BY path, start_line"""
        ).fetchall()
    except sqlite3.OperationalError:
        return (), ()
    finally:
        connection.close()

    if not rows:
        return (), ()

    facts = [
        QuarkusRouteFact(
            kind, subject, fact_target, value, Path(path), start_line, end_line, flavor
        )
        for kind, subject, fact_target, value, path, start_line, end_line, flavor in rows
    ]

    target_class, _, target_method = target.signature.partition("#")
    target_method_name = target_method.split("(", maxsplit=1)[0]

    relationships: list[ImpactRelationship] = []
    unresolved: list[UnresolvedItem] = []
    seen: set[tuple[str, Path, int, int, str]] = set()

    for fact in facts:
        if fact.kind == "route_base":
            continue
        fact_class, _, fact_method = fact.subject.partition("#")
        if fact_method:
            if fact_class != target_class or fact_method != target_method_name:
                continue
        elif fact_class != target_class:
            continue
        if fact.kind == "route_unresolved":
            unresolved.append(
                UnresolvedItem(
                    fact.value or "Reactive Route could not be proven.",
                    fact.path,
                    fact.start_line,
                    fact.end_line,
                    evidence_handle=f"quarkus_route:{fact.path.as_posix()}:{fact.start_line}-{fact.end_line}",
                )
            )
            continue
        if fact.kind != "route_method":
            continue
        meta = json.loads(fact.value or "{}")
        flavor = fact.flavor or "unknown"
        confidence = "high" if flavor != "unknown" else "medium"
        caller = fact.target or (f"{'/'.join(meta.get('methods', []))} {meta.get('path', '')}".strip())
        handle = f"quarkus_route:{fact.path.as_posix()}:{fact.start_line}-{fact.end_line}"
        chain = (handle,)
        business_data = {
            "flavor": flavor,
            "route": caller,
            "methods": meta.get("methods", []),
            "path": meta.get("path"),
            "handler_type": meta.get("handler_type"),
            "produces": meta.get("produces", []),
            "consumes": meta.get("consumes", []),
            "order": meta.get("order"),
            "source": meta.get("source"),
        }
        if meta.get("build_profile_conditions"):
            business_data["build_profile_conditions"] = meta.get("build_profile_conditions")
        key = ("quarkus_http_route", caller, fact.path, fact.start_line, fact.end_line, handle)
        if key in seen:
            continue
        seen.add(key)
        relationships.append(
            ImpactRelationship(
                "quarkus_http_route",
                caller,
                fact.path,
                fact.start_line,
                fact.end_line,
                handle,
                confidence,
                False,
                None,
                evidence_chain=chain,
                business_view=json.dumps(business_data),
            )
        )

    for fact in facts:
        if fact.kind != "router_unresolved":
            continue
        fact_class, _, fact_method = fact.subject.partition("#")
        if fact_method:
            if fact_class != target_class or fact_method != target_method_name:
                continue
        elif fact_class != target_class:
            continue
        unresolved.append(
            UnresolvedItem(
                fact.value or "Programmatic router registration remained unresolved.",
                fact.path,
                fact.start_line,
                fact.end_line,
                evidence_handle=f"quarkus_route:{fact.path.as_posix()}:{fact.start_line}-{fact.end_line}",
            )
        )

    return tuple(relationships), tuple(unresolved)


def _quarkus_cdi_relationships(
    connection_data_path: Path,
    owner: str,
    target: ImpactTarget,
    build_profiles: tuple[str, ...] = (),
) -> tuple[tuple[ImpactRelationship, ...], tuple[UnresolvedItem, ...]]:
    connection = sqlite3.connect(connection_data_path)
    try:
        rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line, scope
            FROM quarkus_cdi_facts ORDER BY path, start_line, kind, subject"""
        ).fetchall()
        declarations = connection.execute(
            """SELECT kind, qualified_name, signature, path, start_line, end_line
            FROM java_declarations"""
        ).fetchall()
        invocations = connection.execute(
            """SELECT name, receiver, caller, path, start_line, end_line, argument_count
            FROM java_invocations"""
        ).fetchall()
    except sqlite3.OperationalError:
        return (), ()
    finally:
        connection.close()

    if not rows:
        return (), ()

    cdi_facts = [
        QuarkusCDIFact(
            kind, subject, target, value, Path(path), start_line, end_line, scope
        )
        for kind, subject, target, value, path, start_line, end_line, scope in rows
    ]

    relationships: list[ImpactRelationship] = []
    unresolved: list[UnresolvedItem] = []

    beans = [f for f in cdi_facts if f.kind == "cdi_bean"]
    producers = [f for f in cdi_facts if f.kind == "cdi_producer"]
    injections = [f for f in cdi_facts if f.kind == "cdi_injection"]
    dynamics = [f for f in cdi_facts if f.kind == "cdi_dynamic"]

    def matching_owner(sub: str) -> bool:
        if sub == owner:
            return True
        if sub.rsplit("#", maxsplit=1)[0] == owner:
            return True
        return sub.rsplit(".", maxsplit=1)[-1] == owner.rsplit(".", maxsplit=1)[-1]

    for dyn in dynamics:
        if matching_owner(dyn.subject):
            unresolved.append(
                _unresolved(
                    f"Dynamic CDI pattern ({dyn.value}) in {dyn.subject} is unresolved.",
                    dyn.path,
                    dyn.start_line,
                    dyn.end_line,
                    "quarkus_cdi",
                )
            )

    target_class = owner.rsplit(".", maxsplit=1)[-1]
    target_full_class = owner
    target_sig = target.signature.split("(")[0]

    for inj in injections:
        inj_type = inj.target or ""
        inj_type_simple = inj_type.rsplit(".", maxsplit=1)[-1]
        inj_named = inj.scope  # @Named qualifier if present

        matching_candidates = []

        # Match class beans
        for b in beans:
            # Check profile conditions
            if b.scope:
                if "if:" in b.scope:
                    if_prof = b.scope.split("if:", 1)[1].split(";")[0]
                    if build_profiles and if_prof not in build_profiles:
                        continue
                if "unless:" in b.scope:
                    unless_prof = b.scope.split("unless:", 1)[1].split(";")[0]
                    if build_profiles and unless_prof in build_profiles:
                        continue

            b_simple = b.subject.rsplit(".", maxsplit=1)[-1]
            type_matches = (
                b.subject == inj_type
                or b_simple == inj_type_simple
                or (b.target and inj_type_simple in b.target)
            )
            if not type_matches:
                continue

            # Check @Named qualifier match
            if inj_named and b.value and inj_named != b.value:
                continue
            matching_candidates.append(("bean", b))

        # Match producer methods
        for p in producers:
            p_ret = p.target or ""
            p_ret_simple = p_ret.rsplit(".", maxsplit=1)[-1]
            if inj_type_simple == p_ret_simple or inj_type == p_ret:
                if inj_named and p.value and inj_named != p.value:
                    continue
                matching_candidates.append(("producer", p))

        if not matching_candidates:
            unresolved.append(
                _unresolved(
                    f"Quarkus CDI Injection Point '{inj.value}' of type '{inj.target}' in {inj.subject} has no matching CDI bean candidate in indexed Java source.",
                    inj.path,
                    inj.start_line,
                    inj.end_line,
                    "quarkus_cdi",
                )
            )
            continue
        elif len(matching_candidates) > 1:
            candidate_names = ", ".join(item[1].subject for item in matching_candidates)
            unresolved.append(
                _unresolved(
                    f"Quarkus CDI Injection Point '{inj.value}' of type '{inj.target}' in {inj.subject} has multiple candidate beans ({candidate_names}); ambiguous CDI injection is unresolved.",
                    inj.path,
                    inj.start_line,
                    inj.end_line,
                    "quarkus_cdi",
                )
            )
            continue

        cand_kind, selected_item = matching_candidates[0]
        inj_owner = inj.subject.split("#")[0]
        if cand_kind == "bean":
            selected_bean = selected_item
            if selected_bean.subject == target_full_class or selected_bean.subject.endswith("." + target_class) or (selected_bean.target and target_class in selected_bean.target):
                evidence_handle = f"quarkus_cdi:{inj.path.as_posix()}:{inj.start_line}-{inj.end_line}"
                relationships.append(
                    ImpactRelationship(
                        "quarkus_cdi_injection",
                        selected_bean.subject,
                        inj.path,
                        inj.start_line,
                        inj.end_line,
                        evidence_handle,
                        "high",
                        False,
                        None,
                        evidence_chain=(evidence_handle,),
                    )
                )

                method_name = target.signature.split("#", maxsplit=1)[-1].partition("(")[0]
                for inv in invocations:
                    inv_name, inv_receiver, inv_caller, inv_path, inv_start, inv_end, _ = inv
                    if inv_name == method_name and inv_caller and inv_caller.startswith(inj_owner):
                        inv_handle = f"invocation:{inv_path}:{inv_start}-{inv_end}"
                        relationships.append(
                            ImpactRelationship(
                                "quarkus_cdi_dispatch",
                                inv_caller,
                                Path(inv_path),
                                inv_start,
                                inv_end,
                                inv_handle,
                                "high",
                                False,
                                None,
                                evidence_chain=(evidence_handle, inv_handle),
                            )
                        )
        elif cand_kind == "producer":
            selected_producer = selected_item
            if selected_producer.subject == target_sig or selected_producer.subject.startswith(owner):
                evidence_handle = f"quarkus_cdi:{inj.path.as_posix()}:{inj.start_line}-{inj.end_line}"
                relationships.append(
                    ImpactRelationship(
                        "quarkus_cdi_injection",
                        selected_producer.subject,
                        inj.path,
                        inj.start_line,
                        inj.end_line,
                        evidence_handle,
                        "high",
                        False,
                        None,
                        evidence_chain=(evidence_handle,),
                    )
                )

                for inv in invocations:
                    inv_name, inv_receiver, inv_caller, inv_path, inv_start, inv_end, _ = inv
                    if inv_caller and inv_caller.startswith(inj_owner):
                        inv_handle = f"invocation:{inv_path}:{inv_start}-{inv_end}"
                        relationships.append(
                            ImpactRelationship(
                                "quarkus_cdi_dispatch",
                                inv_caller,
                                Path(inv_path),
                                inv_start,
                                inv_end,
                                inv_handle,
                                "high",
                                False,
                                None,
                                evidence_chain=(evidence_handle, inv_handle),
                            )
                        )

    return tuple(relationships), tuple(unresolved)


def _is_quarkus_build_time_key(key: str) -> bool:
    if not key.startswith("quarkus."):
        return False
    build_prefixes = (
        "quarkus.package.",
        "quarkus.native.",
        "quarkus.datasource.db-kind",
        "quarkus.hibernate-orm.database.generation",
        "quarkus.index-dependency.",
        "quarkus.banner.",
    )
    return any(key.startswith(p) for p in build_prefixes)


def _quarkus_configuration_facts(path: Path, source: bytes) -> tuple[QuarkusConfigFact, ...]:
    text = source.decode("utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".properties":
        return tuple(_quarkus_properties_facts(path, text.splitlines()))
    if suffix in {".yml", ".yaml"}:
        return tuple(_quarkus_yaml_facts(path, text.splitlines()))
    return ()


def _quarkus_properties_facts(path: Path, lines: list[str]) -> list[QuarkusConfigFact]:
    file_profile = _spring_file_profile(path)
    facts: list[QuarkusConfigFact] = []
    for line_number, line in enumerate(lines, 1):
        line_str = line.strip()
        if not line_str or line_str.startswith("#") or line_str.startswith("!"):
            continue
        match = re.match(r"^\s*(?:%([^.]+)\.)?([^#!\s][^:=\s]*)\s*[:=]\s*(.*?)\s*$", line)
        if match:
            inline_profile = match.group(1)
            key = match.group(2).strip()
            value = match.group(3).strip()
            profile = inline_profile or file_profile
            is_build_time = _is_quarkus_build_time_key(key)
            facts.append(
                QuarkusConfigFact(
                    "quarkus_property_source",
                    key,
                    None,
                    value,
                    path,
                    line_number,
                    line_number,
                    profile=profile,
                    is_build_time=is_build_time,
                )
            )
    return facts


def _quarkus_yaml_facts(path: Path, lines: list[str]) -> list[QuarkusConfigFact]:
    file_profile = _spring_file_profile(path)
    document_profile = file_profile
    facts: list[QuarkusConfigFact] = []
    parents: list[tuple[int, str]] = []
    for line_number, line in enumerate(lines, 1):
        if line.strip() == "---":
            parents.clear()
            document_profile = file_profile
            continue
        if not line.strip() or line.lstrip().startswith("#") or line.lstrip().startswith("-"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        match = re.match(r"\s*([^:#]+):(?:\s*(.*?))?\s*$", line)
        if not match:
            continue
        key, value = match.group(1).strip(), (match.group(2) or "").strip()
        while parents and parents[-1][0] >= indent:
            parents.pop()
        if key.startswith("%") and not parents:
            document_profile = key.lstrip("%").strip("'\"")
            continue
        full_key = ".".join([parent[1] for parent in parents] + [key])
        if value:
            value = value.strip("'\"")
            is_build_time = _is_quarkus_build_time_key(full_key)
            facts.append(
                QuarkusConfigFact(
                    "quarkus_property_source",
                    full_key,
                    None,
                    value,
                    path,
                    line_number,
                    line_number,
                    profile=document_profile,
                    is_build_time=is_build_time,
                )
            )
        else:
            parents.append((indent, key))
    return facts


def _to_kebab_case(name: str) -> str:
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1-\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1-\2', s1).lower()


def _extract_config_mapping_methods(
    interface_decl: JavaDeclaration,
    lines: list[str],
    prefix: str,
    path: Path,
    declarations: tuple[JavaDeclaration, ...],
    facts: list[QuarkusConfigFact],
) -> None:
    interface_lines = lines[interface_decl.start_line - 1 : interface_decl.end_line]
    interface_text = "\n".join(interface_lines)

    method_matches = re.finditer(
        r"(?P<annotations>(?:@[A-Za-z_$][\w$.]*(?:\([^)]*\))?\s*)*)"
        r"(?P<return_type>[A-Za-z_$][\w$.]*)\s+(?P<method_name>[A-Za-z_$][\w$]*)\s*\(\s*\)\s*;",
        interface_text,
    )
    for match in method_matches:
        annotations_text = match.group("annotations") or ""
        return_type = match.group("return_type").strip()
        method_name = match.group("method_name").strip()

        match_line = interface_decl.start_line + interface_text.count("\n", 0, match.start())
        with_name_match = re.search(r'@WithName\s*\(\s*["\']([^"\']+)["\']\s*\)', annotations_text)
        with_parent_name = "@WithParentName" in annotations_text

        if with_name_match:
            segment = with_name_match.group(1)
        else:
            segment = _to_kebab_case(method_name)

        if with_parent_name:
            key = prefix
        else:
            key = f"{prefix}.{segment}" if prefix else segment

        nested_decl = next(
            (d for d in declarations if d.kind == "interface" and (d.name == return_type or d.qualified_name.endswith("." + return_type))),
            None,
        )
        if nested_decl is not None:
            _extract_config_mapping_methods(nested_decl, lines, key, path, declarations, facts)
        else:
            subject = f"{interface_decl.qualified_name}#{method_name}"
            facts.append(
                QuarkusConfigFact(
                    "quarkus_property_consumer",
                    subject,
                    key,
                    None,
                    path,
                    match_line,
                    match_line,
                )
            )
            facts.append(
                QuarkusConfigFact(
                    "quarkus_property_consumer",
                    interface_decl.qualified_name,
                    key,
                    None,
                    path,
                    match_line,
                    match_line,
                )
            )


def _quarkus_java_facts(
    path: Path, source: bytes, declarations: tuple[JavaDeclaration, ...]
) -> tuple[QuarkusConfigFact, ...]:
    text = source.decode("utf-8", errors="replace")
    lines = text.splitlines()
    facts: list[QuarkusConfigFact] = []
    for idx, line in enumerate(lines, 1):
        if "@ConfigProperty" in line:
            matches = re.finditer(r"@ConfigProperty\s*(?:\(([^)]*)\))?", line)
            for m in matches:
                args = m.group(1) or ""
                name_match = re.search(r'\bname\s*=\s*["\']([^"\']+)["\']', args)
                if not name_match:
                    name_match = re.search(r'^\s*["\']([^"\']+)["\']', args)
                key_name = name_match.group(1) if name_match else ""
                default_match = re.search(r'\bdefaultValue\s*=\s*["\']([^"\']+)["\']', args)
                default_val = default_match.group(1) if default_match else None

                owner_decl = next(
                    (d for d in declarations if d.path == path and d.start_line <= idx <= d.end_line),
                    None,
                )
                subject = owner_decl.qualified_name if owner_decl else ""
                is_optional = "Optional" in line
                meta = []
                if default_val is not None:
                    meta.append(f"default:{default_val}")
                if is_optional:
                    meta.append("optional")
                value_meta = ";".join(meta) if meta else None

                if key_name:
                    facts.append(
                        QuarkusConfigFact(
                            "quarkus_property_consumer",
                            subject,
                            key_name,
                            value_meta,
                            path,
                            idx,
                            idx,
                        )
                    )

    if "@ConfigMapping" in text:
        for decl in declarations:
            if decl.kind == "interface" and decl.path == path:
                decl_text = "\n".join(lines[decl.start_line - 1 : decl.end_line])
                mapping_match = re.search(r"@ConfigMapping\s*(?:\(([^)]*)\))?", decl_text)
                if mapping_match:
                    args = mapping_match.group(1) or ""
                    prefix_match = re.search(r'\bprefix\s*=\s*["\']([^"\']+)["\']', args)
                    prefix = prefix_match.group(1) if prefix_match else ""
                    _extract_config_mapping_methods(
                        decl, lines, prefix, path, declarations, facts
                    )

    return tuple(facts)


def _quarkus_config_relationships(
    connection_data_path: Path,
    owner: str,
    build_profiles: tuple[str, ...],
    runtime_profiles: tuple[str, ...],
) -> tuple[tuple[ImpactRelationship, ...], tuple[UnresolvedItem, ...]]:
    connection = sqlite3.connect(connection_data_path)
    try:
        rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line, profile, is_build_time
            FROM quarkus_config_facts ORDER BY path, start_line, kind, subject"""
        ).fetchall()
    except sqlite3.OperationalError:
        return (), ()
    finally:
        connection.close()

    if not rows:
        return (), ()

    facts = [
        QuarkusConfigFact(
            kind, subject, target, value, Path(path), start_line, end_line, profile, bool(is_build_time)
        )
        for kind, subject, target, value, path, start_line, end_line, profile, is_build_time in rows
    ]

    relationships: list[ImpactRelationship] = []
    unresolved: list[UnresolvedItem] = []

    parent_profiles = set()
    for f in facts:
        if f.kind == "quarkus_property_source" and f.subject == "quarkus.config.profile.parent" and f.value:
            parent_profiles.add(f.value)

    active_build_set = set(build_profiles) | parent_profiles
    active_runtime_set = set(runtime_profiles or build_profiles) | parent_profiles

    def matching_owner(sub: str) -> bool:
        if sub == owner:
            return True
        if sub.rsplit("#", maxsplit=1)[0] == owner:
            return True
        return sub.rsplit(".", maxsplit=1)[-1] == owner.rsplit(".", maxsplit=1)[-1]

    consumer_facts = [
        f for f in facts
        if f.kind == "quarkus_property_consumer" and matching_owner(f.subject)
    ]
    source_facts = [f for f in facts if f.kind == "quarkus_property_source"]

    for consumer in consumer_facts:
        if not consumer.target:
            continue
        consumer_handle = f"quarkus_config:{consumer.path.as_posix()}:{consumer.start_line}-{consumer.end_line}"
        matching_sources = [s for s in source_facts if s.subject == consumer.target]

        if not matching_sources:
            if not consumer.value or ("optional" not in consumer.value and "default:" not in consumer.value):
                unresolved.append(
                    _unresolved(
                        f"Quarkus config property '{consumer.target}' consumed by {consumer.subject} has no matching local property source.",
                        consumer.path,
                        consumer.start_line,
                        consumer.end_line,
                        "quarkus_config",
                    )
                )

        relationships.append(
            ImpactRelationship(
                "property_consumer",
                consumer.target,
                consumer.path,
                consumer.start_line,
                consumer.end_line,
                consumer_handle,
                "high",
                False,
                None,
                evidence_chain=(consumer_handle,),
            )
        )

        for source in matching_sources:
            source_handle = f"quarkus_config:{source.path.as_posix()}:{source.start_line}-{source.end_line}"
            if source.is_build_time:
                if active_build_set:
                    applies = (source.profile is None or source.profile in active_build_set)
                    conditional = False
                else:
                    applies = True
                    conditional = (source.profile is not None)
            else:
                if active_runtime_set:
                    applies = (source.profile is None or source.profile in active_runtime_set)
                    conditional = False
                else:
                    applies = True
                    conditional = (source.profile is not None)

            if applies:
                relationships.append(
                    ImpactRelationship(
                        "property_source",
                        source.subject,
                        source.path,
                        source.start_line,
                        source.end_line,
                        source_handle,
                        "medium" if conditional else "high",
                        conditional,
                        source.profile,
                        evidence_chain=(consumer_handle, source_handle),
                    )
                )

                if source.value and "${" in source.value:
                    for expr_match in re.finditer(r"\$\{([^}:]+)(?::[^}]*)?\}", source.value):
                        ref_key = expr_match.group(1).strip()
                        ref_sources = [s for s in source_facts if s.subject == ref_key]
                        if not ref_sources:
                            if re.fullmatch(r"[A-Z0-9_]+", ref_key):
                                unresolved.append(
                                    _unresolved(
                                        f"Environment-variable configuration override was not resolved for {ref_key}.",
                                        source.path,
                                        source.start_line,
                                        source.end_line,
                                        "quarkus_config",
                                    )
                                )
                        for ref_source in ref_sources:
                            ref_handle = f"quarkus_config:{ref_source.path.as_posix()}:{ref_source.start_line}-{ref_source.end_line}"
                            chain = (consumer_handle, source_handle, ref_handle)
                            relationships.append(
                                ImpactRelationship(
                                    "property_source",
                                    ref_source.subject,
                                    ref_source.path,
                                    ref_source.start_line,
                                    ref_source.end_line,
                                    ref_handle,
                                    "medium" if conditional else "high",
                                    conditional,
                                    ref_source.profile,
                                    evidence_chain=chain,
                                )
                            )

    return tuple(relationships), tuple(unresolved)


def _analyze_quarkus_build_files(
    contents_by_path: dict[Path, bytes]
) -> tuple[QuarkusBuildFact, ...]:
    facts: list[QuarkusBuildFact] = []
    build_files = {
        path: content
        for path, content in contents_by_path.items()
        if path.name in {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}
    }
    for path, content in sorted(build_files.items(), key=lambda item: str(item[0])):
        text = content.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if path.name == "pom.xml":
            facts.extend(_parse_maven_quarkus_facts(path, text, lines))
        elif path.name in {"build.gradle", "build.gradle.kts"}:
            facts.extend(_parse_gradle_quarkus_facts(path, text, lines))
        elif path.name in {"settings.gradle", "settings.gradle.kts"}:
            facts.extend(_parse_gradle_settings_facts(path, text, lines))
    return tuple(
        sorted(
            facts,
            key=lambda f: (str(f.path), f.start_line, f.kind, f.subject, f.target or ""),
        )
    )


def _parse_maven_quarkus_facts(
    path: Path, text: str, lines: list[str]
) -> list[QuarkusBuildFact]:
    facts: list[QuarkusBuildFact] = []

    for idx, line in enumerate(lines, 1):
        for match in re.finditer(r"<module>\s*([^<]+)\s*</module>", line):
            facts.append(
                QuarkusBuildFact(
                    "module",
                    match.group(1).strip(),
                    str(path.parent.as_posix()),
                    "maven",
                    path,
                    idx,
                    idx,
                )
            )

    bom_match = re.search(r"<(?:[A-Za-z_$][\w$.-]*:)?artifactId>\s*(quarkus-(?:platform-)?bom|quarkus-universe-bom)\s*</", text)
    if bom_match:
        start_line = text.count("\n", 0, bom_match.start()) + 1
        facts.append(
            QuarkusBuildFact(
                "platform",
                "io.quarkus:quarkus-bom",
                "platform",
                bom_match.group(1),
                path,
                start_line,
                start_line,
            )
        )

    plugin_match = re.search(r"<(?:[A-Za-z_$][\w$.-]*:)?artifactId>\s*quarkus-maven-plugin\s*</", text)
    if plugin_match:
        start_line = text.count("\n", 0, plugin_match.start()) + 1
        facts.append(
            QuarkusBuildFact(
                "plugin",
                "io.quarkus:quarkus-maven-plugin",
                "quarkus",
                "maven",
                path,
                start_line,
                start_line,
            )
        )

    dep_blocks = re.finditer(
        r"<(?:[A-Za-z_$][\w$.-]*:)?dependency\b[^>]*>(.*?)</(?:[A-Za-z_$][\w$.-]*:)?dependency>",
        text,
        re.DOTALL,
    )
    for dep in dep_blocks:
        dep_text = dep.group(1)
        group_match = re.search(r"<(?:[A-Za-z_$][\w$.-]*:)?groupId>\s*([^<]+)\s*</", dep_text)
        art_match = re.search(r"<(?:[A-Za-z_$][\w$.-]*:)?artifactId>\s*([^<]+)\s*</", dep_text)
        group_id = group_match.group(1).strip() if group_match else ""
        artifact_id = art_match.group(1).strip() if art_match else ""
        if group_id.startswith("io.quarkus") or artifact_id.startswith("quarkus-"):
            if artifact_id in {"quarkus-bom", "quarkus-universe-bom", "quarkus-maven-plugin"}:
                continue
            start_line = text.count("\n", 0, dep.start()) + 1
            end_line = text.count("\n", 0, dep.end()) + 1
            ext_status = _quarkus_extension_status(artifact_id)
            facts.append(
                QuarkusBuildFact(
                    "extension",
                    artifact_id,
                    group_id or "io.quarkus",
                    ext_status,
                    path,
                    start_line,
                    end_line,
                )
            )

    profile_blocks = re.finditer(
        r"<(?:[A-Za-z_$][\w$.-]*:)?profile\b[^>]*>(.*?)</(?:[A-Za-z_$][\w$.-]*:)?profile>",
        text,
        re.DOTALL,
    )
    for prof in profile_blocks:
        prof_text = prof.group(1)
        id_match = re.search(r"<(?:[A-Za-z_$][\w$.-]*:)?id>\s*([^<]+)\s*</", prof_text)
        prof_id = id_match.group(1).strip() if id_match else ""
        if prof_id == "native" or "quarkus.package.type" in prof_text or "native-image" in prof_text:
            start_line = text.count("\n", 0, prof.start()) + 1
            end_line = text.count("\n", 0, prof.end()) + 1
            facts.append(
                QuarkusBuildFact(
                    "profile",
                    prof_id or "native",
                    "profile",
                    "native",
                    path,
                    start_line,
                    end_line,
                    profile=prof_id or "native",
                )
            )

    return facts


def _parse_gradle_quarkus_facts(
    path: Path, text: str, lines: list[str]
) -> list[QuarkusBuildFact]:
    facts: list[QuarkusBuildFact] = []

    for idx, line in enumerate(lines, 1):
        if re.search(r"\b(?:id|apply\s+plugin:)\s*[\"']io\.quarkus", line) or re.search(r"\bid\s*\(\s*[\"']io\.quarkus", line):
            facts.append(
                QuarkusBuildFact(
                    "plugin",
                    "io.quarkus",
                    "quarkus",
                    "gradle",
                    path,
                    idx,
                    idx,
                )
            )

    bom_match = re.search(r"[\"']io\.quarkus:(quarkus-(?:platform-)?bom):[^\"']+[\"']", text)
    if bom_match:
        start_line = text.count("\n", 0, bom_match.start()) + 1
        facts.append(
            QuarkusBuildFact(
                "platform",
                "io.quarkus:quarkus-bom",
                "platform",
                bom_match.group(1),
                path,
                start_line,
                start_line,
            )
        )

    for idx, line in enumerate(lines, 1):
        for match in re.finditer(r"[\"'](?:io\.quarkus:)?(quarkus-[\w-]+)(?::[^\"']*)?[\"']", line):
            artifact_id = match.group(1)
            if artifact_id in {"quarkus-bom", "quarkus-universe-bom", "quarkus-gradle-plugin"}:
                continue
            ext_status = _quarkus_extension_status(artifact_id)
            facts.append(
                QuarkusBuildFact(
                    "extension",
                    artifact_id,
                    "io.quarkus",
                    ext_status,
                    path,
                    idx,
                    idx,
                )
            )

    for idx, line in enumerate(lines, 1):
        if "quarkusBuild" in line or ("native" in line.lower() and "quarkus" in text.lower()):
            if "quarkus.package.type" in line or "native" in line:
                facts.append(
                    QuarkusBuildFact(
                        "profile",
                        "native",
                        "profile",
                        "native",
                        path,
                        idx,
                        idx,
                        profile="native",
                    )
                )

    return facts


def _parse_gradle_settings_facts(
    path: Path, text: str, lines: list[str]
) -> list[QuarkusBuildFact]:
    facts: list[QuarkusBuildFact] = []
    for idx, line in enumerate(lines, 1):
        for match in re.finditer(r"\binclude\s*(?:\(\s*)?[\"']:?([^\"']+)[\"']", line):
            facts.append(
                QuarkusBuildFact(
                    "module",
                    match.group(1).replace(":", "/"),
                    str(path.parent.as_posix()),
                    "gradle",
                    path,
                    idx,
                    idx,
                )
            )
    return facts


def _quarkus_extension_status(artifact_id: str) -> str:
    legacy_extensions = {
        "quarkus-resteasy",
        "quarkus-docker",
        "quarkus-smallrye-metrics",
        "quarkus-resteasy-jsonb",
        "quarkus-resteasy-jackson",
    }
    return "legacy" if artifact_id in legacy_extensions else "current"


def _repository_index_status(repository_root: Path) -> RepositoryIndexStatus:
    root = repository_root.resolve()
    database_path = root / '.changescope' / 'index.sqlite'
    if not database_path.is_file():
        return RepositoryIndexStatus('missing', root, False, None, 0, 0, 0, 0, None)

    try:
        connection = sqlite3.connect(database_path)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    'SELECT name FROM sqlite_master WHERE type = \'table\''
                )
            }
            if 'metadata' not in tables:
                return RepositoryIndexStatus('unreadable', root, True, None, 0, 0, 0, 0, None)

            metadata = dict(connection.execute('SELECT key, value FROM metadata'))

            def count(table: str) -> int:
                if table not in tables:
                    return 0
                return int(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0])

            snapshot = _read_index_snapshot(connection, root)
            return RepositoryIndexStatus(
                'ready',
                root,
                True,
                metadata.get('schema_version'),
                count('source_files'),
                count('java_declarations') + count('vbnet_declarations'),
                count('java_invocations') + count('vbnet_invocations'),
                count('soap_facts'),
                snapshot,
            )
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        return RepositoryIndexStatus('unreadable', root, True, None, 0, 0, 0, 0, None)


def _quarkus_build_impact_evidence(
    database_path: Path, target_path: Path
) -> tuple[list[str], list[UnresolvedItem]]:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            """SELECT kind, subject, target, value, path, start_line, end_line, profile
            FROM quarkus_build_facts ORDER BY path, start_line"""
        ).fetchall()
    finally:
        connection.close()

    if not rows:
        return [], []

    facts = [
        QuarkusBuildFact(
            kind, subject, target, value, Path(path), start_line, end_line, profile
        )
        for kind, subject, target, value, path, start_line, end_line, profile in rows
    ]

    target_posix = target_path.as_posix()
    build_facts_by_file: dict[Path, list[QuarkusBuildFact]] = {}
    for fact in facts:
        build_facts_by_file.setdefault(fact.path, []).append(fact)

    best_build_file: Path | None = None
    best_match_len = -1

    for build_file in build_facts_by_file:
        build_dir = build_file.parent.as_posix()
        if build_dir == "." or target_posix.startswith(build_dir + "/"):
            match_len = len(build_dir)
            if match_len > best_match_len:
                best_match_len = match_len
                best_build_file = build_file

    if best_build_file is None and build_facts_by_file:
        best_build_file = sorted(build_facts_by_file.keys(), key=lambda p: str(p))[0]

    module_facts = build_facts_by_file.get(best_build_file, []) if best_build_file else []
    module_dir = best_build_file.parent.as_posix() if best_build_file else "."
    extensions = [f for f in module_facts if f.kind == "extension"]
    plugins = [f for f in module_facts if f.kind == "plugin"]
    platforms = [f for f in module_facts if f.kind == "platform"]

    assumptions: list[str] = []
    unresolved: list[UnresolvedItem] = []

    if extensions or plugins:
        ext_desc = ", ".join(
            f"{f.subject} [{f.value}]" if f.value else f.subject for f in extensions
        )
        plugin_desc = plugins[0].subject if plugins else "Quarkus build"
        assumptions.append(
            f"Quarkus module '{module_dir}' detected via {best_build_file.as_posix()} "
            f"({plugin_desc}; extensions: {ext_desc or 'none'})."
        )
    elif platforms or any(f.kind == "module" for f in facts):
        ref_fact = module_facts[0] if module_facts else facts[0]
        unresolved.append(
            _unresolved(
                f"Quarkus module capability for '{module_dir}' is inherited or unmapped; framework flavor is unconfirmed.",
                ref_fact.path,
                ref_fact.start_line,
                ref_fact.end_line,
                "quarkus_build",
            )
        )
    elif facts:
        ext_desc = ", ".join(f.subject for f in facts if f.kind == "extension")
        assumptions.append(
            f"Quarkus build evidence detected across repository (extensions: {ext_desc or 'none'})."
        )

    return assumptions, unresolved


def _workspace_catalog_summary(catalog_root: Path) -> WorkspaceCatalogSummary:
    root = catalog_root.resolve()
    database_path = root / '.changescope' / 'catalog.sqlite'
    if not database_path.is_file():
        return WorkspaceCatalogSummary('missing', root, False)

    try:
        connection = sqlite3.connect(database_path)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    'SELECT name FROM sqlite_master WHERE type = ?', ('table',)
                )
            }
            repositories: tuple[CatalogRepository, ...] = ()
            mappings: tuple[CatalogMapping, ...] = ()
            if 'catalog_repositories' in tables:
                repositories = tuple(
                    CatalogRepository(
                        repository_id,
                        Path(repository_path),
                        git_commit or None,
                        working_tree_state,
                    )
                    for repository_id, repository_path, git_commit, working_tree_state in connection.execute(
                        'SELECT repository_id, repository_path, git_commit, working_tree_state '
                        'FROM catalog_repositories ORDER BY repository_id'
                    )
                )
            if 'catalog_mappings' in tables:
                mappings = tuple(
                    CatalogMapping(
                        source_repository_id,
                        contract_kind,
                        contract_key,
                        target_repository_id,
                        target_contract_key,
                        provenance,
                    )
                    for source_repository_id, contract_kind, contract_key,
                    target_repository_id, target_contract_key, provenance in connection.execute(
                        'SELECT source_repository_id, contract_kind, contract_key, '
                        'target_repository_id, target_contract_key, provenance '
                        'FROM catalog_mappings '
                        'ORDER BY source_repository_id, contract_kind, contract_key, '
                        'target_repository_id, target_contract_key'
                    )
                )
            return WorkspaceCatalogSummary('ready', root, True, repositories, mappings)
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        return WorkspaceCatalogSummary('unreadable', root, True)


def _catalog_db_path(catalog_root: Path) -> Path:
    catalog_dir = catalog_root.resolve() / ".changescope"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    return catalog_dir / "catalog.sqlite"


def _ensure_catalog_tables(connection: sqlite3.Connection) -> bool:
    repo_table_missing = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'catalog_repositories'"
    ).fetchone() is None
    mapping_table_missing = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'catalog_mappings'"
    ).fetchone() is None

    connection.execute(
        """CREATE TABLE IF NOT EXISTS catalog_repositories (
        repository_id TEXT PRIMARY KEY,
        repository_path TEXT NOT NULL,
        git_commit TEXT,
        working_tree_state TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS catalog_mappings (
        source_repository_id TEXT NOT NULL,
        contract_kind TEXT NOT NULL,
        contract_key TEXT NOT NULL,
        target_repository_id TEXT NOT NULL,
        target_contract_key TEXT NOT NULL,
        provenance TEXT NOT NULL DEFAULT ''
        )"""
    )
    return repo_table_missing or mapping_table_missing


def _catalog_register_repository(request: CatalogRegisterRepositoryRequest) -> CatalogResult:
    repo_path = request.repository_path.resolve()
    db_path = repo_path / ".changescope" / "index.sqlite"
    snapshot: IndexSnapshot | None = None
    if db_path.is_file():
        conn = sqlite3.connect(db_path)
        try:
            snapshot = _read_index_snapshot(conn, repo_path)
        finally:
            conn.close()
    else:
        snapshot = _snapshot(repo_path)

    git_commit = snapshot.git_commit if snapshot else None
    working_tree_state = snapshot.working_tree_state if snapshot else "clean"

    catalog_db = _catalog_db_path(request.catalog_root)
    conn = sqlite3.connect(catalog_db)
    try:
        _ensure_catalog_tables(conn)
        conn.execute(
            """INSERT OR REPLACE INTO catalog_repositories(
            repository_id, repository_path, git_commit, working_tree_state
            ) VALUES (?, ?, ?, ?)""",
            (request.repository_id, repo_path.as_posix(), git_commit, working_tree_state),
        )
        conn.commit()
    finally:
        conn.close()

    cat_repo = CatalogRepository(
        repository_id=request.repository_id,
        repository_path=repo_path,
        git_commit=git_commit,
        working_tree_state=working_tree_state,
    )
    return CatalogResult("registered", repository=cat_repo, snapshot=snapshot)


def _catalog_register_mapping(request: CatalogRegisterMappingRequest) -> CatalogResult:
    catalog_db = _catalog_db_path(request.catalog_root)
    conn = sqlite3.connect(catalog_db)
    try:
        _ensure_catalog_tables(conn)
        conn.execute(
            """INSERT INTO catalog_mappings(
            source_repository_id, contract_kind, contract_key, target_repository_id, target_contract_key, provenance
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                request.source_repository_id,
                request.contract_kind,
                request.contract_key,
                request.target_repository_id,
                request.target_contract_key,
                request.provenance,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    mapping = CatalogMapping(
        source_repository_id=request.source_repository_id,
        contract_kind=request.contract_kind,
        contract_key=request.contract_key,
        target_repository_id=request.target_repository_id,
        target_contract_key=request.target_contract_key,
        provenance=request.provenance,
    )
    return CatalogResult("registered", mapping=mapping)


def _catalog_resolve_mapping(request: CatalogResolveMappingRequest) -> CatalogResult:
    catalog_db = request.catalog_root.resolve() / ".changescope" / "catalog.sqlite"
    if not catalog_db.is_file():
        return CatalogResult(
            "missing",
            unresolved_items=(
                _unresolved("No Workspace Catalog exists. Register repositories and mappings first."),
            ),
        )

    conn = sqlite3.connect(catalog_db)
    try:
        _ensure_catalog_tables(conn)

        source_repo_row = conn.execute(
            "SELECT repository_id, repository_path, git_commit, working_tree_state FROM catalog_repositories WHERE repository_id = ?",
            (request.source_repository_id,),
        ).fetchone()

        if source_repo_row is None:
            return CatalogResult(
                "missing",
                unresolved_items=(
                    _unresolved(f"Source repository '{request.source_repository_id}' is not registered in the Workspace Catalog."),
                ),
            )

        rows = conn.execute(
            """SELECT source_repository_id, contract_kind, contract_key, target_repository_id, target_contract_key, provenance
            FROM catalog_mappings
            WHERE source_repository_id = ? AND contract_kind = ? AND contract_key = ?""",
            (request.source_repository_id, request.contract_kind, request.contract_key),
        ).fetchall()

        if not rows:
            return CatalogResult(
                "missing",
                unresolved_items=(
                    _unresolved(f"No explicit contract mapping registered for contract key '{request.contract_key}' (kind: {request.contract_kind}) in source repository '{request.source_repository_id}'."),
                ),
            )

        mappings = tuple(
            CatalogMapping(r[0], r[1], r[2], r[3], r[4], r[5])
            for r in rows
        )

        if len(mappings) > 1:
            return CatalogResult("ambiguous", candidates=mappings)

        resolved_mapping = mappings[0]

        target_repo_row = conn.execute(
            "SELECT repository_id, repository_path, git_commit, working_tree_state FROM catalog_repositories WHERE repository_id = ?",
            (resolved_mapping.target_repository_id,),
        ).fetchone()

        if target_repo_row is None:
            return CatalogResult(
                "missing",
                mapping=resolved_mapping,
                unresolved_items=(
                    _unresolved(f"Target repository '{resolved_mapping.target_repository_id}' is not registered in the Workspace Catalog."),
                ),
            )

        target_repo_path = Path(target_repo_row[1])
        registered_target_commit = target_repo_row[2]

        target_index_db = target_repo_path / ".changescope" / "index.sqlite"
        if not target_index_db.is_file():
            return CatalogResult(
                "missing",
                mapping=resolved_mapping,
                unresolved_items=(
                    _unresolved(f"Target repository '{resolved_mapping.target_repository_id}' local Repository Index is missing at '{target_repo_path}'."),
                ),
            )

        target_conn = sqlite3.connect(target_index_db)
        try:
            current_target_snapshot = _read_index_snapshot(target_conn, target_repo_path)
        finally:
            target_conn.close()

        if current_target_snapshot.git_commit != registered_target_commit:
            return CatalogResult(
                "stale",
                mapping=resolved_mapping,
                unresolved_items=(
                    _unresolved(
                        f"Target repository '{resolved_mapping.target_repository_id}' index snapshot is stale "
                        f"(registered commit: {registered_target_commit or 'none'}, current commit: {current_target_snapshot.git_commit or 'none'})."
                    ),
                ),
                snapshot=current_target_snapshot,
            )

        return CatalogResult("resolved", mapping=resolved_mapping, snapshot=current_target_snapshot)
    finally:
        conn.close()


def _insert_vbnet_facts(
    connection: sqlite3.Connection,
    declarations: tuple[VBNETDeclaration, ...],
    invocations: tuple[VBNETInvocation, ...],
    facts: tuple[VBNETFact, ...],
) -> None:
    connection.executemany(
        """INSERT INTO vbnet_declarations(
        kind, name, qualified_name, signature, path, start_line, end_line, is_test, is_private, language
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                d.kind, d.name, d.qualified_name, d.signature, str(d.path),
                d.start_line, d.end_line, int(d.is_test), int(d.is_private), getattr(d, "language", "vbnet"),
            )
            for d in declarations
        ),
    )
    connection.executemany(
        """INSERT INTO vbnet_invocations(
        name, receiver, caller, path, start_line, end_line, is_test, argument_count, language
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                i.name, i.receiver, i.caller, str(i.path), i.start_line, i.end_line,
                int(i.is_test), i.argument_count, getattr(i, "language", "vbnet"),
            )
            for i in invocations
        ),
    )
    connection.executemany(
        """INSERT INTO vbnet_facts(
        kind, subject, target, value, path, start_line, end_line, extra_info
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                f.kind, f.subject, f.target, f.value, str(f.path), f.start_line, f.end_line, f.extra_info,
            )
            for f in facts
        ),
    )


def _read_file_text_with_encoding(path: Path) -> tuple[str, str | None]:
    raw = path.read_bytes()
    return _decode_file_text(raw)


def _decode_file_text(raw: bytes) -> tuple[str, str | None]:
    for enc in ("utf-8-sig", "utf-16", "cp950", "cp1252"):
        try:
            text = raw.decode(enc).replace("\r\n", "\n").replace("\r", "\n")
            return text, None
        except UnicodeDecodeError:
            pass
    return "", "Encoding decode failure"


def _discover_vbnet_files(
    root: Path,
) -> tuple[tuple[Path, ...], tuple[Path, ...], dict[Path, str], dict[Path, str]]:
    indexed_files: list[Path] = []
    read_failures: list[Path] = []
    contents_by_path: dict[Path, str] = {}
    file_hashes_by_path: dict[Path, str] = {}

    vb_extensions = {".vb", ".vbproj", ".sln", ".resx", ".config", ".sql"}
    for current_dir, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRECTORY_NAMES]
        rel_dir = Path(current_dir).relative_to(root)
        for filename in filenames:
            file_path = rel_dir / filename
            ext = file_path.suffix.lower()
            if ext not in vb_extensions and filename.lower() not in {"app.config", "web.config"}:
                continue
            full_path = root / file_path
            try:
                raw = full_path.read_bytes()
                content, err = _decode_file_text(raw)
            except OSError:
                read_failures.append(file_path)
                continue
            if err:
                read_failures.append(file_path)
            else:
                indexed_files.append(file_path)
                contents_by_path[file_path] = content
                file_hashes_by_path[file_path] = _content_hash(raw)

    return (
        tuple(sorted(indexed_files, key=str)),
        tuple(sorted(read_failures, key=str)),
        contents_by_path,
        file_hashes_by_path,
    )


def _analyze_vbnet_files(
    root: Path,
    indexed_files: tuple[Path, ...],
    contents_by_path: dict[Path, str],
) -> tuple[tuple[VBNETDeclaration, ...], tuple[VBNETInvocation, ...], tuple[VBNETFact, ...], tuple[ParseFailure, ...]]:
    declarations: list[VBNETDeclaration] = []
    invocations: list[VBNETInvocation] = []
    facts: list[VBNETFact] = []
    parse_failures: list[ParseFailure] = []

    for path in indexed_files:
        content = contents_by_path.get(path, "")
        ext = path.suffix.lower()
        if ext == ".vb":
            decls, invs, fcts, errs = _parse_vb_source_file(path, content)
            declarations.extend(decls)
            invocations.extend(invs)
            facts.extend(fcts)
            parse_failures.extend(errs)
        elif ext == ".vbproj":
            facts.extend(_parse_vbproj_content(path, content))
        elif ext == ".sln":
            facts.extend(_parse_sln_content(path, content))
        elif ext == ".resx":
            facts.extend(_parse_resx_content(path, content))
        elif ext == ".config" or path.name.lower() in {"app.config", "web.config"}:
            facts.extend(_parse_config_content(path, content))
        elif ext == ".sql":
            facts.extend(_parse_sql_content(path, content))

    return (
        tuple(declarations),
        tuple(invocations),
        tuple(facts),
        tuple(parse_failures),
    )


def _parse_vb_source_file(
    path: Path, content: str
) -> tuple[list[VBNETDeclaration], list[VBNETInvocation], list[VBNETFact], list[ParseFailure]]:
    declarations: list[VBNETDeclaration] = []
    invocations: list[VBNETInvocation] = []
    facts: list[VBNETFact] = []
    parse_failures: list[ParseFailure] = []

    lines = content.splitlines()
    is_test_file = "test" in path.name.lower() or "tests" in path.name.lower()

    processed_lines: list[tuple[int, str]] = []
    idx = 0
    while idx < len(lines):
        orig_line_no = idx + 1
        current_line = lines[idx]
        while current_line.rstrip().endswith(" _") and idx + 1 < len(lines):
            idx += 1
            current_line = current_line.rstrip()[:-2] + " " + lines[idx].lstrip()
        processed_lines.append((orig_line_no, current_line))
        idx += 1

    scope_stack: list[str] = []
    current_method_name: str | None = None
    current_method_is_test: bool = is_test_file
    in_designer_region: bool = False
    in_option_strict_off: bool = False
    pending_attributes: list[str] = []

    for orig_line_no, raw_line in processed_lines:
        line = raw_line.strip()
        if not line or line.startswith("'") or line.lower().startswith("rem "):
            continue

        if re.match(r"(?i)^option\s+strict\s+off\b", line):
            in_option_strict_off = True
            continue

        if re.match(r"(?i)^#region\s+[\"'].*designer.*[\"']", line):
            in_designer_region = True
            facts.append(VBNETFact(kind="designer_provenance", subject="designer_region_start", target=None, value=line, path=path, start_line=orig_line_no, end_line=orig_line_no))
            continue
        elif re.match(r"(?i)^#end\s+region\b", line):
            in_designer_region = False
            facts.append(VBNETFact(kind="designer_provenance", subject="designer_region_end", target=None, value=line, path=path, start_line=orig_line_no, end_line=orig_line_no))
            continue

        if line.startswith("#"):
            continue

        attr_match = re.findall(r"<([A-Za-z0-9_]+)(?:\(.*?\))?>", line)
        if attr_match:
            pending_attributes.extend(attr_match)

        type_match = re.match(r"(?i)^(?:public|private|protected|friend|mustinherit|notinheritable|partial|\s)*\s*(class|module|structure|interface)\s+([a-z0-9_\[\]]+)", line)
        if type_match:
            kind = type_match.group(1).lower()
            name = type_match.group(2).strip("[]")
            scope_stack.append(name)
            qual_name = ".".join(scope_stack)
            decl = VBNETDeclaration(
                kind=kind,
                name=name,
                qualified_name=qual_name,
                signature=qual_name,
                path=path,
                start_line=orig_line_no,
                end_line=orig_line_no,
                is_test=is_test_file or any("test" in a.lower() for a in pending_attributes),
                is_private="private" in line.lower(),
            )
            declarations.append(decl)
            pending_attributes.clear()
            continue

        if re.match(r"(?i)^end\s+(class|module|structure|interface)\b", line):
            if scope_stack:
                scope_stack.pop()
            continue

        withevents_match = re.match(r"(?i)^(?:private|public|protected|friend|\s)*\s*withevents\s+([a-z0-9_\[\]]+)\s+as\s+([a-z0-9_\[\]\.]+)", line)
        if withevents_match:
            field_name = withevents_match.group(1).strip("[]")
            field_type = withevents_match.group(2).strip("[]")
            owner_class = scope_stack[-1] if scope_stack else "Global"
            facts.append(VBNETFact(
                kind="withevents_field",
                subject=f"{owner_class}.{field_name}",
                target=field_type,
                value=field_type,
                path=path,
                start_line=orig_line_no,
                end_line=orig_line_no,
            ))
            continue

        method_match = re.match(r"(?i)^(?:public|private|protected|friend|shared|mustoverride|overridable|overrides|shadows|withevents|optional|\s)*\s*(sub|function|property)\s+([a-z0-9_\[\]]+)(?:\s*\((.*?)\))?(?:\s+as\s+([a-z0-9_\[\]\.]+))?(?:\s+handles\s+(.*?))?$", line)
        if method_match:
            m_kind = method_match.group(1).lower()
            m_name = method_match.group(2).strip("[]")
            handles_clause = method_match.group(5)
            owner_class = scope_stack[-1] if scope_stack else "Global"
            sig = f"{owner_class}#{m_name}"
            current_method_name = sig
            current_method_is_test = is_test_file or any("test" in a.lower() for a in pending_attributes)
            var_type_map = {}

            decl = VBNETDeclaration(
                kind=m_kind,
                name=m_name,
                qualified_name=f"{owner_class}.{m_name}",
                signature=sig,
                path=path,
                start_line=orig_line_no,
                end_line=orig_line_no,
                is_test=current_method_is_test,
                is_private="private" in line.lower(),
            )
            declarations.append(decl)
            pending_attributes.clear()

            if in_designer_region or m_name.lower() == "initializecomponent":
                facts.append(VBNETFact(
                    kind="designer_provenance",
                    subject=sig,
                    target=None,
                    value="InitializeComponent" if m_name.lower() == "initializecomponent" else "designer_region",
                    path=path,
                    start_line=orig_line_no,
                    end_line=orig_line_no,
                ))

            if handles_clause:
                for h_item in handles_clause.split(","):
                    h_item = h_item.strip()
                    if h_item:
                        facts.append(VBNETFact(
                            kind="event_wiring",
                            subject=f"{owner_class}.{h_item}",
                            target=sig,
                            value=h_item,
                            path=path,
                            start_line=orig_line_no,
                            end_line=orig_line_no,
                        ))
            continue

        if re.match(r"(?i)^end\s+(sub|function|property)\b", line):
            current_method_name = None
            var_type_map = {}
            continue

        if current_method_name:
            owner_class = scope_stack[-1] if scope_stack else "Global"

            addhandler_match = re.match(r"(?i)^addhandler\s+([a-z0-9_\.\[\]]+)\s*,\s*addressof\s+([a-z0-9_\[\]]+)", line)
            if addhandler_match:
                evt_expr = addhandler_match.group(1).strip("[]")
                handler = addhandler_match.group(2).strip("[]")
                facts.append(VBNETFact(
                    kind="event_wiring",
                    subject=f"{owner_class}.{evt_expr}",
                    target=f"{owner_class}#{handler}",
                    value="AddHandler",
                    path=path,
                    start_line=orig_line_no,
                    end_line=orig_line_no,
                ))
                continue

            remhandler_match = re.match(r"(?i)^removehandler\s+([a-z0-9_\.\[\]]+)\s*,\s*addressof\s+([a-z0-9_\[\]]+)", line)
            if remhandler_match:
                evt_expr = remhandler_match.group(1).strip("[]")
                handler = remhandler_match.group(2).strip("[]")
                facts.append(VBNETFact(
                    kind="event_wiring",
                    subject=f"{owner_class}.{evt_expr}",
                    target=f"{owner_class}#{handler}",
                    value="RemoveHandler",
                    path=path,
                    start_line=orig_line_no,
                    end_line=orig_line_no,
                ))
                continue

            proc_match = re.search(r"(?i)(?:Process\.Start|Shell)\s*\(\s*[\"']([^\"']+)[\"']", line)
            if proc_match:
                exe_target = proc_match.group(1)
                facts.append(VBNETFact(
                    kind="process_launch",
                    subject=current_method_name,
                    target=exe_target,
                    value="Process.Start",
                    path=path,
                    start_line=orig_line_no,
                    end_line=orig_line_no,
                ))

            cfg_match = re.search(r"(?i)ConfigurationSettings\.AppSettings(?:\([\"']([^\"']+)[\"']\)|\[[\"']([^\"']+)[\"']\])", line)
            if cfg_match:
                key = cfg_match.group(1) or cfg_match.group(2)
                facts.append(VBNETFact(
                    kind="config_fact",
                    subject=current_method_name,
                    target=key,
                    value="ConfigurationSettings.AppSettings",
                    path=path,
                    start_line=orig_line_no,
                    end_line=orig_line_no,
                ))

            cmd_text_match = re.search(r"(?i)\.CommandText\s*=\s*[\"']([^\"']+)[\"']", line)
            if cmd_text_match:
                cmd_text = cmd_text_match.group(1)
                facts.append(VBNETFact(
                    kind="adonet_fact",
                    subject=current_method_name,
                    target=cmd_text,
                    value="CommandText",
                    path=path,
                    start_line=orig_line_no,
                    end_line=orig_line_no,
                ))
            if re.search(r"(?i)\.CommandType\s*=\s*(?:CommandType\.)?StoredProcedure", line):
                facts.append(VBNETFact(
                    kind="adonet_fact",
                    subject=current_method_name,
                    target="StoredProcedure",
                    value="CommandType",
                    path=path,
                    start_line=orig_line_no,
                    end_line=orig_line_no,
                ))

            co_match = re.search(r"(?i)CreateObject\s*\(\s*[\"']([^\"']+)[\"']\)", line)
            if co_match:
                prog_id = co_match.group(1)
                facts.append(VBNETFact(
                    kind="com_fact",
                    subject=current_method_name,
                    target=prog_id,
                    value="CreateObject",
                    path=path,
                    start_line=orig_line_no,
                    end_line=orig_line_no,
                ))

            dim_match = re.match(r"(?i)^\s*dim\s+([a-z0-9_\[\]]+)\s+as\s+(?:new\s+)?([a-z0-9_\[\]\.]+)", line)
            if dim_match:
                v_name = dim_match.group(1).strip("[]")
                v_type = dim_match.group(2).strip("[]")
                var_type_map[v_name.lower()] = v_type

            inv_matches = re.finditer(r"(?:(?:\b([A-Za-z0-9_\[\]]+)\s*\.)|(?:\bCall\s+))?\b([A-Za-z0-9_\[\]]+)\s*\(", line)
            for inv_m in inv_matches:
                recv_var = inv_m.group(1)
                method_name = inv_m.group(2)
                if method_name.lower() in {"if", "while", "for", "select", "sub", "function", "synclock", "using", "typeof", "ctype", "directcast", "trycast", "createobject", "getobject", "process.start", "shell"}:
                    continue
                resolved_recv = None
                if recv_var:
                    recv_var_clean = recv_var.strip("[]").lower()
                    if recv_var_clean in {"me", "mybase", "myclass"}:
                        resolved_recv = owner_class
                    elif recv_var_clean in var_type_map:
                        resolved_recv = var_type_map[recv_var_clean]
                    elif recv_var_clean == "object" or in_option_strict_off:
                        resolved_recv = "Object"
                    else:
                        resolved_recv = recv_var.strip("[]")
                else:
                    resolved_recv = owner_class

                invocations.append(VBNETInvocation(
                    name=method_name.strip("[]"),
                    receiver=resolved_recv,
                    caller=current_method_name,
                    path=path,
                    start_line=orig_line_no,
                    end_line=orig_line_no,
                    is_test=current_method_is_test,
                ))

    return declarations, invocations, facts, parse_failures


def _parse_vbproj_content(path: Path, content: str) -> list[VBNETFact]:
    facts: list[VBNETFact] = []
    asm_match = re.search(r"(?i)AssemblyName\s*=\s*[\"']([^\"']+)[\"']|<AssemblyName>([^<]+)</AssemblyName>", content)
    asm_name = (asm_match.group(1) or asm_match.group(2)) if asm_match else path.stem

    root_ns_match = re.search(r"(?i)RootNamespace\s*=\s*[\"']([^\"']+)[\"']|<RootNamespace>([^<]+)</RootNamespace>", content)
    root_ns = (root_ns_match.group(1) or root_ns_match.group(2)) if root_ns_match else asm_name

    startup_match = re.search(r"(?i)StartupObject\s*=\s*[\"']([^\"']+)[\"']|<StartupObject>([^<]+)</StartupObject>", content)
    startup_obj = (startup_match.group(1) or startup_match.group(2)) if startup_match else None

    facts.append(VBNETFact(
        kind="project_fact",
        subject=path.as_posix(),
        target=asm_name,
        value=root_ns,
        path=path,
        start_line=1,
        end_line=1,
        extra_info=startup_obj,
    ))

    ref_matches = re.finditer(r"(?i)<Reference\s+Name\s*=\s*[\"']([^\"']+)[\"'](?:\s+GUID\s*=\s*[\"']([^\"']+)[\"'])?", content)
    for ref_m in ref_matches:
        ref_name = ref_m.group(1)
        ref_guid = ref_m.group(2)
        if ref_guid or "interop" in ref_name.lower():
            facts.append(VBNETFact(
                kind="com_reference",
                subject=ref_name,
                target=ref_guid,
                value="COM Reference",
                path=path,
                start_line=1,
                end_line=1,
            ))
        else:
            facts.append(VBNETFact(
                kind="project_reference",
                subject=path.as_posix(),
                target=ref_name,
                value="Reference",
                path=path,
                start_line=1,
                end_line=1,
            ))
    return facts


def _parse_sln_content(path: Path, content: str) -> list[VBNETFact]:
    facts: list[VBNETFact] = []
    proj_matches = re.finditer(r'Project\("\{[A-F0-9-]+\}"\)\s*=\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"(\{[A-F0-9-]+\})"', content, re.IGNORECASE)
    for p_m in proj_matches:
        p_name = p_m.group(1)
        p_path = p_m.group(2)
        facts.append(VBNETFact(
            kind="solution_project",
            subject=p_name,
            target=p_path,
            value=path.as_posix(),
            path=path,
            start_line=1,
            end_line=1,
        ))
    return facts


def _parse_resx_content(path: Path, content: str) -> list[VBNETFact]:
    facts: list[VBNETFact] = []
    try:
        root_elem = ElementTree.fromstring(content)
        for elem in root_elem.findall("data"):
            name = elem.get("name")
            if name:
                facts.append(VBNETFact(
                    kind="designer_provenance",
                    subject=name,
                    target=None,
                    value="resx_data",
                    path=path,
                    start_line=1,
                    end_line=1,
                ))
    except Exception:
        pass
    return facts


def _parse_config_content(path: Path, content: str) -> list[VBNETFact]:
    facts: list[VBNETFact] = []
    try:
        root_elem = ElementTree.fromstring(content)
        app_settings = root_elem.find("appSettings")
        if app_settings is not None:
            for add_elem in app_settings.findall("add"):
                key = add_elem.get("key")
                val = add_elem.get("value")
                if key:
                    facts.append(VBNETFact(
                        kind="config_definition",
                        subject=key,
                        target=val,
                        value="appSettings",
                        path=path,
                        start_line=1,
                        end_line=1,
                    ))
    except Exception:
        pass
    return facts


def _parse_sql_content(path: Path, content: str) -> list[VBNETFact]:
    facts: list[VBNETFact] = []
    proc_matches = re.finditer(r"(?i)CREATE\s+(?:PROCEDURE|PROC)\s+([A-Za-z0-9_\.\[\]]+)", content)
    for p_m in proc_matches:
        proc_name = p_m.group(1).strip("[]")
        facts.append(VBNETFact(
            kind="sql_definition",
            subject=proc_name,
            target=proc_name,
            value="StoredProcedure",
            path=path,
            start_line=1,
            end_line=1,
        ))
    return facts


def _analyze_vbnet_impact(request: ImpactRequest, connection: sqlite3.Connection, snapshot: IndexSnapshot) -> ImpactResult:
    target_str = request.target or ""
    rows = connection.execute(
        "SELECT kind, name, qualified_name, signature, path, start_line, end_line, is_test, is_private FROM vbnet_declarations"
    ).fetchall()

    matching_decls = []
    for r in rows:
        kind, name, qual_name, sig, path_str, s_line, e_line, is_test, is_priv = r
        if sig.lower() == target_str.lower() or name.lower() == target_str.lower():
            matching_decls.append(r)

    if not matching_decls:
        for r in rows:
            kind, name, qual_name, sig, path_str, s_line, e_line, is_test, is_priv = r
            if qual_name.lower() == target_str.lower():
                matching_decls.append(r)

    if not matching_decls:
        return ImpactResult(
            outcome="not_found",
            requested_target=target_str,
            target=None,
            candidates=(),
            relationships=(),
            assumptions=("Target signature not found in VB.NET index.",),
            unresolved_items=(),
            snapshot=snapshot,
            manual_verification_surfaces=(),
        )

    if len(matching_decls) > 1:
        candidates = tuple(
            ImpactTarget(
                signature=r[3],
                path=Path(r[4]),
                start_line=r[5],
                end_line=r[6],
                evidence_handle=f"vbnet_declaration:{r[4]}:{r[5]}-{r[6]}",
                language="vbnet",
            )
            for r in matching_decls
        )
        return ImpactResult(
            outcome="ambiguous",
            requested_target=target_str,
            target=None,
            candidates=candidates,
            relationships=(),
            assumptions=("Multiple matching VB.NET declarations found for target.",),
            unresolved_items=(),
            snapshot=snapshot,
            manual_verification_surfaces=(),
        )

    r = matching_decls[0]
    kind, name, qual_name, sig, path_str, s_line, e_line, is_test, is_priv = r
    resolved_target = ImpactTarget(
        signature=sig,
        path=Path(path_str),
        start_line=s_line,
        end_line=e_line,
        evidence_handle=f"vbnet_declaration:{path_str}:{s_line}-{e_line}",
        language="vbnet",
    )

    owner_class = sig.split("#")[0] if "#" in sig else qual_name
    member_name = sig.split("#")[1] if "#" in sig else name

    relationships: list[ImpactRelationship] = []
    unresolved_items: list[UnresolvedItem] = []
    manual_surfaces: list[ManualVerificationSurface] = []

    inv_rows = connection.execute(
        "SELECT name, receiver, caller, path, start_line, end_line, is_test FROM vbnet_invocations WHERE LOWER(name) = ?",
        (member_name.lower(),),
    ).fetchall()

    for inv in inv_rows:
        inv_name, inv_recv, inv_caller, inv_path_str, inv_s, inv_e, inv_test = inv
        if inv_caller and inv_caller.lower() != sig.lower():
            if inv_recv is None or inv_recv.lower() in {owner_class.lower(), "me", "mybase"}:
                handle = f"vbnet_invocation:{inv_path_str}:{inv_s}-{inv_e}"
                relationships.append(ImpactRelationship(
                    kind="VB.NET Direct Call",
                    caller=inv_caller,
                    path=Path(inv_path_str),
                    start_line=inv_s,
                    end_line=inv_e,
                    evidence_handle=handle,
                    confidence="high",
                    evidence_chain=(resolved_target.evidence_handle, handle),
                    language="vbnet",
                ))

    callee_rows = connection.execute(
        "SELECT name, receiver, caller, path, start_line, end_line FROM vbnet_invocations WHERE LOWER(caller) = ?",
        (sig.lower(),),
    ).fetchall()
    for callee in callee_rows:
        c_name, c_recv, c_caller, c_path_str, c_s, c_e = callee
        if c_recv == "Object":
            handle = f"vbnet_invocation:{c_path_str}:{c_s}-{c_e}"
            unresolved_items.append(UnresolvedItem(
                message=f"VB.NET Late-Bound Call: invocation '{c_name}' on Object or untyped receiver inside {sig}",
                path=Path(c_path_str),
                start_line=c_s,
                end_line=c_e,
                evidence_handle=handle,
            ))
        elif c_recv and c_recv.lower() not in {"me", "mybase"}:
            handle = f"vbnet_invocation:{c_path_str}:{c_s}-{c_e}"
            relationships.append(ImpactRelationship(
                kind="VB.NET Direct Call",
                caller=f"{sig} -> {c_recv}#{c_name}",
                path=Path(c_path_str),
                start_line=c_s,
                end_line=c_e,
                evidence_handle=handle,
                confidence="high",
                evidence_chain=(resolved_target.evidence_handle, handle),
                language="vbnet",
            ))

    fact_rows = connection.execute(
        "SELECT kind, subject, target, value, path, start_line, end_line, extra_info FROM vbnet_facts"
    ).fetchall()

    for f in fact_rows:
        f_kind, f_sub, f_tgt, f_val, f_path_str, f_s, f_e, f_extra = f
        handle = f"vbnet_fact:{f_kind}:{f_path_str}:{f_s}-{f_e}"
        if f_kind == "event_wiring":
            if f_tgt and f_tgt.lower() == sig.lower():
                relationships.append(ImpactRelationship(
                    kind="WinForms Event Wiring",
                    caller=f_sub,
                    path=Path(f_path_str),
                    start_line=f_s,
                    end_line=f_e,
                    evidence_handle=handle,
                    confidence="high",
                    evidence_chain=(resolved_target.evidence_handle, handle),
                    language="vbnet",
                ))
                manual_surfaces.append(ManualVerificationSurface(
                    kind="winforms_event",
                    description=f"WinForms control event '{f_sub}' reaches handler '{sig}'.",
                    path=Path(f_path_str),
                    start_line=f_s,
                    end_line=f_e,
                    evidence_handle=handle,
                ))

        elif f_kind == "process_launch":
            if f_sub and f_sub.lower() == sig.lower():
                target_exe = f_tgt
                proj_matches = [
                    pf for pf in fact_rows if pf[0] == "project_fact" and pf[2].lower() == target_exe.replace(".exe", "").lower()
                ]
                if len(proj_matches) == 1:
                    match_proj = proj_matches[0]
                    proj_handle = f"vbnet_fact:project_fact:{match_proj[4]}:{match_proj[5]}-{match_proj[6]}"
                    relationships.append(ImpactRelationship(
                        kind="Local Process Boundary",
                        caller=f"{sig} -> {target_exe}",
                        path=Path(f_path_str),
                        start_line=f_s,
                        end_line=f_e,
                        evidence_handle=handle,
                        confidence="high",
                        evidence_chain=(resolved_target.evidence_handle, handle, proj_handle),
                        language="vbnet",
                    ))
                else:
                    unresolved_items.append(UnresolvedItem(
                        message=f"External Process Boundary: process launch '{target_exe}' cannot be uniquely resolved to a local project.",
                        path=Path(f_path_str),
                        start_line=f_s,
                        end_line=f_e,
                        evidence_handle=handle,
                    ))

        elif f_kind == "config_fact":
            if f_sub and f_sub.lower() == sig.lower():
                cfg_key = f_tgt
                cfg_defs = [
                    cf for cf in fact_rows if cf[0] == "config_definition" and cf[1] == cfg_key
                ]
                if cfg_defs:
                    match_cfg = cfg_defs[0]
                    cfg_handle = f"vbnet_fact:config_definition:{match_cfg[4]}:{match_cfg[5]}-{match_cfg[6]}"
                    relationships.append(ImpactRelationship(
                        kind="AppSettings Configuration",
                        caller=f"{sig} -> AppSettings['{cfg_key}']",
                        path=Path(f_path_str),
                        start_line=f_s,
                        end_line=f_e,
                        evidence_handle=handle,
                        confidence="high",
                        evidence_chain=(resolved_target.evidence_handle, handle, cfg_handle),
                        language="vbnet",
                    ))

        elif f_kind == "adonet_fact":
            if f_sub and f_sub.lower() == sig.lower():
                relationships.append(ImpactRelationship(
                    kind="VB.NET Data Access Boundary",
                    caller=f"{sig} -> ADO.NET ({f_tgt})",
                    path=Path(f_path_str),
                    start_line=f_s,
                    end_line=f_e,
                    evidence_handle=handle,
                    confidence="medium",
                    evidence_chain=(resolved_target.evidence_handle, handle),
                    language="vbnet",
                ))
                if f_tgt:
                    sql_defs = [sf for sf in fact_rows if sf[0] == "sql_definition" and sf[1].lower() == f_tgt.lower()]
                    if sql_defs:
                        match_sql = sql_defs[0]
                        sql_handle = f"vbnet_fact:sql_definition:{match_sql[4]}:{match_sql[5]}-{match_sql[6]}"
                        relationships.append(ImpactRelationship(
                            kind="StoredProcedure Reference",
                            caller=f"ADO.NET Command -> {f_tgt}",
                            path=Path(match_sql[4]),
                            start_line=match_sql[5],
                            end_line=match_sql[6],
                            evidence_handle=sql_handle,
                            confidence="high",
                            evidence_chain=(resolved_target.evidence_handle, handle, sql_handle),
                            language="vbnet",
                        ))

        elif f_kind == "com_fact":
            if f_sub and f_sub.lower() == sig.lower():
                unresolved_items.append(UnresolvedItem(
                    message=f"COM Interop Boundary: dynamic COM call '{f_val}' with target '{f_tgt}' in {sig} remains unresolved.",
                    path=Path(f_path_str),
                    start_line=f_s,
                    end_line=f_e,
                    evidence_handle=handle,
                ))

    test_callers = [r for r in relationships if any(t_r[7] for t_r in rows if t_r[3].lower() == r.caller.lower())]
    if not test_callers:
        manual_surfaces.append(ManualVerificationSurface(
            kind="manual_verification_surface",
            description=f"Form or control logic '{sig}' requires manual verification surface testing.",
            path=resolved_target.path,
            start_line=resolved_target.start_line,
            end_line=resolved_target.end_line,
            evidence_handle=resolved_target.evidence_handle,
        ))

    return ImpactResult(
        outcome="resolved",
        requested_target=target_str,
        target=resolved_target,
        candidates=(),
        relationships=tuple(relationships),
        assumptions=("VB.NET WinForms local impact path assembled from inspectable source evidence.",),
        unresolved_items=tuple(unresolved_items),
        snapshot=snapshot,
        manual_verification_surfaces=tuple(manual_surfaces),
    )
