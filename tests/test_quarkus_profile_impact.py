from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from changescope.application import (
    ChangeScopeApplication,
    EvidenceRequest,
    ImpactRequest,
    IndexRequest,
)
from changescope.cli import main


class TestQuarkusProfileImpact(unittest.TestCase):

    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_quarkus_config_property_consumer_and_source_matching(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/GreetingResource.java",
                "package com.example;\n"
                "import org.eclipse.microprofile.config.inject.ConfigProperty;\n"
                "public class GreetingResource {\n"
                "    @ConfigProperty(name = \"greeting.message\", defaultValue = \"hello\")\n"
                "    String message;\n"
                "    public String hello() { return message; }\n"
                "}\n",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "greeting.message=Welcome to Quarkus!\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "GreetingResource#hello"))

            self.assertEqual(result.outcome, "resolved")
            property_kinds = [r.kind for r in result.relationships if "property" in r.kind]
            self.assertIn("property_consumer", property_kinds)
            self.assertIn("property_source", property_kinds)

            consumer = next(r for r in result.relationships if r.kind == "property_consumer")
            self.assertEqual(consumer.caller, "greeting.message")
            self.assertFalse(consumer.conditional)

            source = next(r for r in result.relationships if r.kind == "property_source")
            self.assertEqual(source.caller, "greeting.message")
            self.assertEqual(source.evidence_handle, f"quarkus_config:src/main/resources/application.properties:1-1")

    def test_quarkus_inline_profile_properties_conditional_without_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/GreetingResource.java",
                "package com.example;\n"
                "import org.eclipse.microprofile.config.inject.ConfigProperty;\n"
                "public class GreetingResource {\n"
                "    @ConfigProperty(name = \"greeting.message\")\n"
                "    String message;\n"
                "    public String hello() { return message; }\n"
                "}\n",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "%dev.greeting.message=Dev Hello\n"
                "%prod.greeting.message=Prod Hello\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            # Without profile selection -> conditional
            result_no_profile = app.execute(ImpactRequest(repository, "GreetingResource#hello"))
            sources_no_prof = [r for r in result_no_profile.relationships if r.kind == "property_source"]
            self.assertTrue(any(s.conditional for s in sources_no_prof))
            self.assertTrue(any("profile-specific configuration remains conditional" in a for a in result_no_profile.assumptions))

            # With build profile selection -> active and non-conditional for dev
            result_dev = app.execute(
                ImpactRequest(repository, "GreetingResource#hello", build_profiles=("dev",))
            )
            dev_sources = [r for r in result_dev.relationships if r.kind == "property_source" and r.profile == "dev"]
            self.assertTrue(len(dev_sources) > 0)
            self.assertFalse(dev_sources[0].conditional)
            self.assertTrue(any("Quarkus build profiles: dev" in a for a in result_dev.assumptions))

    def test_quarkus_profile_file_properties_and_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/ConfigBean.java",
                "package com.example;\n"
                "import org.eclipse.microprofile.config.inject.ConfigProperty;\n"
                "public class ConfigBean {\n"
                "    @ConfigProperty(name = \"app.title\")\n"
                "    String title;\n"
                "    public String getTitle() { return title; }\n"
                "}\n",
            )
            self._write(
                repository / "src/main/resources/application-staging.properties",
                "app.title=Staging Title\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result_staging = app.execute(
                ImpactRequest(repository, "ConfigBean#getTitle", runtime_profiles=("staging",))
            )
            staging_sources = [r for r in result_staging.relationships if r.kind == "property_source"]
            self.assertEqual(len(staging_sources), 1)
            self.assertEqual(staging_sources[0].profile, "staging")
            self.assertFalse(staging_sources[0].conditional)
            self.assertTrue(any("Quarkus runtime profiles: staging" in a for a in result_staging.assumptions))

    def test_quarkus_build_time_vs_runtime_profile_independence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/DatabaseService.java",
                "package com.example;\n"
                "import org.eclipse.microprofile.config.inject.ConfigProperty;\n"
                "public class DatabaseService {\n"
                "    @ConfigProperty(name = \"quarkus.datasource.db-kind\")\n"
                "    String dbKind;\n"
                "    public void connect() {}\n"
                "}\n",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "%dev.quarkus.datasource.db-kind=h2\n"
                "%prod.quarkus.datasource.db-kind=postgresql\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            # Build profile 'dev' fixes db-kind to h2. Runtime profile 'prod' must NOT override build profile selection for build-time keys.
            result = app.execute(
                ImpactRequest(
                    repository,
                    "DatabaseService#connect",
                    build_profiles=("dev",),
                    runtime_profiles=("prod",),
                )
            )

            dev_sources = [r for r in result.relationships if r.kind == "property_source" and r.profile == "dev"]
            self.assertTrue(len(dev_sources) > 0)
            self.assertFalse(dev_sources[0].conditional)

    def test_cli_quarkus_profile_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/AppService.java",
                "package com.example;\n"
                "import org.eclipse.microprofile.config.inject.ConfigProperty;\n"
                "public class AppService {\n"
                "    @ConfigProperty(name = \"app.name\")\n"
                "    String name;\n"
                "    public void run() {}\n"
                "}\n",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "%test.app.name=TestApp\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            with patch("sys.stdout", new=StringIO()) as stdout:
                with patch("pathlib.Path.cwd", return_value=repository):
                    exit_code = main([
                        "impact",
                        "AppService#run",
                        "--build-profile", "test",
                        "--runtime-profile", "test",
                        "--format", "json",
                    ])
            self.assertEqual(exit_code, 0)
            json_output = json.loads(stdout.getvalue())
            self.assertEqual(json_output["outcome"], "resolved")
            self.assertTrue(any("Quarkus build profiles: test" in a for a in json_output["assumptions"]))
            self.assertTrue(any("Quarkus runtime profiles: test" in a for a in json_output["assumptions"]))

    def test_quarkus_config_evidence_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "src/main/resources/application.properties",
                "quarkus.http.port=8080\n",
            )
            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))
            handle = "quarkus_config:src/main/resources/application.properties:1-1"
            nav = app.execute(EvidenceRequest(repository, handle, context_lines=0))
            self.assertIn("quarkus.http.port=8080", nav.content)


if __name__ == "__main__":
    unittest.main()
