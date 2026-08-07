from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from changescope.application import (
    CatalogRegisterMappingRequest,
    CatalogRegisterRepositoryRequest,
    CatalogResolveMappingRequest,
    ChangeScopeApplication,
    IndexRequest,
)
from changescope.cli import main


class WorkspaceCatalogTests(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_registers_repositories_and_explicit_contract_mapping_and_resolves_exact_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            catalog_root = Path(temporary_directory)
            repo_a = catalog_root / "order-service"
            repo_b = catalog_root / "payment-service"

            self._write(
                repo_a / "src/main/java/example/OrderService.java",
                "package example; class OrderService { void placeOrder() {} }",
            )
            self._write(
                repo_b / "src/main/java/example/PaymentService.java",
                "package example; class PaymentService { void processPayment() {} }",
            )

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repo_a))
            app.execute(IndexRequest(repo_b))

            # 1. Register repo A and repo B in Workspace Catalog
            reg_a = app.execute(
                CatalogRegisterRepositoryRequest(
                    catalog_root=catalog_root,
                    repository_id="order-service",
                    repository_path=repo_a,
                )
            )
            self.assertEqual(reg_a.outcome, "registered")
            self.assertEqual(reg_a.repository.repository_id, "order-service")

            reg_b = app.execute(
                CatalogRegisterRepositoryRequest(
                    catalog_root=catalog_root,
                    repository_id="payment-service",
                    repository_path=repo_b,
                )
            )
            self.assertEqual(reg_b.outcome, "registered")
            self.assertEqual(reg_b.repository.repository_id, "payment-service")

            # 2. Register explicit typed contract mapping
            reg_map = app.execute(
                CatalogRegisterMappingRequest(
                    catalog_root=catalog_root,
                    source_repository_id="order-service",
                    contract_kind="soap",
                    contract_key="{http://example.org/orders}OrderPortType#placeOrder",
                    target_repository_id="payment-service",
                    target_contract_key="{http://example.org/payments}PaymentPortType#processPayment",
                    provenance="explicit WSDL contract mapping",
                )
            )
            self.assertEqual(reg_map.outcome, "registered")
            self.assertEqual(reg_map.mapping.source_repository_id, "order-service")
            self.assertEqual(reg_map.mapping.provenance, "explicit WSDL contract mapping")

            # 3. Resolve exact mapping
            resolved = app.execute(
                CatalogResolveMappingRequest(
                    catalog_root=catalog_root,
                    source_repository_id="order-service",
                    contract_kind="soap",
                    contract_key="{http://example.org/orders}OrderPortType#placeOrder",
                )
            )

            self.assertEqual(resolved.outcome, "resolved")
            self.assertIsNotNone(resolved.mapping)
            self.assertEqual(resolved.mapping.target_repository_id, "payment-service")
            self.assertEqual(
                resolved.mapping.target_contract_key,
                "{http://example.org/payments}PaymentPortType#processPayment",
            )

    def test_reports_missing_unregistered_mapping_or_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            catalog_root = Path(temporary_directory)
            repo_a = catalog_root / "order-service"
            self._write(repo_a / "Order.java", "class Order {}")

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repo_a))

            # Query non-existent catalog
            res_no_cat = app.execute(
                CatalogResolveMappingRequest(
                    catalog_root=catalog_root,
                    source_repository_id="order-service",
                    contract_kind="rest",
                    contract_key="POST /orders",
                )
            )
            self.assertEqual(res_no_cat.outcome, "missing")

            # Register repo A
            app.execute(CatalogRegisterRepositoryRequest(catalog_root, "order-service", repo_a))

            # Query missing mapping
            res_no_map = app.execute(
                CatalogResolveMappingRequest(
                    catalog_root=catalog_root,
                    source_repository_id="order-service",
                    contract_kind="rest",
                    contract_key="POST /orders",
                )
            )
            self.assertEqual(res_no_map.outcome, "missing")
            self.assertIn("No explicit contract mapping registered", res_no_map.unresolved_items[0].message)

    def test_reports_stale_mapping_when_target_repo_commit_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            catalog_root = Path(temporary_directory)
            repo_a = catalog_root / "order-service"
            repo_b = catalog_root / "payment-service"
            self._write(repo_a / "Order.java", "class Order {}")
            self._write(repo_b / "Payment.java", "class Payment {}")

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repo_a))
            app.execute(IndexRequest(repo_b))

            app.execute(CatalogRegisterRepositoryRequest(catalog_root, "order-service", repo_a))
            app.execute(CatalogRegisterRepositoryRequest(catalog_root, "payment-service", repo_b))
            app.execute(
                CatalogRegisterMappingRequest(
                    catalog_root=catalog_root,
                    source_repository_id="order-service",
                    contract_kind="rest",
                    contract_key="POST /payments",
                    target_repository_id="payment-service",
                    target_contract_key="POST /process",
                )
            )

            # Manually update registered commit in catalog DB to simulate target commit drift
            cat_db = catalog_root / ".changescope" / "catalog.sqlite"
            conn = sqlite3.connect(cat_db)
            conn.execute(
                "UPDATE catalog_repositories SET git_commit = 'old_commit_123' WHERE repository_id = 'payment-service'"
            )
            conn.commit()
            conn.close()

            res_stale = app.execute(
                CatalogResolveMappingRequest(
                    catalog_root=catalog_root,
                    source_repository_id="order-service",
                    contract_kind="rest",
                    contract_key="POST /payments",
                )
            )
            self.assertEqual(res_stale.outcome, "stale")
            self.assertIn("stale", res_stale.unresolved_items[0].message)

    def test_handles_older_or_missing_catalog_schema_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            catalog_root = Path(temporary_directory)
            repo_a = catalog_root / "service-a"
            self._write(repo_a / "A.java", "class A {}")

            app = ChangeScopeApplication()
            app.execute(IndexRequest(repo_a))

            cat_db = catalog_root / ".changescope" / "catalog.sqlite"
            cat_db.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(cat_db)
            conn.execute("CREATE TABLE dummy (x TEXT)")
            conn.commit()
            conn.close()

            reg_res = app.execute(
                CatalogRegisterRepositoryRequest(
                    catalog_root=catalog_root,
                    repository_id="service-a",
                    repository_path=repo_a,
                )
            )
            self.assertEqual(reg_res.outcome, "registered")

    def test_cli_workspace_catalog_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            catalog_root = Path(temporary_directory)
            repo_a = catalog_root / "app-a"
            repo_b = catalog_root / "app-b"
            self._write(repo_a / "A.java", "class A {}")
            self._write(repo_b / "B.java", "class B {}")

            cwd = Path.cwd()
            try:
                import os
                os.chdir(repo_a)
                main(["index"])

                os.chdir(repo_b)
                main(["index"])

                os.chdir(catalog_root)

                out = io.StringIO()
                with redirect_stdout(out):
                    code = main(["catalog", "register-repo", "--id", "app-a", "--path", "app-a"])
                self.assertEqual(code, 0)

                out = io.StringIO()
                with redirect_stdout(out):
                    code = main(["catalog", "register-repo", "--id", "app-b", "--path", "app-b"])
                self.assertEqual(code, 0)

                out = io.StringIO()
                with redirect_stdout(out):
                    code = main([
                        "catalog", "register-mapping",
                        "--source-repo", "app-a",
                        "--kind", "rest",
                        "--key", "GET /api/b",
                        "--target-repo", "app-b",
                        "--target-key", "GET /api/b",
                        "--provenance", "OpenAPI spec link",
                    ])
                self.assertEqual(code, 0)

                out = io.StringIO()
                with redirect_stdout(out):
                    code = main([
                        "catalog", "resolve",
                        "--source-repo", "app-a",
                        "--kind", "rest",
                        "--key", "GET /api/b",
                        "--format", "json",
                    ])
                self.assertEqual(code, 0)
                json_report = json.loads(out.getvalue())
                self.assertEqual(json_report["outcome"], "resolved")
                self.assertEqual(json_report["mapping"]["target_repository_id"], "app-b")
            finally:
                os.chdir(cwd)
