"""分野マップのベクトル係留層 — ``atlas_anchor_embeddings`` / ``atlas_anchor_aliases``
（migration 074）の DB プリミティブ。

正本: ``docs/features/atlas_vector_anchoring_design.md`` §3 / §7。

``core/atlas_gaps/store.py`` / ``core/landscape/store.py`` と同じ流儀:

- セッションは原則 **呼び出し側が管理する**（第1引数が ``session``）。本モジュールは
  commit / rollback / close を行わない — 呼び出し側（builder・API 層）が1トランザク
  ションとして束ねる。自前でセッションを開く経路（``session=None``）を持つ関数は
  ``core.postgres.get_session()`` + try/finally で必ず閉じる。
- SQL は ``sqlalchemy.text`` で書き、ORM を使わない。
- FastAPI / services / LLM SDK に依存しない（開発ルール2）。
- バインドの型キャストは ``CAST(:x AS uuid)`` / ``CAST(:x AS vector)`` の形で書く。

不変条項（設計書 §2）:

- **VA6 情報を落とさない**: 別名に行削除は無い。見送りは ``status='dismissed'``、
  その取り消しは ``'confirmed'`` への遷移（UNIQUE が重複行を防ぐ）。
  本モジュールで唯一の ``DELETE FROM`` は :func:`replace_domain_embeddings` の
  (domain_key, skeleton_version) 単位の全置換で、これは設計書 §3 要点4 が明示する
  例外（アンカーベクトルは**導出データであり正本ではない** — help_kb
  ``vector.py`` のスナップショット同期と同じ扱い）。ガードレールが関数単位で許可する。
- **VA9 骨格へ書き込まない**: 本モジュールに ``atlas_skeletons`` への
  INSERT/UPDATE は存在しない。
- **VA2 数値非表示**: 行 dict は DB 界面までのもので、cosine 生値・similarity は
  ここでは扱わない（比較は ``query.py`` の純計算）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import text as sa_text

from core.atlas_vectors import schema

logger = logging.getLogger(__name__)

_ALIAS_COLUMNS_SQL = """
    id::text, domain_key, node_id, alias, normalized_alias, status, source,
    evidence, created_by::text, decided_by::text, created_at, updated_at
