"""claim 単位のつまづき集約（§3.5、k-匿名・段階ラベル化）。

原稿スタジオ右ペインの「根拠リンク ⇄ つまづき」トグルが呼ぶサマリー。
claim ごとに 4 軸を段階表示する:
  1. 誤り率（mismatch 率）— 段階ラベル（低 / 中 / 高）
  2. 記号降下頻度 — レンジ（3-5 / 6-10 / 11+）
  3. 判定×自己確認の乖離 — 段階ラベル（item 健全性の代理指標）
  4. よくある質問・誤解 — 既存 B層資産の再利用（interest_traces kind='question' で
     structure_anchor.anchor_id が当該 claim のもの）を k-匿名集約

k-匿名: k=3。**人数（DISTINCT user）**が k 未満のセルは「まだデータなし」
（応答行数ではなく人数で数える。D層 naive_signal と同じ扱い。P3）。
数値の生値・個人特定情報は出さない。
テスト初期はほぼ全て「まだデータなし」になる（fail-closed を許容。k は緩めない）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text as sa_text

from core.postgres import get_session as _pg_session
from core.reconstruction.health import K_ANON, count_range, gate_by_users, rate_level

_NO_DATA = "まだデータなし"


def _fetch_claim_items(session, document_id: str) -> dict[str, dict]:
    """document の claim ごとに item のプロンプト・件数集計を返す。"""
    rows = session.execute(
        sa_text("""
            SELECT i.claim_id::text,
                   COUNT(r.id)                                                   AS n,
                   COUNT(*) FILTER (WHERE r.machine_verdict='mismatch')          AS n_mismatch,
                   COUNT(*) FILTER (WHERE r.descended_to_symbol)                 AS n_descend,
                   COUNT(*) FILTER (WHERE r.self_check='verdict_wrong')          AS n_dissent,
                   COUNT(*) FILTER (WHERE r.machine_verdict='mismatch'
                                      AND r.self_check='agreed')                 AS n_self_disagree,
                   COUNT(DISTINCT i.id)                                          AS n_items,
                   COUNT(DISTINCT r.user_id)                                     AS n_users
            FROM reconstruction_items i
            LEFT JOIN learner_reconstructions r ON r.item_id = i.id
            WHERE i.document_id = :doc AND i.status <> 'retired'
            GROUP BY i.claim_id
        """),
        {"doc": document_id},
    ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out[r[0]] = {
            "n": int(r[1] or 0),
            "n_mismatch": int(r[2] or 0),
            "n_descend": int(r[3] or 0),
            "n_dissent": int(r[4] or 0),
            "n_self_disagree": int(r[5] or 0),
            "n_items": int(r[6] or 0),
            "n_users": int(r[7] or 0),
        }
    return out


def _fetch_claim_meta(session, document_id: str, claim_ids: list[str]) -> dict[str, dict]:
    if not claim_ids:
        return {}
    rows = session.execute(
        sa_text("""
            SELECT id::text, claim_type, text, source_scope
            FROM theory_claims
            WHERE document_id = :doc AND id::text = ANY(:ids)
        """),
        {"doc": document_id, "ids": claim_ids},
    ).fetchall()
    meta: dict[str, dict] = {}
    for r in rows:
        text = r[2] or ""
        meta[r[0]] = {
            "claim_type": r[1] or "",
            # ラベル用の短い抜粋のみ（教員向けなので本文可。学習者には出さない）
            "label": (text[:80] + "…") if len(text) > 80 else text,
        }
    return meta


def _fetch_anchored_questions(session, claim_ids: list[str]) -> dict[str, int]:
    """kind='question' で structure_anchor.anchor_id が当該 claim の痕跡を claim 単位で数える。

    k-匿名の母数なので**人数（DISTINCT user_id）**で数える（同一学習者の連投で開示されない）。
    """
    if not claim_ids:
        return {}
    rows = session.execute(
        sa_text("""
            SELECT payload->'structure_anchor'->>'anchor_id' AS anchor_id,
                   COUNT(DISTINCT user_id) AS n
            FROM interest_traces
            WHERE kind = 'question'
              AND status <> 'superseded'
              AND payload->'structure_anchor'->>'anchor_id' = ANY(:ids)
            GROUP BY payload->'structure_anchor'->>'anchor_id'
        """),
        {"ids": claim_ids},
    ).fetchall()
    return {str(r[0]): int(r[1] or 0) for r in rows if r[0]}


def get_stumble_summary(document_id: str, claim_ids: list[str] | None = None) -> dict:
    """document のつまづきサマリー（claim ごとに 4 軸・k-匿名・段階ラベル）。"""
    session = _pg_session()
    try:
        agg = _fetch_claim_items(session, document_id)
        ids = [cid for cid in agg.keys()]
        if claim_ids:
            wanted = set(claim_ids)
            ids = [c for c in ids if c in wanted]
        meta = _fetch_claim_meta(session, document_id, ids)
        questions = _fetch_anchored_questions(session, ids)
    finally:
        session.close()

    claims_out = []
    for cid in ids:
        a = agg.get(cid, {})
        n = int(a.get("n", 0))
        n_users = int(a.get("n_users", 0))
        q_n = int(questions.get(cid, 0))  # 人数（DISTINCT user）
        m = meta.get(cid, {})
        claims_out.append({
            "claim_id": cid,
            "claim_type": m.get("claim_type", ""),
            "label": m.get("label", ""),
            "n_items": int(a.get("n_items", 0)),
            "axes": {
                "error_rate": gate_by_users(rate_level(a.get("n_mismatch", 0), n), n_users),
                "symbol_descent": gate_by_users(count_range(int(a.get("n_descend", 0))), n_users),
                "verdict_self_check_divergence": _divergence_label(a, n, n_users),
                "faq": {
                    "questions": count_range(q_n),
                    "has_data": q_n >= K_ANON,
                },
            },
            "has_data": n_users >= K_ANON or q_n >= K_ANON,
        })
    # まだデータのある claim を上に（教員が見るべき順）。無ければ label 順で安定。
    claims_out.sort(key=lambda c: (not c["has_data"], c["label"]))
    return {"document_id": document_id, "claims": claims_out, "k_anonymity": K_ANON}


def _divergence_label(agg: dict, n: int, n_users: int) -> str:
    """判定×自己確認の乖離（mismatch なのに agreed / verdict_wrong dissent）の段階ラベル。"""
    if n_users < K_ANON:
        return _NO_DATA
    divergent = int(agg.get("n_self_disagree", 0)) + int(agg.get("n_dissent", 0))
    return rate_level(divergent, n)
