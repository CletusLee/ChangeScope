import tempfile
import unittest
from pathlib import Path

from changescope.application import (
    ChangeScopeApplication,
    ImpactRequest,
    IndexRequest,
)


class TestQuarkusBoundaryEvidence(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_quarkus_main_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-core</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/MainApp.java",
                "package com.example;\n"
                "import io.quarkus.runtime.annotations.QuarkusMain;\n"
                "import io.quarkus.runtime.QuarkusApplication;\n"
                "@QuarkusMain\n"
                "public class MainApp implements QuarkusApplication {\n"
                "    @Override\n"
                "    public int run(String... args) { return 0; }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "MainApp#run"))
            self.assertEqual(result.outcome, "resolved")
            main_rels = [r for r in result.relationships if r.kind == "quarkus_main_entry" or "Quarkus Main" in str(r)]
            self.assertTrue(len(main_rels) > 0)

    def test_startup_lifecycle_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-core</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/InitBean.java",
                "package com.example;\n"
                "import io.quarkus.runtime.StartupEvent;\n"
                "import jakarta.enterprise.event.Observes;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "@ApplicationScoped\n"
                "public class InitBean {\n"
                "    void onStart(@Observes StartupEvent ev) {}\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "InitBean#onStart"))
            self.assertEqual(result.outcome, "resolved")
            life_rels = [r for r in result.relationships if r.kind == "quarkus_lifecycle" or "Startup" in str(r)]
            self.assertTrue(len(life_rels) > 0)

    def test_panache_persistence_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-hibernate-orm-panache</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/PersonRepository.java",
                "package com.example;\n"
                "import io.quarkus.hibernate.orm.panache.PanacheRepository;\n"
                "import jakarta.enterprise.context.ApplicationScoped;\n"
                "@ApplicationScoped\n"
                "public class PersonRepository implements PanacheRepository<Object> {\n"
                "    public void customQuery() {}\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "PersonRepository#customQuery"))
            self.assertEqual(result.outcome, "resolved")
            pers_rels = [r for r in result.relationships if r.kind == "quarkus_persistence" or "Panache" in str(r)]
            self.assertTrue(len(pers_rels) > 0)
            unresolved = [u for u in result.unresolved_items if "Panache" in u.message or "CRUD" in u.message or "persistence" in u.message.lower()]
            self.assertTrue(len(unresolved) > 0)

    def test_messaging_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-smallrye-reactive-messaging</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/PriceConsumer.java",
                "package com.example;\n"
                "import org.eclipse.microprofile.reactive.messaging.Incoming;\n"
                "public class PriceConsumer {\n"
                "    @Incoming(\"prices\")\n"
                "    public void process(double price) {}\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "PriceConsumer#process"))
            self.assertEqual(result.outcome, "resolved")
            msg_unresolved = [u for u in result.unresolved_items if "Messaging" in u.message or "Incoming" in u.message or "reactive" in u.message.lower()]
            self.assertTrue(len(msg_unresolved) > 0)

    def test_scheduler_graphql_grpc_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-scheduler</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/CronTask.java",
                "package com.example;\n"
                "import io.quarkus.scheduler.Scheduled;\n"
                "public class CronTask {\n"
                "    @Scheduled(every = \"10s\")\n"
                "    public void runCron() {}\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "CronTask#runCron"))
            self.assertEqual(result.outcome, "resolved")
            sched_unresolved = [u for u in result.unresolved_items if "Scheduled" in u.message or "Scheduler" in u.message]
            self.assertTrue(len(sched_unresolved) > 0)

    def test_kotlin_scala_coverage_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-core</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/kotlin/com/example/App.kt",
                "package com.example\nfun main() {}\n",
            )
            self._write(
                repository / "src/main/java/com/example/JavaService.java",
                "package com.example;\n"
                "public class JavaService {\n"
                "    public void serve() {}\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "JavaService#serve"))
            self.assertEqual(result.outcome, "resolved")
            gap_unresolved = [u for u in result.unresolved_items if "Kotlin" in u.message or "Scala" in u.message or "coverage gap" in u.message.lower()]
            self.assertTrue(len(gap_unresolved) > 0)


if __name__ == "__main__":
    unittest.main()
