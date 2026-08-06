import tempfile
import unittest
from pathlib import Path

from changescope.application import (
    ChangeScopeApplication,
    ImpactRequest,
    IndexRequest,
)


class TestQuarkusSecurityEvidence(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_method_security_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-security</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/AdminResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "import jakarta.annotation.security.RolesAllowed;\n"
                "@Path(\"/admin\")\n"
                "public class AdminResource {\n"
                "    @GET\n"
                "    @RolesAllowed(\"admin\")\n"
                "    public String getAdminData() {\n"
                "        return \"secret\";\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "AdminResource#getAdminData"))
            self.assertEqual(result.outcome, "resolved")
            sec_rels = [r for r in result.relationships if r.kind in ("quarkus_security", "quarkus_security_policy")]
            self.assertTrue(len(sec_rels) > 0)
            self.assertTrue(any("admin" in str(r) for r in sec_rels))

    def test_method_annotation_overrides_class(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-security</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/MixedResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "import jakarta.annotation.security.RolesAllowed;\n"
                "import jakarta.annotation.security.PermitAll;\n"
                "@Path(\"/mixed\")\n"
                "@RolesAllowed(\"user\")\n"
                "public class MixedResource {\n"
                "    @GET\n"
                "    @Path(\"/public\")\n"
                "    @PermitAll\n"
                "    public String getPublicData() {\n"
                "        return \"public\";\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "MixedResource#getPublicData"))
            self.assertEqual(result.outcome, "resolved")
            sec_rels = [r for r in result.relationships if r.kind in ("quarkus_security", "quarkus_security_policy")]
            self.assertTrue(len(sec_rels) > 0)
            self.assertTrue(any("PermitAll" in str(r) for r in sec_rels))

    def test_unannotated_method_inherits_class_security(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-security</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/SecuredResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "import jakarta.annotation.security.RolesAllowed;\n"
                "@Path(\"/secured\")\n"
                "@RolesAllowed(\"manager\")\n"
                "public class SecuredResource {\n"
                "    @GET\n"
                "    public String getData() {\n"
                "        return \"data\";\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "SecuredResource#getData"))
            self.assertEqual(result.outcome, "resolved")
            sec_rels = [r for r in result.relationships if r.kind in ("quarkus_security", "quarkus_security_policy")]
            self.assertTrue(len(sec_rels) > 0)
            self.assertTrue(any("manager" in str(r) for r in sec_rels))

    def test_permissions_allowed_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-security</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/OrderResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.POST;\n"
                "import jakarta.ws.rs.Path;\n"
                "import io.quarkus.security.PermissionsAllowed;\n"
                "@Path(\"/orders\")\n"
                "public class OrderResource {\n"
                "    @POST\n"
                "    @PermissionsAllowed(\"orders:write\")\n"
                "    public void createOrder() {}\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "OrderResource#createOrder"))
            self.assertEqual(result.outcome, "resolved")
            sec_rels = [r for r in result.relationships if r.kind in ("quarkus_security", "quarkus_security_policy")]
            self.assertTrue(len(sec_rels) > 0)
            self.assertTrue(any("orders:write" in str(r) for r in sec_rels))

    def test_path_based_config_security_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-security</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "quarkus.http.auth.permission.admin-policy.paths=/admin/*\n"
                "quarkus.http.auth.permission.admin-policy.policy=roles-allowed\n"
                "quarkus.http.auth.permission.admin-policy.roles-allowed=admin\n",
            )
            self._write(
                repository / "src/main/java/com/example/AdminControl.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "@Path(\"/admin/dashboard\")\n"
                "public class AdminControl {\n"
                "    @GET\n"
                "    public String viewDashboard() {\n"
                "        return \"dash\";\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "AdminControl#viewDashboard"))
            self.assertEqual(result.outcome, "resolved")
            sec_rels = [r for r in result.relationships if r.kind in ("quarkus_security", "quarkus_security_policy")]
            self.assertTrue(len(sec_rels) > 0)

    def test_role_property_expression_evidence_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-security</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/resources/application.properties",
                "app.roles.admin=superadmin\n",
            )
            self._write(
                repository / "src/main/java/com/example/SuperResource.java",
                "package com.example;\n"
                "import jakarta.ws.rs.GET;\n"
                "import jakarta.ws.rs.Path;\n"
                "import jakarta.annotation.security.RolesAllowed;\n"
                "@Path(\"/super\")\n"
                "public class SuperResource {\n"
                "    @GET\n"
                "    @RolesAllowed(\"${app.roles.admin}\")\n"
                "    public String superData() {\n"
                "        return \"super\";\n"
                "    }\n"
                "}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "SuperResource#superData"))
            self.assertEqual(result.outcome, "resolved")
            cfg_rels = [r for r in result.relationships if len(r.evidence_chain) > 1 and any("quarkus_config" in str(ch) for ch in r.evidence_chain)]
            self.assertTrue(len(cfg_rels) > 0 or any("app.roles.admin" in str(r) for r in result.relationships))

    def test_unresolved_custom_identity_augmentor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            self._write(
                repository / "pom.xml",
                "<project><dependencies><dependency><groupId>io.quarkus</groupId>"
                "<artifactId>quarkus-security</artifactId></dependency></dependencies></project>",
            )
            self._write(
                repository / "src/main/java/com/example/CustomAugmentor.java",
                "package com.example;\n"
                "import io.quarkus.security.identity.SecurityIdentityAugmentor;\n"
                "public class CustomAugmentor implements SecurityIdentityAugmentor {}\n",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repository))

            result = app.execute(ImpactRequest(repository, "CustomAugmentor"))
            self.assertEqual(result.outcome, "resolved")
            unresolved = [u for u in result.unresolved_items if "augmentor" in u.message.lower() or "identity" in u.message.lower() or "custom" in u.message.lower()]
            self.assertTrue(len(unresolved) > 0)


if __name__ == "__main__":
    unittest.main()
