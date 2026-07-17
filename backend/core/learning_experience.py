"""学習者体験レイヤー(B層)の共有ロジック。

仕様書 `episteme-graph_learner-experience-spec.md` の4レイヤーの共有部品を集約する:
tier 判定(L1 SourceTierJudge/OutOfSourceGuard)・位置アンカー(L2)・関心痕跡のビュー(L3)。

このモジュールは **(A) 教材生成パイプラインに依存しない**（承認情報は読み取りのみ）。
Stage 1〜4 で全 Mock を本実装へ置換済み（経緯は MOCKS.md を参照）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text as _sa_text

# ---------------------------------------------------------------------------
# tier 定義（L1 信頼性レイヤー）
# ---------------------------------------------------------------------------

TIER_APPROVED = "approved"        # 承認済み（教員承認済み教材由来）
TIER_SOURCE = "source"            # 原典（論文本文に根拠あり、未承認）
TIER_OUT_OF_SOURCE = "out_of_source"  # 参考（出典提示なし・十分な根拠なし）

# 安全側の集約順序（弱い → 強い）。overall_tier は最弱に引きずる。
_TIER_STRENGTH = {TIER_OUT_OF_SOURCE: 0, TIER_SOURCE: 1, TIER_APPROVED: 2}

# Stage M 暫定の類似度閾値。Stage 2 で承認フラグ・出典メタと統合する。
_SOURCE_SCORE_THRESHOLD = 0.45


def judge_source_tier(chunk: dict[str, Any]) -> str:
    """検索ヒットした1チャンクに tier を付与する（L1 SourceTierJudge）。

    承認の正本は (A) の `theory_components.status='teacher_reviewed'`。検索層が chunk に
    付与した ``approved`` フラグ（teacher_reviewed な component に紐づくか）と類似度から判定する:

    - ``approved is True``        → approved（教員が承認した教材由来）
    - 類似度が閾値以上            → source（原典・未承認）
    - それ以外                    → out_of_source（未踏）

    不可侵の一線: ``approved`` が明示的に True のときだけ承認に昇格する。
    candidate / draft / 不明・混在は決して approved 扱いしない（安全側へ倒す）。
    """
    if chunk.get("approved") is True:
        return TIER_APPROVED
    score = float(chunk.get("score") or 0.0)
    if score >= _SOURCE_SCORE_THRESHOLD:
        return TIER_SOURCE
    return TIER_OUT_OF_SOURCE


def approved_chunk_ids(session, chunk_ids: list[str]) -> set[str]:
    """教員承認済み(teacher_reviewed)の theory_components に紐づく chunk id 集合を返す（L1）。

    承認の正本は ``theory_components.status='teacher_reviewed'``（A層が生成・教員が検証）。
    chunk へのリンクは ``primary_chunk_id``（直接）と ``source_chunks``（JSONB 配列）の両方を辿る。
    **A層は読み取りのみ**。該当が無ければ空集合を返し、呼び出し側は安全側で source/out_of_source に倒す。
    """
    ids = [str(c) for c in (chunk_ids or []) if c]
    if not ids:
        return set()
    try:
        rows = session.execute(
            _sa_text("""
                SELECT DISTINCT cid FROM (
                    SELECT primary_chunk_id::text AS cid
                    FROM theory_components
                    WHERE status = 'teacher_reviewed'
                      AND primary_chunk_id::text = ANY(:ids)
                    UNION
                    SELECT elem AS cid
                    FROM theory_components tc,
                         LATERAL jsonb_array_elements_text(tc.source_chunks) AS elem
                    WHERE tc.status = 'teacher_reviewed'
                      AND elem = ANY(:ids)
                ) q
                WHERE cid IS NOT NULL
            """),
            {"ids": ids},
        ).fetchall()
        return {str(r[0]) for r in rows}
    except Exception:
        # theory_components 未整備のコース等では安全側（承認なし）にフォールバック。
        return set()


def aggregate_overall_tier(tiers: list[str]) -> str:
    """回答全体の格を、寄与した根拠の最弱 tier に引きずって安全側で集約する。"""
    if not tiers:
        return TIER_OUT_OF_SOURCE
    return min(tiers, key=lambda t: _TIER_STRENGTH.get(t, 0))


def tier_floor(tier: str, floor: str) -> str:
    """tier が floor より弱ければ floor へ引き上げる（強い方を返す）。

    プロンプトに実根拠（例: 現在表示中のトピック教材）を注入済みのとき、集約結果が
    out_of_source（教材の裏づけなし）へ落ちて事実と矛盾するのを防ぐ用途。
    approved を floor に渡さないこと（承認への昇格は judge_source_tier の
    ``approved is True`` 経路のみ — 不可侵の一線）。
    """
    return max([tier, floor], key=lambda t: _TIER_STRENGTH.get(t, 0))


def attach_tiers(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """チャンク dict 群に tier を付与して返す（破壊的: 各 dict に 'tier' を追記）。"""
    for c in chunks:
        if "tier" not in c:
            c["tier"] = judge_source_tier(c)
    return chunks


def out_of_source_notice() -> str:
    """out_of_source（参考）時に回答の冒頭へ添える、断定しない定型バナー（L1 OutOfSourceGuard）。"""
    return (
        "⚠️ これは**出典が提示できない「参考」情報**です。\n"
        "教材（承認済み/原典）の裏づけはなく、一般的な学術知識に基づく参考であり、断定ではありません。"
    )


def out_of_source_guard_instruction() -> str:
    """未踏時に生成プロンプトへ注入する順序ゲート指示（Productive Failure）。

    根拠が弱いときに即断定させず、予想→根拠の所在→（あれば）誤答との対比→原典 tier の明示、
    の順で進ませる。仕様書 §5（PS-I による概念理解・転移）に基づく。
    """
    return (
        "【重要・未踏ガード】この質問は教材内に十分な根拠が見つかっていません。次の順序で応答すること:\n"
        "1. 断定しない。「教材では確認できない」と最初に明示する。\n"
        "2. 学習者自身の予想を一度引き出す問いを投げる（すぐに答えを与えない）。\n"
        "3. 一般知識で補う場合は『これは教材の裏づけがない参考情報』と区別して示す。\n"
        "4. 確実な事実と推測を混ぜない。誇張・断定表現を避ける。"
    )


# ---------------------------------------------------------------------------
# 位置・復帰（L2 位置・復帰レイヤー）
# ---------------------------------------------------------------------------


def build_position_anchor(
    topic_id: str,
    segment_id: str | int | None = None,
    scroll_offset: float | int | None = None,
) -> dict[str, Any]:
    """復帰位置アンカー {topic_id, segment_id, scroll_offset} を組み立てる（L2）。

    segment_id / scroll_offset はクライアントが報告した実際の現在位置。未指定なら
    先頭(0)。トピックを跨がない単段の DetourStack 入口として origin に保持され、
    「本筋へ戻る」で同じ位置に復帰する。
    """
    return {
        "topic_id": topic_id,
        "segment_id": int(segment_id) if segment_id is not None else 0,
        "scroll_offset": int(scroll_offset) if scroll_offset is not None else 0,
    }


# ---------------------------------------------------------------------------
# 資産化（L3）: 関心痕跡 / 未解決の問いボックス
# ---------------------------------------------------------------------------


_TRACE_KIND_WORD = {"misconception": "誤答", "question": "問い", "detour": "寄り道"}


def _days_ago(dt) -> int | None:
    """tz-aware/naive 両対応で経過日数を返す（不明は None）。"""
    if not dt:
        return None
    try:
        import datetime as _dt
        now = _dt.datetime.now(dt.tzinfo) if getattr(dt, "tzinfo", None) else _dt.datetime.now()
        return max(0, (now - dt).days)
    except Exception:
        return None


def build_traces_view(rows: list) -> dict[str, Any]:
    """interest_traces の行 → 問いの軌跡ビュー（traces + revisit_cue）に変換する（純関数）。

    rows: ``(id, kind, status, payload(dict|json), created_at)`` の並び。
    再訪のころ合い(revisit_cue)は「最も古い未解決(open/revisited)の誤答 > 問い」を
    決定論的に選ぶ簡易版（DecayPolicy の本格的な間隔最適化は Stage 4）。
    """
    import json as _json

    from core.structure_anchor.schema import ANCHOR_TYPE_LABELS, DOUBT_TYPE_LABELS

    traces: list[dict[str, Any]] = []
    cue_candidate = None
    for r in rows:
        payload = r[3] if isinstance(r[3], dict) else (_json.loads(r[3]) if r[3] else {})
        days = _days_ago(r[4]) if len(r) > 4 else None
        trace = {
            "id": str(r[0]),
            "kind": r[1],
            "status": r[2],
            "text": (payload or {}).get("text", ""),
            "context_label": (payload or {}).get("context_label", ""),
            # 「この問いに戻る」の逆引き（元の往復へジャンプ）。旧行には無いので空文字許容。
            "message_id": (payload or {}).get("message_id", ""),
        }
        # 構造帰属（structure_anchor）: 確定済み（learner_selected/confirmed）のみビューに載せる。
        # llm_candidate はダイジェスト経由でのみ提示する（P1/P7）。
        anchor = (payload or {}).get("structure_anchor") or {}
        if (
            anchor.get("attribution_source") in ("learner_selected", "confirmed")
            and anchor.get("status", "active") == "active"
        ):
            atype = anchor.get("anchor_type", "segment")
            dtype = anchor.get("doubt_type", "unclassified")
            trace["structure_anchor"] = {
                "anchor_type": atype,
                "anchor_type_label": ANCHOR_TYPE_LABELS.get(atype, atype),
                "anchor_label": anchor.get("anchor_label", ""),
                "doubt_type": dtype,
                "doubt_type_label": DOUBT_TYPE_LABELS.get(dtype, DOUBT_TYPE_LABELS["unclassified"]),
            }
        traces.append(trace)
        if r[2] in ("open", "revisited") and (days is None or days >= 1):
            rank = 0 if r[1] == "misconception" else 1
            key = (rank, -(days or 0))
            if cue_candidate is None or key < cue_candidate[0]:
                cue_candidate = (key, trace, days)

    revisit_cue = None
    if cue_candidate:
        _, t, days = cue_candidate
        word = _TRACE_KIND_WORD.get(t["kind"], "問い")
        when = f"{days}日前" if days else "先日"
        # 構造帰属があれば「どこに・どう引っかかっていたか」で再訪キューを構造化する（Stage 3）。
        t_anchor = t.get("structure_anchor") or {}
        if t_anchor.get("anchor_label"):
            headline = (
                f"「{t_anchor['anchor_label']}」の"
                f"{t_anchor.get('doubt_type_label', '引っかかり')}に戻るころ合い"
            )
        else:
            headline = f"前に引っかかった{word}、今なら見えるかも"
        revisit_cue = {
            "kind_label": "再訪のころ合い",
            "headline": headline,
            "sub": f"{when}の{word}です。間隔をあけて戻ると定着しやすいタイミングです。",
        }

    return {"traces": traces, "revisit_cue": revisit_cue}


# ---------------------------------------------------------------------------
# 可視化・活用（L4）の集計は services.aggregate_interest_dashboard（DB集計）に実装。
# ---------------------------------------------------------------------------
