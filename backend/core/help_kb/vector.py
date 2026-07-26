"""docs/manual のベクトル補助層（Phase 3 ①）。

正本: ``docs/features/manual_help_kb_design.md`` §5 Phase 3 ①。

非ベクトル索引（``core/help_kb/index.py`` / ``manual.py``）が無ヒットのときのみ
呼ばれる**補助**層であり、既定経路（documented ヒット時）のコスト・レイテンシは
変えない。専用テーブル ``manual_sections``（migration 058）を使い、教材本文の
埋め込みテーブルには一切触れない（§1-1 の相乗り禁止を継承）。

設計上の必須条件（§5 Phase 3 ①、実装で厳守する）:

1. **専用テーブル**（教材本文の埋め込みテーブルを汚染しない）。
2. **シード = 全置換スナップショット**: 現行の docs/manual 節集合に無い
   ``(audience, file, anchor)`` 行は、同期処理内で **同一トランザクションで DELETE**
   する（孤児行が古い説明を返し続けるドリフトを遮断する）。
3. **凍結検証を通過した節のみ埋め込む**: ``validate_manual()`` が違反を返したら
   同期を丸ごとスキップする（fail-closed。DELETE も含め一切書き込まない）。
4. **埋め込み失敗時は既存行を消さない**: embedding 生成（API呼び出し）が失敗したら、
   upsert も DELETE も一切実行せず、正直にスキップを返す（fail-open — データを
   壊さない。中途半端な部分適用も行わない）。
5. 70節規模のため HNSW 等の ANN インデックスは作らない（§5 Phase 3 注記）。

``core/help_kb`` は FastAPI を import しない（core 層の規約）。本モジュールにも
削除 API は無い（``sync_manual_vectors`` 内の全置換スナップショット DELETE は
設計書 §5 Phase 3 ② が明示要求する同期の一部であり、公開の削除 API ではない —
ガードレール ``test_help_kb_guardrails.py`` 側で本ファイルのみ例外として
許容している）。
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Optional

from sqlalchemy import text as sa_text

from . import manual as _manual
from .validator import validate_manual

logger = logging.getLogger(__name__)

# モジュール全体の有効スイッチ。"0" 系の値で sync/検索とも即スキップ/空にする
# （§5 Phase 3 ①。既存の ENABLED フラグ規約 — core/status/watcher.py /
# core/versioning/worker.py — と同じ判定式を踏襲する）。
_ENV_TRUE = ("1", "true", "True", "yes")


def _vector_enabled() -> bool:
    return os.getenv("HELP_KB_VECTOR_ENABLED", "1") in _ENV_TRUE


# 保守的なコサイン距離しきい値。無関係な節を「近い」として返すと HELP ルートの
# 捏造禁止原則（P4）に抵触するため、非ベクトル語彙重なり検索より厳しめに倒す
# （0.55 = pgvector cosine distance。0=完全一致、2=正反対。0.55 は「話題が近い」
# を超えて「ほぼ無関係」まで含めてしまう緩さを避けるための保守値）。
_MAX_COSINE_DISTANCE = 0.55


def _content_hash(title: str, body: str) -> str:
    payload = f"{title}\x00{body}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _current_snapshot() -> dict[tuple[str, str, str], dict[str, str]]:
    """全 audience の凍結節スナップショットを ``(audience, file, anchor) -> {title, body, content_hash}`` で返す。

    ``manual._sections_for_audience("system_admin")`` は上位継承で3ディレクトリ
    全部を1回ずつ読む（system_admin は student/teacher/system_admin の和集合）。
    各節の ``audience`` キーは索引構築時の ``audience_tag``（= 出所ディレクトリ名）
    そのものなので、これが正本の全置換スナップショットになる。
    """
    sections = _manual._sections_for_audience("system_admin")
    snapshot: dict[tuple[str, str, str], dict[str, str]] = {}
    for sec in sections:
        audience = sec.get("audience") or ""
        file = sec.get("file") or ""
        anchor = sec.get("anchor") or ""
        if not audience or not file or not anchor:
            continue
        title = sec.get("title") or ""
        body = sec.get("body") or ""
        snapshot[(audience, file, anchor)] = {
            "title": title,
            "body": body,
            "content_hash": _content_hash(title, body),
        }
    return snapshot


def _fetch_existing_hashes(session: Any) -> dict[tuple[str, str, str], str]:
    rows = session.execute(
        sa_text("SELECT audience, file, anchor, content_hash FROM manual_sections")
    ).fetchall()
    return {(row[0], row[1], row[2]): row[3] for row in rows}


def _empty_result(*, skipped: bool, reason: Optional[str] = None, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "skipped": skipped,
        "embedded": 0,
        "unchanged": 0,
        "deleted": 0,
        "total": 0,
    }
    if reason is not None:
        result["reason"] = reason
    result.update(extra)
    return result


def sync_manual_vectors(session: Any = None) -> dict[str, Any]:
    """docs/manual の凍結節を ``manual_sections`` へ全置換スナップショット同期する。

    Returns
    -------
    dict
        ``{skipped, embedded, unchanged, deleted, total}``（スキップ時は
        ``reason`` も付与）。事実のみを返す（confidence/score 相当の数値は含まない）。
    """
    if not _vector_enabled():
        return _empty_result(skipped=True, reason="disabled")

    violations = validate_manual()
    if violations:
        # fail-closed: 凍結検証を通過していない節を埋め込まない。DELETE も含め
        # 一切書き込まない（既存行はそのまま — §必須条件3）。
        logger.warning(
            "manual vector sync skipped: %d validator violation(s)", len(violations)
        )
        return _empty_result(
            skipped=True, reason="validator_violations", violations_count=len(violations)
        )

    current = _current_snapshot()

    owns_session = session is None
    try:
        sess = session if session is not None else _open_session()
    except Exception:  # noqa: BLE001 — DB 不達で起動やチャットを壊さない
        logger.warning(
            "manual vector sync: DB session unavailable; skipped", exc_info=True
        )
        return _empty_result(skipped=True, reason="db_unavailable")
    try:
        existing_hashes = _fetch_existing_hashes(sess)

        to_embed_keys = [
            key for key, rec in current.items()
            if existing_hashes.get(key) != rec["content_hash"]
        ]
        unchanged_count = len(current) - len(to_embed_keys)

        embeddings: dict[tuple[str, str, str], list[float]] = {}
        if to_embed_keys:
            texts = [
                f"{current[key]['title']}\n\n{current[key]['body']}".strip()
                for key in to_embed_keys
            ]
            try:
                vectors = _embed_texts(texts)
            except Exception:
                # 埋め込み API 失敗時は既存行を消さない・部分適用もしない
                # （§必須条件4）。DB へは一切書き込まずスキップを正直に返す。
                logger.warning(
                    "manual vector sync: embedding generation failed; no writes performed",
                    exc_info=True,
                )
                return _empty_result(
                    skipped=True, reason="embedding_failed", unchanged=unchanged_count,
                    total=len(current),
                )
            if len(vectors) != len(to_embed_keys):
                logger.warning(
                    "manual vector sync: embedding count mismatch (%d texts, %d vectors); no writes performed",
                    len(to_embed_keys), len(vectors),
                )
                return _empty_result(
                    skipped=True, reason="embedding_count_mismatch",
                    unchanged=unchanged_count, total=len(current),
                )
            embeddings = dict(zip(to_embed_keys, vectors))

        for key in to_embed_keys:
            audience, file, anchor = key
            rec = current[key]
            vector = embeddings.get(key)
            sess.execute(
                sa_text(
                    """
                    INSERT INTO manual_sections
                        (audience, file, anchor, title, body, content_hash, embedding, updated_at)
                    VALUES
                        (:audience, :file, :anchor, :title, :body, :content_hash,
                         CAST(:embedding AS vector), now())
                    ON CONFLICT (audience, file, anchor) DO UPDATE
                        SET title = EXCLUDED.title,
                            body = EXCLUDED.body,
                            content_hash = EXCLUDED.content_hash,
                            embedding = EXCLUDED.embedding,
                            updated_at = now()
                    """
                ),
                {
                    "audience": audience,
                    "file": file,
                    "anchor": anchor,
                    "title": rec["title"],
                    "body": rec["body"],
                    "content_hash": rec["content_hash"],
                    "embedding": str(vector),
                },
            )

        # 全置換スナップショット: 現行集合に無い行を同一トランザクションで DELETE
        # する（§必須条件2。孤児行 = 存在しない機能の古い説明を残さない）。
        orphan_keys = [key for key in existing_hashes if key not in current]
        for audience, file, anchor in orphan_keys:
            sess.execute(
                sa_text(
                    "DELETE FROM manual_sections "
                    "WHERE audience = :audience AND file = :file AND anchor = :anchor"
                ),
                {"audience": audience, "file": file, "anchor": anchor},
            )

        sess.commit()
        return {
            "skipped": False,
            "embedded": len(to_embed_keys),
            "unchanged": unchanged_count,
            "deleted": len(orphan_keys),
            "total": len(current),
        }
    except Exception:
        try:
            sess.rollback()
        except Exception:  # noqa: BLE001 — 接続断では rollback 自体も失敗しうる
            pass
        if owns_session:
            # 自前セッション（本番・lifespan 経路）は fail-open: 同期は次回起動・
            # 次回 refresh で追随できるため、例外を漏らして呼び出し元を壊さない。
            # 注入セッション（テスト・呼び出し元管理）は従来どおり伝播する。
            logger.warning(
                "manual vector sync failed; no partial writes kept", exc_info=True
            )
            return _empty_result(skipped=True, reason="db_error")
        raise
    finally:
        if owns_session:
            try:
                sess.close()
            except Exception:  # noqa: BLE001
                pass


def vector_search_manual(
    query: str,
    *,
    audience: str,
    limit: int = 3,
    session: Any = None,
) -> list[dict[str, Any]]:
    """``manual_sections`` の pgvector cosine 類似検索（非ベクトル無ヒット時の補助）。

    ``search_manual`` と完全同形の dict（file/anchor/title/body/audience/citation/
    documented）を返す — score/distance/confidence キーは含めない（§6-9 相当）。
    audience は ``manual.AUDIENCES`` 以外なら ``ValueError``（fail-closed、
    ``search_manual`` と同じ強制点）。しきい値 ``_MAX_COSINE_DISTANCE`` を超える
    行は「無関係」として除外する（無ヒットの方が捏造より安全, P4）。
    """
    if audience not in _manual.AUDIENCES:
        raise ValueError(f"unknown audience: {audience!r}")

    if not _vector_enabled():
        return []

    query_text = (query or "").strip()
    if not query_text:
        return []

    try:
        vectors = _embed_texts([query_text])
    except Exception:
        logger.warning("manual vector search: query embedding failed", exc_info=True)
        return []
    if not vectors or not vectors[0]:
        return []
    qvec = vectors[0]

    visible = list(_manual._VISIBLE_DIRS[audience])

    owns_session = session is None
    sess = session if session is not None else _open_session()
    try:
        rows = sess.execute(
            sa_text(
                """
                SELECT audience, file, anchor, title, body,
                       (embedding <=> CAST(:qvec AS vector)) AS distance
                  FROM manual_sections
                 WHERE audience = ANY(:visible)
                   AND embedding IS NOT NULL
                 ORDER BY distance ASC
                 LIMIT :limit
                """
            ),
            {"qvec": str(qvec), "visible": visible, "limit": max(1, int(limit))},
        ).fetchall()
    except Exception:
        logger.warning("manual vector search query failed", exc_info=True)
        return []
    finally:
        if owns_session:
            sess.close()

    results: list[dict[str, Any]] = []
    for row in rows:
        distance = row[5]
        if distance is None or float(distance) > _MAX_COSINE_DISTANCE:
            continue
        row_audience, file, anchor, title, body = row[0], row[1], row[2], row[3], row[4]
        body_text = body or ""
        results.append(
            {
                "file": file,
                "anchor": anchor,
                "title": title or "",
                "body": body_text,
                "audience": row_audience,
                "citation": f"manual/{row_audience}/{file}#{anchor}",
                "documented": bool(body_text.strip()),
            }
        )
    return results


def _open_session() -> Any:
    from core.postgres import get_session

    return get_session()


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """埋め込み生成を ``usage_context(feature="admin:help_kb_embed")`` 配下で行う。

    ベンダ依存 (``core.llm``) の import をこの関数内に閉じ込める
    （``core/library/search.py::_embed_query`` と同型の遅延 import）。
    """
    from core.llm import generate_embeddings
    from core.llm_usage.context import usage_context

    with usage_context("admin:help_kb_embed"):
        return generate_embeddings(texts)


__all__ = ["sync_manual_vectors", "vector_search_manual"]
