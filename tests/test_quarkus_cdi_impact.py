from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from changescope.application import (
    ChangeScopeApplication,
    ImpactRequest,
    IndexRequest,
)


class TestQuarkusCDIImpact(unittest.TestCase):

    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_unique_cdi_injection_and_method_dispatch(self) -> None:
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
                "    @Override\n"
                "    public void processPayment(double amount) {}\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/OrderResource.java",
                "package com.example;\n"
                "import jakarta.inject.Inject;\n"
                "public class OrderResource {\n"
                "    @Inject\n"
                "    PaymentService paymentService;\n"
                "    public void checkout() {\n"
                "        paymentService.processPayment(99.99);\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "PaymentServiceImpl#processPayment"))
            self.assertEqual(result.outcome, "resolved")
            callers = [r for r in result.relationships if r.caller == "com.example.OrderResource#checkout"]
            self.assertTrue(len(callers) > 0)
            self.assertEqual(callers[0].confidence, "high")

    def test_ambiguous_multiple_cdi_bean_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/NotificationService.java",
                "package com.example;\n"
                "public interface NotificationService {\n"
                "    void notifyUser(String msg);\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/EmailNotificationService.java",
                "package com.example;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "@ApplicationScoped\n"
                "public class EmailNotificationService implements NotificationService {\n"
                "    public void notifyUser(String msg) {}\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/SmsNotificationService.java",
                "package com.example;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "@ApplicationScoped\n"
                "public class SmsNotificationService implements NotificationService {\n"
                "    public void notifyUser(String msg) {}\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/UserResource.java",
                "package com.example;\n"
                "import jakarta.inject.Inject;\n"
                "public class UserResource {\n"
                "    @Inject\n"
                "    NotificationService notificationService;\n"
                "    public void register() {\n"
                "        notificationService.notifyUser(\"welcome\");\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "EmailNotificationService#notifyUser"))
            self.assertEqual(result.outcome, "resolved")
            self.assertTrue(
                any("ambiguous CDI injection" in u.message.lower() or "multiple" in u.message.lower() for u in result.unresolved_items)
            )

    def test_unsatisfied_cdi_injection_point(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/AuditResource.java",
                "package com.example;\n"
                "import jakarta.inject.Inject;\n"
                "public class AuditResource {\n"
                "    @Inject\n"
                "    com.example.AuditLogger auditLogger;\n"
                "    public void audit() {\n"
                "        auditLogger.log();\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "AuditResource#audit"))
            self.assertEqual(result.outcome, "resolved")
            self.assertTrue(
                any("has no matching cdi bean" in u.message.lower() or "unsatisfied" in u.message.lower() or "not present" in u.message.lower() for u in result.unresolved_items)
            )


if __name__ == "__main__":
    unittest.main()
