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


class SOAPClientsPayloadsAndHandlersTests(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_traces_generated_and_injected_soap_clients(self) -> None:
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

            # Service Endpoint Interface
            self._write(
                root / "src/main/java/example/OrderPortType.java",
                """package example;
                import javax.jws.WebService;

                @WebService(targetNamespace = "http://example.org/orders")
                public interface OrderPortType {
                    void placeOrder();
                }""",
            )

            # Generated WebServiceClient view
            self._write(
                root / "src/main/java/example/OrderService.java",
                """package example;
                import javax.xml.ws.WebServiceClient;
                import javax.xml.ws.WebEndpoint;
                import javax.xml.ws.Service;

                @WebServiceClient(name = "OrderService", targetNamespace = "http://example.org/orders", wsdlLocation = "wsdl/order_service.wsdl")
                public class OrderService extends Service {
                    @WebEndpoint(name = "OrderPort")
                    public OrderPortType getOrderPort() {
                        return null;
                    }
                }""",
            )

            # Injected client consumer using @WebServiceRef
            self._write(
                root / "src/main/java/example/OrderConsumer.java",
                """package example;
                import javax.xml.ws.WebServiceRef;

                public class OrderConsumer {
                    @WebServiceRef
                    private OrderService orderService;

                    public void process() {
                        OrderPortType port = orderService.getOrderPort();
                        port.placeOrder();
                    }
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
            client_rels = [r for r in impact_res.relationships if r.kind in ("soap_client_call", "soap_endpoint_implementation")]
            self.assertTrue(len(client_rels) > 0)

    def test_connects_xml_payload_bindings_wrappers_and_faults(self) -> None:
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

            # Payload class with JAXB annotations
            self._write(
                root / "src/main/java/example/OrderPayload.java",
                """package example;
                import javax.xml.bind.annotation.XmlRootElement;
                import javax.xml.bind.annotation.XmlType;

                @XmlRootElement(name = "OrderElement", namespace = "http://example.org/orders")
                @XmlType(name = "OrderType", namespace = "http://example.org/orders")
                public class OrderPayload {
                    private String id;
                    public String getId() { return id; }
                }""",
            )

            # Fault class
            self._write(
                root / "src/main/java/example/OrderException.java",
                """package example;
                import javax.xml.ws.WebFault;

                @WebFault(name = "OrderFault", targetNamespace = "http://example.org/orders")
                public class OrderException extends Exception {
                }""",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(root))

            impact_res = app.execute(
                ImpactRequest(
                    repository_root=root,
                    target="OrderPayload#getId",
                )
            )

            self.assertEqual(impact_res.outcome, "resolved")

    def test_attaches_soap_handlers_and_jbossws_cxf_configuration(self) -> None:
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

            # Endpoint with HandlerChain
            self._write(
                root / "src/main/java/example/OrderServiceImpl.java",
                """package example;
                import javax.jws.WebService;
                import javax.jws.HandlerChain;

                @WebService(targetNamespace = "http://example.org/orders")
                @HandlerChain(file = "handler-chain.xml")
                public class OrderServiceImpl {
                    public void placeOrder() {}
                }""",
            )

            # JBossWS CXF config
            self._write(
                root / "WEB-INF/jbossws-cxf.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
                <beans xmlns="http://www.springframework.org/schema/beans"
                       xmlns:cxf="http://cxf.apache.org/core">
                    <cxf:bus>
                        <cxf:inInterceptors>
                            <bean class="example.LoggingInterceptor"/>
                        </cxf:inInterceptors>
                    </cxf:bus>
                </beans>""",
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
