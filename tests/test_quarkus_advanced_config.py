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


class TestQuarkusAdvancedConfig(unittest.TestCase):

    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_config_mapping_interface_indexing_and_impact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/ServerConfig.java",
                "package com.example;\n"
                "import io.smallrye.config.ConfigMapping;\n"
                "import io.smallrye.config.WithName;\n"
                "@ConfigMapping(prefix = \"server\")\n"
                "public interface ServerConfig {\n"
                "    String host();\n"
                "    @WithName(\"http-port\")\n"
                "    int port();\n"
                "    LogConfig log();\n"
                "    interface LogConfig {\n"
                "        boolean enabled();\n"
                "    }\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/ServerService.java",
                "package com.example;\n"
                "public class ServerService {\n"
                "    ServerConfig config;\n"
                "    public String getHost() {\n"
                "        return config.host();\n"
                "    }\n"
                "}\n",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "server.host=localhost\n"
                "server.http-port=8080\n"
                "server.log.enabled=true\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "ServerConfig#host"))
            self.assertEqual(result.outcome, "resolved")
            sources = [r for r in result.relationships if r.kind == "property_source"]
            self.assertTrue(any(s.caller == "server.host" for s in sources))

    def test_property_expression_evidence_chains(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/ApiService.java",
                "package com.example;\n"
                "import org.eclipse.microprofile.config.inject.ConfigProperty;\n"
                "public class ApiService {\n"
                "    @ConfigProperty(name = \"api.endpoint\")\n"
                "    String endpoint;\n"
                "    public String getEndpoint() { return endpoint; }\n"
                "}\n",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "base.url=http://localhost:8080\n"
                "api.endpoint=${base.url}/v1\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "ApiService#getEndpoint"))
            self.assertEqual(result.outcome, "resolved")
            sources = [r for r in result.relationships if r.kind == "property_source"]
            self.assertTrue(any(s.caller == "api.endpoint" for s in sources))
            
            # Verify multi-step Evidence Chain: consumer handle -> api.endpoint handle -> base.url handle
            expr_source = next((s for s in sources if s.caller == "base.url"), None)
            self.assertIsNotNone(expr_source)
            self.assertEqual(len(expr_source.evidence_chain), 3)

    def test_microprofile_config_properties_location(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/AppConfig.java",
                "package com.example;\n"
                "import org.eclipse.microprofile.config.inject.ConfigProperty;\n"
                "public class AppConfig {\n"
                "    @ConfigProperty(name = \"mp.app.name\")\n"
                "    String appName;\n"
                "    public String getAppName() { return appName; }\n"
                "}\n",
            )
            self._write(
                repository / "src/main/resources/META-INF/microprofile-config.properties",
                "mp.app.name=MicroProfileApp\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "AppConfig#getAppName"))
            self.assertEqual(result.outcome, "resolved")
            sources = [r for r in result.relationships if r.kind == "property_source"]
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0].caller, "mp.app.name")
            self.assertIn("META-INF/microprofile-config.properties", sources[0].evidence_handle)

    def test_parent_profile_and_multiple_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/FeatureService.java",
                "package com.example;\n"
                "import org.eclipse.microprofile.config.inject.ConfigProperty;\n"
                "public class FeatureService {\n"
                "    @ConfigProperty(name = \"app.feature\")\n"
                "    String feature;\n"
                "    public String getFeature() { return feature; }\n"
                "}\n",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "quarkus.config.profile.parent=common\n"
                "%common.app.feature=common-enabled\n"
                "%dev.app.feature=dev-enabled\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            # Query with build profile dev, common parent profile should also match
            result = app.execute(
                ImpactRequest(repository, "FeatureService#getFeature", build_profiles=("dev",))
            )
            sources = [r for r in result.relationships if r.kind == "property_source"]
            self.assertTrue(len(sources) > 0)
            self.assertTrue(any(s.profile == "dev" for s in sources))

    def test_unresolved_environment_variable_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/EnvService.java",
                "package com.example;\n"
                "import org.eclipse.microprofile.config.inject.ConfigProperty;\n"
                "public class EnvService {\n"
                "    @ConfigProperty(name = \"external.db.host\")\n"
                "    String dbHost;\n"
                "    public String getDbHost() { return dbHost; }\n"
                "}\n",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "external.db.host=${DB_HOST}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "EnvService#getDbHost"))
            self.assertEqual(result.outcome, "resolved")
            self.assertTrue(
                any("Environment-variable configuration override was not resolved" in u.message for u in result.unresolved_items)
            )


if __name__ == "__main__":
    unittest.main()
