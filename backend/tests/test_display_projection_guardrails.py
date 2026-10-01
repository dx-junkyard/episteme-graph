"""表示投影層の横断ガードレール（``docs/features/display_projection_design.md``）。

構造的に守るもの:

- (a) DP1: ``/api/learning`` / ``/api/me`` / ``/api/atlas`` の全ルートが
  ``LearnerDisplayRoute``（戻り値が ``project_for_learner`` を通る）であること
- (b) それらの ``response_model`` に学習者向け禁止キーのフィールドが無いこと
- (c) DP2: 統合語彙が旧 4 系統 + 新形の内部 ID を捕まえ、論文の式番号・数式を捕まえないこと
- (d) ``core/display_projection.py`` が FastAPI / sqlalchemy / LLM を import しないこと
- (e) 旧遮断器が語彙を ``display_projection`` から参照していること（同一オブジェクト）
- (f) 表示キーの内部 ID・禁止キーが射影後に残らないこと（番地キーは残る = DP3）
"""

from __future__ import annotations

import sys
import typing
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    iter_app_routes,
)

from core import display_projection as dp  # noqa: E402

LEARNER_PREFIXES = ("/api/learning", "/api/me", "/api/atlas")
#: 学習者向けプレフィックスにあっても表示投影の対象外にするルート定義モジュール。
#: 招待（groups）は運用操作で、別のガードレールがキーを検査している（設計書 §4）。
EXCLUDED_ROUTE_MODULES = {"groups"}

#: 旧 4 系統（learner_context_common / deliberation.labels / theory_modules /
#: graph_paper_layer）の見本 + 今回追加した形。
BAD_TOKENS = (
    "ev_0001",
    "evidence_0012",
    "synth_claim_0001",
    "claim_span_001",
    "claim_0004",
    "span_001",
    "support:intro:2",
    "node_3",
    "derivation_eq_tex_b16",
    "system_derivation_2",
    "sys_1_step_2",
    "step_001",
    "theory_op_0001",
    "eq_op_0007",
    "comp_003",
    "0f8fad5b-d9cb-469f-a165-70867728950e",
    "eq_eqcand_inline_blk_3df32664_",
    "eq_blk_004_0084",
    "eq_tex_b14",
    "claim_span_12",
    "theory_op_3",
    "sym_0",
    "k1:abcdef0123456789",
)
GOOD_TOKENS = (
    "eq_12",
    "eq_2_7",
    "eq_(3.1)",
    "E=mc^2",
    "x_1",
    r"\alpha_i",
    "claim_type",
    "derivation_in",
    "mention_claim",
    "node_id",
    "式 (12)",
    "step by step",
)


def _route_module(route) -> str:
    endpoint = getattr(route, "endpoint", None)
    inner = getattr(endpoint, "__wrapped__", endpoint)
    return str(getattr(inner, "__module__", "")).rsplit(".", 1)[-1]


def _unwrapped(route):
    try:
        return object.__getattribute__(route, "_route")
    except AttributeError:
        return route


@pytest.fixture(scope="module")
def learner_routes() -> list:
    pytest.importorskip("fastapi")
    from api.main import app

    return [
        route
        for route in iter_app_routes(app)
        if str(getattr(route, "path", "")).startswith(LEARNER_PREFIXES)
        and _route_module(route) not in EXCLUDED_ROUTE_MODULES
    ]


class TestEveryLearnerRouteIsProjected:
    def test_the_scan_finds_a_realistic_population(self, learner_routes):
        assert len(learner_routes) > 60, len(learner_routes)

    def test_every_learner_route_is_a_learner_display_route(self, learner_routes):
        offending = [
            getattr(route, "path", "")
            for route in learner_routes
            if type(_unwrapped(route)).__name__ != "LearnerDisplayRoute"
            or not getattr(getattr(route, "endpoint", None), "__learner_display_projected__", False)
        ]
        assert offending == [], (
            "学習者向けルートは route_class=LearnerDisplayRoute のルーターに置く: " + ", ".join(offending)
        )


