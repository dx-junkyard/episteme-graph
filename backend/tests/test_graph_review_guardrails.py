"""グラフ対話レビュー — 不変条項（GR1〜GR8）の構造的ガードレール。

正本: ``docs/features/graph_dialogue_review_design.md`` §2/§8。
- GR1 確定は人間のみ（AI 応答経路から承認 API を呼ばない・プロンプトの契約フレーズ）
- GR3 数値非表示（confidence の生値を grounding / 応答に出さない）
- GR5 コスト相乗り（専用の上限 env を作らない・CostGate は W層と共有）
- GR8 描画の正本一元化（レビュー画面は LectureStudio.graphView へ委譲）
- migration 075 は sessions のみ（annotations の CHECK を触らない）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    extract_function_source,
)

CORE_PATH = BACKEND / "core" / "deliberation" / "graph_dialogue.py"
CORE_SRC = CORE_PATH.read_text(encoding="utf-8")
ROUTE_SRC = (BACKEND / "api" / "routes" / "deliberation.py").read_text(encoding="utf-8")
TC_SRC = (BACKEND / "api" / "routes" / "theory_components.py").read_text(encoding="utf-8")
MIGRATION_SRC = (BACKEND / "db" / "075_graph_dialogue_sessions.sql").read_text(encoding="utf-8")
JS_PATH = ROOT / "frontend" / "public" / "js" / "admin-graph-review.js"
JS_SRC = JS_PATH.read_text(encoding="utf-8")


class TestCoreIsolation:
    def test_core_does_not_import_fastapi_or_routes(self):
        assert_module_tree_does_not_import(CORE_PATH, {"fastapi", "routes", "services"})

    def test_core_does_not_write_annotations(self):
        # グラフ全体対話は候補注釈を生成しない（migration 075 の前提）。
        assert "element_annotations" not in CORE_SRC
        # structured output は reply のみ（annotations フィールドを持たない）。
        start = CORE_SRC.index("class _GraphTurnOutput")
        end = CORE_SRC.index("@dataclass", start)
        assert "annotations" not in CORE_SRC[start:end]

    def test_no_dedicated_cost_env(self):
        # GR5: CostGate は W層（dialogue.check_and_count_llm_call）に相乗りし、
        # 専用の上限 env（GRAPH_CHAT_MAX_* 等）を作らない。
        assert "GRAPH_CHAT_MAX" not in CORE_SRC.upper()
        assert "graph_chat_max" not in CORE_SRC
        assert "dialogue.check_and_count_llm_call" in ROUTE_SRC


class TestPromptContract:
    def test_hypothesis_style_and_no_approval_directive(self):
        # GR1: 仮説文体 + 承認判断の非代行 + 捏造ガード + 数値禁止の契約フレーズ。
        assert "〜の可能性があります" in CORE_SRC
        assert "承認・却下の判断は教員が行います" in CORE_SRC
        assert "グラフに現れていない関係・根拠を作らないでください" in CORE_SRC
        assert "数値の確信度・スコアを述べないでください" in CORE_SRC

    def test_grounding_never_emits_confidence_numbers(self):
        # graph_grounding_to_text が edge/node の confidence を書き出す行を持たない。
        body = extract_function_source(CORE_SRC, "graph_grounding_to_text")
        assert "confidence" not in body


class TestNoAiApprovalPath:
    def test_llm_reply_path_never_calls_review_apis(self):
        # AI 応答の処理（sendChat / ensureSession / renderChatLog 等の対話コード）から
        # 承認 API を呼ばない。承認 API の呼び出しは明示ボタンのハンドラ
        # （reviewComponent / approveClaim）のみに存在する。
        approve_calls = [m.start() for m in re.finditer(r"/approve|/reject|/review\"", JS_SRC)]
        assert approve_calls, "承認 API 呼び出しが JS に存在すること（画面の目的）"
        for fn in ("sendChat", "ensureSession", "renderChatLog", "renderChatAnnotations", "decideAnnotation", "switchChatMode"):
            body = _js_function_body(JS_SRC, fn)
            assert "theory-components" not in body, fn
            assert "/claims/" not in body, fn

    def test_core_never_imports_review_transition(self):
        # core は theory_components を SELECT で**読む**だけ（live review_status の合成）。
        # 書き込み・承認遷移の呼び出しは一切持たない（GR1/GR2）。
        assert "UPDATE theory_components" not in CORE_SRC
        assert "INSERT INTO theory_components" not in CORE_SRC
        assert "_transition_component_review" not in CORE_SRC
        assert "review_claim" not in CORE_SRC


def _js_function_body(source: str, name: str) -> str:
    """``function name(...) { ... }`` の本体をブレース対応で抜き出す（ES5 前提）。"""
    marker = "function " + name + "("
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[brace : i + 1]
    raise AssertionError(f"unterminated function: {name}")


class TestMigration075:
    def test_only_sessions_check_is_touched(self):
        assert "deliberation_sessions" in MIGRATION_SRC
        assert "document_graph" in MIGRATION_SRC
        # annotations の CHECK を触らない（グラフ全体対話は注釈なし）。
        assert "ALTER TABLE element_annotations" not in MIGRATION_SRC

    def test_element_types_registry_not_polluted(self):
        # ElementRef の語彙（overview / context / annotations の解決対象）には加えない。
        from core.deliberation import schema as delib_schema

        assert "document_graph" not in delib_schema.ELEMENT_TYPES


class TestFeatureRegistration:
    def test_graph_chat_feature_is_registered(self):
        from core.llm_usage.schema import KNOWN_FEATURES
        from core import llm_policy

        assert "deliberation:graph_chat" in KNOWN_FEATURES
        assert llm_policy.scene_for_feature("deliberation:graph_chat") == llm_policy.SCENE_DELIBERATION
        # 実効モデル解決が例外なく通る（既定 env → fast tier）。
        assert llm_policy.resolve_scene_model("deliberation:graph_chat").model


class TestApprovalServerGate:
    def test_approval_problem_fields_match_studio_gate(self):
        # サーバ側の承認可能性チェックはスタジオの lsTheoryCanApprove と同じ対象
        # フィールド集合（設計書 §4）。
        assert set(TC_SRC_FIELDS()) == {
            "inputs", "outputs", "preconditions", "constraints", "invalid_conditions",
        }

    def test_approve_endpoint_does_not_accept_body_fields(self):
        # 遷移専用 API: リクエストボディで内容を受けない（同時編集の巻き戻し防止）。
        src = extract_function_source(TC_SRC, "approve_theory_component")
        assert "body" not in src.split("current_user")[0]


def TC_SRC_FIELDS():
    import routes.theory_components as tc

    return tc._APPROVAL_EVIDENCE_FIELDS


class TestGraphViewDelegation:
    def test_review_screen_uses_shared_graph_view(self):
        # GR8: レビュー画面は vis のノード/エッジ仕様・オプションを自前で持たない。
        assert "graphView" in JS_SRC
        assert "visNodeSpec" in JS_SRC and "visEdgeSpec" in JS_SRC and "networkOptions" in JS_SRC
        # スタイル定数（borderDashes / groups の色表）は studio 側の正本のみに存在する。
        assert "borderDashes" not in JS_SRC
        assert "#f0fdf4" not in JS_SRC

    def test_studio_exposes_graph_view(self):
        studio_src = (ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8")
        assert "graphView: {" in studio_src
        assert "filterByLayer: lsGraphFilterByLayer" in studio_src
        assert "networkOptions: lsGraphNetworkOptions" in studio_src
