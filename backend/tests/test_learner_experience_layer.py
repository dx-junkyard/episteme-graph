"""学習者体験レイヤー(B層) の契約・ロジックテスト（Stage 1〜4）。

カバー範囲:
- L1 信頼性: judge_source_tier（承認/原典/未踏）・aggregate_overall_tier の安全側集約・
  approved_chunk_ids のフォールバック・OutOfSourceGuard の非断定文
- L2 位置: build_position_anchor / origin の segment・scroll 保持
- L3 資産化: build_traces_view（status主役の並び・再訪のころ合い算出）
- 契約変更(§3.3): chat.search_chunks() が tier 付き dict のリストを返すこと
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.learning_experience import (
    TIER_APPROVED,
    TIER_OUT_OF_SOURCE,
    TIER_SOURCE,
    aggregate_overall_tier,
    attach_tiers,
    approved_chunk_ids,
    build_position_anchor,
    build_traces_view,
    judge_source_tier,
    out_of_source_guard_instruction,
    out_of_source_notice,
    tier_floor,
)


# ---------------------------------------------------------------------------
# L1 信頼性レイヤー
# ---------------------------------------------------------------------------

def test_judge_source_tier_approved_only_when_teacher_reviewed():
    # 承認は approved=True（teacher_reviewed に紐づく）のときだけ昇格する。
    assert judge_source_tier({"approved": True, "score": 0.10}) == TIER_APPROVED


def test_judge_source_tier_high_score_is_source_not_approved():
    # 不可侵の一線: 承認に紐づかない高類似度は source 止まり（approved にしない）。
    tier = judge_source_tier({"score": 0.92})
    assert tier == TIER_SOURCE
    assert tier != TIER_APPROVED
    # approved が明示 False でも昇格しない
    assert judge_source_tier({"approved": False, "score": 0.92}) == TIER_SOURCE


def test_judge_source_tier_low_score_falls_to_out_of_source():
    assert judge_source_tier({"score": 0.10}) == TIER_OUT_OF_SOURCE
    assert judge_source_tier({}) == TIER_OUT_OF_SOURCE  # score 欠落も安全側


def test_approved_chunk_ids_empty_and_fallback():
    # 空入力 → 空集合。例外（テーブル未整備など）→ 安全側で空集合。
    assert approved_chunk_ids(MagicMock(), []) == set()
    broken = MagicMock()
    broken.execute.side_effect = Exception("no table")
    assert approved_chunk_ids(broken, ["id-1"]) == set()


def test_approved_chunk_ids_returns_reviewed_set():
    fake = MagicMock()
    fake.execute.return_value.fetchall.return_value = [("chunk-a",), ("chunk-b",)]
    assert approved_chunk_ids(fake, ["chunk-a", "chunk-b", "chunk-c"]) == {"chunk-a", "chunk-b"}


def test_out_of_source_guard_instruction_is_non_assertive():
    g = out_of_source_guard_instruction()
    assert "断定しない" in g
    assert "予想" in g  # 学習者の予想を促す（Productive Failure）


def test_tier_floor_lifts_weaker_but_never_lowers():
    # トピック教材の実根拠がある回答は out_of_source から source へ引き上げる
    assert tier_floor(TIER_OUT_OF_SOURCE, TIER_SOURCE) == TIER_SOURCE
    # 既に強い格は floor で下がらない（approved を維持）
    assert tier_floor(TIER_APPROVED, TIER_SOURCE) == TIER_APPROVED
    assert tier_floor(TIER_SOURCE, TIER_SOURCE) == TIER_SOURCE


def test_aggregate_overall_tier_is_weakest_link():
    assert aggregate_overall_tier([TIER_SOURCE, TIER_OUT_OF_SOURCE]) == TIER_OUT_OF_SOURCE
    assert aggregate_overall_tier([TIER_SOURCE, TIER_SOURCE]) == TIER_SOURCE
    # 根拠なし → 未踏
    assert aggregate_overall_tier([]) == TIER_OUT_OF_SOURCE


def test_attach_tiers_adds_tier_field_in_place():
    chunks = [{"text": "a", "score": 0.8}, {"text": "b", "score": 0.1}]
    out = attach_tiers(chunks)
    assert out is chunks  # 破壊的（in-place）契約
    assert out[0]["tier"] == TIER_SOURCE
    assert out[1]["tier"] == TIER_OUT_OF_SOURCE


def test_out_of_source_notice_does_not_assert():
    notice = out_of_source_notice()
    # 「参考」情報である旨と、断定しない文言を含むこと（ラベルは「未踏」→「参考」へ変更）
    assert "参考" in notice
    assert "断定" in notice


# ---------------------------------------------------------------------------
# L2 位置・復帰レイヤー
# ---------------------------------------------------------------------------

def test_build_position_anchor_shape():
    # Stage 1: クライアント報告の実位置を保持する（mock 印は持たない）。
    anchor = build_position_anchor("topic-1", segment_id=3, scroll_offset=120)
    assert anchor["topic_id"] == "topic-1"
    assert anchor["segment_id"] == 3
    assert anchor["scroll_offset"] == 120
    assert "_mock" not in anchor


def test_build_position_anchor_defaults():
    anchor = build_position_anchor("topic-1")
    assert anchor["segment_id"] == 0
    assert anchor["scroll_offset"] == 0


def test_origin_carries_position_anchor():
    # LearningSupportOrigin が segment/scroll を保持し、寄り道→復帰で正確に戻せる。
    from core.learning_support_agent import LearningSupportAgent
    agent = LearningSupportAgent("course-1", {"chapters": [{"title": "第1章"}]})
    origin = agent.origin_for_topic(
        "topic-1", {"title": "T1", "chapter_index": 0}, segment_id=4, scroll_offset=88
    )
    assert origin.segment_id == 4
    assert origin.scroll_offset == 88
    dumped = agent.return_to_path_result(None)  # smoke: model_dump 互換
    assert "answer" in dumped.model_dump()


# ---------------------------------------------------------------------------
# L3 / L4 mock データの印とプライバシー
# ---------------------------------------------------------------------------

def test_build_traces_view_shape_and_cue():
    import datetime
    old = datetime.datetime.now() - datetime.timedelta(days=5)
    recent = datetime.datetime.now()
    rows = [
        ("t1", "misconception", "open", {"text": "符号ミス", "context_label": "第1章"}, old),
        ("t2", "question", "open", {"text": "近似の破綻条件は?", "context_label": "第1章"}, recent),
        ("t3", "question", "resolved", {"text": "解決済みの問い", "context_label": "第3章"}, old),
    ]
    view = build_traces_view(rows)
    assert [t["id"] for t in view["traces"]] == ["t1", "t2", "t3"]  # 入力順を保持
    assert view["traces"][0]["text"] == "符号ミス"
    # 再訪のころ合いは「最も古い未解決の誤答」を優先して選ぶ
    assert view["revisit_cue"] is not None
    assert "誤答" in view["revisit_cue"]["headline"]


def test_build_traces_view_exposes_message_id_for_jump():
    """「この問いに戻る」で元の往復へジャンプするため、payload.message_id をビューへ通す。"""
    import datetime
    recent = datetime.datetime.now()
    rows = [
        ("t1", "question", "open",
         {"text": "近似の破綻条件は?", "context_label": "第1章", "message_id": "msg-abc"}, recent),
        ("t2", "question", "open", {"text": "id の無い旧行"}, recent),  # 旧行は message_id 無し
    ]
    view = build_traces_view(rows)
    assert view["traces"][0]["message_id"] == "msg-abc"
    assert view["traces"][1]["message_id"] == ""  # 後方互換: 無ければ空文字（フロントは再送信にフォールバック）


def test_build_traces_view_no_cue_when_all_resolved_or_recent():
    import datetime
    recent = datetime.datetime.now()
    rows = [
        ("t1", "question", "resolved", {"text": "x"}, recent),
        ("t2", "question", "open", {"text": "y"}, recent),  # 当日 → 再訪対象外
    ]
    view = build_traces_view(rows)
    assert view["revisit_cue"] is None
    assert "_mock" not in view  # 実データ。mock 印は持たない


# InterestDashboard 集計 (aggregate_interest_dashboard) は DB バインドのため
# Docker 結合テストで検証する（本ユニットでは純関数 build_traces_view までを対象）。


# ---------------------------------------------------------------------------
# 契約変更(§3.3): chat.search_chunks は tier 付き dict のリストを返す
# ---------------------------------------------------------------------------

def test_search_chunks_returns_tiered_dicts():
    from core import chat

    # 新契約: SELECT は id を先頭に含む 5 カラム。
    fake_rows = [
        ("id-A", "本文A", "論文タイトル", "a.pdf", 0.81),
        ("id-B", "本文B", "", "b.pdf", 0.20),
    ]
    fake_session = MagicMock()
    fake_session.execute.return_value.fetchall.return_value = fake_rows

    with patch.object(chat, "_embed_query", return_value=[0.0] * 8), \
         patch.object(chat, "get_embedding_dim", return_value=8), \
         patch.object(chat, "get_session", return_value=fake_session), \
         patch.object(chat, "approved_chunk_ids", return_value=set()):  # 承認なし
        result = chat.search_chunks("質問", "material-1", top_k=5)

    assert isinstance(result, list)
    assert all(isinstance(r, dict) for r in result)
    # 契約: 各要素は id/text/source_title/source_file/score/approved/tier を持つ
    for r in result:
        assert {"id", "text", "source_title", "source_file", "score", "approved", "tier"} <= set(r)
    assert result[0]["tier"] == TIER_SOURCE       # 高類似度・未承認 → source
    assert result[1]["tier"] == TIER_OUT_OF_SOURCE  # 低類似度 → 未踏
    # 承認なしなので approved は出ない（不可侵の一線）
    assert all(r["tier"] != TIER_APPROVED for r in result)


def test_search_chunks_approved_when_teacher_reviewed():
    from core import chat

    fake_rows = [("id-A", "本文A", "論文タイトル", "a.pdf", 0.81)]
    fake_session = MagicMock()
    fake_session.execute.return_value.fetchall.return_value = fake_rows

    with patch.object(chat, "_embed_query", return_value=[0.0] * 8), \
         patch.object(chat, "get_embedding_dim", return_value=8), \
         patch.object(chat, "get_session", return_value=fake_session), \
         patch.object(chat, "approved_chunk_ids", return_value={"id-A"}):  # 承認済み
        result = chat.search_chunks("質問", "material-1", top_k=5)

    assert result[0]["approved"] is True
    assert result[0]["tier"] == TIER_APPROVED
