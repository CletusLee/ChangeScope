from __future__ import annotations

from dataclasses import dataclass
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


class ChangeScopeApplication:
    """The application-service seam shared by CLI, tests, and future adapters."""

    def execute(self, request: IndexRequest) -> IndexResult:
        return _index_repository(request.repository_root)


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
    parser = Parser(Language(tree_sitter_java.language()))
    declarations: list[JavaDeclaration] = []
    invocations: list[JavaInvocation] = []
    parse_failures: list[ParseFailure] = []
    for path, source in sorted(contents_by_path.items(), key=lambda item: str(item[0])):
        is_test = any(path.is_relative_to(root) for root in test_roots)
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
    if node.type in type_kinds:
        name = _node_text(node.child_by_field_name("name"), source)
        qualified_name = _qualified_type_name(package_name, (*enclosing_types, name))
        declarations.append(
            JavaDeclaration(
                type_kinds[node.type], name, qualified_name, qualified_name, path, node.start_point.row + 1,
                node.end_point.row + 1, is_test,
            )
        )
        for child in node.children:
            _collect_java_facts(
                child, source, path, package_name, is_test, (*enclosing_types, name), caller,
                declarations, invocations,
            )
        return
    if node.type in {"method_declaration", "constructor_declaration"}:
        name = _node_text(node.child_by_field_name("name"), source)
        kind = "constructor" if node.type == "constructor_declaration" else "method"
        owner = _qualified_type_name(package_name, enclosing_types)
        qualified_name = f"{owner}#{name}" if owner else name
        signature = f"{qualified_name}({_parameter_types(node, source)})"
        declarations.append(
            JavaDeclaration(
                kind, name, qualified_name, signature, path, node.start_point.row + 1,
                node.end_point.row + 1, is_test,
            )
        )
        for child in node.children:
            _collect_java_facts(
                child, source, path, package_name, is_test, enclosing_types, qualified_name,
                declarations, invocations,
            )
        return
    if node.type == "method_invocation":
        name = _node_text(node.child_by_field_name("name"), source)
        receiver_node = node.child_by_field_name("object")
        receiver = _node_text(receiver_node, source) if receiver_node is not None else None
        invocations.append(
            JavaInvocation(
                name, receiver, caller, path, node.start_point.row + 1, node.end_point.row + 1,
                is_test,
            )
        )
    for child in node.children:
        _collect_java_facts(
            child, source, path, package_name, is_test, enclosing_types, caller,
            declarations, invocations,
        )


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
    connection.execute(
        "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS source_files (path TEXT PRIMARY KEY, status TEXT NOT NULL)"
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
    connection.execute("DELETE FROM metadata")
    connection.execute("DELETE FROM source_files")
    connection.execute("DELETE FROM java_declarations")
    connection.execute("DELETE FROM java_invocations")
    connection.execute("DELETE FROM parse_failures")
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        (
            ("repository_root", str(result.snapshot.repository_root)),
            ("git_commit", result.snapshot.git_commit or ""),
            ("working_tree_state", result.snapshot.working_tree_state),
            ("source_roots", "\n".join(map(str, result.source_roots))),
        ),
    )
    connection.executemany(
        "INSERT INTO source_files(path, status) VALUES (?, ?)",
        ((str(path), "indexed") for path in result.indexed_files),
    )
    connection.executemany(
        "INSERT INTO source_files(path, status) VALUES (?, ?)",
        ((str(path), "unreadable") for path in result.read_failures),
    )
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
            for declaration in result.declarations
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
            for invocation in result.invocations
        ),
    )
    connection.executemany(
        "INSERT INTO parse_failures(path, start_line, start_column, message) VALUES (?, ?, ?, ?)",
        (
            (failure.path.as_posix(), failure.start_line, failure.start_column, failure.message)
            for failure in result.parse_failures
        ),
    )