def _model_field_names(annotation, seen: set) -> set[str]:
    names: set[str] = set()
    if annotation is None or id(annotation) in seen:
        return names
    seen.add(id(annotation))
    fields = getattr(annotation, "model_fields", None)
    if isinstance(fields, dict):
        for name, info in fields.items():
            names.add(getattr(info, "alias", None) or name)
            names |= _model_field_names(getattr(info, "annotation", None), seen)
        return names
    for arg in typing.get_args(annotation):
        names |= _model_field_names(arg, seen)
    return names


class TestResponseModels:
    def test_learner_response_models_have_no_forbidden_fields(self, learner_routes):
        offending: dict[str, list[str]] = {}
        for route in learner_routes:
            names = _model_field_names(getattr(route, "response_model", None), set())
            leaked = sorted(names & dp.FORBIDDEN_KEYS_LEARNER)
            if leaked:
                offending[getattr(route, "path", "")] = leaked
        assert offending == {}, offending


class TestVocabulary:
    @pytest.mark.parametrize("token", BAD_TOKENS)
    def test_internal_ids_are_caught(self, token):
        assert dp.is_internal_id_token(token), token
        assert dp.contains_internal_id(f"見出し {token} の説明"), token
        assert token not in dp.mask_internal_ids(f"見出し {token} の説明")

    @pytest.mark.parametrize("token", GOOD_TOKENS)
    def test_paper_numbers_and_math_are_not_caught(self, token):
        assert not dp.contains_internal_id(token), token


class TestPurity:
    def test_display_projection_imports_no_framework(self):
        src = (BACKEND / "core" / "display_projection.py").read_text(encoding="utf-8")
        assert_source_does_not_import(
            src,
            ["fastapi", "starlette", "sqlalchemy", "openai", "anthropic", "core.llm", "pydantic"],
            context="core/display_projection.py",
        )


class TestLegacyMaskersDelegate:
    """DP2: 旧遮断器は語彙を再定義せず display_projection のオブジェクトを参照する。"""

    def test_learner_context_common(self):
        from core import learner_context_common as lcc

        assert lcc._INTERNAL_ID_LABEL_RES is dp.LEARNER_INTERNAL_ID_LABEL_RES
        assert lcc._EMBEDDED_INTERNAL_ID_RE is dp.LEARNER_EMBEDDED_INTERNAL_ID_RE
        assert lcc.ROLE_INTERNAL_TOKEN_RE is dp.LEARNER_ROLE_INTERNAL_TOKEN_RE
        assert lcc._EXTRA_INTERNAL_TOKEN_RE is dp.LEARNER_EXTRA_INTERNAL_TOKEN_RE
        assert lcc._EQUATION_NUMBER_LABEL_RE is dp.LEARNER_EQUATION_NUMBER_LABEL_RE
        assert lcc._UUID_LABEL_RE is dp.UUID_FULL_RE
        assert lcc._GENERIC_ITEM_LABELS is dp.GENERIC_ITEM_LABELS

    def test_deliberation_labels(self):
        from core.deliberation import labels

        assert labels._INTERNAL_ID_PREFIX_RES is dp.DELIBERATION_INTERNAL_ID_PREFIX_RES
        assert labels._EMBEDDED_INTERNAL_ID_RES is dp.DELIBERATION_EMBEDDED_INTERNAL_ID_RES
        assert labels._PAPER_EQUATION_NUMBER_RE is dp.PAPER_EQUATION_NUMBER_RE
        assert labels._EQUATION_ID_TOKEN_RE is dp.DELIBERATION_EQUATION_ID_TOKEN_RE

    def test_theory_modules_schema(self):
        from core.theory_modules import schema

        assert schema.INTERNAL_ID_RE is dp.THEORY_MODULE_INTERNAL_ID_RE
        assert schema.FORBIDDEN_KEYS is dp.THEORY_MODULE_FORBIDDEN_KEYS
        assert schema.UNIDENTIFIED_ELEMENT_TEXT is dp.UNIDENTIFIED_ELEMENT_TEXT
        assert schema._EQUATION_ID_WITH_TAIL_RE is dp.THEORY_MODULE_EQUATION_ID_WITH_TAIL_RE

    def test_graph_paper_layer_schema(self):
        from core.graph_paper_layer import schema

        assert schema.FORBIDDEN_KEYS is dp.PAPER_LAYER_FORBIDDEN_KEYS
        assert schema.INTERNAL_ID_PREFIXES is dp.PAPER_LAYER_INTERNAL_ID_PREFIXES

    def test_other_maskers(self):
        from core import course_content_builder, reference_health
        from core.discuss import opening
        from core.doubt import seminar_brief

        assert course_content_builder._INTERNAL_EQUATION_ID_RE is dp.COURSE_CONTENT_INTERNAL_EQUATION_ID_RE
        assert course_content_builder._LEARNER_INTERNAL_ID_RE is dp.COURSE_CONTENT_LEARNER_INTERNAL_ID_RE
        assert reference_health._INTERNAL_ID_RE is dp.REFERENCE_HEALTH_INTERNAL_ID_RE
        assert opening._FORBIDDEN_NUMERIC_KEYS is dp.DISCUSS_OPENING_FORBIDDEN_NUMERIC_KEYS
        assert seminar_brief._FORBIDDEN_NUMERIC_KEYS is dp.SEMINAR_BRIEF_FORBIDDEN_NUMERIC_KEYS

    def test_teacher_forbidden_keys_cover_legacy_sets(self):
        assert set(dp.THEORY_MODULE_FORBIDDEN_KEYS) <= dp.FORBIDDEN_KEYS_TEACHER
        assert set(dp.PAPER_LAYER_FORBIDDEN_KEYS) <= dp.FORBIDDEN_KEYS_TEACHER

    def test_learner_forbidden_keys_cover_learner_legacy_sets(self):
        assert set(dp.DISCUSS_OPENING_FORBIDDEN_NUMERIC_KEYS) <= dp.FORBIDDEN_KEYS_LEARNER
        assert set(dp.SEMINAR_BRIEF_FORBIDDEN_NUMERIC_KEYS) <= dp.FORBIDDEN_KEYS_LEARNER
        assert set(dp.PAPER_LAYER_FORBIDDEN_KEYS) <= dp.FORBIDDEN_KEYS_LEARNER


