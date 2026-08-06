import tempfile
import unittest
from pathlib import Path

from changescope.application import (
    ChangeScopeApplication,
    ImpactRequest,
    IndexRequest,
)


class TestQuarkusNativePathImpact(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_rest_dto_native_reflection_evidence_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-resteasy-reactive</artifactId></dependency></dependencies></project>",
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
            self._write(
                repository / "src/main/java/com/example/UserResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "@Path(\"/users\")\n"
                "public class UserResource {\n"
                "    @GET\n"
                "    public UserDTO getUser() { return new UserDTO(); }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "UserResource#getUser"))
            self.assertEqual(result.outcome, "resolved")
            dto_rels = [r for r in result.relationships if r.kind == "quarkus_native_dto" or ("UserDTO" in str(r) and len(r.evidence_chain) > 1)]
            self.assertTrue(len(dto_rels) > 0)

    def test_rest_client_dto_native_reflection_evidence_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-rest-client-reactive</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/AccountDTO.java",
                "package com.example;\n"
                "import io.quarkus.runtime.annotations.RegisterForReflection;\n"
                "@RegisterForReflection\n"
                "public class AccountDTO {\n"
                "    public String id;\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/AccountClient.java",
                "package com.example;\n"
                "import org.eclipse.microprofile.rest.client.inject.RegisterRestClient;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "@RegisterRestClient\n"
                "public interface AccountClient {\n"
                "    @GET\n"
                "    @Path(\"/account\")\n"
                "    AccountDTO getAccount();\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "AccountClient#getAccount"))
            self.assertEqual(result.outcome, "resolved")
            client_dto_rels = [r for r in result.relationships if r.kind == "quarkus_native_dto" or ("AccountDTO" in str(r) and len(r.evidence_chain) > 1)]
            self.assertTrue(len(client_dto_rels) > 0)

    def test_uncovered_native_dto_risk_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-resteasy-reactive</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "quarkus.package.type=native\n",
            )
            self._write(
                repository / "src/main/java/com/example/RawDTO.java",
                "package com.example;\n"
                "public class RawDTO {\n"
                "    public String data;\n"
                "}\n",
            )
            self._write(
                repository / "src/main/java/com/example/RawResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.POST;\n"
                "import jakarta.ws.rs.Path;\n"
                "@Path(\"/raw\")\n"
                "public class RawResource {\n"
                "    @POST\n"
                "    public void postRaw(RawDTO dto) {}\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "RawResource#postRaw"))
            self.assertEqual(result.outcome, "resolved")
            unresolved = [u for u in result.unresolved_items if "RawDTO" in u.message or "reflection" in u.message.lower() or "native" in u.message.lower()]
            self.assertTrue(len(unresolved) > 0)

    def test_native_package_config_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-resteasy-reactive</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "quarkus.package.type=native\n"
                "quarkus.native.additional-build-args=-H:+ReportExceptionStackTraces\n",
            )
            self._write(
                repository / "src/main/java/com/example/NativeAppResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "@Path(\"/app\")\n"
                "public class NativeAppResource {\n"
                "    @GET\n"
                "    public String getApp() { return \"app\"; }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "NativeAppResource#getApp", build_profiles=("native",)))
            self.assertEqual(result.outcome, "resolved")
            cfg_rels = [r for r in result.relationships if "quarkus.package.type" in str(r) or "quarkus.native" in str(r) or "native" in r.kind.lower()]
            self.assertTrue(len(cfg_rels) > 0)


if __name__ == "__main__":
    unittest.main()
