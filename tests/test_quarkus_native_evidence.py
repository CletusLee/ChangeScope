import tempfile
import unittest
from pathlib import Path

from changescope.application import (
    ChangeScopeApplication,
    ImpactRequest,
    IndexRequest,
)


class TestQuarkusNativeEvidence(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_register_for_reflection_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-core</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/UserDTO.java",
                "package com.example;\n"
                "import io.quarkus.runtime.annotations.RegisterForReflection;\n"
                "@RegisterForReflection\n"
                "public class UserDTO {\n"
                "    private String name;\n"
                "    public String getName() { return name; }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "UserDTO#getName"))
            self.assertEqual(result.outcome, "resolved")
            nat_rels = [r for r in result.relationships if "native" in r.kind.lower() or "reflection" in str(r).lower()]
            self.assertTrue(len(nat_rels) > 0)

    def test_register_for_proxy_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-core</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/MyApi.java",
                "package com.example;\n"
                "public interface MyApi {\n"
                "    void execute();\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/ProxyConfig.java",
                "package com.example;\n"
                "import io.quarkus.runtime.annotations.RegisterForProxy;\n"
                "@RegisterForProxy(targets = {MyApi.class})\n"
                "public class ProxyConfig {}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "MyApi#execute"))
            self.assertEqual(result.outcome, "resolved")
            nat_rels = [r for r in result.relationships if "proxy" in r.kind.lower() or "proxy" in str(r).lower()]
            self.assertTrue(len(nat_rels) > 0)

    def test_meta_inf_services_spi_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-core</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/MyPlugin.java",
                "package com.example;\n"
                "public interface MyPlugin {\n"
                "    void run();\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/MyPluginImpl.java",
                "package com.example;\n"
                "public class MyPluginImpl implements MyPlugin {\n"
                "    public void run() {}\n"
                "}\n",
            )
            self._write(
                repository / "src/main/resources/META-INF/services/com.example.MyPlugin",
                "com.example.MyPluginImpl\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "MyPluginImpl#run"))
            self.assertEqual(result.outcome, "resolved")
            spi_rels = [r for r in result.relationships if "spi" in r.kind.lower() or "services" in str(r).lower() or "native" in r.kind.lower()]
            self.assertTrue(len(spi_rels) > 0)

    def test_native_image_reflection_config_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-core</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/resources/META-INF/native-image/reflection-config.json",
                '[\n  {\n    "name": "com.example.ConfigData",\n    "allDeclaredConstructors": true\n  }\n]\n',
            )
            self._write(
                repository / "src/main/java/com/example/ConfigData.java",
                "package com.example;\n"
                "public class ConfigData {\n"
                "    public String getValue() { return \"v\"; }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "ConfigData#getValue"))
            self.assertEqual(result.outcome, "resolved")
            json_rels = [r for r in result.relationships if "reflection-config.json" in str(r) or "native" in r.kind.lower()]
            self.assertTrue(len(json_rels) > 0)

    def test_reflection_code_usage_evidence_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-core</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/ReflectTarget.java",
                "package com.example;\n"
                "import io.quarkus.runtime.annotations.RegisterForReflection;\n"
                "@RegisterForReflection\n"
                "public class ReflectTarget {\n"
                "    public void doWork() {}\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/ReflectCaller.java",
                "package com.example;\n"
                "public class ReflectCaller {\n"
                "    public void callReflect() throws Exception {\n"
                "        Class<?> clazz = Class.forName(\"com.example.ReflectTarget\");\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "ReflectTarget#doWork"))
            self.assertEqual(result.outcome, "resolved")
            ref_rels = [r for r in result.relationships if "ReflectCaller" in str(r) or "Class.forName" in str(r) or "native" in r.kind.lower()]
            self.assertTrue(len(ref_rels) > 0)

    def test_generated_build_outputs_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-core</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "target/generated-sources/META-INF/native-image/reflection-config.json",
                '[\n  {\n    "name": "com.example.TargetGeneratedData"\n  }\n]\n',
            )
            self._write(
                repository / "src/main/java/com/example/TargetGeneratedData.java",
                "package com.example;\n"
                "public class TargetGeneratedData {\n"
                "    public void getGen() {}\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "TargetGeneratedData#getGen"))
            self.assertEqual(result.outcome, "resolved")
            target_rels = [r for r in result.relationships if "target/" in str(r).lower() or "target\\" in str(r).lower()]
            self.assertEqual(len(target_rels), 0)


if __name__ == "__main__":
    unittest.main()