def _walk(value, key=None):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, None
            yield from _walk(v, k)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _walk(v, key)
    else:
        yield key, value


class TestLeakScan:
    def _payload(self) -> dict:
        sentences = {token: f"この要素は {token} から導かれます" for token in BAD_TOKENS}
        return {
            "label": BAD_TOKENS[0],
            "title": BAD_TOKENS[1],
            "facts": [sentences[t] for t in BAD_TOKENS],
            "items": [
                {
                    "id": token,
                    "element_type": "theory_claim",
                    "label": token,
                    "sublabel": sentences[token],
                    "relation_label": "を根拠とする",
                    "confidence": 0.8,
                    "node_ids": [token],
                    "anything_else": sentences[token],
                }
                for token in BAD_TOKENS
            ],
            "score": 3,
            "counts": {"a": 1},
            "stable_key": "k1:abcdef0123456789",
            "meta": {"weight": 0.1, "chain": list(BAD_TOKENS)},
        }

    def test_no_bad_token_outside_address_keys_and_forbidden_keys_gone(self):
        projected = dp.project_for_learner(self._payload())
        keys = {k for k, _ in _walk(projected) if k is not None}
        assert not (keys & dp.FORBIDDEN_KEYS_LEARNER)
        for key, value in _walk(projected):
            if not isinstance(value, str) or dp.is_address_key(key):
                continue
            if key == "chain":  # 1 トークンの番地の列（DP3）
                continue
            for token in BAD_TOKENS:
                assert token not in value, (key, value)

    def test_address_keys_keep_their_values(self):
        projected = dp.project_for_learner(self._payload())
        assert [item["id"] for item in projected["items"]] == list(BAD_TOKENS)
        assert projected["items"][0]["node_ids"] == [BAD_TOKENS[0]]
        assert projected["meta"]["chain"] == list(BAD_TOKENS)


# ---------------------------------------------------------------------------
# DP7: 教員向けの名指しの面（グラフレビュー・論文の順・理論モジュール・ゼミ前ブリーフ・
# W層の要素文脈・グラフ全体対話）は project_for_teacher を通る
# ---------------------------------------------------------------------------

import ast  # noqa: E402
import json  # noqa: E402

FIXTURES = BACKEND / "tests" / "fixtures" / "theory_modules"

