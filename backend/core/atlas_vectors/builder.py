"""分野マップのベクトル係留層 — アンカーベクトルの構築。

正本: ``docs/features/atlas_vector_anchoring_design.md`` §4 / §5。

現行凍結骨格の全ノードについて、確定情報（確定別名 + 確定配置の evidence 引用）を
織り込んだプロトタイプ合成テキストを作り、``source_hash`` の比較で**変化分だけ**を
1バッチで埋め込み、(domain_key, skeleton_version) 単位で全置換保存する。

不変条項:

- **VA3 埋め込みは凍結時・教員起点・パイプラインのみ**: 本モジュールの呼び出し地点は
  ①freeze 後の best-effort 再構築 ②教員の明示 refresh ③別名登録後の単ノード再構築
  に限る（学習者起点の経路を作らない）。**起動時の自動バックフィルもしない**。
- **VA5 埋め込みモデルは chunks と同一**（3072次元と結合。scene を持たない
  ``embedding:atlas_anchors`` で U層に帰属させる）。
- **VA6**: 保存は全置換のみ（DELETE は ``store.replace_domain_embeddings`` に閉じる）。
- **VA9 骨格へ書き込まない**: 骨格は ``atlas_store.load_frozen_skeleton`` で**読むだけ**。
- コスト上限は ``ATLAS_VECTOR_MAX_CALLS_PER_DAY``（in-memory CostGate。日次キーは
  :func:`annotate` 側と**共有**する — 同じ層の embedding バッチ回数として数える）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import text as sa_text

from core.atlas_vectors import schema, store
from core.llm_worker.cost_gate import CostGate, today_str

logger = logging.getLogger(__name__)

#: 日次上限のカウンタ（プロセス内。API サーバは単一プロセス運用 — cost_gate の前提）。
#: :mod:`core.atlas_vectors.annotate` と**同じインスタンス**を共有する（構築も注記も
#: 同じ層の embedding バッチであり、上限は層で1本にする — 設計書 §7）。
_gate = CostGate()

#: 日次カウンタのキー接頭辞。
_DAILY_KEY_PREFIX = "atlas_vectors"

# skip 理由の語彙（呼び出し側の分岐・テストが読む。事実のみ・数値を含めない）。
SKIP_NO_FROZEN_SKELETON = "no_frozen_skeleton"
SKIP_DAILY_LIMIT = "daily_call_limit_reached"
SKIP_NO_NODES = "no_skeleton_nodes"


def _clean(value: object) -> str:
    return str(value or "").strip()


def _daily_key() -> tuple[str, str]:
    return (_DAILY_KEY_PREFIX, today_str())


def check_daily_gate(daily_limit: int) -> bool:
    """層で共有する日次ゲート（構築 / 注記の両方がこれを通す）。"""
    return _gate.check_and_count(
        daily_limit=int(daily_limit),
        daily_key=_daily_key(),
        prune_stale_daily=True,
    )


def reset_daily_counter() -> None:
    """日次カウンタを初期化する（テスト用。本番コードから呼ばない）。"""
    _gate.daily_counts.clear()
    _gate.session_counts.clear()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """テキスト群を1バッチで埋め込む（U層計測つき）。

    依存の重さを入口に持ち込まないため、``core.llm`` はここで遅延 import する
    （``core/help_kb/vector.py::_embed_texts`` / ``ranking.py::_embed`` と同型）。
    """
    from core.llm import generate_embeddings
    from core.llm_usage.context import usage_context

    with usage_context("embedding:atlas_anchors"):
        return generate_embeddings(texts)


# ---------------------------------------------------------------------------
# 素材の収集（骨格 + 確定情報）
# ---------------------------------------------------------------------------


def _skeleton_node_specs(skeleton: Any) -> list[dict]:
    """凍結骨格 → プロトタイプ合成の素材（region が先、その配下 concept が続く）。

    骨格ノードは id + label しか持たない（description は無い）ので、精密化は
    確定別名と確定配置の evidence 引用に依存する（設計書 §4）。
    """
    specs: list[dict] = []
    for region in getattr(skeleton, "regions", ()) or ():
        region_id = _clean(getattr(region, "id", ""))
        if not region_id:
            continue
        region_label = _clean(getattr(region, "label", "")) or region_id
        concepts = list(getattr(region, "concepts", ()) or ())
        child_labels = [
            _clean(getattr(c, "label", "")) or _clean(getattr(c, "id", ""))
            for c in concepts
        ]
        specs.append(
            {
                "node_id": region_id,
                "node_kind": "region",
                "label": region_label,
                "region_id": "",
                "region_label": "",
                "child_labels": [c for c in child_labels if c],
            }
        )
        for concept in concepts:
            concept_id = _clean(getattr(concept, "id", ""))
            if not concept_id:
                continue
            specs.append(
                {
                    "node_id": concept_id,
                    "node_kind": "concept",
                    "label": _clean(getattr(concept, "label", "")) or concept_id,
                    "region_id": region_id,
                    "region_label": region_label,
                    "child_labels": [],
                }
            )
    return specs


def collect_evidence_quotes(
    session: Any, domain_key: str
) -> dict[str, list[str]]:
    """``{node_id: [evidence quote, ...]}``（確定配置のみ・placement id 昇順）。

    ``landscape_placements``（migration 065）の ``status='confirmed'`` 行の
    ``evidence`` JSONB（``[{"quote", "claim_id"}, ...]``）から引用文だけを拾う。
    **読み取り専用・fail-soft**（読めなければ空 dict — 根拠が無いだけで
    プロトタイプは label から作れる, VA4）。

    件数・文字数の上限は :mod:`core.atlas_vectors.schema` の定数
    （合成側でも切るが、無駄な読み込みを避けるためここでも打ち切る）。
    """
    domain = _clean(domain_key)
    if not domain:
        return {}
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT node_id, evidence FROM landscape_placements
                 WHERE domain_key = :domain_key AND status = 'confirmed'
                 ORDER BY id
                """
            ),
            {"domain_key": domain},
        ).fetchall()
    except Exception:  # noqa: BLE001 — 根拠が引けなくても構築は成立させる
        logger.warning(
            "atlas anchor: confirmed placement evidence unavailable for %s (non-fatal)",
            domain, exc_info=True,
        )
        return {}

    out: dict[str, list[str]] = {}
    for row in rows:
        node_id = _clean(row[0])
        if not node_id:
            continue
        bucket = out.setdefault(node_id, [])
        if len(bucket) >= schema.MAX_EVIDENCE_QUOTES:
            continue
        evidence = row[1]
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except (TypeError, ValueError):
                continue
        if not isinstance(evidence, list):
            continue
        for item in evidence:
            if not isinstance(item, Mapping):
                continue
            quote = " ".join(str(item.get("quote") or "").split())
            if not quote:
                continue
            bucket.append(quote[: schema.MAX_EVIDENCE_QUOTE_CHARS])
            if len(bucket) >= schema.MAX_EVIDENCE_QUOTES:
                break
    return out


