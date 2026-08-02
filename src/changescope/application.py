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


@dataclass(frozen=True)
class IndexRequest:
    repository_root: Path


@dataclass(frozen=True)
class ImpactRequest:
    repository_root: Path
    target: str


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
    match = re.fullmatch(r"(?:declaration|invocation):(.+):(\d+)-(\d+)", request.evidence_handle)
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
        database_path, candidates[0]
    )
    return ImpactResult(
        "resolved",
        request.target,
        candidates[0],
        (),
        relationships,
        (
            "Structural analysis asserts only explicit invocation syntax tied to the resolved target.",
        ),
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


def _unresolved(
    message: str, path: Path | None = None, start_line: int | None = None, end_line: int | None = None
) -> UnresolvedItem:
    evidence_handle = (
        _evidence_handle("invocation", path, start_line, end_line)
        if path is not None and start_line is not None and end_line is not None
        else None
    )
    return UnresolvedItem(message, path, start_line, end_line, evidence_handle)


def _direct_relationships(
    database_path: Path, target: ImpactTarget
) -> tuple[tuple[ImpactRelationship, ...], tuple[UnresolvedItem, ...]]:
    owner, method_name = target.signature.split("#", maxsplit=1)
    method_name = method_name.split("(", maxsplit=1)[0]
    target_class = owner.rsplit(".", maxsplit=1)[-1]
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            """SELECT receiver, caller, path, start_line, end_line, is_test
            FROM java_invocations WHERE name = ? ORDER BY path, start_line""",
            (method_name,),
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
    return tuple(relationships), tuple(unresolved_items)


def _is_direct_construction(receiver: str, owner: str) -> bool:
    return bool(re.fullmatch(rf"new\s+{re.escape(owner)}\s*\([^)]*\)", receiver))


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
    declarations, invocations, parse_failures = _analyze_java_files(
        contents_by_path, _test_source_roots(root, source_roots)
    )
    snapshot = _snapshot(root)
    result = IndexResult(
        source_roots=source_roots,
        indexed_files=indexed_files,
        excluded_directories=excluded_directories,
        read_failures=read_failures,
        declarations=declarations,
        invocations=invocations,
        parse_failures=parse_failures,
        snapshot=snapshot,
    )
    _write_index(result)
    return result


def _refresh_index_if_needed(root: Path) -> None:
    """Refresh changed Java paths, or all paths when test-root classification changes."""
    database_path = root / ".changescope" / "index.sqlite"
    source_roots = _discover_source_roots(root)
    indexed_files, read_failures, contents_by_path = _java_files(root, source_roots)
    current_files = {
        str(path): ("indexed", _content_hash(contents_by_path[path]))
        for path in indexed_files
    }
    current_files.update({str(path): ("unreadable", "") for path in read_failures})
    connection = sqlite3.connect(database_path)
    try:
        with connection:
            _initialize_index_schema(connection)
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
                _replace_changed_source_records(
                    connection, changed_paths, current_files, declarations, invocations, parse_failures
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
    _write_metadata(connection, result.snapshot, result.source_roots)
    connection.executemany(
        "INSERT INTO source_files(path, status, content_hash) VALUES (?, ?, ?)",
        (
            (str(path), "indexed", _file_content_hash(result.snapshot.repository_root / path))
            for path in result.indexed_files
        ),
    )
    connection.executemany(
        "INSERT INTO source_files(path, status, content_hash) VALUES (?, ?, ?)",
        ((str(path), "unreadable", "") for path in result.read_failures),
    )
    _insert_java_facts(connection, result.declarations, result.invocations, result.parse_failures)


def _initialize_index_schema(connection: sqlite3.Connection) -> None:
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
        is_test INTEGER NOT NULL
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
    declaration_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(java_declarations)")
    }
    if "signature" not in declaration_columns:
        connection.execute(
            "ALTER TABLE java_declarations ADD COLUMN signature TEXT NOT NULL DEFAULT ''"
        )
    source_file_columns = {row[1] for row in connection.execute("PRAGMA table_info(source_files)")}
    if "content_hash" not in source_file_columns:
        connection.execute(
            "ALTER TABLE source_files ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''"
        )


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
) -> None:
    for path in changed_paths:
        connection.execute("DELETE FROM source_files WHERE path = ?", (path,))
        connection.execute("DELETE FROM java_declarations WHERE path = ?", (path,))
        connection.execute("DELETE FROM java_invocations WHERE path = ?", (path,))
        connection.execute("DELETE FROM parse_failures WHERE path = ?", (path,))
    connection.executemany(
        "INSERT INTO source_files(path, status, content_hash) VALUES (?, ?, ?)",
        ((path, status, content_hash) for path, (status, content_hash) in current_files.items() if path in changed_paths),
    )
    _insert_java_facts(connection, declarations, invocations, parse_failures)


def _insert_java_facts(
    connection: sqlite3.Connection,
    declarations: Iterable[JavaDeclaration],
    invocations: Iterable[JavaInvocation],
    parse_failures: Iterable[ParseFailure],
) -> None:
    connection.executemany(
        """INSERT INTO java_declarations(
        kind, name, qualified_name, signature, path, start_line, end_line, is_test
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
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


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _file_content_hash(path: Path) -> str:
    try:
        return _content_hash(path.read_bytes())
    except OSError:
        return ""
