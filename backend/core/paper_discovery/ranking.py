"""候補の関連度ランキング（Phase 3 — 設計書 §6 / 第3層「並べ替え」）。

分野の**取り込み済みコーパスの重心**と候補アブストラクトの埋め込みとの cosine で
候補を並べ替える。第1層（arXiv カテゴリ）と第2層（キーフレーズ）の絞り込みは
変えない — ここがやるのは**並べ替えだけ**で、候補を捨てない（PD6）。

論文レーダー（``docs/features/paper_radar_design.md`` §5.2）の**距離帯**も同じ機構の
別投影としてここに置く（:func:`document_centroid` + :func:`band_candidates`）。分野重心の
代わりに seed 教材の重心を使い、順位の代わりに帯ラベルを付けるだけで、embedding の
接触点・日次予算・fail-soft の規律は共有する。

不変条項:

- **PD4 数値スコアを見せない**: cosine の生値は関数の外へ出さない。DTO に載るのは
  並び順と段階ラベル（``relevance_label`` / ``distance_label``）だけで、そのラベルも
  ``core.label_vocab``（正本）からのみ引く
  （このモジュールに閾値・ラベル文字列を直書きしない）。
- **PR2 測れないものにラベルを付けない**: 距離帯は未測定（cosine が ``None``）の候補に
  ``distance_label`` を付けない（``GradedScale`` の慎重側フォールバックを通さない —
  「測れなかった」を「遠い」に化けさせないため。関連度ランキング側は従来どおり
  慎重側へ倒す既存挙動のまま）。
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

from core.label_vocab import DISCOVERY_RELEVANCE_SCALE, RADAR_DISTANCE_SCALE
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

#: 論文レーダー（§5.2）の帯分けで、seed の基準ベクトルが作れなかったときの事実文。
NOTE_NO_SEED = "この教材から距離の基準を作れませんでした。新着順で表示します。"


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


def _document_means(
    session,
    document_ids: Sequence[str],
    per_document: int,
) -> dict[str, list[float]]:
    """document ごとの「先頭 N チャンクの平均ベクトル」を決定論的に作る。

    :func:`field_centroid`（分野の2段平均）と :func:`document_centroid`（1 document）が
    共有する下請け（同じ SQL 形をコピペで2実装にしない）。embedding が引けない
    document はキーごと現れない。
    """
    ids = [str(d).strip() for d in (document_ids or ()) if str(d or "").strip()]
    if not ids:
        return {}

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
        {"document_ids": sorted(ids), "per_document": per_document},
    ).fetchall()

    by_document: dict[str, list[list[float]]] = {}
    for row in rows:
        document_id = str(row[0] or "").strip()
        vector = _parse_vector(row[1])
        if not document_id or vector is None:
            continue
        by_document.setdefault(document_id, []).append(vector)

    means: dict[str, list[float]] = {}
    for document_id in sorted(by_document):
        mean = _mean(by_document[document_id])
        if mean is not None:
            means[document_id] = mean
    return means


def _per_document_limit(chunks_per_document: Any) -> int:
    try:
        return max(1, int(chunks_per_document))
    except (TypeError, ValueError):
        return DEFAULT_CHUNKS_PER_DOCUMENT


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
    means = _document_means(session, document_ids, _per_document_limit(chunks_per_document))
    return _mean([means[key] for key in sorted(means)])


def document_centroid(
    session,
    document_id: str,
    *,
    chunks_per_document: int = DEFAULT_CHUNKS_PER_DOCUMENT,
) -> Optional[list[float]]:
    """1 document のチャンク埋め込みから重心を作る（論文レーダーの seed ベクトル）。

    :func:`field_centroid` の2段平均を1 document に縮めたもの（先頭
    ``chunks_per_document`` チャンクの平均）。チャンク上限も分野重心と共有する
    ので、seed と候補の測り方が画面によって変わらない。

    Returns:
        重心ベクトル。チャンクなし・embedding なしは ``None``（**正常な状態**。
        呼び出し側は要旨からの疑似 seed へ、それも無ければ新着順へ縮退する — PR2）。
    """
    key = str(document_id or "").strip()
    if not key:
        return None
    means = _document_means(session, [key], _per_document_limit(chunks_per_document))
    return means.get(key)


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


def _attach_landing(payload: dict, vector: Optional[Sequence[float]], anchor_context: Any) -> None:
    """候補に「取り込むと地図のどこに落ちそうか」の事実を足す（VA層 §8）。

    ``anchor_context`` は ``{"anchors": [AnchorVector, ...], "skeleton_version": str}``
    に、optional で ``exclude_node_ids``（起点論文が既に配置されているノードの集合）を
    足したもの。**追加の embedding 呼び出しはゼロ**（並べ替え・帯分けで既に作った
    候補ベクトルを流用する）。骨格版は呼び出し側が知っている値をそのまま刻む
    （VA8 閉世界の正直さ）。

    付くキーは2つで、それぞれ独立に足りなければ**キー自体を付けない**（VA4）:

    - ``landing``: 最も近いアンカー（下位帯・アンカー不在・未測定では付かない）。
    - ``new_facets``: ``exclude_node_ids`` が渡されたときだけ導出する「候補は近いのに
      起点論文が配置されていないアンカー」のラベル（最上位帯のみ）。
    """
    if not anchor_context or not vector:
        return
    anchors = list((anchor_context or {}).get("anchors") or [])
    version = str((anchor_context or {}).get("skeleton_version") or "").strip()
    if not anchors or not version:
        return
    try:
        from core.atlas_vectors.query import (  # 遅延 import
            landing_for_vector,
            new_facet_labels,
        )

        landing = landing_for_vector(vector, anchors)
        facets: list[str] = []
        if "exclude_node_ids" in (anchor_context or {}):
            facets = new_facet_labels(
                vector, anchors, (anchor_context or {}).get("exclude_node_ids") or ()
            )
    except Exception:  # noqa: BLE001 — 着地予測が出ないだけ（検索は成立させる）
        logger.warning("landing prediction failed (non-fatal)", exc_info=True)
        return
    if landing:
        payload["landing"] = {
            "node_label": landing.get("node_label") or "",
            "region_label": landing.get("region_label") or "",
            "nearness_label": landing.get("nearness_label") or "",
            "skeleton_version": version,
        }
    if facets:
        payload["new_facets"] = list(facets)


def rank_candidates(
    session,
    domain_key: str,
    candidates: Sequence[dict],
    *,
    daily_limit: Optional[int] = None,
    anchor_context: Optional[dict] = None,
) -> dict:
    """候補を分野の重心との関連度で並べ替える（PD4 — 生スコアは返さない）。

    Args:
        anchor_context: 着地予測（VA層 §8）の材料
            ``{"anchors": [AnchorVector, ...], "skeleton_version": str}``。
            省略時は従来どおり ``landing`` キーを付けない（完全後方互換）。
            アンカーとの照合は DB・LLM に触れない純計算
            （``core.atlas_vectors.query.landing_for_vector``）で、**候補の埋め込みは
            並べ替えの1バッチを流用する**（発見層の embedding 予算は増えない）。

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
        parsed = _parse_vector(vector)
        similarity = cosine_similarity(centroid, parsed or [])
        payload = dict(item)
        # 段階ラベルは正本のスケールからのみ引く（未測定は最も慎重な段階へ倒れる）。
        payload["relevance_label"] = DISCOVERY_RELEVANCE_SCALE.label_for(similarity)
        # 着地予測（VA層 §8）。同じベクトルの使い回しなので追加コールは無い。
        _attach_landing(payload, parsed, anchor_context)
        scored.append((index, similarity, payload))

    # 未測定（None）は最後尾へ。同点は入力順（= 新着順）を保つ安定ソート。
    scored.sort(key=lambda row: (-(row[1] if row[1] is not None else -math.inf), row[0]))
    return {"available": True, "ordered": [row[2] for row in scored]}