#: (ルート定義モジュール, エンドポイント関数名)。大きな admin ルーターに同居する面は
#: エンドポイント本体で射影の入口を明示的に呼ぶ（ルーター全体には掛けない）。
TEACHER_NAMED_ENDPOINTS = (
    ("theory_components", "get_component_graph"),
    ("theory_components", "get_document_paper_layer"),
    ("theory_components", "get_document_theory_modules"),
    ("theory_components", "get_document_theory_modules_related"),
    ("deliberation", "get_element_overview"),
    ("deliberation", "get_element_context"),
    ("deliberation", "open_graph_dialogue_session"),
    ("deliberation", "post_graph_dialogue_message"),
)
#: 射影の入口（いずれも最終的に ``project_for_teacher`` を呼ぶ）。
TEACHER_PROJECTION_ENTRIES = ("project_for_teacher(", "project_teacher_dto(", "_teacher_view(")


def _function_source(module: str, name: str) -> str:
    path = BACKEND / "api" / "routes" / f"{module}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
    raise AssertionError(f"{module}.{name} が見つかりません")


class TestTeacherNamedSurfacesAreProjected:
    def test_seminar_brief_router_is_a_teacher_display_route(self):
        from display_route import TeacherDisplayRoute
        from routes import seminar_brief

        assert seminar_brief.admin_router.route_class is TeacherDisplayRoute
        assert seminar_brief.admin_router.routes, "ゼミ前ブリーフのルートが登録されていない"
        for route in seminar_brief.admin_router.routes:
            assert isinstance(route, TeacherDisplayRoute), route.path

    @pytest.mark.parametrize("module,name", TEACHER_NAMED_ENDPOINTS)
    def test_named_endpoint_calls_the_teacher_projection(self, module, name):
        src = _function_source(module, name)
        assert any(entry in src for entry in TEACHER_PROJECTION_ENTRIES), f"{module}.{name}"

    def test_projection_entries_reach_project_for_teacher(self):
        assert "project_for_teacher(" in _function_source("theory_components", "project_teacher_dto")
        view = _function_source("deliberation", "_teacher_view")
        assert "project_teacher_dto(" in view and "project_for_teacher(" in view

    def test_admin_routers_are_not_wrapped_wholesale(self):
        # 件数・段階ラベルを正当に返す admin API が同居するため、大きなルーターには掛けない。
        from display_route import TeacherDisplayRoute
        from routes import deliberation, theory_components

        assert theory_components.router.route_class is not TeacherDisplayRoute
        assert deliberation.router.route_class is not TeacherDisplayRoute

    def test_teacher_ui_does_not_read_stripped_keys(self):
        # 教員の画面が落とすキー（数値・指紋）を読んでいないこと（module_key は番地なので残す）。
        sources = [
            ROOT / "frontend" / "public" / "js" / name
            for name in ("admin-graph-review.js", "deliberation.js")
        ]
        stripped = sorted(
            k for k in dp.FORBIDDEN_KEYS_TEACHER if k not in {"count", "counts", "limit", "k", "confidence"}
        )
        for path in sources:
            text = path.read_text(encoding="utf-8")
            for key in stripped:
                assert f".{key}" not in text and f'["{key}"]' not in text, (path.name, key)


class _NoRows:
    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _FakeSession:
    def execute(self, *args, **kwargs):
        return _NoRows()

    def close(self):
        pass


def _fixture_docs():
    return sorted(FIXTURES.glob("*.json"))


def _equation_ids(artifacts: dict) -> set[str]:
    payload = artifacts.get("equation_semantics") or {}
    return {
        str(r.get("equation_id"))
        for r in (payload.get("records") or payload.get("equations") or [])
        if isinstance(r, dict) and r.get("equation_id")
    }


def _leaks(value, equation_ids: set[str], key=None, out=None):
    import re

    out = [] if out is None else out
    if isinstance(value, dict):
        for k, v in value.items():
            _leaks(v, equation_ids, k, out)
    elif isinstance(value, list):
        for v in value:
            _leaks(v, equation_ids, key, out)
    elif isinstance(value, str) and not dp.is_address_key(key) and not dp.is_address_value(value):
        if dp.is_internal_id_token(value) and not dp.is_display_key(key):
            return out  # 1 トークンの番地の列（DP3）
        for m in dp.INTERNAL_ID_TOKEN_RE.finditer(value):
            out.append((key, m.group(0)))
        for eid in equation_ids:
            if re.search(r"(?<![A-Za-z0-9_])" + re.escape(eid) + r"(?![A-Za-z0-9_])", value):
                out.append((key, eid))
    return out


