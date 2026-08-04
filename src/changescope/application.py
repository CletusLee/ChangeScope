from __future__ import annotations

from dataclasses import dataclass
import hashlib
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


@dataclass(frozen=True)
class JavaInvocation:
    name: str
    receiver: str | None
    caller: str | None
    path: Path
    start_line: int
    end_line: int
    is_test: bool


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
class ParseFailure:
    path: Path
    start_line: int
    start_column: int
    message: str


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
    configuration_files: tuple[Path, ...] = ()


@dataclass(frozen=True)
class IndexRequest:
    repository_root: Path


@dataclass(frozen=True)
class ImpactRequest:
    repository_root: Path
    target: str
    profiles: tuple[str, ...] = ()


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
class ImpactResult:
    outcome: str
    requested_target: str
    target: ImpactTarget | None
    candidates: tuple[ImpactTarget, ...]
    relationships: tuple[ImpactRelationship, ...]
    assumptions: tuple[str, ...]
    unresolved_items: tuple[UnresolvedItem, ...]
    snapshot: IndexSnapshot | None


class ChangeScopeApplication:
    """The application-service seam shared by CLI, tests, and future adapters."""

    def execute(self, request: IndexRequest | ImpactRequest | EvidenceRequest | SourceRequest) -> IndexResult | ImpactResult | SourceNavigation:
        if isinstance(request, IndexRequest):
            return _index_repository(request.repository_root)
        if isinstance(request, EvidenceRequest):
            return _evidence_context(request)
        if isinstance(request, SourceRequest):
            return _source_range(request)
        return _impact_repository(request)


def _evidence_context(request: EvidenceRequest) -> SourceNavigation:
    match = re.fullmatch(r"(?:declaration|invocation|spring):(.+):(\d+)-(\d+)", request.evidence_handle)
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
    lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
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
            "SELECT 1 FROM source_files WHERE path = ? AND status = 'indexed'", (str(path),)
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError("Source navigation is limited to files in the local Repository Index.")
    return source_path


