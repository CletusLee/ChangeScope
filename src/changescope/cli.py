from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from changescope.application import ChangeScopeApplication, IndexRequest, IndexResult


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="changescope")
    subcommands = parser.add_subparsers(dest="command", required=True)
    index_command = subcommands.add_parser("index", help="index the current repository")
    index_command.add_argument(
        "--format", choices=("text", "json"), default="text", help="report format"
    )
    parsed = parser.parse_args(arguments)

    if parsed.command == "index":
        result = ChangeScopeApplication().execute(IndexRequest(Path.cwd()))
        _render_index_result(result, parsed.format)
        return 0
    raise AssertionError(f"Unhandled command: {parsed.command}")


def _render_index_result(result: IndexResult, output_format: str) -> None:
    report = _index_report(result)
    if output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"Indexed repository: {report['snapshot']['repository_root']}")
    print(f"Source roots: {', '.join(report['source_roots']) or 'none'}")
    print(f"Indexed Java files: {len(report['indexed_files'])}")
    print(f"Java declarations: {report['declaration_count']}")
    print(f"Explicit invocation evidence: {report['invocation_count']}")
    print("Included Java files:")
    for path in report["indexed_files"]:
        print(f"- {path}")
    print(f"Excluded directories: {', '.join(report['excluded_directories']) or 'none'}")
    print(f"Read failures: {', '.join(report['read_failures']) or 'none'}")
    print(f"Parse failures: {len(report['parse_failures'])}")
    for failure in report["parse_failures"]:
        print(f"- {failure['path']}:{failure['start_line']}:{failure['start_column']} {failure['message']}")
    print(
        "Snapshot: "
        f"{report['snapshot']['git_commit'] or 'no Git commit'} "
        f"({report['snapshot']['working_tree_state']})"
    )


def _index_report(result: IndexResult) -> dict[str, object]:
    return {
        "source_roots": [_report_path(path) for path in result.source_roots],
        "indexed_files": [_report_path(path) for path in result.indexed_files],
        "excluded_directories": [_report_path(path) for path in result.excluded_directories],
        "read_failures": [_report_path(path) for path in result.read_failures],
        "declaration_count": len(result.declarations),
        "invocation_count": len(result.invocations),
        "parse_failures": [
            {
                "path": _report_path(failure.path),
                "start_line": failure.start_line,
                "start_column": failure.start_column,
                "message": failure.message,
            }
            for failure in result.parse_failures
        ],
        "snapshot": {
            "repository_root": str(result.snapshot.repository_root),
            "git_commit": result.snapshot.git_commit,
            "working_tree_state": result.snapshot.working_tree_state,
        },
    }


def _report_path(path: Path) -> str:
    return path.as_posix()
