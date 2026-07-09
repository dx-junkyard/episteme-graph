"""structure_anchor.input_builder / schema の単体テスト。

LLM 入力の構築（候補ブロック・問いの整形・文字数キャップ）と語彙ヘルパーを検証する。
外部 API は一切呼び出さない。
"""

from __future__ import annotations

import json

from core.structure_anchor.input_builder import (
    MAX_INPUT_CHARS,
    build_user_content,
)
from core.structure_anchor.schema import (
    ANCHOR_TYPES,
    DOUBT_TYPES,
    AnchorContext,
    QuestionRecord,
    anchor_type_for_element,
    build_anchor_payload,
)


def _context(n_questions: int = 2) -> AnchorContext:
    return AnchorContext(
        course_id="c1",
        topic_id="t1",
        topic_title="線形応答理論",
        questions=[
            QuestionRecord(
                trace_id=f"tr_{i:03d}", text=f"質問その{i}です。", segment_id=i,
                cited_chunk_ids=["ch_a"],
            )
            for i in range(n_questions)
        ],
        claims=[{"id": "clm_001", "text": "主張テキスト"}],
        chunks=[{"id": "ch_a", "head": "チャンク冒頭"}],
        segments=[{"id": "seg_0", "label": "第1章"}],
    )


class TestBuildUserContent:
    def test_contains_questions_and_blocks(self):
        content = build_user_content(_context())
        assert "trace_id=tr_000" in content
        assert "trace_id=tr_001" in content
        assert "clm_001" in content
        assert "seg_0" in content
        assert "theory_basis" in content  # stage 語彙が候補として渡る
        assert "segment_id=seg_1" in content
        assert "cited_chunks=ch_a" in content

    def test_over_limit_drops_oldest_questions(self):
        ctx = _context(8)
        # 各問いを巨大化して上限超過を誘発
        for q in ctx.questions:
            q.text = "長い質問。" * 500
        content = build_user_content(ctx)
        assert len(content) <= MAX_INPUT_CHARS + 4000  # render 単位で切るため多少の余裕
        assert "tr_007" in content   # 新しい問いは必ず残る
        assert "tr_000" not in content  # 古い問いから落ちる

    def test_block_text_is_trimmed(self):
        ctx = _context()
        ctx.claims = [{"id": "clm_x", "text": "あ" * 500}]
        content = build_user_content(ctx)
        block = content.split("claims:")[1].split("\n")[0]
        assert len(block) < 500


class TestSchemaHelpers:
    def test_element_type_mapping(self):
        assert anchor_type_for_element("formula") == "equation"
        assert anchor_type_for_element("concept") == "concept"
        assert anchor_type_for_element("relationship") == "concept"
        assert anchor_type_for_element("keyword") == "concept"
        assert anchor_type_for_element("reference") == "chunk"
        assert anchor_type_for_element("citation") == "chunk"
        assert anchor_type_for_element(None) == "concept"
        assert anchor_type_for_element("unknown") == "concept"

    def test_build_anchor_payload_normalizes_vocabulary(self):
        payload = build_anchor_payload(
            anchor_type="bogus", anchor_id="x", anchor_label="L" * 200,
            doubt_type="bogus", attribution_source="bogus",
            confidence=0.123456,
        )
        assert payload["anchor_type"] == "segment"          # 語彙外は最粗粒度へ縮退
        assert payload["doubt_type"] == "unclassified"      # 語彙外は unclassified（P4）
        assert payload["attribution_source"] == "llm_candidate"
        assert len(payload["anchor_label"]) <= 80
        assert payload["confidence"] == 0.123
        assert payload["status"] == "active"
        assert payload["detector_version"].startswith("structure-anchor/")

    def test_payload_is_json_serializable(self):
        payload = build_anchor_payload(
            anchor_type="claim", anchor_id="clm_1", anchor_label="l",
            doubt_type="definition", attribution_source="learner_selected",
        )
        assert json.loads(json.dumps(payload)) == payload

    def test_vocabularies_include_unclassified_and_degradation_targets(self):
        assert "unclassified" in DOUBT_TYPES
        for coarse in ("concept", "chunk", "segment"):
            assert coarse in ANCHOR_TYPES

    def test_allowed_ids_by_type(self):
        ctx = _context()
        assert ctx.allowed_ids("claim") == {"clm_001"}
        assert ctx.allowed_ids("chunk") == {"ch_a"}
        assert "theory_basis" in ctx.allowed_ids("stage")
        assert ctx.allowed_ids("equation") == set()