def _impact_repository(request: ImpactRequest) -> ImpactResult:
    root = request.repository_root.resolve()
    database_path = root / ".changescope" / "index.sqlite"
    if not database_path.is_file():
        return ImpactResult(
            "index_missing", request.target, None, (), (), (),
            (_unresolved("No local Repository Index exists. Run `changescope index` first."),), None,
        )
    _refresh_index_if_needed(root)
    target_parts = request.target.split("#")
    if len(target_parts) != 2 or not all(target_parts):
        return ImpactResult(
            "invalid_target", request.target, None, (), (), (),
            (_unresolved("Use the target form Class#method."),), _read_index_snapshot(database_path, root),
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
        snapshot = _read_index_snapshot(connection, root)
    finally:
        connection.close()
    if not candidates:
        return ImpactResult("not_found", request.target, None, (), (), (), (), snapshot)
    if len(candidates) > 1:
        return ImpactResult("ambiguous", request.target, None, candidates, (), (), (), snapshot)
    relationships, unresolved_items = _direct_relationships(
        database_path, candidates[0], request.profiles
    )
    assumptions = [
        "Structural analysis asserts only explicit invocation syntax tied to the resolved target.",
    ]
    if request.profiles:
        assumptions.append(
            "Active Spring profiles: " + ", ".join(request.profiles) + "."
        )
    else:
        assumptions.append(
            "No Spring profile was selected; profile-specific configuration remains conditional."
        )
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
    database_path: Path, target: ImpactTarget, profiles: tuple[str, ...] = ()
) -> tuple[tuple[ImpactRelationship, ...], tuple[UnresolvedItem, ...]]:
    owner, method_name = target.signature.split("#", maxsplit=1)
    method_name = method_name.split("(", maxsplit=1)[0]
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            """SELECT receiver, caller, path, start_line, end_line, is_test
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
    finally:
        connection.close()
    relationships: list[ImpactRelationship] = []
    unresolved_items = [
        _unresolved(
            "Structural analysis does not resolve receiver types, overload dispatch, inheritance, reflection, dependency injection, or framework dispatch."
        )
    ]
    for receiver, caller, path, start_line, end_line, is_test in rows:
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
    return tuple(relationships), tuple(unresolved_items)


def _is_direct_construction(receiver: str, owner: str) -> bool:
    return bool(re.fullmatch(rf"new\s+{re.escape(owner)}\s*\([^)]*\)", receiver))


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
    spring_facts = _analyze_spring_files(
        {**contents_by_path, **configuration_contents}, declarations
    )
    snapshot = _snapshot(root)
    result = IndexResult(
        source_roots=source_roots,
        indexed_files=indexed_files,
        excluded_directories=excluded_directories,
        read_failures=tuple(sorted(set(read_failures) | set(configuration_read_failures), key=str)),
        declarations=declarations,
        invocations=invocations,
        parse_failures=parse_failures,
        snapshot=snapshot,
        spring_facts=spring_facts,
        configuration_files=configuration_files,
    )
    _write_index(result)
    return result


def _refresh_index_if_needed(root: Path) -> None:
    """Refresh changed Java paths, or all paths when test-root classification changes."""
    database_path = root / ".changescope" / "index.sqlite"
    source_roots = _discover_source_roots(root)
    indexed_files, read_failures, contents_by_path = _java_files(root, source_roots)
    configuration_files, configuration_read_failures, configuration_contents = _configuration_files(
        root, source_roots
    )
    current_files = {
        str(path): ("indexed", _content_hash(contents_by_path[path]))
        for path in indexed_files
    }
    current_files.update({
        str(path): ("indexed", _content_hash(configuration_contents[path]))
        for path in configuration_files
    })
    current_files.update({
        str(path): ("unreadable", "")
        for path in (*read_failures, *configuration_read_failures)
    })
    connection = sqlite3.connect(database_path)
    try:
        with connection:
            declaration_schema_changed = _initialize_index_schema(connection)
            previous_files = {
                path: (status, content_hash)
                for path, status, content_hash in connection.execute(
                    "SELECT path, status, content_hash FROM source_files"
                )
            }
            previous_test_roots = connection.execute(
                "SELECT value FROM metadata WHERE key = 'test_source_roots'"
            ).fetchone()
            current_test_roots = _test_source_roots(root, source_roots)
            changed_paths = {
                path for path in set(previous_files) | set(current_files)
                if previous_files.get(path) != current_files.get(path)
            }
            if declaration_schema_changed:
                changed_paths.update(current_files)
            if previous_test_roots is None or previous_test_roots[0] != _root_list_value(current_test_roots):
                changed_paths.update(current_files)
            if changed_paths:
                changed_contents = {
                    path: source for path, source in contents_by_path.items()
                    if str(path) in changed_paths
                }
                declarations, invocations, parse_failures = _analyze_java_files(
                    changed_contents, current_test_roots
                )
                changed_configuration_contents = {
                    path: source for path, source in configuration_contents.items()
                    if str(path) in changed_paths
                }
                spring_facts = _analyze_spring_files(
                    {**changed_contents, **changed_configuration_contents}, declarations
                )
                _replace_changed_source_records(
                    connection,
                    changed_paths,
                    current_files,
                    declarations,
                    invocations,
                    parse_failures,
                    spring_facts,
                )
            _write_metadata(connection, _snapshot(root), source_roots)
    finally:
        connection.close()


def _discover_source_roots(root: Path) -> tuple[Path, ...]:
    declared_build_roots = _declared_build_source_roots(root)
    if declared_build_roots:
        return declared_build_roots

    conventional_roots = tuple(
        candidate
        for candidate in (Path("src/main/java"), Path("src/test/java"))
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
                if not filename.endswith(".java"):
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
                if path.suffix.lower() not in {".properties", ".yml", ".yaml", ".xml"}:
                    continue
                relative_path = path.relative_to(root)
                try:
                    indexed[relative_path] = path.read_bytes()
                except OSError:
                    read_failures.add(relative_path)

    for path in root.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".properties", ".yml", ".yaml", ".xml"}:
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
            invocations.append(
                JavaInvocation(
                    name,
                    receiver,
                    current_caller,
                    path,
                    current.start_point.row + 1,
                    current.end_point.row + 1,
                    is_test,
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
    _write_metadata(connection, result.snapshot, result.source_roots)
    indexed_paths = tuple(dict.fromkeys((*result.indexed_files, *result.configuration_files)))
    connection.executemany(
        "INSERT INTO source_files(path, status, content_hash) VALUES (?, ?, ?)",
        (
            (str(path), "indexed", _file_content_hash(result.snapshot.repository_root / path))
            for path in indexed_paths
        ),
    )
    connection.executemany(
        "INSERT INTO source_files(path, status, content_hash) VALUES (?, ?, ?)",
        ((str(path), "unreadable", "") for path in result.read_failures),
    )
    _insert_java_facts(connection, result.declarations, result.invocations, result.parse_failures)
    _insert_spring_facts(connection, result.spring_facts)


def _initialize_index_schema(connection: sqlite3.Connection) -> bool:
    spring_schema_missing = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'spring_facts'"
    ).fetchone() is None
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
        is_test INTEGER NOT NULL
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
    declaration_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(java_declarations)")
    }
    if "signature" not in declaration_columns:
        connection.execute(
            "ALTER TABLE java_declarations ADD COLUMN signature TEXT NOT NULL DEFAULT ''"
        )
    declaration_schema_changed = "is_private" not in declaration_columns
    if declaration_schema_changed:
        connection.execute(
            "ALTER TABLE java_declarations ADD COLUMN is_private INTEGER NOT NULL DEFAULT 0"
        )
    source_file_columns = {row[1] for row in connection.execute("PRAGMA table_info(source_files)")}
    if "content_hash" not in source_file_columns:
        connection.execute(
            "ALTER TABLE source_files ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''"
        )
    return declaration_schema_changed or spring_schema_missing


def _write_metadata(
    connection: sqlite3.Connection, snapshot: IndexSnapshot, source_roots: tuple[Path, ...]
) -> None:
    connection.execute("DELETE FROM metadata")
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        (
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
) -> None:
    for path in changed_paths:
        connection.execute("DELETE FROM source_files WHERE path = ?", (path,))
        connection.execute("DELETE FROM java_declarations WHERE path = ?", (path,))
        connection.execute("DELETE FROM java_invocations WHERE path = ?", (path,))
        connection.execute("DELETE FROM parse_failures WHERE path = ?", (path,))
        connection.execute("DELETE FROM spring_facts WHERE path = ?", (path,))
    connection.executemany(
        "INSERT INTO source_files(path, status, content_hash) VALUES (?, ?, ?)",
        ((path, status, content_hash) for path, (status, content_hash) in current_files.items() if path in changed_paths),
    )
    _insert_java_facts(connection, declarations, invocations, parse_failures)
    _insert_spring_facts(connection, spring_facts)


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
        name, receiver, caller, path, start_line, end_line, is_test
        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                invocation.name,
                invocation.receiver,
                invocation.caller,
                str(invocation.path),
                invocation.start_line,
                invocation.end_line,
                int(invocation.is_test),
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


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _file_content_hash(path: Path) -> str:
    try:
        return _content_hash(path.read_bytes())
    except OSError:
        return ""