# ---------------------------------------------------------------------------
# 構築本体
# ---------------------------------------------------------------------------


def build_anchor_embeddings(
    domain_key: str,
    *,
    session: Any = None,
    force: bool = False,
    node_ids: Optional[Iterable[str]] = None,
) -> dict:
    """現行凍結骨格のアンカーベクトルを構築する（設計書 §4 / §5）。

    手順:

    1. ``atlas_store.load_frozen_skeleton`` で現行凍結版を読む（無ければ skip）
    2. 確定別名 + 確定配置の evidence 引用を集める
    3. プロトタイプ合成テキストを作り、保存済み ``source_hash`` と比較する
    4. 変化分（``force=True`` なら全件）だけを1バッチで埋め込む
    5. 変化しなかったノードは**保存済みベクトルを読み直して**全置換保存する

    Args:
        domain_key: 分野キー（= cartridge_id 名前空間）。
        session: 注入セッション（省略時は自前で開いて必ず閉じる）。
        force: ハッシュ一致でも再埋め込みする。
        node_ids: 指定があればそのノードだけを再構築の対象にする
            （別名登録後の単ノード再埋め込み）。他ノードは保存済みベクトルを
            引き継いだうえで全置換書き込みを行う。

    Returns:
        ``{"status": "completed", "domain_key", "skeleton_version", "total_nodes",
        "embedded", "reused"}`` または
        ``{"status": "skipped", "skipped_reason": ...}``。

    Raises:
        埋め込み・DB の例外はそのまま送出する（手動 refresh の呼び出し元が事実を
        受け取れるようにするため）。freeze フック側は呼び出しを try/except で包み、
        凍結を止めない（VA4 — fail-soft の責務は呼び出し側）。
    """
    return store.with_session(
        _build_anchor_embeddings, session, domain_key, force=force, node_ids=node_ids
    )