@pytest.fixture
def theory_routes(monkeypatch):
    import routes.theory_components as tc

    state: dict = {}
    monkeypatch.setattr(tc, "_ensure_document_viewable", lambda *a: None)
    monkeypatch.setattr(tc, "_components_for_document", lambda doc: [])
    monkeypatch.setattr(
        tc,
        "_stored_component_graph",
        lambda doc: dict(json.loads(json.dumps(state["graph"])), edges=[], graph_id="g1", document_id=doc),
    )
    monkeypatch.setattr(tc, "_normalize_stored_component_graph", lambda doc, graph, comps: graph)
    monkeypatch.setattr(tc, "document_run_artifacts", lambda doc, **k: state["artifacts"])
    monkeypatch.setattr(tc, "_pg_session", lambda: _FakeSession())
    monkeypatch.setattr(tc, "_paper_layer_figure_rows", lambda doc: [])
    monkeypatch.setattr(tc, "_paper_layer_explanation_rows", lambda doc: [])
    return tc, state


class TestTeacherFixtureLeakScan:
    """実論文 3 本の fixture から組んだ教員向け DTO の表示文に内部 ID が残らない。"""

    @pytest.mark.parametrize("fixture", _fixture_docs(), ids=lambda p: p.stem)
    def test_theory_component_dtos_have_no_internal_ids_in_display_text(self, theory_routes, fixture):
        tc, state = theory_routes
        data = json.loads(fixture.read_text(encoding="utf-8"))
        state["graph"], state["artifacts"] = data["graph_json"], data["artifacts"]
        doc = data["document_id"]
        equation_ids = _equation_ids(data["artifacts"])
        dtos = {
            "component-graph": tc.get_component_graph(doc, current_user={}).model_dump(),
            "paper-layer": tc.get_document_paper_layer(doc, current_user={}),
            "theory-modules": tc.get_document_theory_modules(doc, current_user={}),
        }
        for name, dto in dtos.items():
            assert not _leaks(dto, equation_ids), (name, _leaks(dto, equation_ids)[:5])
            keys = {k for k, _ in _walk(dto) if k is not None}
            assert not (keys & (dp.FORBIDDEN_KEYS_TEACHER - {"confidence"})), name

    def test_fixture_population_is_realistic(self, theory_routes):
        # 射影なしの DTO には実際に内部 ID が表示文に含まれていた（このテストが空振りでない）。
        tc, state = theory_routes
        data = json.loads((FIXTURES / "arxiv_2609_15375v1_tex.json").read_text(encoding="utf-8"))
        state["graph"], state["artifacts"] = data["graph_json"], data["artifacts"]
        raw = tc.build_paper_layer_for_document(data["document_id"])
        assert _leaks(raw, set())

    def test_related_dto_is_projected(self, theory_routes, monkeypatch):
        tc, _state = theory_routes
        import routes.library as library_routes
        from core.library import structural_matches

        monkeypatch.setattr(tc, "_resolve_document", lambda ref: {"id": "11111111-1111-1111-1111-111111111111"})
        monkeypatch.setattr(library_routes, "_document_access_checker", lambda uid: (lambda d: True))
        monkeypatch.setattr(
            structural_matches,
            "build_related_for_document",
            lambda *a, **k: {
                "available": True,
                "facts": ["同じ構造の外枠 theory_op_0003 を持つ論文があります。"],
                "modules": [{"module_key": "m2:abc", "titles": ["A"], "structure_fingerprint": "fp"}],
                "hidden": False,
            },
        )
        out = tc.get_document_theory_modules_related("doc", current_user={"id": "u"})
        assert "theory_op_0003" not in out["facts"][0]
        assert out["modules"][0]["module_key"] == "m2:abc"  # 番地（UI のキー）は残る
        assert "structure_fingerprint" not in out["modules"][0]
