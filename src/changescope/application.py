from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import subprocess
from typing import Iterable
from xml.etree import ElementTree


EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".changescope",
        ".git",
        ".gradle",
        ".idea",
        ".mvn",
        ".settings",
        "build",
        "deps",
        "generated",
        "generated-sources",
        "generated-test-sources",
        "node_modules",
        "out",
        "target",
        "vendor",
    }
)


@dataclass(frozen=True)
class IndexSnapshot:
    repository_root: Path
    git_commit: str | None
    working_tree_state: str


@dataclass(frozen=True)
class IndexResult:
    source_roots: tuple[Path, ...]
    indexed_files: tuple[Path, ...]
    excluded_directories: tuple[Path, ...]
    read_failures: tuple[Path, ...]
    snapshot: IndexSnapshot


def index_repository(repository_root: Path) -> IndexResult:
    """Build a local repository index and describe exactly what it contains."""
    root = repository_root.resolve()
    source_roots = _discover_source_roots(root)
    excluded_directories = _excluded_directories(root)
    indexed_files, read_failures = _java_files(root, source_roots)
    snapshot = _snapshot(root)
    _write_index(root, source_roots, indexed_files, read_failures, snapshot)
    return IndexResult(
        source_roots=source_roots,
        indexed_files=indexed_files,
        excluded_directories=excluded_directories,
        read_failures=read_failures,
        snapshot=snapshot,
    )


def _discover_source_roots(root: Path) -> tuple[Path, ...]:
    conventional_roots = tuple(
        candidate
        for candidate in (Path("src/main/java"), Path("src/test/java"))
        if (root / candidate).is_dir()
    )
    if conventional_roots:
        return conventional_roots

    eclipse_roots = _eclipse_source_roots(root)
    if eclipse_roots:
        return eclipse_roots

    if (root / "src").is_dir():
        return (Path("src"),)
    return (Path("."),)


def _eclipse_source_roots(root: Path) -> tuple[Path, ...]:
    classpath = root / ".classpath"
    if not classpath.is_file():
        return ()
    try:
        entries = ElementTree.parse(classpath).getroot().findall("classpathentry")
    except (ElementTree.ParseError, OSError):
        return ()
    roots = []
    for entry in entries:
        if entry.get("kind") != "src":
            continue
        path = entry.get("path")
        if not path:
            continue
        candidate = Path(path)
        if candidate.is_absolute() or not (root / candidate).is_dir():
            continue
        roots.append(candidate)
    return tuple(dict.fromkeys(roots))


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
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    indexed_files: list[Path] = []
    read_failures: list[Path] = []
    for source_root in source_roots:
        for candidate in (root / source_root).rglob("*.java"):
            relative_path = candidate.relative_to(root)
            if _is_excluded(relative_path):
                continue
            try:
                candidate.read_bytes()
            except OSError:
                read_failures.append(relative_path)
                continue
            indexed_files.append(relative_path)
    return tuple(sorted(set(indexed_files), key=str)), tuple(sorted(read_failures, key=str))


def _is_excluded(relative_path: Path) -> bool:
    return any(part in EXCLUDED_DIRECTORY_NAMES for part in relative_path.parts[:-1])


def _snapshot(root: Path) -> IndexSnapshot:
    commit = _git_output(root, "rev-parse", "HEAD")
    status = _git_output(root, "status", "--porcelain")
    if commit is None or status is None:
        return IndexSnapshot(root, None, "unavailable")
    return IndexSnapshot(root, commit, "dirty" if status else "clean")


def _git_output(root: Path, *arguments: str) -> str | None:
    completed = subprocess.run(
        ("git", "-C", str(root), "-c", f"safe.directory={root}", *arguments),
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _write_index(
    root: Path,
    source_roots: tuple[Path, ...],
    indexed_files: tuple[Path, ...],
    read_failures: tuple[Path, ...],
    snapshot: IndexSnapshot,
) -> None:
    database_directory = root / ".changescope"
    database_directory.mkdir(exist_ok=True)
    connection = sqlite3.connect(database_directory / "index.sqlite")
    try:
        with connection:
            _replace_index_contents(
                connection, source_roots, indexed_files, read_failures, snapshot
            )
    finally:
        connection.close()


def _replace_index_contents(
    connection: sqlite3.Connection,
    source_roots: tuple[Path, ...],
    indexed_files: tuple[Path, ...],
    read_failures: tuple[Path, ...],
    snapshot: IndexSnapshot,
) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS source_files (path TEXT PRIMARY KEY, status TEXT NOT NULL)"
    )
    connection.execute("DELETE FROM metadata")
    connection.execute("DELETE FROM source_files")
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        (
            ("repository_root", str(snapshot.repository_root)),
            ("git_commit", snapshot.git_commit or ""),
            ("working_tree_state", snapshot.working_tree_state),
            ("source_roots", "\n".join(map(str, source_roots))),
        ),
    )
    connection.executemany(
        "INSERT INTO source_files(path, status) VALUES (?, ?)",
        ((str(path), "indexed") for path in indexed_files),
    )
    connection.executemany(
        "INSERT INTO source_files(path, status) VALUES (?, ?)",
        ((str(path), "unreadable") for path in read_failures),
    )
