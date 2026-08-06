from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from changescope.application import (
    ChangeScopeApplication,
    ImpactRequest,
    IndexRequest,
)


class TestQuarkusCDIAdvanced(unittest.TestCase):

    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_single_constructor_and_initializer_injection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/PaymentService.java",
                "package com.example;\n"
                "public interface PaymentService {\n"
                "    void processPayment(double amount);\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/PaymentServiceImpl.java",
                "package com.example;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "@ApplicationScoped\n"
                "public class PaymentServiceImpl implements PaymentService {\n"
                "    public void processPayment(double amount) {}\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/OrderService.java",
                "package com.example;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "@ApplicationScoped\n"
                "public class OrderService {\n"
                "    private final PaymentService paymentService;\n"
                "    public OrderService(PaymentService paymentService) {\n"
                "        this.paymentService = paymentService;\n"
                "    }\n"
                "    public void checkout() {\n"
                "        paymentService.processPayment(50.0);\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "PaymentServiceImpl#processPayment"))
            self.assertEqual(result.outcome, "resolved")
            callers = [r for r in result.relationships if r.caller == "com.example.OrderService#checkout"]
            self.assertTrue(len(callers) > 0)

    def test_producer_method_and_field_injection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/CustomClient.java",
                "package com.example;\n"
                "public class CustomClient {\n"
                "    public void execute() {}\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/ClientProducer.java",
                "package com.example;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "import jakarta.enterprise.inject.Produces;\n"
                "@ApplicationScoped\n"
                "public class ClientProducer {\n"
                "    @Produces\n"
                "    public CustomClient produceClient() {\n"
                "        return new CustomClient();\n"
                "    }\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/ClientConsumer.java",
                "package com.example;\n"
                "import jakarta.inject.Inject;\n"
                "public class ClientConsumer {\n"
                "    @Inject\n"
                "    CustomClient client;\n"
                "    public void run() {\n"
                "        client.execute();\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "ClientProducer#produceClient"))
            self.assertEqual(result.outcome, "resolved")
            callers = [r for r in result.relationships if r.caller == "com.example.ClientConsumer#run"]
            self.assertTrue(len(callers) > 0)

    def test_named_qualifier_disambiguation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/PaymentService.java",
                "package com.example;\n"
                "public interface PaymentService {\n"
                "    void pay();\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/CreditPaymentService.java",
                "package com.example;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "import jakarta.inject.Named;\n"
                "@ApplicationScoped\n"
                "@Named(\"credit\")\n"
                "public class CreditPaymentService implements PaymentService {\n"
                "    public void pay() {}\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/PaypalPaymentService.java",
                "package com.example;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "import jakarta.inject.Named;\n"
                "@ApplicationScoped\n"
                "@Named(\"paypal\")\n"
                "public class PaypalPaymentService implements PaymentService {\n"
                "    public void pay() {}\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/CheckoutResource.java",
                "package com.example;\n"
                "import jakarta.inject.Inject;\n"
                "import jakarta.inject.Named;\n"
                "public class CheckoutResource {\n"
                "    @Inject\n"
                "    @Named(\"credit\")\n"
                "    PaymentService paymentService;\n"
                "    public void doCheckout() {\n"
                "        paymentService.pay();\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "CreditPaymentService#pay"))
            self.assertEqual(result.outcome, "resolved")
            callers = [r for r in result.relationships if r.caller == "com.example.CheckoutResource#doCheckout"]
            self.assertTrue(len(callers) > 0)
            self.assertFalse(any("ambiguous" in u.message.lower() for u in result.unresolved_items))

    def test_build_profile_conditional_beans(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/ConfigService.java",
                "package com.example;\n"
                "public interface ConfigService {\n"
                "    String getEnv();\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/DevConfigService.java",
                "package com.example;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "import io.quarkus.arc.profile.IfBuildProfile;\n"
                "@ApplicationScoped\n"
                "@IfBuildProfile(\"dev\")\n"
                "public class DevConfigService implements ConfigService {\n"
                "    public String getEnv() { return \"dev\"; }\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/ProdConfigService.java",
                "package com.example;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "import io.quarkus.arc.profile.IfBuildProfile;\n"
                "@ApplicationScoped\n"
                "@IfBuildProfile(\"prod\")\n"
                "public class ProdConfigService implements ConfigService {\n"
                "    public String getEnv() { return \"prod\"; }\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/AppResource.java",
                "package com.example;\n"
                "import jakarta.inject.Inject;\n"
                "public class AppResource {\n"
                "    @Inject\n"
                "    ConfigService configService;\n"
                "    public void show() {\n"
                "        configService.getEnv();\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            # With --build-profile dev, DevConfigService is selected uniquely
            result = app.execute(
                ImpactRequest(repository, "DevConfigService#getEnv", build_profiles=("dev",))
            )
            self.assertEqual(result.outcome, "resolved")
            callers = [r for r in result.relationships if r.caller == "com.example.AppResource#show"]
            self.assertTrue(len(callers) > 0)
            self.assertFalse(any("ambiguous" in u.message.lower() for u in result.unresolved_items))

    def test_unresolved_dynamic_cdi_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/DynamicResource.java",
                "package com.example;\n"
                "import jakarta.inject.Inject;\n"
                "import jakarta.enterprise.inject.Instance;\n"
                "public class DynamicResource {\n"
                "    @Inject\n"
                "    Instance<com.example.PaymentService> paymentServices;\n"
                "    public void run() {\n"
                "        paymentServices.get().pay();\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "DynamicResource#run"))
            self.assertEqual(result.outcome, "resolved")
            self.assertTrue(
                any("dynamic" in u.message.lower() or "instance" in u.message.lower() or "unresolved" in u.message.lower() for u in result.unresolved_items)
            )


if __name__ == "__main__":
    unittest.main()
