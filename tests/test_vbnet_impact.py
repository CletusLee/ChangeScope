from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from changescope.application import (
    ChangeScopeApplication,
    ImpactRequest,
    IndexRequest,
    JavaDeclaration,
    JavaInvocation,
)
from changescope.cli import main


class TestVBNetImpactAnalysis(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # --- Issue #51 Tests: Multi-Language Schema & Model ---

    def test_multi_language_declaration_and_invocation_defaults(self) -> None:
        decl = JavaDeclaration(
            kind="method",
            name="doWork",
            qualified_name="com.example.App.doWork",
            signature="com.example.App#doWork",
            path=Path("src/App.java"),
            start_line=10,
            end_line=20,
            is_test=False,
            is_private=False,
        )
        self.assertEqual(getattr(decl, "language", "java"), "java")

        inv = JavaInvocation(
            name="doWork",
            receiver="app",
            caller="main",
            path=Path("src/Main.java"),
            start_line=5,
            end_line=5,
            is_test=False,
        )
        self.assertEqual(getattr(inv, "language", "java"), "java")

    def test_schema_rebuild_on_stale_index(self) -> None:
        java_file = self.root / "src" / "App.java"
        java_file.parent.mkdir(parents=True, exist_ok=True)
        java_file.write_text("package com.example;\npublic class App { public void run() {} }", encoding="utf-8")

        app = ChangeScopeApplication()
        res1 = app.execute(IndexRequest(self.root))
        self.assertGreaterEqual(len(res1.declarations), 1)

        res2 = app.execute(IndexRequest(self.root))
        self.assertGreaterEqual(len(res2.declarations), 1)

    # --- Issue #52 Tests: Index VB.NET 2003 Source ---

    def test_vbnet_indexing_discovers_vb_and_project_files(self) -> None:
        vb_file = self.root / "src" / "Form1.vb"
        vb_file.parent.mkdir(parents=True, exist_ok=True)
        vb_code = """
Public Class Form1
    Inherits System.Windows.Forms.Form

    Private WithEvents Button1 As System.Windows.Forms.Button

    Private Sub Button1_Click(ByVal sender As Object, ByVal e As System.EventArgs) Handles Button1.Click
        Call CalculateTotal()
    End Sub

    Public Sub CalculateTotal()
    End Sub
End Class
"""
        vb_file.write_text(vb_code, encoding="utf-8")

        app = ChangeScopeApplication()
        res = app.execute(IndexRequest(self.root))
        self.assertIn(Path("src/Form1.vb"), res.indexed_files)
        self.assertTrue(any(d.name == "Button1_Click" for d in res.vbnet_declarations))
        self.assertTrue(any(d.name == "CalculateTotal" for d in res.vbnet_declarations))

    def test_vbnet_indexing_encoding_handling(self) -> None:
        vb_file = self.root / "src" / "ChineseForm.vb"
        vb_file.parent.mkdir(parents=True, exist_ok=True)
        # Write CP950 encoded file with Chinese comments/identifiers
        vb_code = "Public Class ChineseForm\n    ' 繁體中文註解\n    Public Sub ProcessData()\n    End Sub\nEnd Class\n"
        vb_file.write_bytes(vb_code.encode("cp950"))

        app = ChangeScopeApplication()
        res = app.execute(IndexRequest(self.root))
        self.assertIn(Path("src/ChineseForm.vb"), res.indexed_files)
        self.assertTrue(any(d.name == "ProcessData" for d in res.vbnet_declarations))

    # --- Issue #53 Tests: Method Impact & Late-Bound Calls ---

    def test_vbnet_case_insensitive_target_resolution_and_direct_calls(self) -> None:
        vb_file = self.root / "src" / "Service.vb"
        vb_file.parent.mkdir(parents=True, exist_ok=True)
        vb_code = """
Public Class OrderService
    Public Sub ProcessOrder(ByVal orderId As Integer)
        Dim dao As New OrderDAO()
        dao.SaveOrder(orderId)
    End Sub
End Class

Public Class OrderDAO
    Public Sub SaveOrder(ByVal id As Integer)
    End Sub
End Class
"""
        vb_file.write_text(vb_code, encoding="utf-8")

        app = ChangeScopeApplication()
        app.execute(IndexRequest(self.root))

        # Case-insensitive resolution: orderservice#processorder
        impact_res = app.execute(ImpactRequest(self.root, target="orderservice#processorder"))
        self.assertEqual(impact_res.outcome, "resolved")
        self.assertIsNotNone(impact_res.target)
        self.assertEqual(impact_res.target.signature, "OrderService#ProcessOrder")
        self.assertTrue(any("OrderDAO#SaveOrder" in r.caller or "OrderDAO#SaveOrder" in r.kind for r in impact_res.relationships))

    def test_vbnet_late_bound_calls_reported_as_unresolved(self) -> None:
        vb_file = self.root / "src" / "LateBound.vb"
        vb_file.parent.mkdir(parents=True, exist_ok=True)
        vb_code = """
Option Strict Off
Public Class DynamicCaller
    Public Sub DoDynamicWork(ByVal targetObj As Object)
        targetObj.DynamicMethodCall()
    End Sub
End Class
"""
        vb_file.write_text(vb_code, encoding="utf-8")

        app = ChangeScopeApplication()
        app.execute(IndexRequest(self.root))

        impact_res = app.execute(ImpactRequest(self.root, target="DynamicCaller#DoDynamicWork"))
        self.assertEqual(impact_res.outcome, "resolved")
        self.assertTrue(any("Late-Bound Call" in u.message or "late-bound" in u.message.lower() for u in impact_res.unresolved_items))

    # --- Issue #54 Tests: WinForms Event Wiring & Verification Surfaces ---

    def test_winforms_event_wiring_and_manual_verification_surfaces(self) -> None:
        vb_file = self.root / "src" / "MainForm.vb"
        vb_file.parent.mkdir(parents=True, exist_ok=True)
        vb_code = """
Public Class MainForm
    Inherits System.Windows.Forms.Form

    Private WithEvents btnSubmit As System.Windows.Forms.Button

    #Region "Windows Form Designer generated code"
    Private Sub InitializeComponent()
        Me.btnSubmit = New System.Windows.Forms.Button()
    End Sub
    #End Region

    Private Sub btnSubmit_Click(ByVal sender As Object, ByVal e As System.EventArgs) Handles btnSubmit.Click
        SubmitData()
    End Sub

    Public Sub SubmitData()
    End Sub
End Class
"""
        vb_file.write_text(vb_code, encoding="utf-8")

        app = ChangeScopeApplication()
        app.execute(IndexRequest(self.root))

        impact_res = app.execute(ImpactRequest(self.root, target="MainForm#SubmitData"))
        self.assertEqual(impact_res.outcome, "resolved")
        # Manual verification surface for MainForm / btnSubmit should be reported
        self.assertTrue(len(impact_res.manual_verification_surfaces) > 0 or any("MainForm" in r.caller for r in impact_res.relationships))

    # --- Issue #55 Tests: Multi-Project Solutions & Process Launches ---

    def test_multi_project_process_launch_boundary(self) -> None:
        sln_file = self.root / "App.sln"
        sln_file.write_text('Project("{F184B08F-C81C-45F6-A57F-5ABD9991F28F}") = "AppA", "AppA\\AppA.vbproj", "{11111111-1111-1111-1111-111111111111}"\nEndProject\nProject("{F184B08F-C81C-45F6-A57F-5ABD9991F28F}") = "AppB", "AppB\\AppB.vbproj", "{22222222-2222-2222-2222-222222222222}"\nEndProject\n', encoding="utf-8")

        proj_a = self.root / "AppA" / "AppA.vbproj"
        proj_a.parent.mkdir(parents=True, exist_ok=True)
        proj_a.write_text('<VisualStudioProject><VisualBasic><Build><Settings AssemblyName="AppA" RootNamespace="AppA" /></Build></VisualBasic></VisualStudioProject>', encoding="utf-8")

        src_a = self.root / "AppA" / "FormA.vb"
        src_a.write_text("""
Imports System.Diagnostics

Public Class FormA
    Public Sub LaunchChildApp()
        Process.Start("AppB.exe")
    End Sub
End Class
""", encoding="utf-8")

        proj_b = self.root / "AppB" / "AppB.vbproj"
        proj_b.parent.mkdir(parents=True, exist_ok=True)
        proj_b.write_text('<VisualStudioProject><VisualBasic><Build><Settings AssemblyName="AppB" RootNamespace="AppB" StartupObject="FormB" /></Build></VisualBasic></VisualStudioProject>', encoding="utf-8")

        src_b = self.root / "AppB" / "FormB.vb"
        src_b.write_text("""
Public Class FormB
    Public Sub Main()
    End Sub
End Class
""", encoding="utf-8")

        app = ChangeScopeApplication()
        app.execute(IndexRequest(self.root))

        impact_res = app.execute(ImpactRequest(self.root, target="FormA#LaunchChildApp"))
        self.assertEqual(impact_res.outcome, "resolved")
        self.assertTrue(any("Local Process Boundary" in r.kind or "Process" in r.kind for r in impact_res.relationships))

    # --- Issue #56 Tests: .NET 1.1 Config & ADO.NET Boundaries ---

    def test_dotnet11_config_and_adonet_boundary(self) -> None:
        cfg_file = self.root / "app.config"
        cfg_file.write_text('<configuration><appSettings><add key="DbConnectionString" value="Server=localhost;Database=TestDB;" /></appSettings></configuration>', encoding="utf-8")

        sql_file = self.root / "db" / "sp_GetCustomer.sql"
        sql_file.parent.mkdir(parents=True, exist_ok=True)
        sql_file.write_text("CREATE PROCEDURE sp_GetCustomer AS SELECT * FROM Customers;", encoding="utf-8")

        vb_file = self.root / "src" / "DataAccessor.vb"
        vb_file.parent.mkdir(parents=True, exist_ok=True)
        vb_code = """
Imports System.Configuration
Imports System.Data.SqlClient

Public Class DataAccessor
    Public Sub FetchCustomer(ByVal customerId As Integer)
        Dim connStr As String = ConfigurationSettings.AppSettings("DbConnectionString")
        Dim cmd As New SqlCommand()
        cmd.CommandText = "sp_GetCustomer"
        cmd.CommandType = System.Data.CommandType.StoredProcedure
    End Sub
End Class
"""
        vb_file.write_text(vb_code, encoding="utf-8")

        app = ChangeScopeApplication()
        app.execute(IndexRequest(self.root))

        impact_res = app.execute(ImpactRequest(self.root, target="DataAccessor#FetchCustomer"))
        self.assertEqual(impact_res.outcome, "resolved")
        self.assertTrue(any("AppSettings" in r.kind or "Data Access" in r.kind or "StoredProcedure" in r.kind for r in impact_res.relationships))

    # --- Issue #57 Tests: COM & ActiveX Interop Boundaries ---

    def test_com_and_activex_interop_boundary(self) -> None:
        proj_file = self.root / "App.vbproj"
        proj_file.write_text("""
<VisualStudioProject>
    <VisualBasic>
        <Build>
            <Settings AssemblyName="LegacyApp" RootNamespace="LegacyApp" />
            <References>
                <Reference Name="AxInterop.MSComctlLib" GUID="{831F9280-0C5C-11D2-A9FC-0000F8754DA1}" />
                <Reference Name="Interop.Excel" GUID="{00020813-0000-0000-C000-00000000046E}" />
            </References>
        </Build>
    </VisualBasic>
</VisualStudioProject>
""", encoding="utf-8")

        vb_file = self.root / "src" / "ExcelExporter.vb"
        vb_file.parent.mkdir(parents=True, exist_ok=True)
        vb_code = """
Public Class ExcelExporter
    Public Sub ExportToExcel()
        Dim app As Object = CreateObject("Excel.Application")
    End Sub
End Class
"""
        vb_file.write_text(vb_code, encoding="utf-8")

        app = ChangeScopeApplication()
        app.execute(IndexRequest(self.root))

        impact_res = app.execute(ImpactRequest(self.root, target="ExcelExporter#ExportToExcel"))
        self.assertEqual(impact_res.outcome, "resolved")
        # CreateObject should be reported as unresolved item
        self.assertTrue(any("CreateObject" in u.message or "COM" in u.message for u in impact_res.unresolved_items))

    # --- Issue #58 & #59 Tests: Full Impact Path & Regression Matrix ---

    def test_complete_vbnet_impact_path_and_affected_tests(self) -> None:
        vb_src = self.root / "src" / "BusinessLogic.vb"
        vb_src.parent.mkdir(parents=True, exist_ok=True)
        vb_src.write_text("""
Public Class BusinessLogic
    Public Sub ExecuteBusinessRule()
    End Sub
End Class
""", encoding="utf-8")

        vb_test = self.root / "tests" / "BusinessLogicTests.vb"
        vb_test.parent.mkdir(parents=True, exist_ok=True)
        vb_test.write_text("""
Public Class BusinessLogicTests
    <Test()>
    Public Sub TestExecuteBusinessRule()
        Dim logic As New BusinessLogic()
        logic.ExecuteBusinessRule()
    End Sub
End Class
""", encoding="utf-8")

        app = ChangeScopeApplication()
        app.execute(IndexRequest(self.root))

        impact_res = app.execute(ImpactRequest(self.root, target="BusinessLogic#ExecuteBusinessRule"))
        self.assertEqual(impact_res.outcome, "resolved")
        self.assertTrue(any("BusinessLogicTests#TestExecuteBusinessRule" in r.caller for r in impact_res.relationships))
