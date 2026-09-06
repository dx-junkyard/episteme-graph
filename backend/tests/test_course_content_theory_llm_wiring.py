"""コース内容生成・理論コンポーネント抽出の U層 / M層 配線ガードレール（2026-09-05）。

対象:
  - ``core/course_content_builder.py``（コース内容生成 = トピック授業用ドラフト）
  - ``core/theory_components.py``（原稿スタジオ「理論」タブの抽出・補完）
  - ``api/routes/theory_components.py``（上記の呼び出し元 = usage_context を張る側）

固定する不変条件:
  1. U層: どちらの経路も ``usage_context`` 配下で LLM を呼ぶ（帰属なし＝
     ``unattributed`` に落ちない）。feature は ``KNOWN_FEATURES`` に収載済み。
  2. M層: LLM 呼び出しに**明示のモデル文字列を渡さない**（``get_llm_params("fast")``
     の ``model`` を渡すと解決順①「呼び出し時指定」に化けて、運用タブ・本人既定・
     実行時 override が一切効かなくなる — 設計書 §3 / M1）。
  3. 新 feature が scene（原稿スタジオ）へ束ねられ、ポリシー行も env も無い環境では
     従来と同じ fast tier に解決される（挙動不変）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FEATURE_COURSE_CONTENT = "admin:course_content"
FEATURE_COMPONENT_EXTRACT = "admin:component_extract"

_COURSE_CONTENT_BUILDER = "core/course_content_builder.py"
_CORE_THEORY = "core/theory_components.py"
_ROUTES_THEORY = "api/routes/theory_components.py"

# core/llm.py 側で「model が None のときだけ resolve_scene_model に委ねる」入口を持つ関数。
_LLM_ENTRY_FUNCTIONS = {
    "generate_text",
    "generate_text_with_structured_output",
    "generate_conversation_turn",
    "generate_structured_with_images",
}


def _read(rel_path: str) -> str:
    return (BACKEND / rel_path).read_text(encoding="utf-8")


def _llm_call_nodes(rel_path: str) -> list[ast.Call]:
    tree = ast.parse(_read(rel_path))
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "")
        if name in _LLM_ENTRY_FUNCTIONS:
            calls.append(node)
    return calls


# ===========================================================================
# 1. M層: 明示モデルを渡さない（get_llm_params の model を使わない）
# ===========================================================================


class TestNoExplicitModelArgument:
    @pytest.mark.parametrize("rel_path", [_COURSE_CONTENT_BUILDER, _CORE_THEORY])
    def test_llm_calls_do_not_pass_model(self, rel_path):
        calls = _llm_call_nodes(rel_path)
        assert calls, f"{rel_path}: no LLM entry-point call found (test target moved?)"
        for call in calls:
            for keyword in call.keywords:
                if keyword.arg != "model":
                    continue
                assert isinstance(keyword.value, ast.Constant) and keyword.value.value is None, (
                    f"{rel_path}:{call.lineno}: LLM 呼び出しに明示のモデルを渡している。"
                    "model は渡さず core/llm.py 入口の resolve_scene_model に委ねること（M1）。"
                )

    @pytest.mark.parametrize("rel_path", [_COURSE_CONTENT_BUILDER, _CORE_THEORY])
    def test_get_llm_params_model_is_never_read(self, rel_path):
        """``get_llm_params(...)`` の戻り値から ``model`` を読まない
        （reasoning_effort の利用は挙動不変のため許容する）。"""
        src = _read(rel_path)
        for forbidden in ('params["model"]', "params['model']", 'params.get("model")', "params.get('model')"):
            assert forbidden not in src, f"{rel_path}: {forbidden} は M層の解決を素通りする"


# ===========================================================================
# 2. U層: usage_context の配線
# ===========================================================================


class TestUsageContextWiring:
    def test_course_content_builder_declares_and_uses_feature(self):
        src = _read(_COURSE_CONTENT_BUILDER)
        assert f'FEATURE_COURSE_CONTENT = "{FEATURE_COURSE_CONTENT}"' in src
        assert "from core.llm_usage.context import usage_context" in src
        assert "with usage_context(\n        FEATURE_COURSE_CONTENT," in src

    def test_course_content_drafts_receive_attribution_ids(self):
        """``build_course_content`` は user_id / course_id を生成関数へ渡す
        （渡さないと誰のどのコースの消費か分からない = U層の帰属が空になる）。"""
        src = _read(_COURSE_CONTENT_BUILDER)
        assert "_generate_course_topic_drafts(\n            course, enriched_topics, user_id=str(user_id), course_id=str(course_id)\n        )" in src

    def test_theory_component_extract_route_wraps_llm_call(self):
        src = _read(_ROUTES_THEORY)
        assert f'usage_context(\n            "{FEATURE_COMPONENT_EXTRACT}"' in src
        # request handler では with 版のみ（bind_usage_context はスレッド再利用で漏れる）
        assert "bind_usage_context(" not in src

    def test_core_theory_components_propagates_context_to_worker_thread(self):
        """core 側はプールスレッドで LLM を呼ぶため、contextvars のスナップショットを
        伝搬させる ``_submit_with_context`` を経由し続けること。"""
        src = _read(_CORE_THEORY)
        assert "contextvars.copy_context()" in src
        assert src.count("_submit_with_context(_call_llm)") == 2


# ===========================================================================
# 2b. 実地検証: LLM 呼び出し時点の帰属が実際に立っている
# ===========================================================================


class TestAttributionAtCallTime:
    def test_course_content_draft_call_is_attributed(self, monkeypatch):
        import core.course_content_builder as builder
        from core.llm_usage.context import current_usage_context

        seen: dict = {}

        def _fake_structured(**kwargs):
            ctx = current_usage_context()
            seen["feature"] = ctx.feature
            seen["user_id"] = ctx.user_id
            seen["course_id"] = ctx.course_id
            seen["model_kwarg"] = kwargs.get("model", "<absent>")
            return {
                "key_concepts": [],
                "student_material": {"source_format": "eg-markdown-v1", "source_text": "本文"},
                "spoken_script": "読み上げ",
                "cautions": [],
                "check_questions": [],
            }

        monkeypatch.setattr(builder, "generate_text_with_structured_output", _fake_structured)
        result = builder._generate_course_topic_drafts(
            {"title": "コース"},
            [{"id": "t1", "title": "セクション1"}],
            user_id="u-1",
            course_id="c-1",
        )
        assert result["drafted_topics"] == 1
        assert seen["feature"] == FEATURE_COURSE_CONTENT
        assert seen["user_id"] == "u-1"
        assert seen["course_id"] == "c-1"
        assert seen["model_kwarg"] == "<absent>", "model を渡すと M層の解決を素通りする"

    def test_theory_enrichment_call_inherits_context_across_thread(self, monkeypatch):
        """core 側はワーカースレッドで呼ぶため、呼び出し元の usage_context が
        スレッドを越えて届くことを実地で確認する。"""
        import core.theory_components as core_theory
        from core.llm_usage.context import current_usage_context, usage_context

        seen: dict = {}

        def _fake_generate_text(**kwargs):
            ctx = current_usage_context()
            seen["feature"] = ctx.feature
            seen["user_id"] = ctx.user_id
            seen["model_kwarg"] = kwargs.get("model", "<absent>")
            return '{"components": []}'

        monkeypatch.setattr(core_theory, "generate_text", _fake_generate_text)
        chunk = {"raw_text": "本文", "smiles_dsl": "", "variables": [], "ancestors": []}
        components = [{"name": "C1", "summary": ""}]
        with usage_context(FEATURE_COMPONENT_EXTRACT, user_id="u-9"):
            core_theory.enrich_theory_components_with_llm(chunk, components)
        assert seen["feature"] == FEATURE_COMPONENT_EXTRACT
        assert seen["user_id"] == "u-9"
        assert seen["model_kwarg"] == "<absent>"


# ===========================================================================
# 3. feature / scene の登録
# ===========================================================================


class TestFeatureRegistration:
    @pytest.mark.parametrize("feature", [FEATURE_COURSE_CONTENT, FEATURE_COMPONENT_EXTRACT])
    def test_feature_is_registered(self, feature):
        from core.llm_usage.schema import KNOWN_FEATURES

        assert feature in KNOWN_FEATURES

    @pytest.mark.parametrize("feature", [FEATURE_COURSE_CONTENT, FEATURE_COMPONENT_EXTRACT])
    def test_feature_resolves_to_lecture_studio_scene(self, feature):
        import core.llm_policy as llm_policy

        assert llm_policy.scene_for_feature(feature) == llm_policy.SCENE_LECTURE_STUDIO

    @pytest.mark.parametrize("feature", [FEATURE_COURSE_CONTENT, FEATURE_COMPONENT_EXTRACT])
    def test_feature_keeps_fast_tier_without_policy_or_env(self, feature, monkeypatch):
        """ポリシー行も env も無い環境では従来（``get_llm_params("fast")``）と同じ
        fast tier のモデルに解決される（挙動不変）。"""
        import core.llm_policy as llm_policy
        from core.llm_usage.context import usage_context

        # 専用 env を持たない = fast tier フォールバック（_FEATURE_TIER_ONLY）
        assert llm_policy._env_model_and_tier(feature) == (None, "fast")
        with usage_context(feature):
            resolved = llm_policy.resolve_scene_model(feature)
        assert resolved.source == llm_policy.SOURCE_TIER_DEFAULT
        assert resolved.model == llm_policy._tier_default_model("fast")
