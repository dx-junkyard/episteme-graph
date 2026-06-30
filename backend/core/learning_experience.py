"""学習者体験レイヤー(B層)の共有ロジック。

仕様書 `episteme-graph_learner-experience-spec.md` の4レイヤーのうち、Stage M
（フルスタックの薄い殻）で導入する tier 判定・位置アンカー・関心痕跡の口を集約する。

このモジュールは **(A) 教材生成パイプラインに依存しない**（読み取り配線は Stage 2 以降）。
Stage M の段階では中身の多くが Mock であり、各 Mock には `EPISTEME_MOCK` タグを付けて
`MOCKS.md` 台帳と grep の二点で取り残しを検出できるようにしている。
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# tier 定義（L1 信頼性レイヤー）
# ---------------------------------------------------------------------------

TIER_APPROVED = "approved"        # 承認済み（教員承認済み教材由来）
TIER_SOURCE = "source"            # 原典（論文本文に根拠あり、未承認）
TIER_OUT_OF_SOURCE = "out_of_source"  # 未踏（十分な根拠なし）

# 安全側の集約順序（弱い → 強い）。overall_tier は最弱に引きずる。
_TIER_STRENGTH = {TIER_OUT_OF_SOURCE: 0, TIER_SOURCE: 1, TIER_APPROVED: 2}

# Stage M 暫定の類似度閾値。Stage 2 で承認フラグ・出典メタと統合する。
_SOURCE_SCORE_THRESHOLD = 0.45


def judge_source_tier(chunk: dict[str, Any]) -> str:
    """検索ヒットした1チャンクに tier を付与する。

    # EPISTEME_MOCK[M01] L1信頼性: (A) の承認フラグを未だ読んでいないため、
    # approved は決して出さず score 閾値だけで source / out_of_source を判定する
    # 簡易ロジック。確信が持てない場合は必ず安全側(out_of_source)へ倒す。
    # — replace in Stage 2 (SourceTierJudge: 承認フラグ・出典メタを配線)
    """
    score = float(chunk.get("score") or 0.0)
    if score >= _SOURCE_SCORE_THRESHOLD:
        # 不可侵の一線: provisional を approved 扱いしない。Stage M では approved は出さない。
        return TIER_SOURCE
    return TIER_OUT_OF_SOURCE


def aggregate_overall_tier(tiers: list[str]) -> str:
    """回答全体の格を、寄与した根拠の最弱 tier に引きずって安全側で集約する。"""
    if not tiers:
        return TIER_OUT_OF_SOURCE
    return min(tiers, key=lambda t: _TIER_STRENGTH.get(t, 0))


def attach_tiers(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """チャンク dict 群に tier を付与して返す（破壊的: 各 dict に 'tier' を追記）。"""
    for c in chunks:
        if "tier" not in c:
            c["tier"] = judge_source_tier(c)
    return chunks


def out_of_source_notice() -> str:
    """未踏（out_of_source）時の、断定しない定型文。

    # EPISTEME_MOCK[M02] L1信頼性: 暫定のガード文。Stage 2 で OutOfSourceGuard の
    # 本実装（予想→根拠→誤答比較→原典tier解説の順序ゲート）に置き換える。
    # — replace in Stage 2
    """
    return (
        "⚠️ この質問に十分に対応する**承認済み/原典の根拠が見つかりませんでした（未踏）**。\n"
        "以下は一般的な学術知識に基づく参考情報であり、教材で裏づけられた断定ではありません。"
    )


# ---------------------------------------------------------------------------
# 位置・復帰（L2 位置・復帰レイヤー）
# ---------------------------------------------------------------------------


def build_position_anchor(
    topic_id: str,
    segment_id: str | int | None = None,
    scroll_offset: float | int | None = None,
) -> dict[str, Any]:
    """復帰位置アンカー {topic_id, segment_id, scroll_offset} を組み立てる。

    # EPISTEME_MOCK[M04] L2位置復帰: segment_id / scroll_offset は呼び出し側から
    # 渡らなければ仮値(0)で埋める。DB永続化なし・寄り道復帰の精密化は未実装。
    # — replace in Stage 1 (PositionAnchor: lecture interrupt と統合、回帰テスト)
    """
    return {
        "topic_id": topic_id,
        "segment_id": segment_id if segment_id is not None else 0,
        "scroll_offset": scroll_offset if scroll_offset is not None else 0,
        "_mock": True,
    }


# ---------------------------------------------------------------------------
# 資産化（L3）: 関心痕跡 / 未解決の問いボックス
# ---------------------------------------------------------------------------


def mock_interest_traces(course_id: str, topic_id: str | None = None) -> dict[str, Any]:
    """問いの軌跡（UnfinishedQuestionBox）を返す（Stage M は mock）。

    再設計（右パネル）に合わせ、status を主役にした curated な痕跡と、再訪のころ合い
    （DecayPolicy の入口）を返す。kind: question|detour|misconception、
    status: open(未解決/未消化) | revisited(再訪推奨) | resolved(解決済み)。

    # EPISTEME_MOCK[M05] L3資産化: interest_traces テーブル未作成のため固定 mock。
    # 総数バッジは廃止し status 軸で見せる。Stage 3 で db/020_interest_trace.sql と
    # 安価な生記録(kind='raw')に置き換える。revisit_cue は Stage 4 (DecayPolicy)。
    # — replace in Stage 3/4
    """
    return {
        "_mock": True,
        "_mock_fields": ["traces", "revisit_cue"],
        "course_id": course_id,
        "topic_id": topic_id,
        "revisit_cue": {
            "kind_label": "再訪のころ合い",
            "headline": "前に符号を取り違えた問い、今なら見えるかも",
            "sub": "5日前に一度訂正済み。間隔をあけて戻ると定着しやすいタイミングです。",
        },
        "traces": [
            {
                "id": "mock-trace-1", "kind": "misconception", "status": "revisited",
                "context_label": "第1章 · 修正重力理論のテストとしての大規模構造",
                "text": "（モック）尖度の規格化で符号を取り違えた",
            },
            {
                "id": "mock-trace-2", "kind": "detour", "status": "open",
                "context_label": "第2章 · Gravity kernels の定義と役割",
                "text": "（モック）前提知識Aに寄り道したが、消化しないまま元のパスに戻った",
            },
            {
                "id": "mock-trace-3", "kind": "question", "status": "open",
                "context_label": "第1章 · フーリエ摂動基底を用いた物質密度揺らぎの記述",
                "text": "（モック）この近似はどの条件で破綻しますか？",
            },
            {
                "id": "mock-trace-4", "kind": "question", "status": "resolved",
                "context_label": "第3章 · 局所バイアス模型の定義",
                "text": "（モック）局所バイアスを3次で打ち切る根拠は？",
            },
        ],
    }


# ---------------------------------------------------------------------------
# 可視化・活用（L4）: 教員向け InterestDashboard（集団集計を既定）
# ---------------------------------------------------------------------------


def mock_interest_dashboard(course_id: str | None = None) -> dict[str, Any]:
    """教員向け関心集約（集団集計）。Stage M は固定 mock。

    # EPISTEME_MOCK[M06] L4可視化: 集団集計を固定 mock データで返す。
    # 個人を特定可能なフィールドは最初から持たせない（プライバシー設計を織り込む）。
    # Stage 4 で interest_traces からの実集計に置き換える。
    # — replace in Stage 4 (InterestDashboard: 集団集計優先・個人非特定)
    """
    return {
        "_mock": True,
        "_mock_fields": ["hotspots", "unfinished_summary"],
        "course_id": course_id,
        "cohort_size": 24,  # 集団規模のみ。個人IDは持たない。
        "hotspots": [
            {"topic_title": "（モック）固有値問題の境界条件", "interest_count": 17, "unfinished_ratio": 0.41},
            {"topic_title": "（モック）摂動展開の収束", "interest_count": 12, "unfinished_ratio": 0.25},
            {"topic_title": "（モック）規格化の物理的意味", "interest_count": 9, "unfinished_ratio": 0.55},
        ],
        "unfinished_summary": {
            "open_questions": 38,
            "repeated_detours": 11,
            "recurring_misconceptions": 6,
        },
    }