"""


def _clean(value: object) -> str:
    return str(value or "").strip()


def _open_session() -> Any:
    """自前セッション（``core.postgres`` の遅延 import — core の純粋性を保つ）。"""
    from core.postgres import get_session

    return get_session()


def parse_vector(raw: Any) -> Optional[list[float]]:
    """pgvector の値を ``list[float]`` へ（解釈できなければ ``None``）。

    ドライバ設定により ``"[0.1,0.2]"`` の文字列で返ることがあるため、リスト /
    タプル / 文字列のいずれも受ける（黙って 0 ベクトルに化けさせない —
    ``core/paper_discovery/ranking.py::_parse_vector`` と同じ規約）。
    """
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        values: Iterable[Any] = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text.startswith("[") or not text.endswith("]"):
            return None
        body = text[1:-1].strip()
        if not body:
            return None
        values = body.split(",")
    else:
        return None

    out: list[float] = []
    for value in values:
        try:
            out.append(float(value))
        except (TypeError, ValueError):
            return None
    return out or None


# ---------------------------------------------------------------------------
# アンカーベクトル（atlas_anchor_embeddings）
# ---------------------------------------------------------------------------


class AnchorVector:
    """1骨格ノードのプロトタイプベクトル（読み出し用の軽量レコード）。

    ``dataclass`` にせず素の class で持つのは、生成元が DB 行のみで、フィールドを
    後から足す予定が無いため（``query.py`` は読むだけ）。
    """

    __slots__ = ("node_id", "node_kind", "label", "region_id", "region_label", "vector")

    def __init__(
        self,
        *,
        node_id: str,
        node_kind: str,
        label: str = "",
        region_id: str = "",
        region_label: str = "",
        vector: Optional[Sequence[float]] = None,
    ) -> None:
        self.node_id = node_id
        self.node_kind = node_kind
        self.label = label
        self.region_id = region_id
        self.region_label = region_label
        self.vector = list(vector) if vector else None

    def __repr__(self) -> str:  # pragma: no cover — デバッグ用
        return (
            f"AnchorVector(node_id={self.node_id!r}, node_kind={self.node_kind!r}, "
            f"label={self.label!r}, has_vector={self.vector is not None})"
        )


def replace_domain_embeddings(
    session: Any,
    domain_key: str,
    skeleton_version: str,
    rows: Sequence[Mapping[str, Any]],
) -> int:
    """(domain_key, skeleton_version) のアンカー行を**全置換**する。

    設計書 §3 要点4 が明示する唯一の DELETE 経路。アンカーベクトルは骨格と確定情報
    からの**導出データ**であり正本ではないので、部分更新で孤児（骨格から消えた
    node_id の古いベクトル）が残り続けるより、版単位のスナップショット置換の方が
    正しい（help_kb ``vector.py::sync_manual_vectors`` と同じ判断）。

    ``rows`` の各要素は ``{node_id, node_kind, source_text, source_hash, embedding}``。
    ``embedding`` は ``list[float]`` か ``None``（ベクトルを作れなかったノードも
    合成テキストとハッシュだけは残す — 次回 refresh の差分判定に必要）。

    **空の ``rows`` では SQL を一切発行しない**（0件の再構築が生きた索引を消さない —
    landscape ``supersede_and_insert_candidates`` と同じ fail-closed）。

    Returns:
        書き込んだ行数。
    """
    domain = _clean(domain_key)
    version = _clean(skeleton_version)
    items = [r for r in (rows or []) if _clean(r.get("node_id"))]
    if not domain or not version or not items:
        return 0

    session.execute(
        sa_text(
            "DELETE FROM atlas_anchor_embeddings "
            "WHERE domain_key = :domain_key AND skeleton_version = :version"
        ),
        {"domain_key": domain, "version": version},
    )

    written = 0
    for row in items:
        node_id = _clean(row.get("node_id"))
        node_kind = _clean(row.get("node_kind"))
        if not schema.is_valid_node_kind(node_kind):
            # 語彙外は CHECK 違反でトランザクションごと落ちるため、ここで弾く。
            logger.warning(
                "atlas anchor: skipping node %s with invalid node_kind %r",
                node_id, node_kind,
            )
            continue
        vector = row.get("embedding")
        session.execute(
            sa_text(
                """
                INSERT INTO atlas_anchor_embeddings
                    (domain_key, skeleton_version, node_id, node_kind,
                     source_text, source_hash, embedding, built_at)
                VALUES
                    (:domain_key, :version, :node_id, :node_kind,
                     :source_text, :source_hash, CAST(:embedding AS vector), now())
                """
            ),
            {
                "domain_key": domain,
                "version": version,
                "node_id": node_id,
                "node_kind": node_kind,
                "source_text": str(row.get("source_text") or ""),
                "source_hash": str(row.get("source_hash") or ""),
                "embedding": str(list(vector)) if vector else None,
            },
        )
        written += 1
    return written


def load_anchor_vectors(
    session: Any,
    domain_key: str,
    skeleton_version: str,
) -> list[AnchorVector]:
    """現行凍結版のアンカーベクトル一覧（ベクトルを持つ行のみ）。

    ``label`` / ``region_id`` / ``region_label`` は骨格側の情報なので DB には無い。
    呼び出し側（builder / query）が骨格から補う想定で、ここでは ``node_id`` の
    ``source_text`` 先頭行を label の代わりに埋める（骨格が引けない読み取り経路でも
    ノード名を事実として出せるようにする — 捏造ではなく合成時に記録した実値）。
    """
    domain = _clean(domain_key)
    version = _clean(skeleton_version)
    if not domain or not version:
        return []
    rows = session.execute(
        sa_text(
            """
            SELECT node_id, node_kind, source_text, embedding
              FROM atlas_anchor_embeddings
             WHERE domain_key = :domain_key
               AND skeleton_version = :version
               AND embedding IS NOT NULL
             ORDER BY node_kind, node_id
            """
        ),
        {"domain_key": domain, "version": version},
    ).fetchall()

    out: list[AnchorVector] = []
    for row in rows:
        vector = parse_vector(row[3])
        if not vector:
            continue
        source_text = str(row[2] or "")
        label = source_text.split("\n", 1)[0].strip()
        out.append(
            AnchorVector(
                node_id=str(row[0] or ""),
                node_kind=str(row[1] or ""),
                label=label,
                vector=vector,
            )
        )
    return out


def anchors_for_domains(
    session: Any, domains: Iterable[Mapping[str, Any]]
) -> dict[str, list[AnchorVector]]:
    """``collect_placement_domains()`` の一覧 → ``{domain_key: [AnchorVector, ...]}``。

    ``query.prefilter_domains`` は純計算（DB 非接触）なので、その入力を作る
    「ドメインごとの現行凍結版アンカー読み出し」をここに置く。ドメイン単位で
    fail-soft — 1件読めなくても他のドメインの絞り込みは成立させる（VA4）。
    """
    out: dict[str, list[AnchorVector]] = {}
    for domain in domains or []:
        domain_key = _clean(domain.get("domain_key"))
        version = _clean(domain.get("skeleton_version"))
        if not domain_key or not version:
            continue
        try:
            out[domain_key] = load_anchor_vectors(session, domain_key, version)
        except Exception:  # noqa: BLE001 — 読めないドメインは絞り込まないだけ
            logger.warning(
                "atlas anchor vectors unavailable for domain %s (non-fatal)",
                domain_key, exc_info=True,
            )
    return out


def stored_hashes(session: Any, domain_key: str, skeleton_version: str) -> dict[str, str]:
    """``{node_id: source_hash}``（refresh の差分判定用）。"""
    domain = _clean(domain_key)
    version = _clean(skeleton_version)
    if not domain or not version:
        return {}
    rows = session.execute(
        sa_text(
            """
            SELECT node_id, source_hash FROM atlas_anchor_embeddings
             WHERE domain_key = :domain_key AND skeleton_version = :version
            """
        ),
        {"domain_key": domain, "version": version},
    ).fetchall()
    return {str(r[0] or ""): str(r[1] or "") for r in rows}


def stored_vectors(
    session: Any, domain_key: str, skeleton_version: str
) -> dict[str, list[float]]:
    """``{node_id: vector}``（変化しなかったノードのベクトル再利用に使う）。

    全置換方式のため、差分だけを埋め込んだ場合でも既存ベクトルを読み直して
    書き戻す必要がある（設計書 §4 — 変化分のみ embed → 全置換保存）。
    """
    domain = _clean(domain_key)
    version = _clean(skeleton_version)
    if not domain or not version:
        return {}
    rows = session.execute(
        sa_text(
            """
            SELECT node_id, embedding FROM atlas_anchor_embeddings
             WHERE domain_key = :domain_key AND skeleton_version = :version
               AND embedding IS NOT NULL
            """
        ),
        {"domain_key": domain, "version": version},
    ).fetchall()
    out: dict[str, list[float]] = {}
    for row in rows:
        vector = parse_vector(row[1])
        if vector:
            out[str(row[0] or "")] = vector
    return out


def coverage_status(session: Any, domain_key: str, skeleton_version: str) -> dict:
    """索引の状態（インフラ事実のみ — VA2 の例外に当たる運用カバレッジ）。

    Returns:
        ``{"total_rows": int, "embedded_rows": int, "built_at": datetime|None}``。
        評価数値ではなく「索引済みか」の運用事実なので、件数を返してよい（VA2 の
        明示された例外）。
    """
    domain = _clean(domain_key)
    version = _clean(skeleton_version)
    if not domain or not version:
        return {"total_rows": 0, "embedded_rows": 0, "built_at": None}
    row = session.execute(
        sa_text(
            """
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE embedding IS NOT NULL),
                   MAX(built_at)
              FROM atlas_anchor_embeddings
             WHERE domain_key = :domain_key AND skeleton_version = :version
            """
        ),
        {"domain_key": domain, "version": version},
    ).fetchone()
    if not row:
        return {"total_rows": 0, "embedded_rows": 0, "built_at": None}
    return {
        "total_rows": int(row[0] or 0),
        "embedded_rows": int(row[1] or 0),
        "built_at": row[2],
    }


# ---------------------------------------------------------------------------
# 別名レジストリ（atlas_anchor_aliases）— status 遷移のみ・行削除なし（VA6）
# ---------------------------------------------------------------------------


def _alias_row_to_dict(row: Any) -> dict:
    evidence = row[7]
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except (TypeError, ValueError):
            evidence = {}
    return {
        "id": row[0] or "",
        "domain_key": row[1] or "",
        "node_id": row[2] or "",
        "alias": row[3] or "",
        "normalized_alias": row[4] or "",
        "status": row[5] or "",
        "source": row[6] or "",
        "evidence": evidence if isinstance(evidence, dict) else {},
        "created_by": row[8] or "",
        "decided_by": row[9] or "",
        "created_at": row[10],
        "updated_at": row[11],
    }


def list_aliases(
    session: Any, domain_key: str, *, include_dismissed: bool = False
) -> list[dict]:
    """ドメインの別名一覧（既定は confirmed のみ）。

    ``include_dismissed=True`` で見送り済みも含める（P4 の可視化。行は消えていない
    ので「見送り済み」フィルタから復帰させられる）。
    """
    domain = _clean(domain_key)
    if not domain:
        return []
    clauses = ["domain_key = :domain_key"]
    params: dict[str, Any] = {"domain_key": domain}
    if not include_dismissed:
        clauses.append("status = :status")
        params["status"] = "confirmed"
    rows = session.execute(
        sa_text(
            f"""
            SELECT {_ALIAS_COLUMNS_SQL} FROM atlas_anchor_aliases
             WHERE {" AND ".join(clauses)}
             ORDER BY node_id, normalized_alias
            """
        ),
        params,
    ).fetchall()
    return [_alias_row_to_dict(r) for r in rows]


def confirmed_aliases_by_node(session: Any, domain_key: str) -> dict[str, list[str]]:
    """``{node_id: [alias, ...]}``（confirmed のみ・normalized 昇順）。

    プロトタイプ合成（``schema.build_anchor_source_text``）と keyphrase 供給の
    両方が読む「還流」の入口（設計書 §7）。
    """
    out: dict[str, list[str]] = {}
    for row in list_aliases(session, domain_key, include_dismissed=False):
        out.setdefault(row["node_id"], []).append(row["alias"])
    return out


def upsert_alias(
    session: Any,
    *,
    domain_key: str,
    node_id: str,
    alias: str,
    source: str = "manual",
    evidence: Optional[Mapping[str, Any]] = None,
    user_id: str,
) -> dict:
    """別名を登録する（既存 ``dismissed`` 行があれば ``confirmed`` へ復帰）。

    行削除をしないため、「見送り → やっぱり登録」は同一行の status 遷移で表す
    （UNIQUE ``(domain_key, node_id, normalized_alias)`` が重複行を防ぐ, VA6）。

    検証（いずれも ``ValueError``。route が 422 に変換する契約）:

    - ``domain_key`` / ``node_id`` / ``alias`` が空
    - ``alias`` の正規化結果が空（空白のみ等）
    - ``user_id`` が空（帰属必須 — 匿名の確定操作を作らない）
    - ``source`` が :data:`schema.ALIAS_SOURCES` の語彙外

    ``node_id`` が現行凍結骨格に実在するかの検査は**呼び出し側（route 層）の責務**
    （骨格の読みは ``atlas_store`` 経由であり、store 層は骨格を知らない）。
    """
    domain = _clean(domain_key)
    node = _clean(node_id)
    text = _clean(alias)
    actor = _clean(user_id)
    if not domain:
        raise ValueError("domain_key is required")
    if not node:
        raise ValueError("node_id is required")
    if not text:
        raise ValueError("alias is required")
    if not actor:
        raise ValueError("user_id is required")
    if not schema.is_valid_alias_source(source):
        raise ValueError(f"invalid alias source: {source!r}")
    normalized = schema.normalize_label(text)
    if not normalized:
        raise ValueError("alias normalizes to an empty string")

    row = session.execute(
        sa_text(
            f"""
            INSERT INTO atlas_anchor_aliases (
                domain_key, node_id, alias, normalized_alias, status, source,
                evidence, created_by, decided_by, created_at, updated_at
            ) VALUES (
                :domain_key, :node_id, :alias, :normalized_alias, 'confirmed', :source,
                CAST(:evidence AS jsonb), CAST(:user_id AS uuid), CAST(:user_id AS uuid),
                now(), now()
            )
            ON CONFLICT (domain_key, node_id, normalized_alias) DO UPDATE
               SET status = 'confirmed',
                   alias = EXCLUDED.alias,
                   source = EXCLUDED.source,
                   evidence = CASE WHEN EXCLUDED.evidence <> '{{}}'::jsonb
                        THEN EXCLUDED.evidence ELSE atlas_anchor_aliases.evidence END,
                   decided_by = EXCLUDED.decided_by,
                   updated_at = now()
            RETURNING {_ALIAS_COLUMNS_SQL}
            """
        ),
        {
            "domain_key": domain,
            "node_id": node,
            "alias": text,
            "normalized_alias": normalized,
            "source": str(source),
            "evidence": json.dumps(dict(evidence or {}), ensure_ascii=False),
            "user_id": actor,
        },
    ).fetchone()
    return _alias_row_to_dict(row)


def dismiss_alias(session: Any, alias_id: str, *, user_id: str) -> Optional[dict]:
    """別名を見送りにする（``status='dismissed'`` へ遷移。**行は消さない**, VA6）。

    Returns:
        更新後の行 dict。該当 id が無ければ ``None``（route が 404 に変換する契約）。
    """
    key = _clean(alias_id)
    actor = _clean(user_id)
    if not key:
        return None
    if not actor:
        raise ValueError("user_id is required")
    row = session.execute(
        sa_text(
            f"""
            UPDATE atlas_anchor_aliases
               SET status = 'dismissed',
                   decided_by = CAST(:user_id AS uuid),
                   updated_at = now()
             WHERE id = CAST(:id AS uuid)
            RETURNING {_ALIAS_COLUMNS_SQL}
            """
        ),
        {"id": key, "user_id": actor},
    ).fetchone()
    return _alias_row_to_dict(row) if row else None


def get_alias(session: Any, alias_id: str) -> Optional[dict]:
    """id 指定の1行（権限・存在確認用）。"""
    key = _clean(alias_id)
    if not key:
        return None
    row = session.execute(
        sa_text(
            f"SELECT {_ALIAS_COLUMNS_SQL} FROM atlas_anchor_aliases "
            "WHERE id = CAST(:id AS uuid)"
        ),
        {"id": key},
    ).fetchone()
    return _alias_row_to_dict(row) if row else None


def with_session(fn, session: Any = None, *args: Any, **kwargs: Any) -> Any:
    """``session`` 省略時に自前セッションを開いて必ず閉じるヘルパー。

    ``atlas_store`` と同じく「注入されたセッションは呼び出し側が管理し、自前で
    開いたものだけ close する」規約を1箇所に閉じ込める。
    """
    if session is not None:
        return fn(session, *args, **kwargs)
    own = _open_session()
    try:
        return fn(own, *args, **kwargs)
    finally:
        try:
            own.close()
        except Exception:  # noqa: BLE001 — 接続断では close 自体も失敗しうる
            logger.debug("atlas_vectors session close failed", exc_info=True)


__all__ = [
    "AnchorVector",
    "anchors_for_domains",
    "confirmed_aliases_by_node",
    "coverage_status",
    "dismiss_alias",
    "get_alias",
    "list_aliases",
    "load_anchor_vectors",
    "parse_vector",
    "replace_domain_embeddings",
    "stored_hashes",
    "stored_vectors",
    "upsert_alias",
    "with_session",
]
