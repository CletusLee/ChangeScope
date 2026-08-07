from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from changescope.application import (
    ChangeScopeApplication,
    EvidenceRequest,
    ImpactRequest,
    IndexRequest,
)
from changescope.cli import main


class SOAPContractsAndEndpointsTests(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_follows_nested_wsdl_and_xsd_imports_with_cyclic_and_remote_safety(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)

            # Main WSDL importing schema A
            self._write(
                root / "wsdl/order_service.wsdl",
                """<?xml version="1.0" encoding="UTF-8"?>
                <definitions xmlns="http://schemas.xmlsoap.org/wsdl/"
                             xmlns:xs="http://www.w3.org/2001/XMLSchema"
                             xmlns:tns="http://example.org/orders"
                             targetNamespace="http://example.org/orders">
                    <import namespace="http://example.org/orders/types" location="../schema/schema_a.xsd"/>
                    <import namespace="http://remote.example.org/external" location="http://remote.example.org/external.wsdl"/>
                    <message name="PlaceOrderRequest">
                        <part name="payload" element="tns:OrderElement"/>
                    </message>
                    <portType name="OrderPortType">
                        <operation name="placeOrder">
                            <input message="tns:PlaceOrderRequest"/>
                        </operation>
                    </portType>
                </definitions>""",
            )

            # Schema A includes Schema B (cyclic back to A)
            self._write(
                root / "schema/schema_a.xsd",
                """<?xml version="1.0" encoding="UTF-8"?>
                <xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
                           targetNamespace="http://example.org/orders">
                    <xs:include schemaLocation="schema_b.xsd"/>
                    <xs:element name="OrderElement" type="xs:string"/>
                </xs:schema>""",
            )

            self._write(
                root / "schema/schema_b.xsd",
                """<?xml version="1.0" encoding="UTF-8"?>
                <xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
                           targetNamespace="http://example.org/orders">
                    <xs:include schemaLocation="schema_a.xsd"/>
                    <xs:complexType name="OrderType"/>
                </xs:schema>""",
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
            self.assertGreaterEqual(len(impact_res.relationships), 2)

            # Evidence chains include WSDL operation -> message -> schema element
            payload_rel = next(r for r in impact_res.relationships if r.kind == "soap_payload" and "OrderElement" in r.caller)
            self.assertEqual(payload_rel.confidence, "high")
            self.assertEqual(len(payload_rel.evidence_chain), 3)

            # Remote import is captured as UnresolvedItem
            unresolved = next(u for u in impact_res.unresolved_items if "Remote or unresolvable" in u.message)
            self.assertIn("http://remote.example.org/external.wsdl", unresolved.message)

            # Evidence navigation works on soap_wsdl handle
            nav_res = app.execute(
                EvidenceRequest(
                    repository_root=root,
                    evidence_handle=payload_rel.evidence_handle,
                )
            )
            self.assertLessEqual(nav_res.start_line, 5)
            self.assertGreaterEqual(nav_res.end_line, 5)
            self.assertIn("OrderElement", nav_res.content)

    def test_connects_portable_javax_and_jakarta_soap_endpoints_and_method_signatures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)

            self._write(
                root / "wsdl/order_service.wsdl",
                """<?xml version="1.0" encoding="UTF-8"?>
                <definitions xmlns="http://schemas.xmlsoap.org/wsdl/"
                             targetNamespace="http://example.org/orders">
                    <message name="PlaceOrderRequest"/>
                    <portType name="OrderPortType">
                        <operation name="placeOrder">
                            <input message="tns:PlaceOrderRequest"/>
                        </operation>
                    </portType>
                </definitions>""",
            )

            # Service Endpoint Interface
            self._write(
                root / "src/main/java/example/OrderPortType.java",
                """package example;
                import javax.jws.WebService;
                import javax.jws.WebMethod;

                @WebService(targetNamespace = "http://example.org/orders")
                public interface OrderPortType {
                    @WebMethod(operationName = "placeOrder")
                    void placeOrder();
                }""",
            )

            # Implementation class
            self._write(
                root / "src/main/java/example/OrderServiceImpl.java",
                """package example;
                import javax.jws.WebService;

                @WebService(endpointInterface = "example.OrderPortType", targetNamespace = "http://example.org/orders")
                public class OrderServiceImpl implements OrderPortType {
                    public void placeOrder() {}
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

            endpoint_rels = [r for r in impact_res.relationships if r.kind == "soap_endpoint_implementation"]
            endpoint_callers = {r.caller for r in endpoint_rels}
            self.assertIn("example.OrderServiceImpl#placeOrder", endpoint_callers)
            self.assertTrue(all(r.confidence == "high" for r in endpoint_rels))

    def test_reports_code_first_endpoints_as_derived_contracts_at_medium_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)

            self._write(
                root / "src/main/java/example/CodeFirstEndpoint.java",
                """package example;
                import jakarta.jws.WebService;
                import jakarta.jws.WebMethod;

                @WebService(name = "CodeFirstPort", serviceName = "CodeFirstService")
                public class CodeFirstEndpoint {
                    @WebMethod
                    public void executeTask() {}
                }""",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(root))

            impact_res = app.execute(
                ImpactRequest(
                    repository_root=root,
                    target="CodeFirstEndpoint#executeTask",
                )
            )

            self.assertEqual(impact_res.outcome, "resolved")
            derived_rel = next(r for r in impact_res.relationships if r.kind == "soap_endpoint_implementation")
            self.assertEqual(derived_rel.confidence, "medium")
            self.assertIn("derived:", derived_rel.caller)
            self.assertTrue(any("derived contract" in a for a in impact_res.assumptions))

    def test_connects_ejb_soap_session_bean_to_unified_impact_neighborhood(self) -> None:
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

            # EJB Local Business Interface
            self._write(
                root / "src/main/java/example/OrderLocal.java",
                """package example;
                import javax.ejb.Local;
                @Local
                public interface OrderLocal {
                    void placeOrder();
                }""",
            )

            # EJB Session Bean exposed as SOAP Endpoint
            self._write(
                root / "src/main/java/example/OrderEjbBean.java",
                """package example;
                import javax.ejb.Stateless;
                import javax.jws.WebService;

                @Stateless
                @WebService(targetNamespace = "http://example.org/orders")
                public class OrderEjbBean implements OrderLocal {
                    public void placeOrder() {}
                }""",
            )

            # EJB Consumer
            self._write(
                root / "src/main/java/example/OrderClient.java",
                """package example;
                import javax.ejb.EJB;

                public class OrderClient {
                    @EJB
                    private OrderLocal orderBean;

                    public void submit() {
                        orderBean.placeOrder();
                    }
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
            # SOAP Endpoint relationship
            ep_rel = next(r for r in impact_res.relationships if r.kind == "soap_endpoint_implementation")
            self.assertEqual(ep_rel.caller, "example.OrderEjbBean#placeOrder")

            # EJB Client caller relationship
            client_rel = next(r for r in impact_res.relationships if r.caller == "example.OrderClient#submit")
            self.assertEqual(client_rel.confidence, "medium")

    def test_parses_web_service_descriptors_and_cli_output_parity(self) -> None:
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

            self._write(
                root / "src/main/java/example/OrderEjbBean.java",
                """package example;
                import javax.jws.WebService;

                @WebService(targetNamespace = "http://example.org/orders")
                public class OrderEjbBean {
                    public void placeOrder() {}
                }""",
            )

            self._write(
                root / "WEB-INF/webservices.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
                <webservices xmlns="http://java.sun.com/xml/ns/javaee">
                    <webservice-description>
                        <webservice-description-name>OrderService</webservice-description-name>
                        <port-component>
                            <port-component-name>OrderPort</port-component-name>
                            <service-endpoint-interface>example.OrderPortType</service-endpoint-interface>
                            <service-impl-bean>
                                <ejb-link>example.OrderEjbBean</ejb-link>
                            </service-impl-bean>
                        </port-component>
                    </webservice-description>
                </webservices>""",
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
                self.assertTrue(any(r["kind"] == "soap_configuration" for r in report["relationships"]))
            finally:
                os.chdir(cwd)
