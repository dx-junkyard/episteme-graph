"""分野の地図 — コーパス概念の骨格配置 (Issue E-2)。

埋め込み(pgvector)ベクトルを受け取り、コーパス概念を骨格領域へ最近傍割当する。
本モジュールは純関数のみで構成し、DB / FastAPI / OpenAI に依存しない
(ベクトルの取得は core/atlas_state.py 側の責務)。

設計上の重要事項:

- **再現性**: 同一コーパス・同一骨格版 → 同一配置。乱数は一切使わず、
  位置は (skeleton_version, region_id, concept_key) の安定ハッシュから導出する
  (シード固定の RNG より強い保証。受け入れ条件3)。
- **binding 優先**: `concept_bindings`(レビュー済みの明示対応)がある概念は
  埋め込み割当をスキップし、対応する骨格概念の領域に置く。
- **誤配置より非表示**: 最近傍距離が閾値 `DISTANCE_THRESHOLD` を超える概念は
  未配置 (unplaced) とし、L1/L2 に出さない。ただし行は落とさない(情報を落とさない)。
- タイブレークはすべて id の辞書順で決定論的に解決する。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

# 未決事項の確定 (issue E): 埋め込み割当の距離閾値。
# コサイン距離 (1 - cosine similarity) がこの値を超える割当は信頼度不足として
# 未配置にする。text-embedding-3-large の同分野内概念は距離 0.3〜0.5 に収まる
# ことが多く、0.5 超は「どの領域とも近くない」とみなす。
DISTANCE_THRESHOLD = 0.5

# 領域内レイアウトの余白 (領域ボックスの端に貼り付かないようにする正規化値)
_LAYOUT_MARGIN = 0.12


# ---------------------------------------------------------------------------
# ベクトル演算 (numpy 非依存 — テスト環境で追加依存を増やさない)
# ---------------------------------------------------------------------------


def cosine_distance(a: list[float], b: list[float]) -> float:
    """コサイン距離 1 - cos(a, b)。ゼロベクトルは最遠 (2.0) を返す。"""
    if not a or not b or len(a) != len(b):
        return 2.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 2.0
    return 1.0 - dot / (na * nb)


def mean_vector(vectors: list[list[float]]) -> list[float] | None:
    """ベクトル平均 (領域プロトタイプ用)。空なら None。"""
    vectors = [v for v in vectors if v]
    if not vectors:
        return None
    dim = len(vectors[0])
    if any(len(v) != dim for v in vectors):
        return None
    return [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]


# ---------------------------------------------------------------------------
# 割当
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlacementResult:
    concept_key: str
    region_id: str | None          # None = 未配置 (L1/L2 に出さない)
    method: str                    # binding | embedding | unplaced
    distance: float | None = None  # embedding 割当時のコサイン距離


def assign_concepts_to_regions(
    concept_vectors: dict[str, list[float]],
    region_prototypes: dict[str, list[float]],
    *,
    bindings: dict[str, str] | None = None,
    threshold: float = DISTANCE_THRESHOLD,
) -> list[PlacementResult]:
    """コーパス概念を骨格領域へ最近傍割当する (決定論的)。

    - concept_vectors: concept_key → 埋め込みベクトル
    - region_prototypes: region_id → 領域プロトタイプベクトル
    - bindings: concept_key → region_id (レビュー済み明示対応。埋め込みをスキップ)
    - 返り値は concept_key の辞書順 (再現性のため)
    """
    bindings = bindings or {}
    results: list[PlacementResult] = []
    for key in sorted(concept_vectors):
        if key in bindings:
            results.append(
                PlacementResult(concept_key=key, region_id=bindings[key], method="binding")
            )
            continue
        vector = concept_vectors[key]
        best_region: str | None = None
        best_distance = 2.0
        # region id の辞書順で走査し、同距離は先勝ち = 辞書順で決定論的
        for region_id in sorted(region_prototypes):
            distance = cosine_distance(vector, region_prototypes[region_id])
            if distance < best_distance - 1e-12:
                best_region = region_id
                best_distance = distance
        if best_region is None or best_distance > threshold:
            results.append(
                PlacementResult(
                    concept_key=key,
                    region_id=None,
                    method="unplaced",
                    distance=best_distance if best_region is not None else None,
                )
            )
        else:
            results.append(
                PlacementResult(
                    concept_key=key,
                    region_id=best_region,
                    method="embedding",
                    distance=best_distance,
                )
            )
    return results


# ---------------------------------------------------------------------------
# 領域内レイアウト (決定論的 — ハッシュ配置)
# ---------------------------------------------------------------------------


def _stable_unit(seed: str, salt: int) -> float:
    """安定ハッシュ → [0, 1)。実行・プロセス・バージョンに依らず同一。"""
    digest = hashlib.sha256(f"{seed}#{salt}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def layout_in_region(
    concept_key: str,
    region_layout: dict[str, float],
    *,
    skeleton_version: str,
    occupied: list[tuple[float, float]] | None = None,
    min_separation: float = 0.08,
    attempts: int = 16,
) -> tuple[float, float]:
    """領域ボックス内の決定論的配置 (正規化座標)。

    骨格の代表概念は骨格座標をそのまま使うため、この関数はコーパス追加概念のみに使う。
    既存配置 (occupied) と近すぎる場合は salt を進めて再ハッシュする
    (力学モデル相当の重なり回避を決定論で行う)。
    """
    rx = float(region_layout.get("x", 0.0))
    ry = float(region_layout.get("y", 0.0))
    rw = float(region_layout.get("w", 1.0))
    rh = float(region_layout.get("h", 1.0))
    seed = f"{skeleton_version}:{concept_key}"
    occupied = occupied or []
    best: tuple[float, float] | None = None
    for salt in range(attempts):
        u = _stable_unit(seed, salt * 2)
        v = _stable_unit(seed, salt * 2 + 1)
        x = rx + rw * (_LAYOUT_MARGIN + u * (1.0 - 2.0 * _LAYOUT_MARGIN))
        y = ry + rh * (_LAYOUT_MARGIN + v * (1.0 - 2.0 * _LAYOUT_MARGIN))
        if all(math.hypot(x - ox, y - oy) >= min_separation for ox, oy in occupied):
            return (x, y)
        if best is None:
            best = (x, y)
    # 回避しきれない密度でも決定論的に最初の候補へ置く (非表示にはしない)
    return best if best is not None else (rx + rw / 2.0, ry + rh / 2.0)


def fog_dots(
    region_id: str,
    region_layout: dict[str, float],
    *,
    skeleton_version: str,
    count: int = 3,
) -> list[tuple[float, float]]:
    """霧領域の匿名ドット座標 (正規化)。概念ラベルは持たせない (§1.2)。

    個数は固定 3 (§16-3 の現実装。モデル知識の概念数には比例させない —
    霧の中身の規模を示唆しない方が「宣言しない」原則に沿う)。
    """
    dots: list[tuple[float, float]] = []
    occupied: list[tuple[float, float]] = []
    for i in range(count):
        x, y = layout_in_region(
            f"fogdot:{region_id}:{i}",
            region_layout,
            skeleton_version=skeleton_version,
            occupied=occupied,
            min_separation=0.03,
        )
        dots.append((x, y))
        occupied.append((x, y))
    return dots


# ---------------------------------------------------------------------------
# pgvector 文字列のパース (embedder が保存した vector 型の読み出し補助)
# ---------------------------------------------------------------------------


def parse_vector(value: object) -> list[float] | None:
    """pgvector の返す '[0.1,0.2,...]' 文字列 / list を list[float] へ。"""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        try:
            return [float(x) for x in value]
        except (TypeError, ValueError):
            return None
    text = str(value).strip()
    if not text.startswith("[") or not text.endswith("]"):
        return None
    body = text[1:-1].strip()
    if not body:
        return None
    try:
        return [float(x) for x in body.split(",")]
    except ValueError:
        return None
