from __future__ import annotations

import tempfile
import unittest
import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from changescope.application import ChangeScopeApplication, EvidenceRequest, ImpactRequest, IndexRequest
from changescope.cli import main


class SpringImpactReportTests(unittest.TestCase):
    def test_reports_a_spring_configuration_boundary_for_a_stereotype_component(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import org.springframework.stereotype.Service;

@Service
class OrderService {
    void placeOrder() {}
}
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertEqual(result.outcome, "resolved")
            self.assertEqual(
                [
                    (relationship.kind, relationship.caller, relationship.confidence)
                    for relationship in result.relationships
                    if relationship.kind == "spring_configuration_boundary"
                ],
                [("spring_configuration_boundary", "example.OrderService", "medium")],
            )
            boundary = next(
                relationship
                for relationship in result.relationships
                if relationship.kind == "spring_configuration_boundary"
            )
            self.assertEqual(
                boundary.evidence_handle,
                "spring:src/main/java/example/OrderService.java:4-4",
            )

    def test_reports_a_unique_spring_bean_consumer_from_field_injection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import org.springframework.stereotype.Service;
@Service
class OrderService { void placeOrder() {} }
""",
            )
            self._write(
                repository / "src/main/java/example/OrderController.java",
                """package example;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Controller;
@Controller
class OrderController {
    @Autowired
    private OrderService service;
}
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertEqual(
                [
                    (relationship.kind, relationship.caller, relationship.confidence)
                    for relationship in result.relationships
                    if relationship.kind == "bean_consumer"
                ],
                [("bean_consumer", "example.OrderController", "high")],
            )

    def test_reports_property_consumption_and_matching_source_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
@Service
class OrderService {
    @Value("${orders.timeout}")
    private String timeout;
    void placeOrder() {}
}
""",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "orders.timeout=30\n",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertEqual(
                [relationship.kind for relationship in result.relationships if relationship.kind.startswith("property_")],
                ["property_consumer", "property_source"],
            )
            property_source = next(
                relationship for relationship in result.relationships if relationship.kind == "property_source"
            )
            self.assertEqual(property_source.caller, "orders.timeout")
            self.assertEqual(
                property_source.evidence_handle,
                "spring:src/main/resources/application.properties:1-1",
            )

    def test_reports_profile_specific_property_evidence_as_conditional_without_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Service;
@Service
@ConfigurationProperties(prefix = "orders")
class OrderService { void placeOrder() {} }
""",
            )
            self._write(repository / "src/main/resources/application.yml", "orders:\n  timeout: 30\n")
            self._write(repository / "src/main/resources/application-prod.yml", "orders:\n  timeout: 60\n")
            self._write(repository / "src/main/resources/application-test.yml", "orders:\n  timeout: 45\n")

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            sources = [
                relationship for relationship in result.relationships
                if relationship.kind == "property_source"
            ]
            self.assertEqual(len(sources), 3)
            self.assertEqual(
                {relationship.profile for relationship in sources},
                {None, "prod", "test"},
            )
            self.assertTrue(next(relationship for relationship in sources if relationship.profile == "prod").conditional)
            self.assertTrue(next(relationship for relationship in sources if relationship.profile == "test").conditional)

            selected = application.execute(
                ImpactRequest(repository, "OrderService#placeOrder", profiles=("prod",))
            )
            selected_sources = [
                relationship for relationship in selected.relationships
                if relationship.kind == "property_source"
            ]
            self.assertEqual(len(selected_sources), 2)
            self.assertFalse(next(relationship for relationship in selected_sources if relationship.profile == "prod").conditional)
            self.assertNotIn("test", {relationship.profile for relationship in selected_sources})

            selected_both = application.execute(
                ImpactRequest(repository, "OrderService#placeOrder", profiles=("prod", "test"))
            )
            selected_both_sources = [
                relationship for relationship in selected_both.relationships
                if relationship.kind == "property_source"
            ]
            self.assertEqual(len(selected_both_sources), 3)
            self.assertEqual(
                {relationship.profile for relationship in selected_both_sources},
                {None, "prod", "test"},
            )
            self.assertFalse(any(relationship.conditional for relationship in selected_both_sources))

    def test_cli_text_exposes_selected_profile_metadata_like_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Service;