# ---------------------------------------------------------------------------
# 距離帯（論文レーダー §5.2 — 並べ替えと同じ機構の別投影）
# ---------------------------------------------------------------------------


def band_candidates(
    session,
    candidates: Sequence[dict],
    *,
    seed_vector: Optional[Sequence[float]] = None,
    seed_text: str = "",
    daily_limit: Optional[int] = None,
    anchor_context: Optional[dict] = None,
) -> dict:
    """候補を seed 教材からの距離帯に分ける（PR2 — 生スコアは返さない）。

    :func:`rank_candidates` と同型（候補テキストを**1バッチ**で埋め込み → cosine →
    段階ラベル）で、相違点は3つ:

    1. 基準が分野重心ではなく **seed 教材**（``seed_vector`` = 呼び出し側が
       :func:`document_centroid` で解決した重心）。作れない場合は ``seed_text``
       （seed 論文の要旨）を候補と**同じバッチの先頭**に入れて埋め込み、疑似 seed
       ベクトルにする（追加コールを増やさない）。
    2. 出力が順位ではなく ``distance_label``（正本は
       ``core.label_vocab.RADAR_DISTANCE_SCALE``）。
    3. **未測定（cosine が ``None``）の候補にはラベルを付けない** — ``distance_label``
       キー自体を省略する（PR2。「測れなかった」を「遠い」に化けさせない）。

    日次ゲート・U層 feature（``discovery:ranking``）は :func:`rank_candidates` と
    共有する（発見層の embedding 予算は1本 — 用途別に env を増やさない）。
    fail-soft の規律も同じで、基準が作れない・埋め込みに失敗した・上限に達したは
    いずれも ``available: False`` + 事実文で**元の並び順のまま**返す。

    Args:
        session: :func:`rank_candidates` と呼び出し形を揃えるために受け取る
            （本関数自体は DB を読まない — seed ベクトルの解決は呼び出し側の責務）。
        candidates: 注釈済み候補（``title`` / ``summary`` を持つ dict）。
        seed_vector: seed 教材の重心。``None`` なら ``seed_text`` を使う。
        seed_text: seed 論文の要旨（重心が作れないときのフォールバック素材）。
        daily_limit: 日次上限（省略時は ``DISCOVERY_RANKING_MAX_CALLS_PER_DAY``）。
        anchor_context: 着地予測・「新しい面」（VA層 §8）の材料
            ``{"anchors": [AnchorVector, ...], "skeleton_version": str,
            "exclude_node_ids"?: set[str]}``。省略時は従来どおり ``landing`` /
            ``new_facets`` キーを付けない（完全後方互換）。:func:`rank_candidates` と
            同じく**候補の埋め込みは帯分けの1バッチを流用する**（追加コールはゼロ）。

    Returns:
        ``{"available": bool, "ordered": [candidate, ...], "note"?: str}``。
        ``available=True`` のとき ``ordered`` は類似度降順（未測定は最後尾・同点は
        入力順の安定ソート）で、測れた候補にだけ ``distance_label`` が付く。
    """
    items = list(candidates or [])
    if not items:
        return _unavailable(NOTE_NO_CANDIDATES, items)

    if daily_limit is None:
        from core.config import get_settings  # 遅延 import（core の純粋性を保つ）

        daily_limit = get_settings().discovery_ranking_max_calls_per_day

    texts = [candidate_text(item) for item in items]
    if not any(texts):
        return _unavailable(NOTE_UNAVAILABLE, items)

    seed = _parse_vector(list(seed_vector)) if seed_vector else None
    fallback_text = " ".join(str(seed_text or "").split())[:MAX_CANDIDATE_TEXT_CHARS]
    if seed is None and not fallback_text:
        # 基準が無い状態で帯を付けると、根拠のない「遠い」を作ってしまう（PR2）。
        return _unavailable(NOTE_NO_SEED, items)

    batch = texts if seed is not None else [fallback_text] + texts
    if not _gate.check_and_count(
        daily_limit=int(daily_limit),
        daily_key=(_DAILY_KEY_PREFIX, today_str()),
        prune_stale_daily=True,
    ):
        return _unavailable(NOTE_LIMIT_REACHED, items)

    try:
        vectors = _embed(batch)
    except Exception:  # noqa: BLE001 — embedding の失敗で検索を落とさない
        logger.warning("radar band embedding failed", exc_info=True)
        return _unavailable(NOTE_UNAVAILABLE, items)

    if len(vectors) != len(batch):
        logger.warning(
            "radar band embedding count mismatch (%s vs %s)", len(vectors), len(batch)
        )
        return _unavailable(NOTE_UNAVAILABLE, items)

    if seed is None:
        seed = _parse_vector(vectors[0])
        vectors = list(vectors[1:])
        if seed is None:
            return _unavailable(NOTE_NO_SEED, items)

    scored: list[tuple[int, Optional[float], dict]] = []
    for index, (item, vector) in enumerate(zip(items, vectors)):
        parsed = _parse_vector(vector)
        similarity = cosine_similarity(seed, parsed or [])
        payload = dict(item)
        if similarity is not None:
            # 測れたものにだけラベルを付ける（未測定はキーごと省略 — PR2）。
            payload["distance_label"] = RADAR_DISTANCE_SCALE.label_for(similarity)
            # 着地予測・新しい面（VA層 §8）も measured な候補にだけ足す。
            # 同じベクトルの使い回しなので追加コールは無い。
            _attach_landing(payload, parsed, anchor_context)
        scored.append((index, similarity, payload))

    # 未測定（None）は最後尾へ。同点は入力順（= 新着順）を保つ安定ソート。
    scored.sort(key=lambda row: (-(row[1] if row[1] is not None else -math.inf), row[0]))
    return {"available": True, "ordered": [row[2] for row in scored]}


def reset_daily_counter() -> None:
    """日次カウンタを初期化する（テスト用。本番コードから呼ばない）。"""
    _gate.daily_counts.clear()
    _gate.session_counts.clear()
