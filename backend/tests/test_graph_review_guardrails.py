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
DIALOGUE_SRC = (BACKEND / "core" / "deliberation" / "dialogue.py").read_text(encoding="utf-8")
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
    def test_stance_label_contract_and_no_approval_directive(self):
        # §15: 留保はラベルで（文ごとの仮説文体は撤去）+ 承認判断の非代行 +
        # 捏造ガード + 数値禁止の契約フレーズ。
        assert (
            "文ごとに「〜の可能性があります」のような留保を繰り返さず、簡潔な断定調で書いてください。"
            in CORE_SRC
        )
        assert "不確かさは返答全体に付く「" in CORE_SRC
        assert "」のラベルで示されます。" in CORE_SRC
        assert "承認・却下の判断は教員が行います" in CORE_SRC
        assert "グラフに現れていない関係・根拠を作らないでください" in CORE_SRC
        assert "数値の確信度・スコアを述べないでください" in CORE_SRC

    def test_per_sentence_hedging_instruction_is_gone(self):
        # 旧契約（文ごとの留保を要求する指示）が復活していないこと（§15 のオーナー裁定）。
        for forbidden in (
            "のような仮説の文体で述べてください",
            "内容の正しさについては断定せず",
            "仮説的な言い回しにしてください",
        ):
            assert forbidden not in CORE_SRC, forbidden
            assert forbidden not in DIALOGUE_SRC, forbidden

    def test_math_delimiter_contract_in_both_headers(self):
        # 生 LaTeX（\(…\)）の漏れを塞ぐ表記契約は両対話モジュールのヘッダに入る。
        # 文言の正本は共通骨格（core/llm_worker/chat_turn.py）の1箇所で、各モジュールは
        # それを連結する（リテラルの二重管理をしない）。
        from core.deliberation import dialogue as dialogue_mod
        from core.deliberation import graph_dialogue as graph_mod
        from core.llm_worker.chat_turn import MATH_DELIMITER_INSTRUCTION

        needle = "数式は必ず `$…$` で区切ってください"
        assert needle in MATH_DELIMITER_INSTRUCTION
        assert MATH_DELIMITER_INSTRUCTION in graph_mod._INSTRUCTION_HEADER  # noqa: SLF001
        assert MATH_DELIMITER_INSTRUCTION in dialogue_mod._INSTRUCTION_HEADER  # noqa: SLF001
        # 各モジュールに文言リテラルをコピペし直していないこと。
        assert needle not in CORE_SRC
        assert needle not in DIALOGUE_SRC

    def test_spoken_contract_is_opt_in_and_single_call(self):
        # 読み上げ契約は response_mode="spoken" のときだけ足す（1ターン=1コールは不変）。
        from core.deliberation import dialogue as dialogue_mod
        from core.deliberation import graph_dialogue as graph_mod

        needle = "音声で読み上げるための spoken を別に返してください。"
        for build in (graph_mod.build_llm_messages, dialogue_mod.build_llm_messages):
            assert needle not in build([], "q", "G")[0]["content"]
            assert needle in build([], "q", "G", response_mode="spoken")[0]["content"]
        for src in (CORE_SRC, DIALOGUE_SRC):
            assert 'response_mode == "spoken"' in src
            # 追加の LLM コール（2回目の generate_*）を作らない。1ターンの実行は
            # 共通骨格 structured_turn の1回だけで、直接呼びはそちらへ集約されている。
            assert src.count("structured_turn(") == 1
            assert "generate_conversation_turn(" not in src

    def test_stance_label_constant_has_a_single_home(self):
        """ラベル定数の正本は core/label_vocab.py の1箇所だけ（リテラル重複の禁止）。"""
        from core.label_vocab import AI_READING_LABEL

        assert AI_READING_LABEL == "AIの読み（未確認）"
        for src, name in (
            (CORE_SRC, "core/deliberation/graph_dialogue.py"),
            (DIALOGUE_SRC, "core/deliberation/dialogue.py"),
            (ROUTE_SRC, "api/routes/deliberation.py"),
        ):
            assert "from core.label_vocab import" in src and "AI_READING_LABEL" in src, name
            assert AI_READING_LABEL not in src, name  # リテラルの再定義・コピペを禁止

        offenders = []
        for path in sorted((BACKEND / "core").rglob("*.py")) + sorted(
            (BACKEND / "api").rglob("*.py")
        ):
            if path.name == "label_vocab.py":
                continue
            if AI_READING_LABEL in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(BACKEND)))
        assert not offenders, offenders

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


class TestApprovalKeepsAnalysisWarnings:
    """是正 F6（2026-09-10・六つのレンズ §4 第1波 #5 / 05_ai.md 提案2）。

    承認時に解析時の警告を空配列で消していた（``validation_warnings = CAST('[]' AS jsonb)``
    と ``payload["validation_warnings"] = []``）。消すと「何を見て承認したか」を
    後から再構成できない（vision §4 改訂原則1 / 原則3）。警告は退避し、承認画面が
    並置する。承認可能性の判定（サーバ強制）は変えない。
    """

    #: 空配列の代入（SQL / Python どちらの綴りも）。
    CLEARED = re.compile(
        r"validation_warnings\s*=\s*(?:CAST\(\s*'\[\]'|\[\s*\]|json\.dumps\(\s*\[\s*\])"
    )

    def test_transition_does_not_clear_warnings(self):
        src = extract_function_source(TC_SRC, "_transition_component_review")
        assert self.CLEARED.search(src) is None

    def test_normalize_payload_does_not_clear_warnings_on_approval(self):
        src = extract_function_source(TC_SRC, "_normalize_payload")
        assert self.CLEARED.search(src) is None
        # 警告の導出そのものは残す（保持であって計算の廃止ではない）。
        assert "_validation_warnings(payload)" in src

    def test_approval_gate_is_unchanged(self):
        """警告を残すことで承認が止まるようにはしない（弁を増やさない）。

        2026-09-10（是正 F11 / A-04）以降、承認ルートは警告を1回**読む** —
        監査の `decision_context` に「何を見て承認したか」を残すためだけで、
        承認可能性の判定（`_component_approval_problems`）には入れない。
        """
        src = extract_function_source(TC_SRC, "approve_theory_component")
        assert "_component_approval_problems(existing)" in src
        # 警告を消さない（是正 F6）。
        assert self.CLEARED.search(src) is None
        # 判定と 422 の区画に警告は現れない（弁にしない）。
        gate = src.split("problems = ")[1].split("return _transition_component_review")[0]
        assert "validation_warnings" not in gate
        # 判定関数そのものも警告を見ない。
        problems_src = extract_function_source(TC_SRC, "_component_approval_problems")
        assert "validation_warnings" not in problems_src

    def test_graph_nodes_project_warnings_for_the_review_screen(self):
        src = extract_function_source(TC_SRC, "_node_validation_warnings")
        # 数値は載せない（field と事実文 message のみ）。
        assert '"message"' in src and '"field"' in src
        for banned in ("confidence", "weight", "score"):
            assert banned not in src

    def test_review_screen_places_warnings_next_to_the_approve_button(self):
        assert "ANALYSIS_WARNING_HEADING" in JS_SRC
        assert "node.validation_warnings" in JS_SRC
        # 件数バッジは作らない（GR3）。
        assert "validation_warnings.length + \"件\"" not in JS_SRC


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
