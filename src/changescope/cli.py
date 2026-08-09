from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from changescope.mcp import run_stdio_server
from changescope.application import (
    CatalogRegisterMappingRequest,
    CatalogRegisterRepositoryRequest,
    CatalogResolveMappingRequest,
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
    impact_command.add_argument("target", nargs="?", default=None, help="Java target in Class#method form")
    impact_command.add_argument(
        "--soap-wsdl", type=Path, default=None, help="WSDL file path relative to repository"
    )
    impact_command.add_argument(
        "--soap-port-type", default=None, help="Port type QName in Clark notation {ns}Name or Name"
    )
    impact_command.add_argument(
        "--soap-operation", default=None, help="WSDL operation name"
    )
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

    catalog_command = subcommands.add_parser("catalog", help="manage the Workspace Catalog")
    catalog_sub = catalog_command.add_subparsers(dest="catalog_action", required=True)

    reg_repo_cmd = catalog_sub.add_parser("register-repo", help="register a repository in the catalog")
    reg_repo_cmd.add_argument("--id", required=True, help="stable repository identity")
    reg_repo_cmd.add_argument("--path", type=Path, required=True, help="local path to repository")
    reg_repo_cmd.add_argument("--format", choices=("text", "json"), default="text")

    reg_map_cmd = catalog_sub.add_parser("register-mapping", help="register an explicit contract mapping in the catalog")
    reg_map_cmd.add_argument("--source-repo", required=True, help="source repository identity")
    reg_map_cmd.add_argument("--kind", required=True, help="contract kind (e.g. rest, soap, ejb)")
    reg_map_cmd.add_argument("--key", required=True, help="logical contract key")
    reg_map_cmd.add_argument("--target-repo", required=True, help="target repository identity")
    reg_map_cmd.add_argument("--target-key", required=True, help="target contract key")
    reg_map_cmd.add_argument("--provenance", default="", help="mapping provenance")
    reg_map_cmd.add_argument("--format", choices=("text", "json"), default="text")

    resolve_cmd = catalog_sub.add_parser("resolve", help="resolve an explicit contract mapping in the catalog")
    resolve_cmd.add_argument("--source-repo", required=True, help="source repository identity")
    resolve_cmd.add_argument("--kind", required=True, help="contract kind (e.g. rest, soap, ejb)")
    resolve_cmd.add_argument("--key", required=True, help="logical contract key")
    resolve_cmd.add_argument("--format", choices=("text", "json"), default="text")

    mcp_command = subcommands.add_parser('mcp', help='run the local stdio MCP server')
    mcp_mode = mcp_command.add_mutually_exclusive_group(required=True)
    mcp_mode.add_argument('--repository-root', '--repository', dest='repository_root', type=Path)
    mcp_mode.add_argument('--workspace-root', '--workspace', dest='workspace_root', type=Path)

    parsed = parser.parse_args(arguments)

    if parsed.command == 'mcp':
        run_stdio_server(
            repository_root=parsed.repository_root,
            workspace_root=parsed.workspace_root,
        )
        return 0

    if parsed.command == "index":
        result = ChangeScopeApplication().execute(IndexRequest(Path.cwd()))
        _render_index_result(result, parsed.format)
        return 0
    if parsed.command == "impact":
        has_soap = any(
            arg is not None
            for arg in (parsed.soap_wsdl, parsed.soap_port_type, parsed.soap_operation)
        )
        if parsed.target is not None and has_soap:
            parser.error("Cannot mix Java target ('Class#method') and SOAP target arguments (--soap-wsdl, --soap-port-type, --soap-operation)")
        if parsed.target is None and not (parsed.soap_wsdl and parsed.soap_port_type and parsed.soap_operation):
            parser.error("Must specify either a Java target ('Class#method') or all SOAP target arguments (--soap-wsdl, --soap-port-type, --soap-operation)")

        result = ChangeScopeApplication().execute(
            ImpactRequest(
                Path.cwd(),
                parsed.target,
                tuple(parsed.profiles),
                tuple(parsed.build_profiles),
                tuple(parsed.runtime_profiles),
                parsed.soap_wsdl,
                parsed.soap_port_type,
                parsed.soap_operation,
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
    if parsed.command == "catalog":
        if parsed.catalog_action == "register-repo":
            cat_result = ChangeScopeApplication().execute(
                CatalogRegisterRepositoryRequest(Path.cwd(), parsed.id, parsed.path)
            )
            _render_catalog_result(cat_result, parsed.format)
            return 0
        if parsed.catalog_action == "register-mapping":
            cat_result = ChangeScopeApplication().execute(
                CatalogRegisterMappingRequest(
                    Path.cwd(),
                    parsed.source_repo,
                    parsed.kind,
                    parsed.key,
                    parsed.target_repo,
                    parsed.target_key,
                    parsed.provenance,
                )
            )
            _render_catalog_result(cat_result, parsed.format)
            return 0
        if parsed.catalog_action == "resolve":
            cat_result = ChangeScopeApplication().execute(
                CatalogResolveMappingRequest(
                    Path.cwd(), parsed.source_repo, parsed.kind, parsed.key
                )
            )
            _render_catalog_result(cat_result, parsed.format)
            return 0 if cat_result.outcome == "resolved" else 2
    raise AssertionError(f"Unhandled command: {parsed.command}")


def _render_index_result(result: IndexResult, output_format: str) -> None:
    report = _index_report(result)
    if output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"Indexed repository: {report['snapshot']['repository_root']}")
    print(f"Source roots: {', '.join(report['source_roots']) or 'none'}")
    print(f"Indexed Java files: {len(report['indexed_files'])}")
    if report.get("vbnet_files"):
        print(f"Indexed VB.NET files: {len(report['vbnet_files'])}")
    print(f"Indexed configuration files: {len(report['configuration_files'])}")
    print(f"Java declarations: {report['declaration_count']}")
    if report.get("vbnet_declaration_count"):
        print(f"VB.NET declarations: {report['vbnet_declaration_count']}")
    print(f"Explicit invocation evidence: {report['invocation_count']}")
    print(f"Spring Configuration Evidence: {report['spring_configuration_evidence_count']}")
    print(f"Quarkus Build Evidence: {report['quarkus_build_evidence_count']}")
    print(f"SOAP Contract Evidence: {report['soap_contract_evidence_count']}")
    print("Included Java files:")
    for path in report["indexed_files"]:
        print(f"- {path}")
    if report.get("vbnet_files"):
        print("Included VB.NET files:")
        for path in report["vbnet_files"]:
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
        "vbnet_files": [_report_path(path) for path in getattr(result, "vbnet_files", ())],
        "configuration_files": [_report_path(path) for path in result.configuration_files],
        "excluded_directories": [_report_path(path) for path in result.excluded_directories],
        "read_failures": [_report_path(path) for path in result.read_failures],
        "declaration_count": len(result.declarations),
        "invocation_count": len(result.invocations),
        "vbnet_declaration_count": len(getattr(result, "vbnet_declarations", ())),
        "vbnet_invocation_count": len(getattr(result, "vbnet_invocations", ())),
        "vbnet_fact_count": len(getattr(result, "vbnet_facts", ())),
        "spring_configuration_evidence_count": len(result.spring_facts),
        "quarkus_build_evidence_count": len(result.quarkus_build_facts),
        "quarkus_configuration_evidence_count": len(result.quarkus_config_facts),
        "quarkus_cdi_evidence_count": len(result.quarkus_cdi_facts),
        "quarkus_rest_evidence_count": len(result.quarkus_rest_facts),
        "quarkus_route_evidence_count": len(result.quarkus_route_facts),
        "quarkus_security_evidence_count": len(result.quarkus_security_facts),
        "quarkus_test_evidence_count": len(result.quarkus_test_facts),
        "quarkus_native_evidence_count": len(result.quarkus_native_facts),
        "quarkus_boundary_evidence_count": len(result.quarkus_boundary_facts),
        "soap_contract_evidence_count": len(result.soap_facts),
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
    if report.get("manual_verification_surfaces"):
        print("Manual verification surfaces:")
        for surface in report["manual_verification_surfaces"]:
            print(f"- {surface['description']} [{surface['kind']}] {surface['evidence_handle']}")
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
        "manual_verification_surfaces": [
            {
                "kind": surface.kind,
                "description": surface.description,
                "path": _report_path(surface.path),
                "start_line": surface.start_line,
                "end_line": surface.end_line,
                "evidence_handle": surface.evidence_handle,
            }
            for surface in getattr(result, "manual_verification_surfaces", ())
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


def _render_catalog_result(result, output_format: str) -> None:
    report = {
        "outcome": result.outcome,
        "repository": {
            "repository_id": result.repository.repository_id,
            "repository_path": result.repository.repository_path.as_posix(),
            "git_commit": result.repository.git_commit,
            "working_tree_state": result.repository.working_tree_state,
        } if result.repository else None,
        "mapping": {
            "source_repository_id": result.mapping.source_repository_id,
            "contract_kind": result.mapping.contract_kind,
            "contract_key": result.mapping.contract_key,
            "target_repository_id": result.mapping.target_repository_id,
            "target_contract_key": result.mapping.target_contract_key,
            "provenance": result.mapping.provenance,
        } if result.mapping else None,
        "candidates": [
            {
                "source_repository_id": c.source_repository_id,
                "contract_kind": c.contract_kind,
                "contract_key": c.contract_key,
                "target_repository_id": c.target_repository_id,
                "target_contract_key": c.target_contract_key,
                "provenance": c.provenance,
            } for c in result.candidates
        ],
        "unresolved_items": [
            {
                "message": item.message,
                "path": item.path.as_posix() if item.path else None,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "evidence_handle": item.evidence_handle,
            } for item in result.unresolved_items
        ],
        "snapshot": _snapshot_report(result.snapshot),
    }
    if output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"Catalog outcome: {report['outcome']}")
    if report["repository"]:
        print(f"Repository ID: {report['repository']['repository_id']}")
        print(f"Location: {report['repository']['repository_path']}")
        print(f"Commit: {report['repository']['git_commit'] or 'none'} ({report['repository']['working_tree_state']})")
    if report["mapping"]:
        m = report["mapping"]
        print(f"Contract mapping: [{m['contract_kind']}] {m['source_repository_id']} ({m['contract_key']}) -> {m['target_repository_id']} ({m['target_contract_key']})")
        if m["provenance"]:
            print(f"Provenance: {m['provenance']}")
    if report["candidates"]:
        print("Candidates:")
        for c in report["candidates"]:
            print(f"- [{c['contract_kind']}] {c['source_repository_id']} ({c['contract_key']}) -> {c['target_repository_id']} ({c['target_contract_key']})")
    if report["unresolved_items"]:
        print("Unresolved items:")
        for item in report["unresolved_items"]:
            print(f"- {item['message']}")
