from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from changescope.application import (
    ChangeScopeApplication,
    EvidenceRequest,
    ImpactRequest,
    ImpactResult,
    IndexRequest,
    IndexResult,
    SourceRequest,
)


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="changescope")
    subcommands = parser.add_subparsers(dest="command", required=True)
    index_command = subcommands.add_parser("index", help="index the current repository")
    index_command.add_argument(
        "--format", choices=("text", "json"), default="text", help="report format"
    )
    impact_command = subcommands.add_parser("impact", help="report local Java method impact")
    impact_command.add_argument("target", help="Java target in Class#method form")
    impact_command.add_argument(
        "--format", choices=("text", "json"), default="text", help="report format"
    )
    impact_command.add_argument(
        "--profile", dest="profiles", action="append", default=[],
        help="select an active Spring profile; repeat for multiple profiles",
    )
    impact_command.add_argument(
        "--build-profile", dest="build_profiles", action="append", default=[],
        help="select a Quarkus build profile; repeat for multiple profiles",
    )
    impact_command.add_argument(
        "--runtime-profile", dest="runtime_profiles", action="append", default=[],
        help="select a Quarkus runtime profile; repeat for multiple profiles",
    )
    evidence_command = subcommands.add_parser("evidence", help="retrieve bounded source evidence")
    evidence_command.add_argument("evidence_handle")
    evidence_command.add_argument("--context-lines", type=int, default=2)
    evidence_command.add_argument("--max-characters", type=int, default=4_000)
    evidence_command.add_argument("--enclosing-symbol", action="store_true")
    evidence_command.add_argument("--format", choices=("text", "json"), default="text")
    source_command = subcommands.add_parser("source", help="retrieve a bounded explicit source range")
    source_command.add_argument("path", help="path relative to the current repository")
    source_command.add_argument("start_line", type=int)
    source_command.add_argument("end_line", type=int)
    source_command.add_argument("--start-column", type=int, default=0)
    source_command.add_argument("--max-characters", type=int, default=4_000)
    source_command.add_argument("--format", choices=("text", "json"), default="text")
    parsed = parser.parse_args(arguments)

    if parsed.command == "index":
        result = ChangeScopeApplication().execute(IndexRequest(Path.cwd()))
        _render_index_result(result, parsed.format)
        return 0
    if parsed.command == "impact":
        result = ChangeScopeApplication().execute(
            ImpactRequest(
                Path.cwd(),
                parsed.target,
                tuple(parsed.profiles),
                tuple(parsed.build_profiles),
                tuple(parsed.runtime_profiles),
            )
        )
        _render_impact_result(result, parsed.format)
        return 0 if result.outcome == "resolved" else 2
    if parsed.command == "evidence":
        result = ChangeScopeApplication().execute(
            EvidenceRequest(
                Path.cwd(), parsed.evidence_handle, parsed.context_lines,
                parsed.max_characters, parsed.enclosing_symbol,
            )
        )
        _render_source_navigation(result, parsed.format)
        return 0
    if parsed.command == "source":
        result = ChangeScopeApplication().execute(
            SourceRequest(
                Path.cwd(), Path(parsed.path), parsed.start_line,
                parsed.end_line, parsed.max_characters, parsed.start_column,
            )
        )
        _render_source_navigation(result, parsed.format)
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
    print(f"Indexed configuration files: {len(report['configuration_files'])}")
    print(f"Java declarations: {report['declaration_count']}")
    print(f"Explicit invocation evidence: {report['invocation_count']}")
    print(f"Spring Configuration Evidence: {report['spring_configuration_evidence_count']}")
    print(f"Quarkus Build Evidence: {report['quarkus_build_evidence_count']}")
    print("Included Java files:")
    for path in report["indexed_files"]:
        print(f"- {path}")
    if report["configuration_files"]:
        print("Included configuration files:")
        for path in report["configuration_files"]:
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
        "configuration_files": [_report_path(path) for path in result.configuration_files],
        "excluded_directories": [_report_path(path) for path in result.excluded_directories],
        "read_failures": [_report_path(path) for path in result.read_failures],
        "declaration_count": len(result.declarations),
        "invocation_count": len(result.invocations),
        "spring_configuration_evidence_count": len(result.spring_facts),
        "quarkus_build_evidence_count": len(result.quarkus_build_facts),
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


