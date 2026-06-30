"""学習者体験レイヤー(B層) Stage M の契約・ロジックテスト。

カバー範囲:
- L1 信頼性: judge_source_tier / aggregate_overall_tier / attach_tiers の安全側挙動
- 不可侵の一線: Stage M の mock 判定が approved を出さないこと
- L2 位置: build_position_anchor の形状
- L3/L4: mock_interest_traces / mock_interest_dashboard が `_mock` 印を持ち、
  ダッシュボードが個人特定フィールドを持たないこと
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
    build_position_anchor,
    judge_source_tier,
    mock_interest_dashboard,
    mock_interest_traces,
    out_of_source_notice,
)


# ---------------------------------------------------------------------------
# L1 信頼性レイヤー
# ---------------------------------------------------------------------------

def test_judge_source_tier_high_score_is_source_not_approved():
    # 不可侵の一線: Stage M は承認フラグ未読のため approved を決して出さない。
    tier = judge_source_tier({"score": 0.92})
    assert tier == TIER_SOURCE
    assert tier != TIER_APPROVED


def test_judge_source_tier_low_score_falls_to_out_of_source():
    assert judge_source_tier({"score": 0.10}) == TIER_OUT_OF_SOURCE
    assert judge_source_tier({}) == TIER_OUT_OF_SOURCE  # score 欠落も安全側


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
    assert "未踏" in notice
    # 断定しない文言であること（参考情報である旨を含む）
    assert "参考" in notice


# ---------------------------------------------------------------------------
# L2 位置・復帰レイヤー
# ---------------------------------------------------------------------------

def test_build_position_anchor_shape():
    anchor = build_position_anchor("topic-1", segment_id=3, scroll_offset=120)
    assert anchor["topic_id"] == "topic-1"
    assert anchor["segment_id"] == 3
    assert anchor["scroll_offset"] == 120
    assert anchor["_mock"] is True


def test_build_position_anchor_defaults():
    anchor = build_position_anchor("topic-1")
    assert anchor["segment_id"] == 0
    assert anchor["scroll_offset"] == 0


# ---------------------------------------------------------------------------
# L3 / L4 mock データの印とプライバシー
# ---------------------------------------------------------------------------

def test_mock_interest_traces_marked():
    data = mock_interest_traces("course-1", "topic-1")
    assert data["_mock"] is True
    assert isinstance(data["traces"], list) and data["traces"]


def test_mock_interest_dashboard_marked_and_anonymous():
    data = mock_interest_dashboard("course-1")
    assert data["_mock"] is True
    # 集団集計のみ。個人特定フィールド（user_id 等）を持たない。
    blob = repr(data).lower()
    assert "user_id" not in blob
    assert "user_name" not in blob
    assert "email" not in blob
    assert data["cohort_size"] >= 1


# ---------------------------------------------------------------------------
# 契約変更(§3.3): chat.search_chunks は tier 付き dict のリストを返す
# ---------------------------------------------------------------------------

def test_search_chunks_returns_tiered_dicts():
    from core import chat

    fake_rows = [
        ("本文A", "論文タイトル", "a.pdf", 0.81),
        ("本文B", "", "b.pdf", 0.20),
    ]
    fake_session = MagicMock()
    fake_session.execute.return_value.fetchall.return_value = fake_rows

    with patch.object(chat, "_embed_query", return_value=[0.0] * 8), \
         patch.object(chat, "get_embedding_dim", return_value=8), \
         patch.object(chat, "get_session", return_value=fake_session):
        result = chat.search_chunks("質問", "material-1", top_k=5)

    assert isinstance(result, list)
    assert all(isinstance(r, dict) for r in result)
    # 契約: 各要素は text/source_title/source_file/score/tier を持つ
    for r in result:
        assert {"text", "source_title", "source_file", "score", "tier"} <= set(r)
    assert result[0]["tier"] == TIER_SOURCE
    assert result[1]["tier"] == TIER_OUT_OF_SOURCE
    # approved は Stage M では出さない（不可侵の一線）
    assert all(r["tier"] != TIER_APPROVED for r in result)
