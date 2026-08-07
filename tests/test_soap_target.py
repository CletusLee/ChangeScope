from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from changescope.application import (
    ChangeScopeApplication,
    EvidenceRequest,
    ImpactRequest,
    IndexRequest,
)
from changescope.cli import main


class SOAPTargetTests(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_indexes_wsdl_and_xsd_facts_and_resolves_unique_soap_operation_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            wsdl_content = """<?xml version="1.0" encoding="UTF-8"?>
<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                  xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
                  xmlns:tns="http://example.org/orders"
                  targetNamespace="http://example.org/orders"
                  name="OrderService">
    <wsdl:types>
        <xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
                   targetNamespace="http://example.org/orders">
            <xs:element name="PlaceOrderRequest" type="tns:PlaceOrderType"/>
            <xs:complexType name="PlaceOrderType">
                <xs:sequence>
                    <xs:element name="orderId" type="xs:string"/>
                </xs:sequence>
            </xs:complexType>
        </xs:schema>
    </wsdl:types>

    <wsdl:message name="PlaceOrderInput">
        <wsdl:part name="parameters" element="tns:PlaceOrderRequest"/>
    </wsdl:message>

    <wsdl:portType name="OrderPortType">
        <wsdl:operation name="placeOrder">
            <wsdl:input message="tns:PlaceOrderInput"/>
        </wsdl:operation>
    </wsdl:portType>

    <wsdl:binding name="OrderBinding" type="tns:OrderPortType">
        <soap:binding style="document" transport="http://schemas.xmlsoap.org/soap/http"/>
        <wsdl:operation name="placeOrder">
            <soap:operation soapAction="http://example.org/orders/placeOrder"/>
        </wsdl:operation>
    </wsdl:binding>

    <wsdl:service name="OrderService">
        <wsdl:port name="OrderPort" binding="tns:OrderBinding">
            <soap:address location="http://localhost:8080/soap/OrderService"/>
        </wsdl:port>
    </wsdl:service>
</wsdl:definitions>
"""
            self._write(repository / "src/main/resources/wsdl/OrderService.wsdl", wsdl_content)

            application = ChangeScopeApplication()
            index_res = application.execute(IndexRequest(repository))
            self.assertGreater(len(index_res.soap_facts), 0)

            # Test unique resolution with Clark notation port-type
            impact_res = application.execute(
                ImpactRequest(
                    repository_root=repository,
                    soap_wsdl=Path("src/main/resources/wsdl/OrderService.wsdl"),
                    soap_port_type="{http://example.org/orders}OrderPortType",
                    soap_operation="placeOrder",
                )
            )

            self.assertEqual(impact_res.outcome, "resolved")
            self.assertIsNotNone(impact_res.target)
            self.assertEqual(
                impact_res.target.signature,
                "{http://example.org/orders}OrderPortType#placeOrder",
            )
            self.assertTrue(impact_res.target.evidence_handle.startswith("soap_wsdl:src/main/resources/wsdl/OrderService.wsdl:"))
            self.assertTrue(any("SOAP analysis ground truth" in a for a in impact_res.assumptions))

    def test_resolves_soap_target_using_local_port_type_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            wsdl_content = """<?xml version="1.0" encoding="UTF-8"?>
<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                  xmlns:tns="http://example.org/orders"
                  targetNamespace="http://example.org/orders">
    <wsdl:portType name="OrderPortType">
        <wsdl:operation name="placeOrder"/>
    </wsdl:portType>
</wsdl:definitions>
"""
            self._write(repository / "OrderService.wsdl", wsdl_content)

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            impact_res = application.execute(
                ImpactRequest(
                    repository_root=repository,
                    soap_wsdl=Path("OrderService.wsdl"),
                    soap_port_type="OrderPortType",
                    soap_operation="placeOrder",
                )
            )

            self.assertEqual(impact_res.outcome, "resolved")
            self.assertIsNotNone(impact_res.target)

    def test_reports_not_found_for_missing_wsdl_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            wsdl_content = """<?xml version="1.0" encoding="UTF-8"?>
<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                  targetNamespace="http://example.org/orders">
    <wsdl:portType name="OrderPortType">
        <wsdl:operation name="cancelOrder"/>
    </wsdl:portType>
</wsdl:definitions>
"""
            self._write(repository / "OrderService.wsdl", wsdl_content)

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            impact_res = application.execute(
                ImpactRequest(
                    repository_root=repository,
                    soap_wsdl=Path("OrderService.wsdl"),
                    soap_port_type="OrderPortType",
                    soap_operation="nonExistentOperation",
                )
            )

            self.assertEqual(impact_res.outcome, "not_found")
            self.assertIn("nonExistentOperation", impact_res.unresolved_items[0].message)

    def test_reports_ambiguous_when_multiple_matching_operations_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            wsdl_content = """<?xml version="1.0" encoding="UTF-8"?>
<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                  xmlns:ns1="http://example.org/ns1"
                  xmlns:ns2="http://example.org/ns2"
                  targetNamespace="http://example.org/orders">
    <wsdl:portType name="OrderPortType">
        <wsdl:operation name="process"/>
    </wsdl:portType>
    <wsdl:portType name="OrderPortType">
        <wsdl:operation name="process"/>
    </wsdl:portType>
</wsdl:definitions>
"""
            self._write(repository / "OrderService.wsdl", wsdl_content)

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            impact_res = application.execute(
                ImpactRequest(
                    repository_root=repository,
                    soap_wsdl=Path("OrderService.wsdl"),
                    soap_port_type="OrderPortType",
                    soap_operation="process",
                )
            )

            self.assertEqual(impact_res.outcome, "ambiguous")
            self.assertEqual(len(impact_res.candidates), 2)

    def test_handles_remote_and_unresolvable_imports_as_unresolved_items(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            wsdl_content = """<?xml version="1.0" encoding="UTF-8"?>
<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                  targetNamespace="http://example.org/orders">
    <wsdl:import namespace="http://external.org/schema" location="http://external.org/schema.wsdl"/>
    <wsdl:portType name="OrderPortType">
        <wsdl:operation name="placeOrder"/>
    </wsdl:portType>
</wsdl:definitions>
"""
            self._write(repository / "OrderService.wsdl", wsdl_content)

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            impact_res = application.execute(
                ImpactRequest(
                    repository_root=repository,
                    soap_wsdl=Path("OrderService.wsdl"),
                    soap_port_type="OrderPortType",
                    soap_operation="placeOrder",
                )
            )

            self.assertEqual(impact_res.outcome, "resolved")
            self.assertTrue(any("Remote or unresolvable" in item.message for item in impact_res.unresolved_items))

    def test_supports_bounded_evidence_navigation_for_soap_wsdl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            wsdl_content = """<?xml version="1.0" encoding="UTF-8"?>
<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                  targetNamespace="http://example.org/orders">
    <wsdl:portType name="OrderPortType">
        <wsdl:operation name="placeOrder"/>
    </wsdl:portType>
</wsdl:definitions>
"""
            self._write(repository / "OrderService.wsdl", wsdl_content)

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            nav_res = application.execute(
                EvidenceRequest(
                    repository_root=repository,
                    evidence_handle="soap_wsdl:OrderService.wsdl:4-4",
                    context_lines=1,
                )
            )

            self.assertIn("placeOrder", nav_res.content)
            self.assertEqual(nav_res.path, Path("OrderService.wsdl"))

    def test_refreshes_older_index_when_soap_schema_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "OrderService.wsdl",
                """<?xml version="1.0"?>
<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/" targetNamespace="http://example.org">
    <wsdl:portType name="OrderPortType">
        <wsdl:operation name="placeOrder"/>
    </wsdl:portType>
</wsdl:definitions>
""",
            )
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            # Simulate an older database by dropping soap_facts
            db_path = repository / ".changescope" / "index.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute("DROP TABLE soap_facts")
            conn.commit()
            conn.close()

            # Next impact request should trigger index refresh and succeed
            impact_res = application.execute(
                ImpactRequest(
                    repository_root=repository,
                    soap_wsdl=Path("OrderService.wsdl"),
                    soap_port_type="OrderPortType",
                    soap_operation="placeOrder",
                )
            )
            self.assertEqual(impact_res.outcome, "resolved")

    def test_cli_soap_target_invocation_and_json_parity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "OrderService.wsdl",
                """<?xml version="1.0"?>
<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/" targetNamespace="http://example.org">
    <wsdl:portType name="OrderPortType">
        <wsdl:operation name="placeOrder"/>
    </wsdl:portType>
</wsdl:definitions>
""",
            )
            cwd = Path.cwd()
            try:
                import os
                os.chdir(repository)

                out = io.StringIO()
                with redirect_stdout(out):
                    code = main(["index"])
                self.assertEqual(code, 0)
                self.assertIn("SOAP Contract Evidence:", out.getvalue())

                out = io.StringIO()
                with redirect_stdout(out):
                    code = main([
                        "impact",
                        "--soap-wsdl", "OrderService.wsdl",
                        "--soap-port-type", "OrderPortType",
                        "--soap-operation", "placeOrder",
                        "--format", "json",
                    ])
                self.assertEqual(code, 0)
                report = json.loads(out.getvalue())
                self.assertEqual(report["outcome"], "resolved")
                self.assertEqual(report["target"]["signature"], "{http://example.org}OrderPortType#placeOrder")
            finally:
                os.chdir(cwd)

    def test_cli_rejects_mixed_java_and_soap_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            cwd = Path.cwd()
            try:
                import os
                os.chdir(repository)

                out = io.StringIO()
                with patch("sys.stderr", new=io.StringIO()):
                    with self.assertRaises(SystemExit):
                        main([
                            "impact",
                            "OrderService#placeOrder",
                            "--soap-wsdl", "OrderService.wsdl",
                            "--soap-port-type", "OrderPortType",
                            "--soap-operation", "placeOrder",
                        ])
            finally:
                os.chdir(cwd)