def _render_source_navigation(result, output_format: str) -> None:
    report = {
        "evidence_handle": result.evidence_handle,
        "path": _report_path(result.path),
        "start_line": result.start_line,
        "end_line": result.end_line,
        "content": result.content,
        "truncated": result.truncated,
        "continuation_start_line": result.continuation_start_line,
        "continuation_start_column": result.continuation_start_column,
    }
    if output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"Evidence: {report['evidence_handle']}")
    print(f"Source: {report['path']}:{report['start_line']}-{report['end_line']}")
    print(report["content"], end="")
    if report["truncated"]:
        print(
            "Continuation starts at "
            f"line {report['continuation_start_line']}, column {report['continuation_start_column']}"
        )


def _render_impact_result(result: ImpactResult, output_format: str) -> None:
    report = _impact_report(result)
    if output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"Impact target: {report['requested_target']}")
    print(f"Outcome: {report['outcome']}")
    if report["target"] is not None:
        print(f"Resolved target: {report['target']['signature']}")
        print(f"Evidence: {report['target']['evidence_handle']}")
    if report["candidates"]:
        print("Candidates:")
        for candidate in report["candidates"]:
            print(f"- {candidate['signature']} ({candidate['evidence_handle']})")
    if report["relationships"]:
        print("Affected relationships:")
        for relationship in report["relationships"]:
            profile = relationship["profile"]
            profile_suffix = ""
            if relationship["conditional"]:
                profile_suffix = f" (conditional profile: {profile or 'unspecified'})"
            elif profile:
                profile_suffix = f" (profile: {profile})"
            view_suffix = (
                f" (business view: {relationship['business_view']})"
                if relationship.get("business_view")
                else ""
            )
            print(
                f"- {relationship['kind']} {relationship['caller']} "
                f"[{relationship['confidence']}] {relationship['evidence_handle']}"
                + profile_suffix
                + view_suffix
            )
            print(f"  Evidence chain: {' -> '.join(relationship['evidence_chain'])}")
    print("Assumptions:")
    for assumption in report["assumptions"]:
        print(f"- {assumption}")
    print("Unresolved items:")
    for item in report["unresolved_items"]:
        evidence = f" {item['evidence_handle']}" if item["evidence_handle"] else ""
        print(f"- {item['message']}{evidence}")
    if report["snapshot"] is not None:
        snapshot = report["snapshot"]
        print(
            "Snapshot: "
            f"{snapshot['git_commit'] or 'no Git commit'} "
            f"({snapshot['working_tree_state']})"
        )


def _impact_report(result: ImpactResult) -> dict[str, object]:
    return {
        "outcome": result.outcome,
        "requested_target": result.requested_target,
        "target": _target_report(result.target) if result.target else None,
        "candidates": [_target_report(candidate) for candidate in result.candidates],
        "relationships": [
            {
                "kind": relationship.kind,
                "caller": relationship.caller,
                "path": _report_path(relationship.path),
                "start_line": relationship.start_line,
                "end_line": relationship.end_line,
                "evidence_handle": relationship.evidence_handle,
                "evidence_chain": list(relationship.evidence_chain),
                "confidence": relationship.confidence,
                "conditional": relationship.conditional,
                "profile": relationship.profile,
                "business_view": relationship.business_view,
            }
            for relationship in result.relationships
        ],
        "assumptions": list(result.assumptions),
        "unresolved_items": [
            {
                "message": item.message,
                "path": _report_path(item.path) if item.path else None,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "evidence_handle": item.evidence_handle,
            }
            for item in result.unresolved_items
        ],
        "snapshot": _snapshot_report(result.snapshot),
    }


def _target_report(target) -> dict[str, object]:
    return {
        "signature": target.signature,
        "path": _report_path(target.path),
        "start_line": target.start_line,
        "end_line": target.end_line,
        "evidence_handle": target.evidence_handle,
    }


def _snapshot_report(snapshot) -> dict[str, object] | None:
    if snapshot is None:
        return None
    return {
        "repository_root": str(snapshot.repository_root),
        "git_commit": snapshot.git_commit,
        "working_tree_state": snapshot.working_tree_state,
    }
