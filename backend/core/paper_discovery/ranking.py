"""候補の関連度ランキング（Phase 3 — 設計書 §6 / 第3層「並べ替え」）。

分野の**取り込み済みコーパスの重心**と候補アブストラクトの埋め込みとの cosine で
候補を並べ替える。第1層（arXiv カテゴリ）と第2層（キーフレーズ）の絞り込みは
変えない — ここがやるのは**並べ替えだけ**で、候補を捨てない（PD6）。

不変条項:

- **PD4 数値スコアを見せない**: cosine の生値は関数の外へ出さない。DTO に載るのは
  並び順と段階ラベル ``relevance_label`` だけで、そのラベルも
  ``core.label_vocab.DISCOVERY_RELEVANCE_SCALE``（正本）からのみ引く
  （このモジュールに閾値・ラベル文字列を直書きしない）。
- **fail-soft**: 重心が作れない / 埋め込みに失敗した / 日次上限に達した — いずれも
  例外にせず ``available: False`` + 事実文で返し、**元の並び順（新着順）のまま**の
  候補を返す。検索そのものは必ず成立させる。
- **LLM 呼び出しは1検索あたり1バッチ**（``core.llm.generate_embeddings`` を1回）。
  U層計測は ``usage_context("discovery:ranking")`` で帰属させる（U3）。
- 本モジュールは ``core.paper_discovery`` パッケージの中で**唯一** ``core.llm`` に
  触れる（他ファイルは LLM 0回のまま。ガードレールが構造として固定する）。
  依存の重さを入口に持ち込まないため、import は関数の中で行う。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import text as sa_text

from core.label_vocab import DISCOVERY_RELEVANCE_SCALE
from core.llm_worker.cost_gate import CostGate, today_str
from core.paper_discovery import corpus

logger = logging.getLogger(__name__)

#: 1 document あたり重心計算に使うチャンク数（先頭から。要旨・導入が入る範囲）。
DEFAULT_CHUNKS_PER_DOCUMENT = 20

#: 重心計算に使う document の上限（新しい順。読み込み量の防波堤）。
MAX_CENTROID_DOCUMENTS = 50

#: 候補テキストの1件あたり最大文字数（アブストラクトは長くても数千字）。
MAX_CANDIDATE_TEXT_CHARS = 4000

#: 日次上限のカウンタ（プロセス内。API サーバは単一プロセス運用 — cost_gate の前提）。
_gate = CostGate()

#: 日次カウンタのキー接頭辞。
_DAILY_KEY_PREFIX = "discovery_ranking"

# 事実文（数値を含めない — PD4 / U5 の流儀）。
NOTE_NO_CANDIDATES = "並べ替える候補がありません。"
NOTE_NO_CORPUS = (
    "この分野には、関連度の基準にできる取り込み済み論文がまだありません。新着順で表示します。"
)
NOTE_LIMIT_REACHED = "本日の関連度計算の上限に達しました。新着順で表示します。"
NOTE_UNAVAILABLE = "関連度を計算できませんでした。新着順で表示します。"


# ---------------------------------------------------------------------------
# ベクトル（pgvector の読み出しは text 表現で返ることがある）
# ---------------------------------------------------------------------------


def _parse_vector(raw: Any) -> Optional[list[float]]:
    """``chunks.embedding`` の値を ``list[float]`` へ。解釈できなければ ``None``。

    pgvector の値はドライバ設定により ``"[0.1,0.2]"`` の文字列で返ることがあるため、
    リスト / タプル / 文字列のいずれも受ける（黙って 0 ベクトルに化けさせない）。
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


def _mean(vectors: Sequence[Sequence[float]]) -> Optional[list[float]]:
    """同一次元のベクトル群の要素平均（次元不一致・空は ``None``）。"""
    usable = [v for v in vectors if v]
    if not usable:
        return None
    dim = len(usable[0])
    usable = [v for v in usable if len(v) == dim]
    if not usable:
        return None
    total = [0.0] * dim
    for vector in usable:
        for index, value in enumerate(vector):
            total[index] += value
    count = float(len(usable))
    return [value / count for value in total]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> Optional[float]:
    """cosine 類似度（次元不一致・ゼロベクトルは ``None`` = 未測定）。

    戻り値は**この層の内部でだけ**使う（DTO へ出さない — PD4）。
    """
    if not left or not right or len(left) != len(right):
        return None
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm <= 0.0 or right_norm <= 0.0:
        return None
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


# ---------------------------------------------------------------------------
# 分野の重心
# ---------------------------------------------------------------------------


