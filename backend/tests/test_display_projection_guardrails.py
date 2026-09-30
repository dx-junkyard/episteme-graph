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
