"""item 健全性の読み取り + 疑わしさランク（§4.1）。

reconstruction_item_health ビューを読み、疑わしさランクを**アプリ側で**計算する
（SQL に埋め込まない）。教員は門番ではなく異常の監査役であり、この review キューは
「怪しいものだけを事後に見る」ための並べ替えである（§3.1）。

疑わしさランク = author_confidence 低 × (mismatch率高 OR 乖離率高 OR dissent あり)。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text as sa_text

from core.postgres import get_session as _pg_session
from core.privacy import K_ANONYMITY as K_ANON
from core.privacy import count_range_gated, gate_label

# k-匿名しきい値。**人数（DISTINCT user）**がこれ未満のシグナルは方向性を出さない
# （まだデータなし）。応答行数ではなく人数で数える（D層 naive_signal と同じ扱い。P3:
# 1人の学習者が複数回答しただけでセルが開示されてはならない）。
# 実体は core/privacy.py の共通 k-匿名ゲート（提案8）。この名前 (K_ANON) は
# core/reconstruction/stumble.py が import しているため後方互換のため残す。

_NO_DATA = "まだデータなし"


def gate_by_users(label: str, n_users: int) -> str:
    """k-匿名ゲート: 学習者の人数が k 未満なら段階ラベル/レンジを出さない。"""
    return gate_label(label, n_users, below_label=_NO_DATA, k=K_ANON)


def count_range(n: int) -> str:
    """件数をレンジ表示に落とす（k-匿名。生の小さい数を出さない）。"""
    return count_range_gated(n, below_label=_NO_DATA, k=K_ANON)


def rate_level(numerator: int, denominator: int) -> str:
    """比率を段階ラベルに落とす。母数が k 未満なら『まだデータなし』。"""
    if denominator < K_ANON:
        return _NO_DATA
    rate = numerator / denominator if denominator else 0.0
    if rate < 0.2:
        return "低"
    if rate < 0.5:
        return "中"
    return "高"


def _suspicion_score(row: dict) -> float:
    """疑わしさの内部スコア（生値は API に出さない。並べ替えとランク帯にのみ使う）。"""
    n = int(row["n_responses"])
    conf = float(row["author_confidence"] or 0.0)
    if n <= 0:
        # 未回答の item は low-confidence のときだけ弱く浮上させる
        return (1.0 - conf) * 0.2
    mismatch_rate = int(row["n_mismatch"]) / n
    self_disagree_rate = int(row["n_verdict_self_disagree"]) / n
    dissent = 1.0 if int(row["n_verdict_dissent"]) > 0 else 0.0
    signal = 0.45 * mismatch_rate + 0.35 * self_disagree_rate + 0.20 * dissent
    return (1.0 - conf) * signal


def _rank_tier(score: float, n_users: int) -> str:
    if n_users < K_ANON:
        return "情報不足"
    if score >= 0.35:
        return "要確認（高）"
    if score >= 0.15:
        return "要確認（中）"
    return "要確認（低）"


def _fetch_health_rows(session, document_id: str | None) -> list[dict]:
    if document_id:
        sql = """
            SELECT h.item_id::text, h.claim_id::text, h.status, h.author_confidence,
                   h.n_responses, h.n_mismatch, h.n_verdict_dissent,
                   h.n_verdict_self_disagree, h.n_descend, h.n_users,
                   i.elicit_mode, i.prompt
            FROM reconstruction_item_health h
            JOIN reconstruction_items i ON i.id = h.item_id
            WHERE i.document_id = :doc
        """
        params: dict[str, Any] = {"doc": document_id}
    else:
        sql = """
            SELECT h.item_id::text, h.claim_id::text, h.status, h.author_confidence,
                   h.n_responses, h.n_mismatch, h.n_verdict_dissent,
                   h.n_verdict_self_disagree, h.n_descend, h.n_users,
                   i.elicit_mode, i.prompt
            FROM reconstruction_item_health h
            JOIN reconstruction_items i ON i.id = h.item_id
        """
        params = {}
    rows = session.execute(sa_text(sql), params).fetchall()
    out: list[dict] = []
    for r in rows:
        out.append({
            "item_id": r[0],
            "claim_id": r[1],
            "status": r[2] or "auto",
            "author_confidence": float(r[3] or 0.0),
            "n_responses": int(r[4] or 0),
            "n_mismatch": int(r[5] or 0),
            "n_verdict_dissent": int(r[6] or 0),
            "n_verdict_self_disagree": int(r[7] or 0),
            "n_descend": int(r[8] or 0),
            "n_users": int(r[9] or 0),
            "elicit_mode": r[10] or "restate",
            "prompt": r[11] or "",
        })
    return out


def get_review_queue(document_id: str | None = None) -> dict:
    """疑わしさランク順の item 一覧（health 集計を段階ラベル / レンジで付与）。

    教員・管理者向け。学習者の個別履歴は含めない（P3）。
    """
    session = _pg_session()
    try:
        rows = _fetch_health_rows(session, document_id)
    finally:
        session.close()

    items = []
    for row in rows:
        score = _suspicion_score(row)
        n = row["n_responses"]
        n_users = row["n_users"]
        items.append({
            "item_id": row["item_id"],
            "claim_id": row["claim_id"],
            "status": row["status"],
            "elicit_mode": row["elicit_mode"],
            "prompt": row["prompt"],
            "author_confidence": round(row["author_confidence"], 2),
            "rank_tier": _rank_tier(score, n_users),
            "signals": {
                "responses": gate_by_users(count_range(n), n_users),
                "mismatch_level": gate_by_users(rate_level(row["n_mismatch"], n), n_users),
                "verdict_dissent": n_users >= K_ANON and row["n_verdict_dissent"] > 0,
                "verdict_self_disagree_level": gate_by_users(
                    rate_level(row["n_verdict_self_disagree"], n), n_users
                ),
                "descend_frequency": gate_by_users(count_range(row["n_descend"]), n_users),
            },
            "_score": score,
        })
    items.sort(key=lambda it: (it.pop("_score"), it["item_id"]), reverse=True)
    return {"items": items, "k_anonymity": K_ANON}