@Service
@ConfigurationProperties(prefix = "orders")
class OrderService { void placeOrder() {} }
""",
            )
            self._write(repository / "src/main/resources/application.yml", "orders:\n  timeout: 30\n")
            self._write(repository / "src/main/resources/application-prod.yml", "orders:\n  timeout: 60\n")
            ChangeScopeApplication().execute(IndexRequest(repository))

            with patch("changescope.cli.Path.cwd", return_value=repository):
                with patch("sys.stdout", new_callable=StringIO) as json_output:
                    main([
                        "impact", "OrderService#placeOrder",
                        "--profile", "prod", "--format", "json",
                    ])
                with patch("sys.stdout", new_callable=StringIO) as text_output:
                    main([
                        "impact", "OrderService#placeOrder",
                        "--profile", "prod",
                    ])

            report = json.loads(json_output.getvalue())
            selected_source = next(
                relationship
                for relationship in report["relationships"]
                if relationship["kind"] == "property_source"
                and relationship["profile"] == "prod"
            )
            self.assertEqual(selected_source["conditional"], False)
            self.assertIn("profile: prod", text_output.getvalue())

    def test_reports_profile_expressions_as_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Service;
@Service
@Profile({"prod", "!test"})
class OrderService { void placeOrder() {} }
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertTrue(
                any("profile expression" in item.message.lower() for item in result.unresolved_items)
            )
            self.assertFalse(
                any(
                    relationship.kind == "spring_configuration_boundary"
                    for relationship in result.relationships
                )
            )

    def test_reports_conditional_configuration_as_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import org.springframework.stereotype.Service;
@Service
class OrderService { void placeOrder() {} }
""",
            )
            self._write(
                repository / "src/main/java/example/AppConfig.java",
                """package example;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;
@Configuration
@ConditionalOnProperty(name = "orders.enabled")
class AppConfig {}
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertTrue(
                any("conditional" in item.message.lower() for item in result.unresolved_items)
            )

    def test_reports_a_java_bean_factory_as_configuration_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
class OrderService { void placeOrder() {} }
""",
            )
            self._write(
                repository / "src/main/java/example/AppConfig.java",
                """package example;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
@Configuration
class AppConfig {
    @Bean
    OrderService orderService() { return new OrderService(); }
}
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertTrue(
                any(
                    relationship.kind == "spring_configuration_boundary"
                    and relationship.caller == "example.AppConfig#orderService"
                    for relationship in result.relationships
                )
            )

    def test_reports_an_explicit_xml_bean_reference_as_a_bean_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                "package example; class OrderService { void placeOrder() {} }\n",
            )
            self._write(
                repository / "src/main/java/example/OrderController.java",
                "package example; class OrderController {}\n",
            )
            self._write(
                repository / "src/main/resources/application-context.xml",
                """<beans>
  <bean id="orderService" class="example.OrderService" />
  <bean id="orderController" class="example.OrderController">
    <property name="service" ref="orderService" />
  </bean>
</beans>
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertTrue(
                any(
                    relationship.kind == "bean_consumer"
                    and relationship.caller == "example.OrderController"
                    for relationship in result.relationships
                )
            )

    def test_cli_exposes_xml_relationships_in_text_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                "package example; class OrderService { void placeOrder() {} }\n",
            )
            self._write(
                repository / "src/main/java/example/OrderController.java",
                "package example; class OrderController {}\n",
            )
            self._write(
                repository / "src/main/resources/application-context.xml",
                """<beans>
  <bean id="orderService" class="example.OrderService" />
  <bean id="orderController" class="example.OrderController">
    <property name="service" ref="orderService" />
  </bean>
</beans>
""",
            )
            ChangeScopeApplication().execute(IndexRequest(repository))

            with patch("changescope.cli.Path.cwd", return_value=repository):
                with patch("sys.stdout", new_callable=StringIO) as json_output:
                    main([
                        "impact", "OrderService#placeOrder", "--format", "json",
                    ])
                with patch("sys.stdout", new_callable=StringIO) as text_output:
                    main(["impact", "OrderService#placeOrder"])

            report = json.loads(json_output.getvalue())
            relationship = next(
                relationship
                for relationship in report["relationships"]
                if relationship["kind"] == "bean_consumer"
            )
            self.assertEqual(relationship["caller"], "example.OrderController")
            self.assertIn(
                relationship["evidence_handle"],
                text_output.getvalue(),
            )

    def test_reports_xml_property_placeholder_consumption_with_local_source_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                "package example; class OrderService { void placeOrder() {} }\n",
            )
            self._write(
                repository / "src/main/resources/application-context.xml",
                """<beans>
  <bean id="orderService"
        class="example.OrderService">
    <property name="timeout" value="${orders.timeout}" />
  </bean>
  <context:property-placeholder location="classpath:declared.properties" />
</beans>
""",
            )
            self._write(
                repository / "src/main/resources/declared.properties",
                "orders.timeout=30\n",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "orders.timeout=99\n",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertTrue(any(relationship.kind == "property_consumer" for relationship in result.relationships))
            property_sources = [
                relationship
                for relationship in result.relationships
                if relationship.kind == "property_source"
            ]
            self.assertEqual(
                [relationship.path.as_posix() for relationship in property_sources],
                ["src/main/resources/declared.properties"],
            )

    def test_reports_xml_profile_expressions_as_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                "package example; class OrderService { void placeOrder() {} }\n",
            )
            self._write(
                repository / "src/main/resources/application-context.xml",
                """<beans profile="prod &amp; !test">
  <bean id="orderService" class="example.OrderService" />
</beans>
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertTrue(
                any("profile expression" in item.message.lower() for item in result.unresolved_items)
            )

    def test_reports_spring_test_context_evidence_for_a_changed_component(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import org.springframework.stereotype.Service;
@Service
class OrderService { void placeOrder() {} }
""",
            )
            self._write(
                repository / "src/test/java/example/OrderServiceTest.java",
                """package example;
import org.springframework.boot.test.context.SpringBootTest;
@SpringBootTest(classes = OrderService.class)
class OrderServiceTest {}
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertTrue(
                any(
                    relationship.kind == "spring_test"
                    and relationship.caller == "example.OrderServiceTest"
                    and relationship.confidence == "medium"
                    for relationship in result.relationships
                )
            )

    def test_leaves_ambiguous_spring_bean_candidates_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                "package example; class OrderService { void placeOrder() {} }\n",
            )
            self._write(
                repository / "src/main/java/example/AppConfig.java",
                """package example;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
@Configuration
class AppConfig {
    @Bean OrderService first() { return new OrderService(); }
    @Bean OrderService second() { return new OrderService(); }
}
""",
            )
            self._write(
                repository / "src/main/java/example/OrderController.java",
                """package example;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Controller;
@Controller
class OrderController {
    @Autowired
    private OrderService service;
}
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertFalse(any(relationship.kind == "bean_consumer" for relationship in result.relationships))
            self.assertTrue(any("multiple local bean candidates" in item.message for item in result.unresolved_items))

    def test_leaves_qualifier_and_primary_selection_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import org.springframework.stereotype.Service;
@Service
class OrderService { void placeOrder() {} }
""",
            )
            self._write(
                repository / "src/main/java/example/AppConfig.java",
                """package example;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
@Configuration
class AppConfig {
    @Bean
    @Primary
    OrderService primaryOrderService() { return new OrderService(); }
}
""",
            )
            self._write(
                repository / "src/main/java/example/OrderController.java",
                """package example;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Controller;
@Controller
class OrderController {
    @Autowired
    @Qualifier("primaryOrderService")
    private OrderService service;
}
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertFalse(any(relationship.kind == "bean_consumer" for relationship in result.relationships))
            messages = [item.message.lower() for item in result.unresolved_items]
            self.assertTrue(any("qualifier" in message for message in messages))
            self.assertTrue(any("primary" in message for message in messages))

    def test_leaves_collection_injection_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import org.springframework.stereotype.Service;
@Service
class OrderService { void placeOrder() {} }
""",
            )
            self._write(
                repository / "src/main/java/example/OrderController.java",
                """package example;
import java.util.List;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Controller;
@Controller
class OrderController {
    @Autowired
    private List<OrderService> services;
}
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertFalse(any(relationship.kind == "bean_consumer" for relationship in result.relationships))
            self.assertTrue(
                any("collection injection" in item.message.lower() for item in result.unresolved_items)
            )

    def test_leaves_spring_proxy_annotations_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
@Service
@Transactional
class OrderService { void placeOrder() {} }
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertTrue(
                any("proxy" in item.message.lower() for item in result.unresolved_items)
            )

    def test_leaves_constructor_qualifier_and_collection_injection_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import org.springframework.stereotype.Service;
@Service
class OrderService { void placeOrder() {} }
""",
            )
            self._write(
                repository / "src/main/java/example/OrderController.java",
                """package example;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Controller;
@Controller
class OrderController {
    OrderController(@Qualifier("orderService") OrderService service) {}
}
""",
            )
            self._write(
                repository / "src/main/java/example/BatchController.java",
                """package example;
import java.util.List;
import org.springframework.stereotype.Controller;
@Controller
class BatchController {
    BatchController(List<OrderService> services) {}
}
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertFalse(any(relationship.kind == "bean_consumer" for relationship in result.relationships))
            messages = [item.message.lower() for item in result.unresolved_items]
            self.assertTrue(any("qualifier" in message for message in messages))
            self.assertTrue(any("collection injection" in message for message in messages))

    def test_reports_spring_junit_test_loading_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import org.springframework.stereotype.Service;
@Service
class OrderService { void placeOrder() {} }
""",
            )
            self._write(
                repository / "src/test/java/example/OrderServiceTest.java",
                """package example;
import org.springframework.test.context.junit.jupiter.SpringJUnitConfig;
@SpringJUnitConfig(OrderService.class)
class OrderServiceTest {}
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertTrue(
                any(
                    relationship.kind == "spring_test"
                    and relationship.caller == "example.OrderServiceTest"
                    and relationship.confidence == "medium"
                    for relationship in result.relationships
                )
            )

    def test_reports_configuration_proxy_enablement_as_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import org.springframework.stereotype.Service;
@Service
class OrderService { void placeOrder() {} }
""",
            )
            self._write(
                repository / "src/main/java/example/AppConfig.java",
                """package example;
import org.springframework.context.annotation.Configuration;
import org.springframework.transaction.annotation.EnableTransactionManagement;
@Configuration
@EnableTransactionManagement
class AppConfig {}
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertTrue(
                any("proxy" in item.message.lower() for item in result.unresolved_items)
            )

    def test_reports_unsupported_component_scan_as_an_unresolved_item(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                "package example; class OrderService { void placeOrder() {} }\n",
            )
            self._write(
                repository / "src/main/java/example/AppConfig.java",
                """package example;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.Configuration;
@Configuration
@ComponentScan("example")
class AppConfig {}
""",
            )

            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))
            result = application.execute(ImpactRequest(repository, "OrderService#placeOrder"))

            self.assertTrue(any("component scanning" in item.message for item in result.unresolved_items))

    def test_navigates_spring_configuration_evidence_with_the_public_evidence_seam(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            source = repository / "src/main/resources/application.properties"
            self._write(source, "orders.timeout=30\n")
            application = ChangeScopeApplication()
            application.execute(IndexRequest(repository))

            result = application.execute(
                EvidenceRequest(repository, "spring:src/main/resources/application.properties:1-1")
            )

            self.assertEqual(result.content, "orders.timeout=30\n")

    def test_cli_accepts_a_repeatable_profile_and_exposes_conditional_fields_in_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            self._write(
                repository / "src/main/java/example/OrderService.java",
                """package example;
import org.springframework.stereotype.Service;
@Service
@org.springframework.boot.context.properties.ConfigurationProperties(prefix = "orders")
class OrderService { void placeOrder() {} }
""",
            )
            self._write(repository / "src/main/resources/application.yml", "orders:\n  timeout: 30\n")
            self._write(repository / "src/main/resources/application-prod.yml", "orders:\n  timeout: 60\n")
            ChangeScopeApplication().execute(IndexRequest(repository))

            with patch("changescope.cli.Path.cwd", return_value=repository):
                with patch("sys.stdout", new_callable=StringIO) as output:
                    exit_code = main(
                        [
                            "impact", "OrderService#placeOrder",
                            "--profile", "prod",
                            "--profile", "test",
                            "--format", "json",
                        ]
                    )

            report = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertIn("conditional", report["relationships"][0])
            self.assertIn("profile", report["relationships"][0])

    @staticmethod
    def _write(path: Path, contents: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
