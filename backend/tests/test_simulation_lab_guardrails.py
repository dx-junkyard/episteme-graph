"""検証シミュレーション層 v1.1「主語は学習者」のガードレール — 構造的に守る不変条項。

正本: ``docs/features/simulation_bridge_design.md`` §14（SB11〜SB15）。
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

from core import trace_registry  # noqa: E402
from core.simulation_bridge import schema  # noqa: E402
from tests.guardrail_helpers import extract_function_source  # noqa: E402

ROUTE = BACKEND / "api" / "routes" / "simulation_lab.py"
ROUTE_SRC = ROUTE.read_text(encoding="utf-8")
SERVICE = BACKEND / "core" / "simulation_bridge" / "service.py"
SERVICES_SRC = (BACKEND / "api" / "services.py").read_text(encoding="utf-8")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _endpoints() -> list[ast.FunctionDef]:
    tree = ast.parse(ROUTE_SRC)
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in {"get", "post", "put", "patch", "delete"}
            for d in node.decorator_list
        ):
            out.append(node)
    return out


class TestRouter:
    def test_router_is_a_learner_display_route(self):
        from display_route import LearnerDisplayRoute
        from routes import simulation_lab as lab

        assert lab.learning_router.route_class is LearnerDisplayRoute
        assert lab.learning_router.prefix == "/api/learning"

    def test_main_includes_the_router(self):
        main_src = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")
        assert "app.include_router(simulation_lab_routes.learning_router)" in main_src

    def test_no_delete_method_and_no_delete_sql(self):
        from routes import simulation_lab as lab

        for route in lab.learning_router.routes:
            assert "DELETE" not in (route.methods or set())
        assert not re.search(r"\bDELETE\s+FROM\b", ROUTE_SRC, re.IGNORECASE)

    def test_no_teacher_gate_on_learner_routes(self):
        # SB11: 試行に承認ゲートを置かない。学習者向けルートは本人ゲートだけ。
        assert "_require_teacher" not in ROUTE_SRC
        for node in _endpoints():
            body = ast.get_source_segment(ROUTE_SRC, node) or ""
            assert "Depends(_get_current_user)" in body, node.name

    def test_learner_routes_never_return_owner_or_creator_ids(self):
        # SB12: 所有者・作成者の user id を DTO に載せない（DTO 組み立て関数の字面で固定）。
        for name in ("_program_dto", "_wiring_dto", "_run_dto", "_parts_catalog_dto"):
            body = extract_function_source(ROUTE_SRC, name)
            for key in ('"owner_user_id"', '"created_by"', '"decided_by"', '"confidence"'):
                assert key not in body, f"{name} が {key} を返している"
        # shared_at は真偽値 shared に変えて返す（刻印時刻は出さない）。
        assert '"shared_at":' not in extract_function_source(ROUTE_SRC, "_run_dto")

    def test_no_confidence_string_constant_in_the_route(self):
        tree = ast.parse(ROUTE_SRC)
        docstrings = {
            id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body
            and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)
        }
        constants = [
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
        ]
        assert not [c for c in constants if "confidence" in c]

    def test_llm_routes_use_learner_features_and_consume_quota_first(self):
        for name, feature in (
            ("generate_simulation_lab_program", "FEATURE_LEARNER_CODEGEN"),
            ("propose_simulation_lab_revision", "FEATURE_LEARNER_REVISION"),
        ):
            src = extract_function_source(ROUTE_SRC, name)
            assert "_consume_quota(uid)" in src
            assert src.index("_consume_quota(uid)") < src.index("generate_text_with_structured_output")
            assert f"feature=schema.{feature}" in src
        assert schema.FEATURE_LEARNER_CODEGEN == "learning:simulation_codegen"
        assert schema.FEATURE_LEARNER_REVISION == "learning:simulation_revision"

    def test_only_two_endpoints_call_the_llm(self):
        callers = {
            node.name for node in _endpoints()
            if "generate_text_with_structured_output" in (ast.get_source_segment(ROUTE_SRC, node) or "")
        }
        assert callers == {"generate_simulation_lab_program", "propose_simulation_lab_revision"}

    def test_learner_does_not_choose_a_model(self):
        # M9: 学習者にモデル名を出さない・選ばせない。
        assert "model=" not in ROUTE_SRC.replace("response_model=", "")

    def test_default_document_comes_from_the_focus_resolver(self):
        # FD1: 「どの論文か」の優先を新しく書かない。
        body = extract_function_source(ROUTE_SRC, "_resolve_document")
        assert "learner_focus_document(" in body

    def test_traces_go_through_record_interest_trace(self):
        assert "INSERT INTO interest_traces" not in ROUTE_SRC
        assert "UPDATE interest_traces" not in ROUTE_SRC
        body = extract_function_source(ROUTE_SRC, "create_simulation_lab_run")
        assert "record_interest_trace(" in body
        assert "schema.TRACE_KIND_VERIFICATION_ATTEMPT" in body


class TestServicePurity:
    def test_service_does_not_import_fastapi(self):
        bad = {n for n in _imports(SERVICE) if n.split(".")[0] in {"fastapi", "starlette"}}
        assert bad == set()

    def test_service_does_not_call_the_llm_directly(self):
        names = _imports(SERVICE)
        assert "core.llm" not in names

    def test_service_has_no_delete(self):
        assert not re.search(r"\bDELETE\s+FROM\b", SERVICE.read_text(encoding="utf-8"), re.IGNORECASE)

    def test_service_rolls_back_every_swallowed_sql_error(self):
        src = SERVICE.read_text(encoding="utf-8")
        for name in ("saved_module_records", "equation_rows", "symbol_rows"):
            assert "rollback_quietly(session)" in extract_function_source(src, name), name


class TestServiceRollback:
    class _Broken:
        def __init__(self):
            self.rollbacks = 0

        def execute(self, *_a, **_k):
            raise RuntimeError('relation "knowledge_equations_live" does not exist')

        def rollback(self):
            self.rollbacks += 1

    def test_equation_rows_rolls_back_on_failure(self):
        from core.simulation_bridge import service

        session = self._Broken()
        assert service.equation_rows(session, "00000000-0000-0000-0000-000000000001", None) == []
        assert session.rollbacks == 1

    @pytest.mark.parametrize("fn", ["saved_module_records", "symbol_rows"])
    def test_other_reads_roll_back_on_failure(self, fn):
        from core.simulation_bridge import service

        session = self._Broken()
        args = (session, "00000000-0000-0000-0000-000000000001")
        result = getattr(service, fn)(*args) if fn == "saved_module_records" else getattr(service, fn)(*args, [])
        assert not result
        assert session.rollbacks == 1


class TestTraceRegistry:
    def test_verification_attempt_is_declared(self):
        spec = trace_registry.TRACE_KINDS[schema.TRACE_KIND_VERIFICATION_ATTEMPT]
        assert spec.learner_trajectory is False
        assert spec.teacher_dashboard is False
        assert spec.personal_map is False
        assert spec.teacher_aggregations == ()

    def test_excluded_from_dashboard_and_trajectory(self):
        for name in ("aggregate_interest_dashboard", "get_interest_traces"):
            assert "'verification_attempt'" in extract_function_source(SERVICES_SRC, name), name

    def test_digests_and_workers_do_not_read_it(self):
        for name in ("get_tension_digest", "get_anchor_digest"):
            assert "verification_attempt" not in extract_function_source(SERVICES_SRC, name)
        for path in ("core/tension/worker.py", "core/structure_anchor/worker.py", "core/personal_graph/queries.py"):
            assert "verification_attempt" not in (BACKEND / path).read_text(encoding="utf-8"), path


class TestRegistrations:
    def test_features_are_known_and_in_the_simulation_scene(self):
        from core import llm_policy
        from core.llm_usage.schema import KNOWN_FEATURES

        for feature in (schema.FEATURE_LEARNER_CODEGEN, schema.FEATURE_LEARNER_REVISION):
            assert feature in KNOWN_FEATURES
            assert llm_policy.scene_for_feature(feature) == llm_policy.SCENE_SIMULATION

    def test_disclosure_declares_learner_touchpoints(self):
        from core import disclosure_axes

        features = {tp.feature for spec in disclosure_axes.all_data_kinds() for tp in spec.ai_touchpoints}
        assert schema.FEATURE_LEARNER_CODEGEN in features
        assert schema.FEATURE_LEARNER_REVISION in features

    def test_wiring_origin_vocabulary(self):
        assert schema.WIRING_ORIGIN_LEARNER in schema.WIRING_ORIGINS
