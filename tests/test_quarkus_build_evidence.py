from pathlib import Path
import sqlite3
import tempfile
import unittest

from changescope.application import (
    ChangeScopeApplication,
    EvidenceRequest,
    ImpactRequest,
    IndexRequest,
)


class TestQuarkusBuildEvidence(unittest.TestCase):

    def test_maven_quarkus_build_evidence_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)
            (root / "src" / "main" / "java" / "com" / "example" / "GreetingResource.java").write_text(
                "package com.example;\n\npublic class GreetingResource {\n"
                "    public String hello() {\n        return \"hello\";\n    }\n}\n",
                encoding="utf-8",
            )
            pom_content = (
                "<project xmlns=\"http://maven.apache.org/POM/4.0.0\">\n"
                "  <modelVersion>4.0.0</modelVersion>\n"
                "  <groupId>com.example</groupId>\n"
                "  <artifactId>demo</artifactId>\n"
                "  <version>1.0.0</version>\n"
                "  <dependencyManagement>\n"
                "    <dependencies>\n"
                "      <dependency>\n"
                "        <groupId>io.quarkus.platform</groupId>\n"
                "        <artifactId>quarkus-bom</artifactId>\n"
                "        <version>3.8.1</version>\n"
                "        <type>pom</type>\n"
                "        <scope>import</scope>\n"
                "      </dependency>\n"
                "    </dependencies>\n"
                "  </dependencyManagement>\n"
                "  <dependencies>\n"
                "    <dependency>\n"
                "      <groupId>io.quarkus</groupId>\n"
                "      <artifactId>quarkus-resteasy-reactive</artifactId>\n"
                "    </dependency>\n"
                "    <dependency>\n"
                "      <groupId>io.quarkus</groupId>\n"
                "      <artifactId>quarkus-resteasy</artifactId>\n"
                "    </dependency>\n"
                "  </dependencies>\n"
                "  <build>\n"
                "    <plugins>\n"
                "      <plugin>\n"
                "        <groupId>io.quarkus.platform</groupId>\n"
                "        <artifactId>quarkus-maven-plugin</artifactId>\n"
                "        <version>3.8.1</version>\n"
                "      </plugin>\n"
                "    </plugins>\n"
                "  </build>\n"
                "  <profiles>\n"
                "    <profile>\n"
                "      <id>native</id>\n"
                "    </profile>\n"
                "  </profiles>\n"
                "</project>\n"
            )
            (root / "pom.xml").write_text(pom_content, encoding="utf-8")

            app = ChangeScopeApplication()
            index_result = app.execute(IndexRequest(root))

            self.assertGreater(len(index_result.quarkus_build_facts), 0)

            fact_kinds = {f.kind for f in index_result.quarkus_build_facts}
            self.assertIn("platform", fact_kinds)
            self.assertIn("plugin", fact_kinds)
            self.assertIn("extension", fact_kinds)
            self.assertIn("profile", fact_kinds)

            # Verify legacy vs current extension classification
            ext_map = {f.subject: f.value for f in index_result.quarkus_build_facts if f.kind == "extension"}
            self.assertEqual(ext_map.get("quarkus-resteasy-reactive"), "current")
            self.assertEqual(ext_map.get("quarkus-resteasy"), "legacy")

            # Impact report integration
            impact_result = app.execute(ImpactRequest(root, "GreetingResource#hello"))
            self.assertEqual(impact_result.outcome, "resolved")
            self.assertTrue(any("Quarkus module" in a for a in impact_result.assumptions))

    def test_gradle_quarkus_build_evidence_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)
            (root / "src" / "main" / "java" / "com" / "example" / "ItemService.java").write_text(
                "package com.example;\n\npublic class ItemService {\n"
                "    public void process() {}\n}\n",
                encoding="utf-8",
            )
            gradle_content = (
                "plugins {\n"
                "    id 'io.quarkus'\n"
                "}\n"
                "dependencies {\n"
                "    implementation enforcedPlatform('io.quarkus:quarkus-bom:3.8.1')\n"
                "    implementation 'io.quarkus:quarkus-arc'\n"
                "    implementation 'io.quarkus:quarkus-resteasy-reactive'\n"
                "}\n"
            )
            (root / "build.gradle").write_text(gradle_content, encoding="utf-8")

            app = ChangeScopeApplication()
            index_result = app.execute(IndexRequest(root))

            self.assertGreater(len(index_result.quarkus_build_facts), 0)
            subjects = {f.subject for f in index_result.quarkus_build_facts}
            self.assertIn("io.quarkus", subjects)
            self.assertIn("quarkus-arc", subjects)

    def test_quarkus_evidence_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)
            (root / "src" / "main" / "java" / "com" / "example" / "App.java").write_text(
                "package com.example;\npublic class App { public void run() {} }\n",
                encoding="utf-8",
            )
            pom_content = (
                "<project>\n"
                "  <dependencies>\n"
                "    <dependency>\n"
                "      <groupId>io.quarkus</groupId>\n"
                "      <artifactId>quarkus-arc</artifactId>\n"
                "    </dependency>\n"
                "  </dependencies>\n"
                "</project>\n"
            )
            (root / "pom.xml").write_text(pom_content, encoding="utf-8")

            app = ChangeScopeApplication()
            index_result = app.execute(IndexRequest(root))
            fact = next(f for f in index_result.quarkus_build_facts if f.kind == "extension")

            handle = f"quarkus_build:{fact.path.as_posix()}:{fact.start_line}-{fact.end_line}"
            nav_result = app.execute(EvidenceRequest(root, handle, context_lines=0))
            self.assertIn("quarkus-arc", nav_result.content)

    def test_schema_migration_for_older_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)
            (root / "src" / "main" / "java" / "com" / "example" / "Foo.java").write_text(
                "package com.example;\npublic class Foo { public void bar() {} }\n",
                encoding="utf-8",
            )
            (root / "pom.xml").write_text(
                "<project><dependencies><dependency><groupId>io.quarkus</groupId><artifactId>quarkus-arc</artifactId></dependency></dependencies></project>",
                encoding="utf-8",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(root))

            # Simulate an older index database missing `quarkus_build_facts` table
            db_path = root / ".changescope" / "index.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute("DROP TABLE quarkus_build_facts")
            conn.commit()
            conn.close()

            # Now run impact query which triggers refresh and auto-migrates missing schema
            impact_result = app.execute(ImpactRequest(root, "Foo#bar"))
            self.assertEqual(impact_result.outcome, "resolved")

            # Verify table was re-created and populated
            conn = sqlite3.connect(db_path)
            count = conn.execute("SELECT COUNT(*) FROM quarkus_build_facts").fetchone()[0]
            conn.close()
            self.assertGreater(count, 0)


if __name__ == "__main__":
    unittest.main()
