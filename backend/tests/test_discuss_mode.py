"""discuss モード（「論文と話す」, discuss モード設計書 §6.2 Phase 1）のテスト。

test_content_grounding.py と同じ流儀（ソース文字列の静的検査。実 DB・実サーバ不使用）で、
以下を検証する:

- schemas.py: `discuss_scope` フィールドと `intent_mode` コメントの4値化
- learning.py: casual と同型の3点バイパス（意図分類・前提知識ゲート・detour化）+
  U層タグの4点すべてに discuss が配線されていること
- discuss 専用システムプロンプトに DM4（即答・生成プロンプト構造的必須）・
  DM1（この論文由来ではない明示）の必須要素が含まれること
- `_discussion` 予約 topic_id → 表示ラベル変換の配線
- スコープ2段切替（course_sources 既定 / all_visible / 不正値 422）と
  `list_course_source_document_ids` の存在、discuss の空ヒット事実文、
  可視集合への無断フォールバックが discuss 分岐に無いこと
- `entry_mode: "discuss"` の痕跡ペイロード配線
- lecture.py のヘルパーが正本（services.list_course_source_document_ids）へ委譲していること
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

LEARNING = ROOT / "backend" / "api" / "routes" / "learning.py"
LECTURE = ROOT / "backend" / "api" / "routes" / "lecture.py"
SERVICES = ROOT / "backend" / "api" / "services.py"
SCHEMAS = ROOT / "backend" / "api" / "schemas.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestSchemaFields:
    def test_intent_mode_comment_mentions_discuss(self):
        source = _read(SCHEMAS)
        body = source.split("class LearningChatRequest")[1].split("\nclass ")[0]
        assert '"discuss"' in body
        assert "論文と話す" in body

    def test_discuss_scope_field_exists(self):
        source = _read(SCHEMAS)
        body = source.split("class LearningChatRequest")[1].split("\nclass ")[0]
        assert "discuss_scope: str | None = None" in body
        assert "course_sources" in body
        assert "all_visible" in body


class TestDiscussionTopicLabel:
    def test_reserved_topic_id_constants_exist(self):
        source = _read(LEARNING)
        assert 'DISCUSSION_TOPIC_ID = "_discussion"' in source
        assert 'DISCUSSION_TOPIC_LABEL = "論文との議論"' in source

    def test_topic_title_label_conversion_wired(self):
        source = _read(LEARNING)
        block = source.split("topic_info = find_course_topic(course_data, topic_id)")[1][:500]
        assert "if topic_id == DISCUSSION_TOPIC_ID:" in block
        assert "topic_title = DISCUSSION_TOPIC_LABEL" in block
        # 既存トピックの通常フォールバックも維持されていること
        assert 'topic_title = topic_info["title"] if topic_info else topic_id' in block


class TestFourBypassPoints:
    """casual と同型の3点バイパス + U層タグの分岐先。discuss の配線漏れを防ぐ。"""

    def test_is_discuss_flag_defined_after_is_casual(self):
        source = _read(LEARNING)
        assert '_is_casual = (body.intent_mode or "").strip() == "casual"' in source
        assert '_is_discuss = (body.intent_mode or "").strip() == "discuss"' in source
        # _is_discuss は _is_casual の直後に定義されること（設計 §6.2 の指定順）
        after_casual = source.split('_is_casual = (body.intent_mode or "").strip() == "casual"')[1]
        assert '_is_discuss = (body.intent_mode or "").strip() == "discuss"' in after_casual[:300]

    def test_intent_classification_bypasses_discuss(self):
        source = _read(LEARNING)
        assert "intent = None if (_is_casual or _is_discuss or _atlas_ctx) else (" in source

    def test_prerequisite_gate_bypasses_discuss(self):
        source = _read(LEARNING)
        assert (
            "prerequisite_intervention = None if (_is_casual or _is_discuss or _atlas_ctx) "
            "else check_prerequisites(" in source
        )

    def test_detour_tuple_includes_discuss(self):
        source = _read(LEARNING)
        assert '(body.intent_mode or "").strip() in ("on_path", "casual", "discuss"):' in source

    def test_chat_feature_tag_has_discuss_branch(self):
        source = _read(LEARNING)
        block = source.split("messages.append({\"role\": \"user\", \"content\": body.message})")[1][:400]
        assert "if _is_discuss:" in block
        assert '_chat_feature = "learning:chat_discuss"' in block
        assert 'elif _is_casual:' in block
        assert '_chat_feature = "learning:chat_casual"' in block

    def test_chat_discuss_feature_registered_in_known_features(self):
        """U3/U7: 新 feature タグは core/llm_usage/schema.py の KNOWN_FEATURES 正本に転記する。"""
        import core.llm_usage.schema as schema

        assert "learning:chat_discuss" in schema.KNOWN_FEATURES

    def test_usage_help_preroute_precedes_is_casual_and_is_discuss(self):
        """設計 §1-3: HELP pre-route は _is_casual 判定より手前。discuss もその後段。"""
        source = _read(LEARNING)
        preroute_idx = source.index("_is_usage_help = (")
        is_casual_idx = source.index('_is_casual = (body.intent_mode or "").strip() == "casual"')
        is_discuss_idx = source.index('_is_discuss = (body.intent_mode or "").strip() == "discuss"')
        assert preroute_idx < is_casual_idx < is_discuss_idx


class TestDiscussSystemPrompt:
    def test_prompt_function_exists(self):
        source = _read(LEARNING)
        assert "def _get_discuss_system_prompt(domain: str, response_persona: str | None = None) -> str:" in source

    def _prompt_body(self) -> str:
        source = _read(LEARNING)
        return source.split("def _get_discuss_system_prompt(")[1].split("\ndef ")[0]

    def test_prompt_is_academic_discussion_tone_not_casual(self):
        body = self._prompt_body()
        assert "ディスカッション" in body
        assert "雑談調にはしないでください" in body
        assert "LaTeX" in body
        assert "出典" in body

    def test_prompt_forbids_socratic_delay(self):
        """DM4: 即答・出し惜しみ禁止（Socratic 遅延禁止の明文化）。"""
        body = self._prompt_body()
        assert "すぐに答えて" in body or "ためらわずすぐに答えて" in body
        assert "出し惜しみ" in body

    def test_prompt_requires_generative_prompt_structurally(self):
        """生成プロンプトの構造的必須要素（確率的付加は不可、と明記されていること）。"""
        body = self._prompt_body()
        assert "必ず" in body
        assert "確率的な付加は不可" in body
        assert "言い換え" in body or "自己説明" in body
        assert "why" in body.lower() and "how" in body.lower()

    def test_prompt_requires_non_paper_disclosure(self):
        """この論文由来ではないことの明示指示。"""
        body = self._prompt_body()
        assert "この論文由来ではない" in body or "この論文に書かれている内容ではなく" in body

    def test_prompt_forbids_numeric_scores(self):
        body = self._prompt_body()
        assert "数値スコア" in body or "数値" in body


class TestDiscussSelectedBeforeCasualForPrompt:
    def test_system_prompt_selection_checks_discuss_first(self):
        source = _read(LEARNING)
        block = source.split("# 5. 回答の生成（ルート統合）")[1][:600]
        # discuss 判定が casual より先（if _is_discuss: ... elif _is_casual: ...）
        discuss_pos = block.index("if _is_discuss:")
        casual_pos = block.index("elif _is_casual:")
        assert discuss_pos < casual_pos
        assert "_get_discuss_system_prompt(domain, response_persona)" in block


class TestScopeResolution:
    def test_list_course_source_document_ids_exists_in_services(self):
        source = _read(SERVICES)
        assert "def list_course_source_document_ids(course_data: dict | None) -> set[str]:" in source

    def test_scope_block_wires_course_sources_default_and_all_visible(self):
        source = _read(LEARNING)
        block = source.split("_discuss_scope = (body.discuss_scope or \"course_sources\").strip()")[1][:700]
        assert 'if _is_discuss and _discuss_scope == "all_visible":' in block
        assert "allowed_document_ids = list_visible_document_ids(current_user[\"id\"])" in block
        assert "elif _is_discuss:" in block
        assert "allowed_document_ids = list_course_source_document_ids(course_data)" in block

    def test_invalid_discuss_scope_raises_422(self):
        source = _read(LEARNING)
        block = source.split('_discuss_scope = (body.discuss_scope or "course_sources").strip()')[1][:500]
        assert 'if _is_discuss and _discuss_scope not in ("course_sources", "all_visible"):' in block
        assert "raise HTTPException(" in block
        assert "status_code=422" in block

    def test_non_discuss_path_keeps_phase0_visible_document_ids(self):
        """非 discuss は Phase 0 のまま list_visible_document_ids のみを使う（挙動不変）。"""
        source = _read(LEARNING)
        block = source.split('_discuss_scope = (body.discuss_scope or "course_sources").strip()')[1][:900]
        assert "else:" in block
        tail = block.split("else:")[-1]
        assert 'allowed_document_ids = list_visible_document_ids(current_user["id"])' in tail

    def test_discuss_empty_hit_uses_factual_no_expansion_text(self):
        """DM1: discuss で該当チャンクが無いとき、他スコープへ無断で広げない事実文。"""
        source = _read(LEARNING)
        block = source.split("if cited_chunks:")[1].split("\n\n    # 5. 回答の生成")[0]
        assert "elif _is_discuss:" in block
        assert "範囲は広げていません" in block
        assert "この論文由来ではないことを明示してください" in block
        # 既存の非 discuss 文言は不変のまま残っていること
        assert (
            "context_block = \"※この質問に直接関連する教材セクションは見つかりませんでした。"
            "一般的な学術知識を用いて回答してください。\""
        ) in block

    def test_discuss_scope_branches_do_not_silently_widen_to_visible_documents(self):
        """course_sources ブランチ自体は list_visible_document_ids を呼ばないこと（無断拡大禁止）。"""
        source = _read(LEARNING)
        scope_block = source.split('_discuss_scope = (body.discuss_scope or "course_sources").strip()')[1]
        scope_block = scope_block.split("chunk_results = search_chunks_with_metadata(")[0]
        # "elif _is_discuss:" ブランチ本体のみ（次の "else:" 手前まで）を切り出す
        course_sources_branch = scope_block.split("elif _is_discuss:")[1].split("else:")[0]
        assert "list_course_source_document_ids(course_data)" in course_sources_branch
        assert "list_visible_document_ids" not in course_sources_branch


class TestOutOfSourceNoticeKeptForDiscuss:
    def test_out_of_source_notice_condition_unchanged_and_documented(self):
        """out_of_source notice は discuss で意図的に維持（DM1）。既存条件式は不変。"""
        source = _read(LEARNING)
        assert "discuss では意図的にこの明示を維持する（DM1: 出所の正直さを弱めない）。" in source
        assert "if overall_tier == TIER_OUT_OF_SOURCE and not _is_casual:" in source


class TestEntryModeTrace:
    def test_trace_payload_has_discuss_entry_mode(self):
        source = _read(LEARNING)
        block = source.split("_trace_payload = {")[1].split("\n    }")[0]
        assert '**({"entry_mode": "discuss"} if _is_discuss else {}),' in block


class TestLectureHelperDelegation:
    """lecture.py の重複実装をなくし services.list_course_source_document_ids へ委譲したこと。"""

    def test_resolve_course_document_ids_delegates(self):
        source = _read(LECTURE)
        body = source.split("def _resolve_course_document_ids(material_ids: list[str]) -> list[str]:")[1].split("\ndef ")[0]
        assert "list_course_source_document_ids(" in body
        # 旧: 独自 SQL（IN プレースホルダ組み立て）を直接発行していない
        assert "SELECT id::text FROM documents WHERE source_path IN" not in body

    def test_course_document_ids_delegates(self):
        source = _read(LECTURE)
        body = source.split("def _course_document_ids(course_data: dict) -> list[str]:")[1].split("\ndef ")[0]
        assert "return list(list_course_source_document_ids(course_data))" in body

    def test_lecture_imports_new_helper_from_services(self):
        source = _read(LECTURE)
        block = source.split("from services import (")[1].split(")")[0]
        assert "list_course_source_document_ids," in block


class TestDiscussScopeValidatedBeforeTruncate:
    """レビュー確定の修正3: discuss_scope の不正値 422 は replace_message_id の

    truncate_chat_and_supersede 実行より前に判定し、履歴だけ巻き戻って処理が
    失敗する片手落ちを防ぐ。
    """

    def test_precheck_appears_before_truncate_call(self):
        from tests.guardrail_helpers import extract_function_source

        body = extract_function_source(_read(LEARNING), "learning_chat")
        precheck_idx = body.index('if (body.intent_mode or "").strip() == "discuss":')
        truncate_idx = body.index("truncate_chat_and_supersede(")
        assert precheck_idx < truncate_idx

    def test_precheck_appears_before_replace_message_id_check(self):
        from tests.guardrail_helpers import extract_function_source

        body = extract_function_source(_read(LEARNING), "learning_chat")
        precheck_idx = body.index('if (body.intent_mode or "").strip() == "discuss":')
        replace_idx = body.index("if body.replace_message_id:")
        assert precheck_idx < replace_idx

    def test_precheck_raises_422_with_expected_detail_text(self):
        from tests.guardrail_helpers import extract_function_source

        body = extract_function_source(_read(LEARNING), "learning_chat")
        precheck = body.split(
            'if (body.intent_mode or "").strip() == "discuss":'
        )[1].split("\n\n    # 1. コースデータを取得")[0]
        assert "raise HTTPException(" in precheck
        assert "status_code=422" in precheck
        # 後段の本検証（discuss_scope 解決ブロック）と同一の利用者向け文言にする。
        assert "discuss_scope には course_sources か all_visible を指定してください" in precheck

    def test_precheck_is_duplicated_not_a_replacement_of_the_later_validation(self):
        """後段の scope 解決ブロックの検証（TestScopeResolution）も残っていること。"""
        source = _read(LEARNING)
        assert source.count(
            'discuss_scope には course_sources か all_visible を指定してください'
        ) == 2


class TestDiscussionOriginTopicTitle:
    """レビュー確定の修正4: EXPLAIN_GRAPH_ELEMENT 経路等で使われる origin の
    topic_title に生の予約 topic_id "_discussion" が渡らないこと。
    """

    def test_origin_topic_info_uses_synthetic_dict_for_discussion(self):
        source = _read(LEARNING)
        block = source.split("if topic_id == DISCUSSION_TOPIC_ID:")[1][:400]
        assert (
            '_origin_topic_info = {"id": DISCUSSION_TOPIC_ID, "title": DISCUSSION_TOPIC_LABEL}'
            in block
        )
        assert "else:" in block
        tail = block.split("else:")[1]
        assert "_origin_topic_info = topic_info" in tail

    def test_origin_for_topic_call_uses_origin_topic_info_variable(self):
        source = _read(LEARNING)
        assert "topic_id, _origin_topic_info, segment_id=_seg, scroll_offset=_scroll" in source
        # 変換前の生 topic_info を直接渡す旧コードには戻っていないこと。
        assert "topic_id, topic_info, segment_id=_seg, scroll_offset=_scroll" not in source

    def test_origin_for_topic_resolves_discussion_label_not_raw_topic_id(self):
        """learning_support_agent.py は非改変のまま、渡す topic_info だけで生文字列露出を防げる。"""
        from core.learning_support_agent import LearningSupportAgent

        agent = LearningSupportAgent("course-1", {})
        origin = agent.origin_for_topic(
            "_discussion", {"id": "_discussion", "title": "論文との議論"},
        )
        assert origin.topic_title == "論文との議論"
        assert origin.topic_title != "_discussion"

        # 対比: 従来のバグ（topic_info=None）を再現すると生文字列に劣化することを明示する。
        buggy_origin = agent.origin_for_topic("_discussion", None)
        assert buggy_origin.topic_title == "_discussion"