def field_centroid(
    session,
    domain_key: str,
    *,
    chunks_per_document: int = DEFAULT_CHUNKS_PER_DOCUMENT,
    max_documents: int = MAX_CENTROID_DOCUMENTS,
) -> Optional[list[float]]:
    """分野の取り込み済み document 群のチャンク埋め込みから重心を決定論的に作る。

    document ごとに先頭 ``chunks_per_document`` チャンクを平均して document ベクトルを
    作り、その document ベクトル群をさらに平均する（チャンク数の多い論文が重心を
    独占しないように2段で平均する）。

    Returns:
        重心ベクトル。対象 document ゼロ・embedding ゼロなら ``None``
        （**正常な状態**であってエラーではない — 呼び出し側は新着順へ縮退する）。
    """
    document_ids = corpus.domain_document_ids(session, domain_key, limit=max_documents)
    if not document_ids:
        return None
    try:
        per_document = max(1, int(chunks_per_document))
    except (TypeError, ValueError):
        per_document = DEFAULT_CHUNKS_PER_DOCUMENT

    rows = session.execute(
        sa_text(
            """
            SELECT document_id::text, embedding
              FROM chunks
             WHERE document_id = ANY(CAST(:document_ids AS uuid[]))
               AND embedding IS NOT NULL
               AND chunk_index < :per_document
             ORDER BY document_id, chunk_index
            """
        ),
        {"document_ids": sorted(document_ids), "per_document": per_document},
    ).fetchall()

    by_document: dict[str, list[list[float]]] = {}
    for row in rows:
        document_id = str(row[0] or "").strip()
        vector = _parse_vector(row[1])
        if not document_id or vector is None:
            continue
        by_document.setdefault(document_id, []).append(vector)

    document_vectors: list[list[float]] = []
    for document_id in sorted(by_document):
        mean = _mean(by_document[document_id])
        if mean is not None:
            document_vectors.append(mean)
    return _mean(document_vectors)


# ---------------------------------------------------------------------------
# 候補の並べ替え
# ---------------------------------------------------------------------------


def candidate_text(candidate: dict) -> str:
    """埋め込みに渡す候補テキスト（タイトル + 要旨）。"""
    title = " ".join(str(candidate.get("title") or "").split())
    summary = " ".join(str(candidate.get("summary") or "").split())
    text = (title + "\n" + summary).strip()
    return text[:MAX_CANDIDATE_TEXT_CHARS]


def _unavailable(note: str, candidates: Sequence[dict]) -> dict:
    """並べ替えなしの結果（元の順序のまま・ラベルを付けない）。"""
    return {"available": False, "note": note, "ordered": list(candidates)}


def _embed(texts: list[str]) -> list[list[float]]:
    """候補テキストをまとめて埋め込む（1検索 = 1バッチコール・U層計測つき）。"""
    # 依存の重さを入口に持ち込まないため、ここで遅延 import する
    # （``core.paper_discovery`` の他ファイルは ``core.llm`` に触れない）。
    from core.llm import generate_embeddings
    from core.llm_usage.context import usage_context

    with usage_context("discovery:ranking"):
        return generate_embeddings(texts)


def rank_candidates(
    session,
    domain_key: str,
    candidates: Sequence[dict],
    *,
    daily_limit: Optional[int] = None,
) -> dict:
    """候補を分野の重心との関連度で並べ替える（PD4 — 生スコアは返さない）。

    Returns:
        ``{"available": bool, "ordered": [candidate, ...], "note"?: str}``。
        ``available=True`` のとき ``ordered`` の各要素は入力候補の複製に
        ``relevance_label``（段階ラベル）を足したもの。``available=False`` のときは
        入力の順序（新着順）そのままで、ラベルは付けない（測れていないものを
        測れたように見せない）。
    """
    items = list(candidates or [])
    if not items:
        return _unavailable(NOTE_NO_CANDIDATES, items)

    if daily_limit is None:
        from core.config import get_settings  # 遅延 import（core の純粋性を保つ）

        daily_limit = get_settings().discovery_ranking_max_calls_per_day

    centroid = None
    try:
        centroid = field_centroid(session, domain_key)
    except Exception:  # noqa: BLE001 — 重心が作れなくても検索は成立させる
        logger.warning("field centroid failed for domain %s", domain_key, exc_info=True)
        return _unavailable(NOTE_UNAVAILABLE, items)
    if centroid is None:
        return _unavailable(NOTE_NO_CORPUS, items)

    texts = [candidate_text(item) for item in items]
    if not any(texts):
        return _unavailable(NOTE_UNAVAILABLE, items)

    if not _gate.check_and_count(
        daily_limit=int(daily_limit),
        daily_key=(_DAILY_KEY_PREFIX, today_str()),
        prune_stale_daily=True,
    ):
        return _unavailable(NOTE_LIMIT_REACHED, items)

    try:
        vectors = _embed(texts)
    except Exception:  # noqa: BLE001 — embedding の失敗で検索を落とさない
        logger.warning("candidate embedding failed for domain %s", domain_key, exc_info=True)
        return _unavailable(NOTE_UNAVAILABLE, items)

    if len(vectors) != len(items):
        logger.warning(
            "embedding count mismatch for domain %s (%s vs %s)",
            domain_key, len(vectors), len(items),
        )
        return _unavailable(NOTE_UNAVAILABLE, items)

    scored: list[tuple[int, Optional[float], dict]] = []
    for index, (item, vector) in enumerate(zip(items, vectors)):
        similarity = cosine_similarity(centroid, _parse_vector(vector) or [])
        payload = dict(item)
        # 段階ラベルは正本のスケールからのみ引く（未測定は最も慎重な段階へ倒れる）。
        payload["relevance_label"] = DISCOVERY_RELEVANCE_SCALE.label_for(similarity)
        scored.append((index, similarity, payload))

    # 未測定（None）は最後尾へ。同点は入力順（= 新着順）を保つ安定ソート。
    scored.sort(key=lambda row: (-(row[1] if row[1] is not None else -math.inf), row[0]))
    return {"available": True, "ordered": [row[2] for row in scored]}


def reset_daily_counter() -> None:
    """日次カウンタを初期化する（テスト用。本番コードから呼ばない）。"""
    _gate.daily_counts.clear()
    _gate.session_counts.clear()
