from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Callable, TextIO
from urllib.parse import quote

from .application import (
    CatalogMapping,
    CatalogSummaryRequest,
    ChangeScopeApplication,
    EvidenceRequest,
    ImpactRequest,
    IndexRequest,
    IndexResult,
    RepositoryIndexStatus,
    RepositoryStatusRequest,
    SourceNavigation,
    SourceRequest,
    WorkspaceCatalogSummary,
)


_PROTOCOL_VERSION = '2025-06-18'
_SERVER_VERSION = '0.1.0'


class MCPError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


@dataclass(frozen=True)
class MCPServerConfig:
    repository_root: Path | None = None
    workspace_root: Path | None = None

    def __post_init__(self) -> None:
        if (self.repository_root is None) == (self.workspace_root is None):
            raise ValueError('Configure exactly one of repository_root or workspace_root.')

    @property
    def mode(self) -> str:
        return 'repository' if self.repository_root is not None else 'workspace'


ProgressSink = Callable[[dict[str, Any]], None]


class ChangeScopeMCPServer:
    '''Thin MCP transport adapter over ChangeScopeApplication.'''

    def __init__(
        self,
        config: MCPServerConfig | None = None,
        *,
        repository_root: Path | None = None,
        workspace_root: Path | None = None,
        application: ChangeScopeApplication | None = None,
    ) -> None:
        if config is None:
            config = MCPServerConfig(repository_root, workspace_root)
        self.config = config
        self.application = application or ChangeScopeApplication()

    def list_tools(self) -> list[dict[str, Any]]:
        return _tool_definitions()

    def list_resources(self) -> list[dict[str, Any]]:
        resources = [
            {
                'uri': 'changescope://catalog',
                'name': 'Workspace Catalog summary',
                'description': 'Configured repositories and explicit catalog mappings; no source or impact content.',
                'mimeType': 'application/json',
            }
        ]
        for repository_id in self._configured_repositories():
            encoded_id = quote(repository_id, safe='')
            resources.extend(
                [
                    {
                        'uri': f'changescope://repositories/{encoded_id}/status',
                        'name': f'Repository Index status: {repository_id}',
                        'description': 'Compact Repository Index availability and counts.',
                        'mimeType': 'application/json',
                    },
                    {
                        'uri': f'changescope://repositories/{encoded_id}/snapshot',
                        'name': f'Index Snapshot: {repository_id}',
                        'description': 'Index Snapshot provenance metadata only.',
                        'mimeType': 'application/json',
                    },
                ]
            )
        return resources

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        progress_token: Any = None,
        progress_sink: ProgressSink | None = None,
    ) -> dict[str, Any]:
        arguments = self._arguments(arguments)
        try:
            if name == 'index_repository':
                self._check_keys(arguments, {'repository_id'})
                payload = self._index_repository(arguments, progress_token, progress_sink)
            elif name == 'analyze_impact':
                self._check_keys(
                    arguments,
                    {
                        'repository_id', 'target', 'soap', 'soap_wsdl',
                        'soap_port_type', 'soap_operation', 'profiles',
                        'build_profiles', 'runtime_profiles',
                    },
                )
                payload = self._analyze_impact(arguments)
            elif name == 'get_evidence':
                self._check_keys(
                    arguments,
                    {
                        'repository_id', 'evidence_handle', 'context_lines',
                        'max_characters', 'enclosing_symbol',
                    },
                )
                payload = self._get_evidence(arguments)
            elif name == 'read_source_range':
                self._check_keys(
                    arguments,
                    {
                        'repository_id', 'path', 'start_line', 'end_line',
                        'start_column', 'max_characters',
                    },
                )
                payload = self._read_source_range(arguments)
            else:
                raise MCPError(-32602, f'Unknown tool: {name}')
        except MCPError:
            raise
        except ValueError as error:
            raise MCPError(-32602, str(error), {'kind': 'invalid_request'}) from error
        return self._tool_result(payload)

    def read_resource(self, uri: str) -> dict[str, Any]:
        if uri == 'changescope://catalog':
            payload = self._catalog_payload()
        else:
            prefix = 'changescope://repositories/'
            if not uri.startswith(prefix):
                raise MCPError(-32602, f'Unknown resource URI: {uri}')
            remainder = uri[len(prefix):]
            if '/' not in remainder:
                raise MCPError(-32602, f'Unknown resource URI: {uri}')
            encoded_id, resource_kind = remainder.rsplit('/', 1)
            from urllib.parse import unquote

            repository_id = unquote(encoded_id)
            self._resolve_repository(repository_id)
            if resource_kind == 'status':
                payload = self._status_payload(repository_id)
            elif resource_kind == 'snapshot':
                payload = self._snapshot_payload(repository_id)
            else:
                raise MCPError(-32602, f'Unknown resource URI: {uri}')
        return {
            'contents': [
                {
                    'uri': uri,
                    'mimeType': 'application/json',
                    'text': json.dumps(payload, sort_keys=True),
                }
            ]
        }

    def handle_request(
        self,
        request: dict[str, Any],
        *,
        send_notification: ProgressSink | None = None,
    ) -> dict[str, Any] | None:
        if request.get('jsonrpc') != '2.0':
            return self._error_response(request.get('id'), -32600, 'JSON-RPC version must be 2.0')
        method = request.get('method')
        request_id = request.get('id')
        is_notification = 'id' not in request

        if method == 'notifications/initialized':
            return None
        try:
            if method == 'ping':
                result: dict[str, Any] = {}
            elif method == 'initialize':
                params = request.get('params') or {}
                if not isinstance(params, dict):
                    raise MCPError(-32602, 'initialize params must be an object.')
                result = self._initialize(params)
            elif method == 'tools/list':
                result = {'tools': self.list_tools()}
            elif method == 'resources/list':
                result = {'resources': self.list_resources()}
            elif method == 'resources/templates/list':
                result = {'resourceTemplates': []}
            elif method == 'resources/read':
                params = request.get('params') or {}
                self._require_string(params, 'uri')
                result = self.read_resource(params['uri'])
            elif method == 'tools/call':
                params = request.get('params') or {}
                self._require_string(params, 'name')
                metadata = params.get('_meta') or {}
                result = self.call_tool(
                    params['name'],
                    params.get('arguments'),
                    progress_token=metadata.get('progressToken'),
                    progress_sink=send_notification,
                )
            else:
                if is_notification:
                    return None
                return self._error_response(request_id, -32601, f'Method not found: {method}')
        except MCPError as error:
            if is_notification:
                return None
            return self._error_response(request_id, error.code, error.message, error.data)
        except Exception as error:
            if is_notification:
                return None
            return self._error_response(
                request_id,
                -32603,
                'Internal ChangeScope MCP error.',
                {'type': type(error).__name__},
            )

        if is_notification:
            return None
        return {'jsonrpc': '2.0', 'id': request_id, 'result': result}

    def run_stdio(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    self._write_message(
                        stdout,
                        self._error_response(None, -32600, 'MCP messages must be JSON objects.'),
                    )
                    continue

                def notify(message: dict[str, Any]) -> None:
                    self._write_message(stdout, message)

                response = self.handle_request(request, send_notification=notify)
                if response is not None:
                    self._write_message(stdout, response)
            except json.JSONDecodeError as error:
                self._write_message(
                    stdout,
                    self._error_response(None, -32700, f'Invalid JSON: {error.msg}'),
                )

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested_version = params.get('protocolVersion')
        protocol_version = requested_version if isinstance(requested_version, str) else _PROTOCOL_VERSION
        return {
            'protocolVersion': protocol_version,
            'capabilities': {
                'tools': {'listChanged': False},
                'resources': {'subscribe': False, 'listChanged': False},
            },
            'serverInfo': {'name': 'changescope', 'version': _SERVER_VERSION},
            'instructions': 'Use configured repository IDs. Index explicitly before analysis or source navigation.',
        }

    def _index_repository(
        self,
        arguments: dict[str, Any],
        progress_token: Any,
        progress_sink: ProgressSink | None,
    ) -> dict[str, Any]:
        repository_id = self._repository_id(arguments)
        repository_root = self._resolve_repository(repository_id)
        phases = (
            ('discovery', 0.2),
            ('parsing', 0.45),
            ('framework_analysis', 0.7),
            ('persistence', 0.9),
        )
        for message, progress in phases:
            self._progress(progress_sink, progress_token, progress, message)
        result = self.application.execute(IndexRequest(repository_root))
        if not isinstance(result, IndexResult):
            raise MCPError(-32603, 'The application service returned an unexpected index result.')
        self._progress(progress_sink, progress_token, 1.0, 'complete')
        return {'repository_id': repository_id, 'outcome': 'indexed', **self._index_report(result)}

    def _analyze_impact(self, arguments: dict[str, Any]) -> dict[str, Any]:
        repository_id = self._repository_id(arguments)
        repository_root = self._resolve_repository(repository_id)
        target = arguments.get('target')
        soap = arguments.get('soap')
        soap_wsdl = arguments.get('soap_wsdl')
        soap_port_type = arguments.get('soap_port_type')
        soap_operation = arguments.get('soap_operation')
        if soap is not None:
            if not isinstance(soap, dict):
                raise MCPError(-32602, 'soap must be an object.')
            soap_wsdl = soap.get('wsdl')
            soap_port_type = soap.get('port_type')
            soap_operation = soap.get('operation')
        has_soap = any(value is not None for value in (soap_wsdl, soap_port_type, soap_operation))
        if target is not None and not isinstance(target, str):
            raise MCPError(-32602, 'target must be a string.')
        if target is not None and has_soap:
            raise MCPError(-32602, 'Specify either target or soap, not both.')
        if target is None and not has_soap:
            raise MCPError(-32602, 'Specify a Java target or a complete SOAP target.')
        if has_soap and not all(isinstance(value, str) and value for value in (soap_wsdl, soap_port_type, soap_operation)):
            raise MCPError(-32602, 'SOAP targets require wsdl, port_type, and operation strings.')
        wsdl_path = self._relative_path(soap_wsdl, 'soap.wsdl') if target is None else None
        request = ImpactRequest(
            repository_root,
            target,
            self._string_tuple(arguments.get('profiles'), 'profiles'),
            self._string_tuple(arguments.get('build_profiles'), 'build_profiles'),
            self._string_tuple(arguments.get('runtime_profiles'), 'runtime_profiles'),
            wsdl_path,
            soap_port_type,
            soap_operation,
        )
        result = self.application.execute(request)
        if result.outcome == 'index_missing':
            return self._missing_index(repository_id, result.requested_target)
        return {'repository_id': repository_id, **self._impact_report(result)}

    def _get_evidence(self, arguments: dict[str, Any]) -> dict[str, Any]:
        repository_id = self._repository_id(arguments)
        repository_root = self._resolve_repository(repository_id)
        status = self.application.execute(RepositoryStatusRequest(repository_root))
        if not isinstance(status, RepositoryIndexStatus):
            raise MCPError(-32603, 'The application service returned an unexpected repository status.')
        evidence_handle = arguments.get('evidence_handle')
        if not isinstance(evidence_handle, str) or not evidence_handle:
            raise MCPError(-32602, 'evidence_handle must be a non-empty string.')
        if not status.index_exists or status.outcome != 'ready':
            return self._missing_index(repository_id, evidence_handle)
        context_lines = self._integer(arguments, 'context_lines', 2, minimum=0)
        max_characters = self._integer(arguments, 'max_characters', 4000, minimum=1)
        enclosing_symbol = arguments.get('enclosing_symbol', False)
        if not isinstance(enclosing_symbol, bool):
            raise MCPError(-32602, 'enclosing_symbol must be a boolean.')
        result = self.application.execute(
            EvidenceRequest(
                repository_root,
                evidence_handle,
                context_lines,
                max_characters,
                enclosing_symbol,
            )
        )
        return {'repository_id': repository_id, 'outcome': 'resolved', **self._navigation_report(result)}

    def _read_source_range(self, arguments: dict[str, Any]) -> dict[str, Any]:
        repository_id = self._repository_id(arguments)
        repository_root = self._resolve_repository(repository_id)
        status = self.application.execute(RepositoryStatusRequest(repository_root))
        if not isinstance(status, RepositoryIndexStatus):
            raise MCPError(-32603, 'The application service returned an unexpected repository status.')
        if not status.index_exists or status.outcome != 'ready':
            return self._missing_index(repository_id, 'source_range')
        path = self._relative_path(arguments.get('path'), 'path')
        start_line = self._integer(arguments, 'start_line', None, minimum=1)
        end_line = self._integer(arguments, 'end_line', None, minimum=1)
        start_column = self._integer(arguments, 'start_column', 0, minimum=0)
        max_characters = self._integer(arguments, 'max_characters', 4000, minimum=1)
        if end_line < start_line:
            raise MCPError(-32602, 'end_line must be greater than or equal to start_line.')
        result = self.application.execute(
            SourceRequest(
                repository_root,
                path,
                start_line,
                end_line,
                max_characters,
                start_column,
            )
        )
        return {'repository_id': repository_id, 'outcome': 'resolved', **self._navigation_report(result)}

    def _catalog_payload(self) -> dict[str, Any]:
        if self.config.mode == 'workspace':
            summary = self.application.execute(CatalogSummaryRequest(self.config.workspace_root))
            if not isinstance(summary, WorkspaceCatalogSummary):
                raise MCPError(-32603, 'The application service returned an unexpected catalog summary.')
        else:
            root = self.config.repository_root.resolve()
            status = self.application.execute(RepositoryStatusRequest(root))
            snapshot = status.snapshot if isinstance(status, RepositoryIndexStatus) else None
            return {
                'mode': self.config.mode,
                'outcome': 'configured',
                'catalog_exists': False,
                'repositories': [
                    {
                        'repository_id': 'current',
                        'repository_path': root.as_posix(),
                        'git_commit': snapshot.git_commit if snapshot else None,
                        'working_tree_state': snapshot.working_tree_state if snapshot else 'unknown',
                    }
                ],
                'mappings': [],
            }
        return {
            'mode': self.config.mode,
            'outcome': summary.outcome,
            'catalog_exists': summary.catalog_exists,
            'repositories': [self._catalog_repository_report(repository) for repository in summary.repositories],
            'mappings': [self._mapping_report(mapping) for mapping in summary.mappings],
        }

    def _status_payload(self, repository_id: str) -> dict[str, Any]:
        status = self.application.execute(
            RepositoryStatusRequest(self._resolve_repository(repository_id))
        )
        if not isinstance(status, RepositoryIndexStatus):
            raise MCPError(-32603, 'The application service returned an unexpected repository status.')
        return {
            'repository_id': repository_id,
            'outcome': status.outcome,
            'repository_root': status.repository_root.as_posix(),
            'index_exists': status.index_exists,
            'schema_version': status.schema_version,
            'indexed_file_count': status.indexed_file_count,
            'declaration_count': status.declaration_count,
            'invocation_count': status.invocation_count,
            'soap_fact_count': status.soap_fact_count,
            'snapshot': self._snapshot_report(status.snapshot),
        }

    def _snapshot_payload(self, repository_id: str) -> dict[str, Any]:
        status = self.application.execute(
            RepositoryStatusRequest(self._resolve_repository(repository_id))
        )
        if not isinstance(status, RepositoryIndexStatus):
            raise MCPError(-32603, 'The application service returned an unexpected repository status.')
        return {
            'repository_id': repository_id,
            'outcome': status.outcome,
            'snapshot': self._snapshot_report(status.snapshot),
        }

    def _configured_repositories(self) -> dict[str, Path]:
        if self.config.mode == 'repository':
            return {'current': self.config.repository_root.resolve()}
        summary = self.application.execute(CatalogSummaryRequest(self.config.workspace_root))
        if not isinstance(summary, WorkspaceCatalogSummary) or summary.outcome not in {'ready', 'configured'}:
            return {}
        return {
            repository.repository_id: repository.repository_path.resolve()
            for repository in summary.repositories
        }

    def _resolve_repository(self, repository_id: str) -> Path:
        repositories = self._configured_repositories()
        if repository_id not in repositories:
            raise MCPError(
                -32602,
                f'Repository ID {repository_id!r} is not configured for this MCP server.',
                {'repository_id': repository_id, 'configured_repository_ids': sorted(repositories)},
            )
        return repositories[repository_id]

    @staticmethod
    def _arguments(arguments: dict[str, Any] | None) -> dict[str, Any]:
        if arguments is None:
            return {}
        if not isinstance(arguments, dict):
            raise MCPError(-32602, 'Tool arguments must be an object.')
        return arguments

    @staticmethod
    def _check_keys(arguments: dict[str, Any], allowed: set[str]) -> None:
        unexpected = sorted(set(arguments) - allowed)
        if unexpected:
            raise MCPError(
                -32602,
                f'Unsupported tool argument(s): {chr(44).join(unexpected)}.',
                {'arguments': unexpected},
            )

    @staticmethod
    def _repository_id(arguments: dict[str, Any]) -> str:
        value = arguments.get('repository_id')
        if not isinstance(value, str) or not value:
            raise MCPError(-32602, 'repository_id must be a configured non-empty string.')
        return value

    @staticmethod
    def _require_string(arguments: dict[str, Any], key: str) -> None:
        if not isinstance(arguments, dict):
            raise MCPError(-32602, 'Request params must be an object.')
        if not isinstance(arguments.get(key), str) or not arguments[key]:
            raise MCPError(-32602, f'{key} must be a non-empty string.')

    @staticmethod
    def _integer(
        arguments: dict[str, Any],
        key: str,
        default: int | None,
        *,
        minimum: int,
    ) -> int:
        value = arguments.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise MCPError(-32602, f'{key} must be an integer greater than or equal to {minimum}.')
        return value

    @staticmethod
    def _string_tuple(value: Any, key: str) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise MCPError(-32602, f'{key} must be an array of strings.')
        return tuple(value)

    @staticmethod
    def _relative_path(value: Any, label: str) -> Path:
        if not isinstance(value, str) or not value:
            raise MCPError(-32602, f'{label} must be a non-empty repository-relative path.')
        path = Path(value)
        if path.is_absolute() or '..' in path.parts:
            raise MCPError(-32602, f'{label} must be repository-relative; arbitrary filesystem paths are not allowed.')
        return path

    @staticmethod
    def _progress(
        sink: ProgressSink | None,
        token: Any,
        progress: float,
        message: str,
    ) -> None:
        if sink is not None and token is not None:
            sink(
                {
                    'jsonrpc': '2.0',
                    'method': 'notifications/progress',
                    'params': {
                        'progressToken': token,
                        'progress': progress,
                        'total': 1,
                        'message': message,
                    },
                }
            )

    @staticmethod
    def _tool_result(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            'content': [
                {
                    'type': 'text',
                    'text': json.dumps(payload, sort_keys=True),
                }
            ],
            'structuredContent': payload,
            'isError': False,
        }

    @staticmethod
    def _missing_index(repository_id: str, requested_target: str) -> dict[str, Any]:
        return {
            'repository_id': repository_id,
            'outcome': 'index_missing',
            'requested_target': requested_target,
            'next_action': {
                'tool': 'index_repository',
                'arguments': {'repository_id': repository_id},
            },
            'message': 'No local Repository Index exists. Index explicitly before analysis or source navigation.',
            'snapshot': None,
        }

    @staticmethod
    def _write_message(stdout: TextIO, message: dict[str, Any]) -> None:
        stdout.write(json.dumps(message, sort_keys=True) + '\n')
        stdout.flush()

    @staticmethod
    def _error_response(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
        error: dict[str, Any] = {'code': code, 'message': message}
        if data is not None:
            error['data'] = data
        return {'jsonrpc': '2.0', 'id': request_id, 'error': error}

    @staticmethod
    def _snapshot_report(snapshot: Any) -> dict[str, Any] | None:
        if snapshot is None:
            return None
        return {
            'repository_root': snapshot.repository_root.as_posix(),
            'git_commit': snapshot.git_commit,
            'working_tree_state': snapshot.working_tree_state,
        }

    @staticmethod
    def _index_report(result: IndexResult) -> dict[str, Any]:
        return {
            'source_roots': [path.as_posix() for path in result.source_roots],
            'indexed_files': [path.as_posix() for path in result.indexed_files],
            'configuration_files': [path.as_posix() for path in result.configuration_files],
            'declaration_count': len(result.declarations) + len(result.vbnet_declarations),
            'invocation_count': len(result.invocations) + len(result.vbnet_invocations),
            'soap_fact_count': len(result.soap_facts),
            'parse_failure_count': len(result.parse_failures),
            'snapshot': ChangeScopeMCPServer._snapshot_report(result.snapshot),
        }

    @staticmethod
    def _impact_report(result: Any) -> dict[str, Any]:
        return {
            'outcome': result.outcome,
            'requested_target': result.requested_target,
            'target': ChangeScopeMCPServer._target_report(result.target),
            'candidates': [ChangeScopeMCPServer._target_report(candidate) for candidate in result.candidates],
            'relationships': [
                {
                    'kind': relationship.kind,
                    'caller': relationship.caller,
                    'path': relationship.path.as_posix(),
                    'start_line': relationship.start_line,
                    'end_line': relationship.end_line,
                    'evidence_handle': relationship.evidence_handle,
                    'evidence_chain': list(relationship.evidence_chain),
                    'confidence': relationship.confidence,
                    'conditional': relationship.conditional,
                    'profile': relationship.profile,
                    'business_view': relationship.business_view,
                    'language': relationship.language,
                }
                for relationship in result.relationships
            ],
            'assumptions': list(result.assumptions),
            'unresolved_items': [
                {
                    'message': item.message,
                    'path': item.path.as_posix() if item.path else None,
                    'start_line': item.start_line,
                    'end_line': item.end_line,
                    'evidence_handle': item.evidence_handle,
                }
                for item in result.unresolved_items
            ],
            'manual_verification_surfaces': [
                {
                    'kind': surface.kind,
                    'description': surface.description,
                    'path': surface.path.as_posix(),
                    'start_line': surface.start_line,
                    'end_line': surface.end_line,
                    'evidence_handle': surface.evidence_handle,
                }
                for surface in getattr(result, 'manual_verification_surfaces', ())
            ],
            'snapshot': ChangeScopeMCPServer._snapshot_report(result.snapshot),
        }

    @staticmethod
    def _target_report(target: Any) -> dict[str, Any] | None:
        if target is None:
            return None
        return {
            'signature': target.signature,
            'path': target.path.as_posix(),
            'start_line': target.start_line,
            'end_line': target.end_line,
            'evidence_handle': target.evidence_handle,
            'language': target.language,
        }

    @staticmethod
    def _navigation_report(result: SourceNavigation) -> dict[str, Any]:
        return {
            'evidence_handle': result.evidence_handle,
            'path': result.path.as_posix(),
            'start_line': result.start_line,
            'end_line': result.end_line,
            'content': result.content,
            'truncated': result.truncated,
            'continuation_start_line': result.continuation_start_line,
            'continuation_start_column': result.continuation_start_column,
        }

    @staticmethod
    def _catalog_repository_report(repository: Any) -> dict[str, Any]:
        return {
            'repository_id': repository.repository_id,
            'repository_path': repository.repository_path.as_posix(),
            'git_commit': repository.git_commit,
            'working_tree_state': repository.working_tree_state,
        }

    @staticmethod
    def _mapping_report(mapping: CatalogMapping) -> dict[str, Any]:
        return {
            'source_repository_id': mapping.source_repository_id,
            'contract_kind': mapping.contract_kind,
            'contract_key': mapping.contract_key,
            'target_repository_id': mapping.target_repository_id,
            'target_contract_key': mapping.target_contract_key,
            'provenance': mapping.provenance,
        }


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        _index_tool_definition(),
        _impact_tool_definition(),
        _evidence_tool_definition(),
        _source_tool_definition(),
    ]


def _index_tool_definition() -> dict[str, Any]:
    return {
        'name': 'index_repository',
        'description': 'Explicitly build or refresh the local Repository Index.',
        'inputSchema': {
            'type': 'object',
            'additionalProperties': False,
            'properties': {
                'repository_id': {
                    'type': 'string',
                    'description': 'Configured repository ID.',
                },
            },
            'required': ['repository_id'],
        },
    }


def _impact_tool_definition() -> dict[str, Any]:
    return {
        'name': 'analyze_impact',
        'description': 'Analyze a configured repository using a Java symbol or SOAP operation target.',
        'inputSchema': {
            'type': 'object',
            'additionalProperties': False,
            'properties': {
                'repository_id': {'type': 'string'},
                'target': {'type': 'string', 'description': 'Java target in Class#method form.'},
                'soap': {
                    'type': 'object',
                    'additionalProperties': False,
                    'properties': {
                        'wsdl': {'type': 'string'},
                        'port_type': {'type': 'string'},
                        'operation': {'type': 'string'},
                    },
                    'required': ['wsdl', 'port_type', 'operation'],
                },
                'soap_wsdl': {'type': 'string'},
                'soap_port_type': {'type': 'string'},
                'soap_operation': {'type': 'string'},
                'profiles': {'type': 'array', 'items': {'type': 'string'}},
                'build_profiles': {'type': 'array', 'items': {'type': 'string'}},
                'runtime_profiles': {'type': 'array', 'items': {'type': 'string'}},
            },
            'required': ['repository_id'],
            'anyOf': [
                {'required': ['target']},
                {'required': ['soap']},
                {'required': ['soap_wsdl', 'soap_port_type', 'soap_operation']},
            ],
        },
    }


def _evidence_tool_definition() -> dict[str, Any]:
    return {
        'name': 'get_evidence',
        'description': 'Retrieve bounded source behind an existing Evidence Handle.',
        'inputSchema': {
            'type': 'object',
            'additionalProperties': False,
            'properties': {
                'repository_id': {'type': 'string'},
                'evidence_handle': {'type': 'string'},
                'context_lines': {'type': 'integer', 'minimum': 0},
                'max_characters': {'type': 'integer', 'minimum': 1},
                'enclosing_symbol': {'type': 'boolean'},
            },
            'required': ['repository_id', 'evidence_handle'],
        },
    }


def _source_tool_definition() -> dict[str, Any]:
    return {
        'name': 'read_source_range',
        'description': 'Read a bounded range from an indexed repository-relative source path.',
        'inputSchema': {
            'type': 'object',
            'additionalProperties': False,
            'properties': {
                'repository_id': {'type': 'string'},
                'path': {'type': 'string'},
                'start_line': {'type': 'integer', 'minimum': 1},
                'end_line': {'type': 'integer', 'minimum': 1},
                'start_column': {'type': 'integer', 'minimum': 0},
                'max_characters': {'type': 'integer', 'minimum': 1},
            },
            'required': ['repository_id', 'path', 'start_line', 'end_line'],
        },
    }


def run_stdio_server(
    *,
    repository_root: Path | None = None,
    workspace_root: Path | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> None:
    ChangeScopeMCPServer(
        MCPServerConfig(repository_root, workspace_root)
    ).run_stdio(stdin, stdout)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='changescope-mcp')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--repository-root', '--repository', dest='repository_root', type=Path)
    mode.add_argument('--workspace-root', '--workspace', dest='workspace_root', type=Path)
    parsed = parser.parse_args(arguments)
    run_stdio_server(
        repository_root=parsed.repository_root,
        workspace_root=parsed.workspace_root,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
