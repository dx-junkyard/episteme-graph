"""検証シミュレーション層（SB層）のガードレール — 構造的に守る不変条項。

正本: ``docs/features/simulation_bridge_design.md`` §2（SB1〜SB10）。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.simulation_bridge import codegen, comparator, schema  # noqa: E402
from tests.guardrail_helpers import extract_function_source  # noqa: E402

CORE_DIR = BACKEND / "core" / "simulation_bridge"
ROUTE = BACKEND / "api" / "routes" / "simulation_bridge.py"
ROUTE_SRC = ROUTE.read_text(encoding="utf-8")
CORE_FILES = sorted(CORE_DIR.glob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _string_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                docstrings.add(id(body[0].value))
    return [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def _calls_named(path: Path, names: set[str]) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in names:
            hits.append(f"{path.name}:{node.lineno}")
    return hits


class TestCorePurity:
    @pytest.mark.parametrize("path", CORE_FILES, ids=lambda p: p.name)
    def test_core_does_not_import_web_frameworks(self, path):
        bad = {n for n in _imports(path) if n.split(".")[0] in {"fastapi", "starlette"}}
        assert bad == set(), f"{path.name} が {bad} を import している"


class TestNoDeletion:
    @pytest.mark.parametrize("path", [*CORE_FILES, ROUTE], ids=lambda p: p.name)
    def test_no_delete_from(self, path):
        assert not re.search(r"\bDELETE\s+FROM\b", path.read_text(encoding="utf-8"), re.IGNORECASE), path.name

    def test_no_delete_route(self):
        assert "@admin_router.delete" not in ROUTE_SRC


class TestNoInProcessExecution:
    @pytest.mark.parametrize("path", [*CORE_FILES, ROUTE], ids=lambda p: p.name)
    def test_no_exec_or_eval_calls(self, path):
        # SB3: 生成コード・部品式を api-server のプロセスで実行しない（実行は sim-runner のみ）。
        assert _calls_named(path, {"exec", "eval", "compile"}) == []


class TestLedgerCeiling:
    def test_store_never_writes_directly_verified(self):
        # SB1: 台帳候補の確定は indirectly_supported 止まり（docstring での言及は検査対象外）。
        assert "directly_verified" not in _string_constants(CORE_DIR / "store.py")
        assert schema.LEDGER_MAX_STATUS == "indirectly_supported"

    def test_store_does_not_touch_evidence_lines(self):
        assert "evidence_lines" not in (CORE_DIR / "store.py").read_text(encoding="utf-8")


class TestRegistrations:
    def test_features_are_known(self):
        from core.llm_usage.schema import KNOWN_FEATURES

        assert schema.FEATURE_CODEGEN in KNOWN_FEATURES
        assert schema.FEATURE_REVISION in KNOWN_FEATURES

    def test_audit_entity_is_in_catalog(self):
        from core.schema import AUDIT_ENTITY_SIMULATION, AUDIT_ENTITY_TYPES

        assert AUDIT_ENTITY_SIMULATION in AUDIT_ENTITY_TYPES

    def test_indicator_is_registered_for_runs(self):
        from core import indicator_catalog as ic

        spec = ic.INDICATORS["simulation-agreement"]
        assert spec.route == "/api/admin/simulation/documents/{document_id}/runs"
        assert spec.values_audience == ic.AUDIENCE_TEACHER

    def test_route_audits_with_the_catalog_constant(self):
        assert "AUDIT_ENTITY_SIMULATION" in ROUTE_SRC


class TestDtoHasNoForbiddenKeys:
    def _walk(self, value):
        if isinstance(value, dict):
            for key, child in value.items():
                assert key not in schema.FORBIDDEN_DTO_KEYS, key
                self._walk(child)
        elif isinstance(value, list):
            for child in value:
                self._walk(child)

    def test_codegen_output(self):
        def fake(messages=None, response_format=None, **kwargs):
            return response_format(
                source_code="def simulate(inputs, params):\n    return {'y': 1.0}\n",
                io_contract={"outputs": [{"symbol": "y", "confidence": 0.9}]},
                facts=["近似なし"],
            )

        draft = codegen.generate_program(
            {"outputs": []},
            equations=[{"agent_equation_id": "eq_1", "latex": "y = 1", "agent_payload": {"confidence": 0.3}}],
            symbols=[],
            structured_fn=fake,
        )
        self._walk(draft.io_contract)
        self._walk(draft.provenance)

    def test_comparator_output(self):
        result = comparator.compare({"y": 1.0}, {"y": {"mean": 1.1, "std": 0.2}}, reference={"y": {"value": 1.0}})
        self._walk(result.to_dict())


class TestRouteGates:
    LLM_ENDPOINTS = ("generate_simulation_program", "propose_simulation_revision")

    @pytest.mark.parametrize("name", LLM_ENDPOINTS)
    def test_llm_routes_consume_daily_quota_before_calling(self, name):
        src = extract_function_source(ROUTE_SRC, name)
        assert "consume_daily_quota(" in src
        assert src.index("consume_daily_quota(") < src.index("generate_text_with_structured_output")
        assert "usage_context(" in src

    def test_only_two_routes_call_the_llm(self):
        tree = ast.parse(ROUTE_SRC)
        callers = set()
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                body = ast.get_source_segment(ROUTE_SRC, node) or ""
                if "structured_fn=generate_text_with_structured_output" in body:
                    callers.add(node.name)
        assert callers == set(self.LLM_ENDPOINTS)

    def test_every_post_endpoint_checks_the_editable_gate(self):
        tree = ast.parse(ROUTE_SRC)
        missing = []
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            posts = [
                d for d in node.decorator_list
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "post"
            ]
            if not posts:
                continue
            body = ast.get_source_segment(ROUTE_SRC, node) or ""
            if "_ensure_document_editable(" not in body:
                missing.append(node.name)
        assert missing == [], f"編集権ゲートの無い POST: {missing}"

    def test_every_endpoint_requires_teacher(self):
        tree = ast.parse(ROUTE_SRC)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in {"get", "post"}
                for d in node.decorator_list
            ):
                body = ast.get_source_segment(ROUTE_SRC, node) or ""
                assert "Depends(_require_teacher)" in body, node.name


class TestNoLearnerSurface:
    def test_corpus_routes_do_not_mention_simulation(self):
        text = (BACKEND / "api" / "routes" / "corpus.py").read_text(encoding="utf-8")
        assert "simulation" not in text.lower()

    def test_learning_chat_only_reads_own_run_for_screen_context(self):
        """第 21 周 R12（§14.4）: 学習チャットは選択中の実行の記録を**読むだけ**（画面文脈 4-e）。

        学習者の試行の入口は ``routes/simulation_lab.py`` に 1 つ。learning.py から生成・実行・共有・
        台帳への書き込みを呼ばない（store の読み取り 2 関数だけを許す）。
        """
        import re

        text = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
        calls = set(re.findall(r"simulation_store\.([A-Za-z_]+)", text))
        assert calls <= {"get_run", "is_owner"}, calls
        for forbidden in ("run_pipeline", "generate_draft", "simulation_bridge import codegen", "sandbox_client", "append_ledger_candidate",
                          "set_run_shared", "create_run", "create_program"):
            assert forbidden not in text, forbidden
