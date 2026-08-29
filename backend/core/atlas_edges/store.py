"""分野マップの関係表示（RE層）— ``atlas_edge_decisions``（migration 076）の
DB プリミティブ。

正本: ``docs/features/atlas_relation_edges_design.md`` §4 / §5。

``core/atlas_gaps/store.py`` と同じ流儀:

- セッションは **呼び出し側が管理する**（``core/postgres.get_session()`` + try/finally）。
  本モジュールは commit / rollback / close を行わない — 呼び出し側（API 層・freeze の
  トランザクション）が1トランザクションとして束ねる。
- 第1引数は必ず ``session``。SQL は ``sqlalchemy.text`` で書き、ORM を使わない。
- FastAPI / services / LLM SDK に依存しない（開発ルール2）。
- バインドパラメータの型キャストは ``CAST(:x AS uuid)`` の形で書く。

不変条項（設計書 §2）:

- **RE5 情報を落とさない**: 本モジュールに DELETE 文は無い（行削除 API を作らない）。
  見送りは ``status='dismissed'``、その取り消しは ``'candidate'`` への遷移で表す。
- **RE3 確定は人間**: 状態遷移は必ず :class:`core.candidate_flow.CandidateFlow` を
  通す（**本番初適用**）。ここに素の状態遷移ロジック（許可遷移の再実装・却下理由の
  再検査）を書かない — ガードレールが構造的に検査する。監査 callable は呼び出し側が
  注入する（core は API 層・``services`` を import しないため）。
- **RE6 候補は保存しない**: 候補行を作る関数は無い。判断行は教員が最初に判断した
  ときに遅延生成する（``INSERT ... ON CONFLICT DO NOTHING`` → 遷移）。
- **RE4 数値非表示**: 件数・スコアを載せる列も関数も無い。
- **RE1 / AB4**: ``atlas_skeletons`` へ書き込まない（骨格の変更は draft→freeze の
  既存フローのみ）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, Mapping, Sequence

from sqlalchemy import text as sa_text

from core import atlas as atlas_module
from core.atlas_edges import schema
from core.candidate_flow import (
    ACTION_CONFIRM,
    ACTION_DISMISS,
    ACTION_RESTORE,
    CandidateFlow,
    CandidateVocabulary,
)
from core.schema import AUDIT_ENTITY_ATLAS_EDGE

logger = logging.getLogger(__name__)

_DECISION_COLUMNS_SQL = """
    id::text, edge_key, status, edge_kind, review_note, applied_version,
    decided_by::text, decided_at, created_at, updated_at
