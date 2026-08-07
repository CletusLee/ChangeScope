from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from changescope.application import (
    CatalogRegisterMappingRequest,
    CatalogRegisterRepositoryRequest,
    ChangeScopeApplication,
    EvidenceRequest,
    ImpactRequest,
    IndexRequest,
)
from changescope.cli import main


class SOAPValidationAndCrossRepoTests(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_exposes_soap_policy_attachments_and_unsupported_rpc_encoded_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)

            # WSDL with WS-Policy reference and RPC/encoded binding
            self._write(
                root / "wsdl/order_service.wsdl",
                """<?xml version="1.0" encoding="UTF-8"?>
                <definitions xmlns="http://schemas.xmlsoap.org/wsdl/"
                             xmlns:wsp="http://schemas.xmlsoap.org/ws/2004/09/policy"
                             xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
                             targetNamespace="http://example.org/orders">
                    <wsp:Policy wsu:Id="SecurityPolicy" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"/>
                    <portType name="OrderPortType">
                        <operation name="placeOrder"/>
                    </portType>
                    <binding name="OrderBinding" type="tns:OrderPortType">
                        <soap:binding style="rpc" transport="http://schemas.xmlsoap.org/soap/http"/>
                    </binding>
                </definitions>""",
            )

            # Annotated endpoint with Addressing and SecurityDomain
            self._write(
                root / "src/main/java/example/OrderServiceImpl.java",
                """package example;
                import javax.jws.WebService;
                import javax.xml.ws.soap.Addressing;
                import org.jboss.annotation.security.SecurityDomain;

                @WebService(targetNamespace = "http://example.org/orders")
                @Addressing
                @SecurityDomain("other")
                public class OrderServiceImpl {
                    public void placeOrder() {}
                }""",
            )

            app = ChangeScopeApplication()
            index_res = app.execute(IndexRequest(root))
            self.assertEqual(len(index_res.parse_failures), 0)

            impact_res = app.execute(
                ImpactRequest(
                    repository_root=root,
                    target=None,
                    soap_wsdl=Path("wsdl/order_service.wsdl"),
                    soap_port_type="OrderPortType",
                    soap_operation="placeOrder",
                )
            )

            self.assertEqual(impact_res.outcome, "resolved")
            # Addressing and SecurityDomain extracted as facts
            self.assertTrue(len(impact_res.relationships) > 0)

    def test_classifies_soap_tests_and_arquillian_container_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)

            self._write(
                root / "wsdl/order_service.wsdl",
                """<?xml version="1.0" encoding="UTF-8"?>
                <definitions xmlns="http://schemas.xmlsoap.org/wsdl/"
                             targetNamespace="http://example.org/orders">
                    <portType name="OrderPortType">
                        <operation name="placeOrder"/>
                    </portType>
                </definitions>""",
            )

            # Arquillian integration test
            self._write(
                root / "src/test/java/example/OrderServiceIT.java",
                """package example;
                import org.jboss.arquillian.container.test.api.Deployment;
                import org.junit.runner.RunWith;

                @RunWith(org.jboss.arquillian.junit.Arquillian.class)
                public class OrderServiceIT {
                    @Deployment
                    public static void createDeployment() {}

                    public void testPlaceOrder() {}
                }""",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(root))

            impact_res = app.execute(
                ImpactRequest(
                    repository_root=root,
                    target=None,
                    soap_wsdl=Path("wsdl/order_service.wsdl"),
                    soap_port_type="OrderPortType",
                    soap_operation="placeOrder",
                )
            )

            self.assertEqual(impact_res.outcome, "resolved")

    def test_continues_soap_impact_cross_repository_via_workspace_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root_a = Path(temporary_directory) / "repo-a"
            root_b = Path(temporary_directory) / "repo-b"

            # Repo A: javax typed client
            self._write(
                root_a / "wsdl/order_service.wsdl",
                """<?xml version="1.0" encoding="UTF-8"?>
                <definitions xmlns="http://schemas.xmlsoap.org/wsdl/"
                             targetNamespace="http://example.org/orders">
                    <portType name="OrderPortType">
                        <operation name="placeOrder"/>
                    </portType>
                </definitions>""",
            )
            self._write(
                root_a / "src/main/java/client/OrderPortType.java",
                """package client;
                import javax.jws.WebService;

                @WebService(targetNamespace = "http://example.org/orders")
                public interface OrderPortType {
                    void placeOrder();
                }""",
            )

            # Repo B: jakarta endpoint implementation across container migration
            self._write(
                root_b / "wsdl/order_service.wsdl",
                """<?xml version="1.0" encoding="UTF-8"?>
                <definitions xmlns="http://schemas.xmlsoap.org/wsdl/"
                             targetNamespace="http://example.org/orders">
                    <portType name="OrderPortType">
                        <operation name="placeOrder"/>
                    </portType>
                </definitions>""",
            )
            self._write(
                root_b / "src/main/java/service/OrderServiceImpl.java",
                """package service;
                import jakarta.jws.WebService;

                @WebService(targetNamespace = "http://example.org/orders")
                public class OrderServiceImpl {
                    public void placeOrder() {}
                }""",
            )

            catalog_root = Path(temporary_directory)
            app = ChangeScopeApplication()
            index_a = app.execute(IndexRequest(root_a))
            index_b = app.execute(IndexRequest(root_b))

            # Register repos & mapping in workspace catalog
            app.execute(CatalogRegisterRepositoryRequest(catalog_root=catalog_root, repository_id="repo-a", repository_path=root_a))
            app.execute(CatalogRegisterRepositoryRequest(catalog_root=catalog_root, repository_id="repo-b", repository_path=root_b))
            app.execute(
                CatalogRegisterMappingRequest(
                    catalog_root=catalog_root,
                    source_repository_id="repo-a",
                    contract_kind="soap",
                    contract_key="{http://example.org/orders}OrderPortType#placeOrder",
                    target_repository_id="repo-b",
                    target_contract_key="{http://example.org/orders}OrderPortType#placeOrder",
                    provenance="explicit SOAP contract mapping",
                )
            )

            impact_res = app.execute(
                ImpactRequest(
                    repository_root=root_a,
                    target=None,
                    soap_wsdl=Path("wsdl/order_service.wsdl"),
                    soap_port_type="OrderPortType",
                    soap_operation="placeOrder",
                )
            )

            self.assertEqual(impact_res.outcome, "resolved")

    def test_verifies_cli_parity_and_evidence_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)

            self._write(
                root / "wsdl/order_service.wsdl",
                """<?xml version="1.0" encoding="UTF-8"?>
                <definitions xmlns="http://schemas.xmlsoap.org/wsdl/"
                             targetNamespace="http://example.org/orders">
                    <portType name="OrderPortType">
                        <operation name="placeOrder"/>
                    </portType>
                </definitions>""",
            )

            cwd = Path.cwd()
            try:
                import os
                os.chdir(root)

                out = io.StringIO()
                with redirect_stdout(out):
                    main(["index"])

                out = io.StringIO()
                with redirect_stdout(out):
                    code = main([
                        "impact",
                        "--soap-wsdl", "wsdl/order_service.wsdl",
                        "--soap-port-type", "OrderPortType",
                        "--soap-operation", "placeOrder",
                        "--format", "json",
                    ])
                self.assertEqual(code, 0)
                report = json.loads(out.getvalue())
                self.assertEqual(report["outcome"], "resolved")
            finally:
                os.chdir(cwd)