def _build_anchor_embeddings(
    session: Any,
    domain_key: str,
    *,
    force: bool = False,
    node_ids: Optional[Iterable[str]] = None,
) -> dict:
    from core import atlas_store  # 遅延 import（core の依存を入口で重くしない）

    domain = _clean(domain_key)
    if not domain:
        return {"status": "skipped", "skipped_reason": SKIP_NO_FROZEN_SKELETON}

    skeleton = atlas_store.load_frozen_skeleton(session, domain)
    if skeleton is None:
        return {"status": "skipped", "skipped_reason": SKIP_NO_FROZEN_SKELETON}

    version = _clean(getattr(skeleton, "version", ""))
    specs = _skeleton_node_specs(skeleton)
    if not version or not specs:
        return {"status": "skipped", "skipped_reason": SKIP_NO_NODES}

    aliases = store.confirmed_aliases_by_node(session, domain)
    quotes = collect_evidence_quotes(session, domain)

    # プロトタイプ合成（決定論。正本は schema.build_anchor_source_text）。
    for spec in specs:
        spec["source_text"] = schema.build_anchor_source_text(
            spec["label"],
            aliases=aliases.get(spec["node_id"]),
            region_label=spec["region_label"],
            child_labels=spec["child_labels"],
            evidence_quotes=quotes.get(spec["node_id"]),
        )
        spec["source_hash"] = schema.source_hash(spec["source_text"])

    known_hashes = store.stored_hashes(session, domain, version)
    known_vectors = store.stored_vectors(session, domain, version)

    subset = {_clean(n) for n in (node_ids or ())} if node_ids is not None else None
    targets: list[dict] = []
    for spec in specs:
        if subset is not None and spec["node_id"] not in subset:
            continue
        if not spec["source_text"]:
            continue
        changed = known_hashes.get(spec["node_id"]) != spec["source_hash"]
        missing = spec["node_id"] not in known_vectors
        if force or changed or missing:
            targets.append(spec)

    embedded = 0
    if targets:
        from core.config import get_settings  # 遅延 import（core の純粋性を保つ）

        daily_limit = get_settings().atlas_vector_max_calls_per_day
        if not check_daily_gate(daily_limit):
            return {"status": "skipped", "skipped_reason": SKIP_DAILY_LIMIT}

        vectors = embed_texts([spec["source_text"] for spec in targets])
        if len(vectors) != len(targets):
            raise ValueError(
                "atlas anchor embedding count mismatch: "
                f"{len(targets)} texts / {len(vectors)} vectors"
            )
        for spec, vector in zip(targets, vectors):
            spec["embedding"] = list(vector) if vector else None
            embedded += 1

    # 全置換書き込み: 今回埋め込まなかったノードは保存済みベクトルを引き継ぐ
    # （設計書 §4 — 変化分のみ embed → 全置換保存）。
    rows: list[dict] = []
    reused = 0
    for spec in specs:
        vector = spec.get("embedding")
        if vector is None:
            vector = known_vectors.get(spec["node_id"])
            if vector is not None:
                reused += 1
        rows.append(
            {
                "node_id": spec["node_id"],
                "node_kind": spec["node_kind"],
                "source_text": spec["source_text"],
                "source_hash": spec["source_hash"],
                "embedding": vector,
            }
        )

    store.replace_domain_embeddings(session, domain, version, rows)
    session.commit()

    return {
        "status": "completed",
        "domain_key": domain,
        "skeleton_version": version,
        "total_nodes": len(specs),
        "embedded": embedded,
        "reused": reused,
    }


def anchors_with_labels(
    session: Any, domain_key: str, skeleton_version: str = ""
) -> tuple[list[store.AnchorVector], str]:
    """現行凍結版のアンカー一覧を骨格の label / region_label で補って返す。

    ``store.load_anchor_vectors`` は DB の ``source_text`` 先頭行しか label にできない
    ため、骨格が読める文脈（着地予測・近傍注記）ではこちらを使う。骨格が読めない・
    版が食い違うときは空リスト（fail-closed — 古い版のアンカーを現行として出さない）。

    Returns:
        ``(anchors, skeleton_version)``。骨格なしは ``([], "")``。
    """
    from core import atlas_store

    domain = _clean(domain_key)
    if not domain:
        return [], ""
    skeleton = atlas_store.load_frozen_skeleton(session, domain)
    if skeleton is None:
        return [], ""
    version = _clean(getattr(skeleton, "version", ""))
    if not version:
        return [], ""
    wanted = _clean(skeleton_version)
    if wanted and wanted != version:
        return [], ""

    specs = {s["node_id"]: s for s in _skeleton_node_specs(skeleton)}
    anchors = store.load_anchor_vectors(session, domain, version)
    out: list[store.AnchorVector] = []
    for anchor in anchors:
        spec = specs.get(anchor.node_id)
        if spec is None:
            # 骨格から消えたノードは現行として出さない（VA8 閉世界の正直さ）。
            continue
        anchor.label = spec["label"]
        anchor.region_id = spec["region_id"]
        anchor.region_label = spec["region_label"] or spec["label"]
        out.append(anchor)
    return out, version


__all__ = [
    "SKIP_DAILY_LIMIT",
    "SKIP_NO_FROZEN_SKELETON",
    "SKIP_NO_NODES",
    "anchors_with_labels",
    "build_anchor_embeddings",
    "check_daily_gate",
    "collect_evidence_quotes",
    "embed_texts",
    "reset_daily_counter",
]