"""

#: この系統の状態語彙（``superseded`` は持たない — 候補は行として保存しないので
#: 「再生成による置換」という概念自体が無い, RE6）。
VOCABULARY = CandidateVocabulary(
    candidate=schema.DECISION_STATUS_CANDIDATE,
    accepted=schema.DECISION_STATUS_ACCEPTED,
    dismissed=schema.DECISION_STATUS_DISMISSED,
)

#: API の action → :class:`CandidateFlow` のメソッド名。
_FLOW_METHODS = {
    schema.ACTION_ACCEPT: "confirm",
    schema.ACTION_DISMISS: "dismiss",
    schema.ACTION_RESTORE: "restore",
}

#: :class:`CandidateFlow` の action 語彙 → 本層の監査 action（設計書 §8）。
#: 共通フローは ``confirm`` と呼ぶが、辺の判断は画面上「採用」なので記帳語彙は
#: ``accept`` に揃える（``atlas_gaps`` の ``AUDIT_ACTION_ACCEPT`` と同じ言葉）。
_FLOW_ACTION_AUDIT = {
    ACTION_CONFIRM: schema.AUDIT_ACTION_ACCEPT,
    ACTION_DISMISS: schema.AUDIT_ACTION_DISMISS,
    ACTION_RESTORE: schema.AUDIT_ACTION_RESTORE,
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _decision_row_to_dict(row: Any) -> dict:
    """判断の DB 行 → dict（列順は :data:`_DECISION_COLUMNS_SQL` と一致させること）。"""
    status = row[2] or schema.DECISION_STATUS_CANDIDATE
    return {
        "id": str(row[0]),
        "edge_key": row[1] or "",
        "status": status,
        "edge_kind": row[3] or "",
        "review_note": row[4] or "",
        "applied_version": row[5] or "",
        "decided_by": row[6] or None,
        "decided_at": _iso(row[7]),
        "created_at": _iso(row[8]) or "",
        "updated_at": _iso(row[9]) or "",
        "status_label": schema.decision_status_label(status),
    }


# ---------------------------------------------------------------------------
# 読み取り
# ---------------------------------------------------------------------------


def _fetch_decisions(session: Any, edge_keys: Iterable[str]) -> dict[str, dict]:
    """``edge_key -> 判断 dict``。空入力では SQL を発行しない。"""
    keys = sorted({_clean(k) for k in (edge_keys or []) if _clean(k)})
    if not keys:
        return {}
    rows = session.execute(
        sa_text(
            f"""
            SELECT {_DECISION_COLUMNS_SQL} FROM atlas_edge_decisions
             WHERE edge_key = ANY(:keys)
            """
        ),
        {"keys": keys},
    ).fetchall()
    out: dict[str, dict] = {}
    for row in rows or []:
        record = _decision_row_to_dict(row)
        out[record["edge_key"]] = record
    return out


def get_decision(session: Any, edge_key: str) -> dict | None:
    """1件の判断（無ければ ``None``）。"""
    key = _clean(edge_key)
    if not key:
        return None
    row = session.execute(
        sa_text(
            f"""
            SELECT {_DECISION_COLUMNS_SQL} FROM atlas_edge_decisions
             WHERE edge_key = :edge_key
            """
        ),
        {"edge_key": key},
    ).fetchone()
    return _decision_row_to_dict(row) if row else None


def dismissed_edge_keys(session: Any, domain_key: str) -> set[str]:
    """当該ドメインの見送り済み ``edge_key`` 集合（糸レイヤーの除外用 = RE8）。"""
    domain = _clean(domain_key)
    if not domain:
        return set()
    rows = session.execute(
        sa_text(
            """
            SELECT edge_key FROM atlas_edge_decisions
             WHERE starts_with(edge_key, :domain_prefix)
               AND status = :dismissed
            """
        ),
        {
            "domain_prefix": schema.edge_key_domain_prefix(domain),
            "dismissed": schema.DECISION_STATUS_DISMISSED,
        },
    ).fetchall()
    return {_clean(r[0]) for r in (rows or []) if _clean(r[0])}


def merge_decisions_into(
    candidates: Sequence[Mapping[str, Any]],
    decisions: Mapping[str, Mapping[str, Any]],
    *,
    include_dismissed: bool = False,
) -> list[dict]:
    """導出済み候補に判断をマージする（**入力を変更しない** — コピーを返す）。

    判断のある候補には ``decision`` キーが付く。``include_dismissed=False``（既定）の
    ときは :data:`schema.SUPPRESSED_DECISION_STATUSES` の候補を落とす（却下の永続性）。
    並び順は入力のまま（導出側が決定論に並べている）。
    """
    out: list[dict] = []
    for candidate in candidates or []:
        record = dict(candidate)
        decision = decisions.get(_clean(record.get("edge_key")))
        if decision is not None:
            status = _clean(decision.get("status"))
            if (
                not include_dismissed
                and status in schema.SUPPRESSED_DECISION_STATUSES
            ):
                continue
            record["decision"] = dict(decision)
        out.append(record)
    return out


# ---------------------------------------------------------------------------
# 判断（遷移は CandidateFlow 経由 — 直書き禁止）
# ---------------------------------------------------------------------------


def _ensure_row(session: Any, edge_key: str) -> dict | None:
    """判断行を ``candidate`` として遅延生成する（既にあれば触らない）。

    候補は保存しない（RE6）ので、行は**教員が最初に判断したときにだけ**生まれる。
    ``ON CONFLICT DO NOTHING`` なので既存行の status / 理由は一切変わらない。
    """
    session.execute(
        sa_text(
            """
            INSERT INTO atlas_edge_decisions (edge_key, status)
                 VALUES (:edge_key, :candidate)
            ON CONFLICT (edge_key) DO NOTHING
            """
        ),
        {"edge_key": edge_key, "candidate": schema.DECISION_STATUS_CANDIDATE},
    )
    return get_decision(session, edge_key)


def _make_apply_status(session: Any) -> Callable[..., dict | None]:
    """:class:`CandidateFlow` に渡す書き込み callable を組む。

    ``metadata['edge_kind']`` が非空なら同時に保存する（採用のときの辺種別）。
    ``review_note`` / ``edge_kind`` は空文字なら既存値を保持する（P4: 状態だけ
    変えたときに理由文・種別を消さない — ``atlas_gaps.upsert_decision`` と同じ規則）。
    """

    def _apply(
        *,
        entity_id: str,
        old_status: str,
        new_status: str,
        actor_id: str | None,
        reason: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict | None:
        payload = dict(metadata or {})
        row = session.execute(
            sa_text(
                f"""
                UPDATE atlas_edge_decisions
                   SET status = :new_status,
                       edge_kind = CASE WHEN :edge_kind <> ''
                            THEN :edge_kind ELSE atlas_edge_decisions.edge_kind END,
                       review_note = CASE WHEN :review_note <> ''
                            THEN :review_note ELSE atlas_edge_decisions.review_note END,
                       decided_by = CAST(:actor_id AS uuid),
                       decided_at = now(),
                       updated_at = now()
                 WHERE edge_key = :edge_key AND status = :old_status
                RETURNING {_DECISION_COLUMNS_SQL}
                """
            ),
            {
                "edge_key": entity_id,
                "old_status": old_status,
                "new_status": new_status,
                "edge_kind": _clean(payload.get("edge_kind")),
                "review_note": _clean(reason),
                "actor_id": _clean(actor_id),
            },
        ).fetchone()
        if row is None:
            # 読み出しと更新のあいだに他の教員が判断した（楽観的な競合）。
            raise ValueError("この関係の判断は、ほかの操作によって変更されています。")
        return _decision_row_to_dict(row)

    return _apply


def decide(
    session: Any,
    *,
    edge_key: str,
    action: str,
    actor_id: str,
    review_note: str = "",
    edge_kind: str = "",
    record_audit: Callable[..., Any],
) -> dict | None:
    """辺候補への教員の判断（``accept`` / ``dismiss`` / ``restore``）。

    遷移の可否・却下理由の必須・帰属（``actor_id``）の検査は
    :class:`core.candidate_flow.CandidateFlow` が行う（本モジュールは再実装しない）。
    ここが足すのは辺固有の検証だけである:

    - ``action='accept'`` は ``edge_kind ∈ core.atlas.EDGE_KINDS`` を必須とする
      （種別の無い辺を骨格へ入れない。語彙外は ``ValueError`` → route が 422）。

    ``record_audit`` は呼び出し側が注入する監査記帳 callable（core は ``services`` を
    import しない）。``entity_type`` / ``entity_id`` / ``action`` / ``old_status`` /
    ``new_status`` / ``actor_id`` / ``reason`` / ``metadata`` のキーワードで呼ばれる。

    Returns:
        遷移結果 dict（``{"entity_id", "action", "old_status", "new_status",
        "applied", "decision"}``）。``restore`` で対象の判断行が無ければ ``None``
        （呼び出し側は 404）。

    Raises:
        ValueError: 引数の不備（``edge_key`` 空 / 未知の action / 採用の種別不正）。
        core.candidate_flow.CandidateTransitionError: 許されない遷移・帰属や却下理由の欠落。
    """
    key = _clean(edge_key)
    if not key:
        raise ValueError("edge_key is required")
    act = _clean(action)
    if act not in _FLOW_METHODS:
        raise ValueError(
            f"invalid action: {action!r} (must be one of {schema.DECIDE_ACTIONS!r})"
        )
    kind = _clean(edge_kind)
    if act == schema.ACTION_ACCEPT and kind not in atlas_module.EDGE_KINDS:
        raise ValueError(
            f"invalid edge kind: {edge_kind!r} "
            f"(must be one of {atlas_module.EDGE_KINDS!r})"
        )

    if act == schema.ACTION_RESTORE:
        # 見送りを取り消す対象が無いなら行を作らない（restore は既存の判断への操作）。
        current = get_decision(session, key)
    else:
        current = _ensure_row(session, key)
    if current is None:
        return None

    def _audit(**kwargs: Any) -> Any:
        """共通フローの action 語彙を本層の監査 action へ写して記帳する（§8）。"""
        kwargs["action"] = _FLOW_ACTION_AUDIT.get(
            kwargs.get("action"), kwargs.get("action")
        )
        return record_audit(**kwargs)

    flow = CandidateFlow(
        vocab=VOCABULARY,
        audit_entity_type=AUDIT_ENTITY_ATLAS_EDGE,
        apply_status=_make_apply_status(session),
        record_audit=_audit,
        require_dismiss_reason=True,
    )
    result = getattr(flow, _FLOW_METHODS[act])(
        key,
        current_status=current["status"],
        actor_id=_clean(actor_id),
        reason=_clean(review_note),
        metadata={"edge_kind": kind} if kind else {},
    )
    out = dict(result)
    # 呼び出し側（route / 監査）には API の語彙で返す（``confirm`` を漏らさない）。
    out["action"] = act
    out["decision"] = result.get("applied")
    return out


# ---------------------------------------------------------------------------
# 凍結との接続（gap 同型。fail-open の収集は route 側）
# ---------------------------------------------------------------------------


def _pair_keys(domain_key: str, pairs: Iterable[Any]) -> set[str]:
    """``(from, to)`` の列を ``edge_key`` 集合へ正規化する（無向・重複排除）。"""
    out: set[str] = set()
    for pair in pairs or []:
        if isinstance(pair, Mapping):
            left, right = pair.get("from"), pair.get("to")
        else:
            try:
                left, right = pair  # type: ignore[misc]
            except (TypeError, ValueError):
                continue
        a, b = schema.undirected_pair(left, right)
        if a and b and a != b:
            out.add(schema.build_edge_key(domain_key, a, b))
    return out


def stamp_applied_versions(
    session: Any,
    *,
    domain_key: str,
    frozen_version: str,
    frozen_edge_pairs: Iterable[Any],
) -> list[str]:
    """凍結時に「実際に反映された」判断へ ``applied_version`` を刻印する（§5）。

    対象は当該ドメインの ``status='accepted'`` かつ ``applied_version=''`` かつ
    **凍結された骨格に無向ペアが実在する**行だけ（採用しただけでは刻印しない）。

    ``frozen_version`` または ``frozen_edge_pairs`` が空なら **SQL を発行せず** ``[]``
    （空集合を「全件」に転ばせない fail-closed）。戻り値は刻印した edge_key の一覧。
    """
    domain = _clean(domain_key)
    version = _clean(frozen_version)
    keys = sorted(_pair_keys(domain, frozen_edge_pairs))
    if not domain or not version or not keys:
        return []
    rows = session.execute(
        sa_text(
            """
            UPDATE atlas_edge_decisions
               SET applied_version = :version, updated_at = now()
             WHERE starts_with(edge_key, :domain_prefix)
               AND status = :accepted
               AND applied_version = ''
               AND edge_key = ANY(:keys)
            RETURNING edge_key
            """
        ),
        {
            "version": version,
            "domain_prefix": schema.edge_key_domain_prefix(domain),
            "accepted": schema.DECISION_STATUS_ACCEPTED,
            "keys": keys,
        },
    ).fetchall()
    return [_clean(r[0]) for r in (rows or [])]


def list_pending_for_freeze(
    session: Any, *, domain_key: str, draft_edge_pairs: Iterable[Any]
) -> list[dict]:
    """公開前チェック用「採用済みでまだ次版の下書きに入っていない辺」（§5）。

    対象は当該ドメインの ``status='accepted'`` かつ ``applied_version=''`` のうち、
    無向ペアが現在の下書きの edges に**無い**もの。``draft_edge_pairs`` が空なら
    「下書きに辺が1本も無い」ので該当する採用済み候補はすべて未反映として返す。

    各要素は ``{edge_key, from_id, to_id, edge_kind, review_note}``。**件数ではなく
    ラベルの列挙**で提示するための形（RE4。ラベルの解決は骨格を持つ route 側）。
    """
    domain = _clean(domain_key)
    if not domain:
        return []
    rows = session.execute(
        sa_text(
            f"""
            SELECT {_DECISION_COLUMNS_SQL} FROM atlas_edge_decisions
             WHERE starts_with(edge_key, :domain_prefix)
               AND status = :accepted
               AND applied_version = ''
             ORDER BY edge_key
            """
        ),
        {
            "domain_prefix": schema.edge_key_domain_prefix(domain),
            "accepted": schema.DECISION_STATUS_ACCEPTED,
        },
    ).fetchall()
    if not rows:
        return []

    draft_keys = _pair_keys(domain, draft_edge_pairs)
    out: list[dict] = []
    for row in rows:
        decision = _decision_row_to_dict(row)
        key = decision["edge_key"]
        if key in draft_keys:
            continue
        _, from_id, to_id = schema.parse_edge_key(key)
        out.append(
            {
                "edge_key": key,
                "from_id": from_id,
                "to_id": to_id,
                "edge_kind": decision["edge_kind"],
                "review_note": decision["review_note"],
            }
        )
    return out


__all__ = [
    "VOCABULARY",
    "decide",
    "dismissed_edge_keys",
    "get_decision",
    "list_pending_for_freeze",
    "merge_decisions_into",
    "stamp_applied_versions",
]
