"""個人地図の暫定ノード（カテゴリギャップ候補 v1-b。設計書 §4.4 裁定 / §5.6）。

正本: ``docs/features/category_gap_candidates_design.md``
（§0 層分離の主題 / §4.4「個人地図の暫定ノード（骨格の外側の居場所）を作るか」の裁定 /
§5.6 学習者側 / §8-2 未決事項）と ``docs/features/personal_knowledge_network_design.md``
（§0 不変条項 PN-1〜7）。

**この層が表すもの**: 本人が扱っている論文（受講コースの sources 由来 document）について、
共有骨格に置けなかった主題を **本人にだけ** 見せる読み時導出の暫定ノード。共有骨格にも
共有候補（``atlas_gap_decisions``）にも一切書き込まない — 論文には即時の居場所を作りつつ、
共有地図の改版経路（匿名・重複排除した論文信号 → 反復閾値 → 教員レビュー → 次版 draft →
凍結）はそのまま一本道に保つ（§4.4 裁定）。

不変条項の写像:

- **PN-2 導出であって記録ではない**: 保存物を作らない。毎回 ``landscape_gap_signals`` の
  ``active`` 行から決定論的に導出する（完了フラグ・キャッシュ・掃除バッチを持たない）。
- **PN-1 本人のみ可視**: 受講ゲートは呼び出し側（``routes/personal_map.py`` の
  ``get_accessible_course_data``）が担う。本モジュールは user_id を受け取らない —
  出す情報はコースの sources 由来 document に閉じている。
- **PN-4 / LS5 数値を見せない**: ``confidence`` / ``weight`` / 件数・順位を **一切載せない**。
  支持論文はタイトルの列挙で示す（「該当論文 N 件」を作らない）。
- **共有候補の内部語彙を漏らさない**: ``cluster_key`` / ``decision`` / ``status`` /
  ``layer`` / ``skeleton_version`` は DTO に出さない。``atlas_gap_decisions`` は
  **読まない**（教員の採用・見送りは学習者の地図に影響しない。個人地図は signal 層のみ）。
- **AB1 一致ゼロは発見**: 置けなかったことは欠陥ではない。文言は事実文のみで、
  督促・警告・行動喚起を作らない（表示側 ``frontend/public/js/personal-map.js``）。
- **事実文の正確性（§8-2 裁定 = 解消済みラベルの除外）**: 帯は「地図の外の主題」なので、
  現行凍結骨格に同名（正規化一致）の領域・概念が既にある主題は暫定ノードにしない。
  除外は **読み時導出のまま**（保存物・完了フラグ・掃除バッチを作らない = G1 / PN-2）で、
  当該 document の再解析を待たずに帯から消える。
- **PN-7 fail-closed**: 骨格の無いコース（地図領域ごと非表示）・取得失敗・テーブル未作成は
  すべて空リストへ縮退する（個人ネットワーク全体を壊さない）。

構成は ``derive.py`` / ``bridges.py`` と同じ「純粋部 + DB 部」の分離:

- 純粋部 :func:`build_provisional_nodes` は fake rows（dict のリスト）だけで検証できる。
- DB 部 :func:`derive_provisional_nodes` は ``core.postgres`` を遅延 import して開閉する。
  ``queries.py``（本人スコープ読みの正本）に対して本モジュールは **コーススコープの
  読み**であるため、``bridges.py`` と同じ理由で自前の読みを持つ。

FastAPI / routes / services / ``core.llm`` は import しない（PN-5。ガードレールは
``backend/tests/test_personal_graph_guardrails.py``）。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping

# ラベル正規化の正本は core/atlas_gaps/schema.py（同一主題の束ね方を再定義しない）。
# 同モジュールは unicodedata しか import しない純粋モジュールなので、DB 依存を
# 持ち込まずに純粋部から使える。
from core.atlas_gaps.schema import (
    SIGNAL_STATUS_ACTIVE,
    normalize_label,
)

logger = logging.getLogger(__name__)

#: 暫定ノード id の接頭辞（共有骨格の node_id と取り違えないための名前空間）。
PROVISIONAL_NODE_ID_PREFIX = "provisional:"

#: 出所ラベル（学習者向け landscape の既存語彙をそのまま使う。サーバ側定数）。
PROVISIONAL_SOURCE_LABEL = "AIによる推定（未確認）"

#: 1コースあたりの上限。超過分は載せない（切り捨てた事実も件数も学習者に出さない）。
MAX_PROVISIONAL_NODES = 12

#: 決定論 id のハッシュ長（衝突より可読性・安定性を優先した固定長）。
_ID_HASH_LENGTH = 16


# ---------------------------------------------------------------------------
# 純粋部（fake rows で検証できる。DB 依存なし）
# ---------------------------------------------------------------------------


def _text(value: object) -> str:
    return str(value or "").strip()


def _signal_order(signal: dict) -> tuple[str, str]:
    """信号の決定論順キー ``(created_at, id)``（``derive.py::_sort_key`` と同型）。"""
    return (_text(signal.get("created_at")), _text(signal.get("id")))


def _attr(obj: object, key: str, default: object = None) -> object:
    """dataclass（``AtlasSkeleton``）と dict の両方から同じキーを読む防御アクセス。"""
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _skeleton_regions(frozen_skeleton: object) -> list:
    """``AtlasSkeleton`` / dict / ``{"atlas_skeleton": {...}}`` から regions を取り出す。"""
    if frozen_skeleton is None:
        return []
    if isinstance(frozen_skeleton, Mapping):
        inner = frozen_skeleton.get("atlas_skeleton")
        if isinstance(inner, Mapping):
            return list(inner.get("regions") or [])
        return list(frozen_skeleton.get("regions") or [])
    return list(_attr(frozen_skeleton, "regions", ()) or ())


def skeleton_labels(frozen_skeleton: object) -> set[str]:
    """現行凍結骨格の **正規化済みラベル集合**（領域 + 概念。§8-2 裁定の除外集合）。

    ``core/atlas_gaps/store.py`` の ``_skeleton_index`` は非公開（``__all__`` 未収録）で、
    かつ id の変種・領域ごとの概念数まで抱き合わせて返す教員レビュー用の索引なので、
    ここでは **ラベルだけ** の公開ヘルパーを持つ（暫定ノードのラベルは
    ``proposed_label`` 由来で、骨格へ取り込まれた node の label も同じ由来なので
    ラベル突合で足りる — id 変種の逆写像は不要）。正規化は
    :func:`core.atlas_gaps.schema.normalize_label` の1本に揃える。
    """
    labels: set[str] = set()
    for region in _skeleton_regions(frozen_skeleton):
        region_label = normalize_label(_attr(region, "label") or "")
        if region_label:
            labels.add(region_label)
        for concept in _attr(region, "concepts", ()) or ():
            concept_label = normalize_label(_attr(concept, "label") or "")
            if concept_label:
                labels.add(concept_label)
    return labels


def provisional_node_id(domain_key: str, normalized_label: str) -> str:
    """暫定ノードの決定論 id。

    ``cluster_key`` を **そのまま出さない**（共有候補機構の内部語彙を学習者に
    見せない）ため、ドメインと正規化ラベルから決定論的なハッシュを作る。同じ主題は
    再読み込み・再解析をまたいで同じ id になる（フロントの選択状態が飛ばない）。
    """
    digest = hashlib.sha256(
        "\n".join((_text(domain_key), _text(normalized_label))).encode("utf-8")
    ).hexdigest()
    return PROVISIONAL_NODE_ID_PREFIX + digest[:_ID_HASH_LENGTH]


def build_provisional_nodes(
    signals: list[dict],
    *,
    document_ids: set[str] | list[str] | None,
    titles: dict[str, str] | None = None,
    resolved_labels: set[str] | None = None,
    limit: int = MAX_PROVISIONAL_NODES,
) -> list[dict]:
    """ギャップ信号 → 個人地図の暫定ノード（純粋関数・非LLM・決定論）。

    手順:

    1. ``document_ids``（コースの sources 由来 document）に属する ``active`` 信号だけを残す
       （空集合は空リスト = fail-closed。「全件」に転ばせない）
    2. :func:`core.atlas_gaps.schema.normalize_label` で同一主題を1ノードに統合する
    3. ``resolved_labels``（現行凍結骨格の正規化ラベル集合 = :func:`skeleton_labels`）に
       一致する主題は **暫定ノードにしない**（§8-2 裁定。既に地図に入った主題を
       「地図の外の主題」として出し続けない。除外は読み時のみで保存物を作らない）
    4. 代表は各グループの **最新**信号（表記と逐語引用は最新のものを使う）
    5. 並びは ``(その主題が最初に現れた created_at, 正規化ラベル)`` の昇順で、
       先頭から ``limit`` 件（``derive.py`` の ``(created_at, id)`` 昇順と同じ流儀）

    返す dict のキーは ``id`` / ``label`` / ``documents`` / ``evidence_quote`` /
    ``source_label`` の5つだけ。**``confidence`` / ``weight`` / ``cluster_key`` /
    ``decision`` / ``layer`` / ``status`` / 件数フィールドを足さない**（PN-4 / LS5 と
    「共有候補の存在を学習者に見せない」§5.6）。
    """
    allowed = {str(d) for d in (document_ids or []) if d}
    if not allowed or int(limit or 0) <= 0:
        return []

    title_map = {str(k): _text(v) for k, v in (titles or {}).items()}
    resolved = {normalize_label(label) for label in (resolved_labels or set())}
    resolved.discard("")

    groups: dict[str, list[dict]] = {}
    for signal in signals or []:
        status = _text(signal.get("status")) or SIGNAL_STATUS_ACTIVE
        if status != SIGNAL_STATUS_ACTIVE:
            # 履歴（superseded）は個人地図に出さない（行は DB に残る = P4）。
            continue
        if _text(signal.get("document_id")) not in allowed:
            continue
        key = normalize_label(signal.get("proposed_label") or "")
        if not key:
            continue
        if key in resolved:
            # 現行凍結骨格に同名の領域・概念がある = もう「地図の外」ではない（§8-2 裁定）。
            continue
        groups.setdefault(key, []).append(signal)

    ordered: list[tuple[tuple[str, str], dict]] = []
    for key, members in groups.items():
        members = sorted(members, key=_signal_order)
        first_seen = _signal_order(members[0])[0]
        representative = members[-1]

        documents: list[dict] = []
        seen_documents: set[str] = set()
        seen_titles: set[str] = set()
        for signal in members:
            document_id = _text(signal.get("document_id"))
            if document_id in seen_documents:
                continue
            seen_documents.add(document_id)
            title = title_map.get(document_id) or _text(signal.get("document_title"))
            if not title or title in seen_titles:
                # 題名が引けない論文は行を作らない（id を代わりに出さない —
                # 学習者に内部 ID を見せても事実が増えない）。
                continue
            seen_titles.add(title)
            documents.append({"title": title})

        ordered.append(
            (
                (first_seen, key),
                {
                    "id": provisional_node_id(representative.get("domain_key"), key),
                    "label": _text(representative.get("proposed_label")),
                    "documents": documents,
                    "evidence_quote": _text(representative.get("evidence_quote")),
                    "source_label": PROVISIONAL_SOURCE_LABEL,
                },
            )
        )

    ordered.sort(key=lambda item: item[0])
    return [node for _, node in ordered[: int(limit)]]


# ---------------------------------------------------------------------------
# DB 部（core.postgres 直読み・try/finally close。開発ルール4）
# ---------------------------------------------------------------------------


def _course_data(session, course_id: str) -> dict:
    """``learning_courses.data`` を dict で返す（無ければ ``{}``）。"""
    from sqlalchemy import text as sa_text  # 遅延 import（純粋部を軽く保つ）

    row = session.execute(
        sa_text("SELECT data FROM learning_courses WHERE id = :course_id"),
        {"course_id": course_id},
    ).fetchone()
    if not row:
        return {}
    raw = row[0]
    if isinstance(raw, dict):
        return raw
    if raw:
        import json

        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001 — 壊れた JSONB で個人地図を落とさない
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def resolve_course_map(session, course_data: dict) -> tuple[str, set[str]]:
    """このコースの「分野の地図」ドメインと、現行凍結骨格の正規化ラベル集合。

    戻り値は ``(domain_key, resolved_labels)``。地図が無ければ ``("", set())``。

    判定は ``GET /api/atlas``（``routes/atlas_view.py``）と**同じ fail-closed 規則**に
    そろえる — 地図が出ないコースで暫定ノードだけが出る、という食い違いを作らない:

    1. ``course_data.cartridge_id`` の明示指定があればそれを使う
    2. 無ければ ``atlas_state.resolve_course_cartridge`` の導出（解析 run 由来）
    3. 凍結骨格が無ければ空（地図なし = 学習者には地図領域ごと出ない）
    4. **導出**で決まった場合は ``course_has_skeleton_anchor`` の妥当性ゲートを通す
       （既定カートリッジへの縮退で、無関係な分野の暫定ノードが出るのを防ぐ）

    ``resolved_labels`` は同じ骨格から :func:`skeleton_labels` で作る除外集合
    （§8-2 裁定）。骨格を2回読まないよう、ドメイン判定と同一の読みから導出する。
    """
    from core import atlas_state, atlas_store
    from core.course_data import course_cartridge_id

    explicit = _text(course_cartridge_id(course_data))
    domain_key = explicit or _text(atlas_state.resolve_course_cartridge(session, course_data))
    if not domain_key:
        return ("", set())
    skeleton = atlas_store.load_learner_skeleton(domain_key, session)
    if skeleton is None:
        return ("", set())
    if not explicit and not atlas_state.course_has_skeleton_anchor(
        session, skeleton, domain_key, course_data
    ):
        return ("", set())
    return (domain_key, skeleton_labels(skeleton))


def resolve_course_map_domain(session, course_data: dict) -> str:
    """:func:`resolve_course_map` のドメインだけを返す薄いラッパー（可読性のため）。"""
    return resolve_course_map(session, course_data)[0]


def derive_provisional_nodes(course_id: str) -> list[dict]:
    """コースビュー用のエントリポイント（読み取り専用・非LLM・DB 非変更）。

    受講ゲートは呼び出し側（``routes/personal_map.py``）が済ませている前提。
    骨格の無いコース・信号ゼロ・取得失敗はすべて ``[]``（PN-7 fail-closed。
    個人ネットワーク本体の応答は壊さない）。
    """
    if not _text(course_id):
        return []
    session = None
    try:
        from core.atlas_gaps import store as gap_store
        from core.personal_graph import queries
        from core.postgres import get_session

        session = get_session()
        course_data = _course_data(session, course_id)
        if not course_data:
            return []
        domain_key, resolved_labels = resolve_course_map(session, course_data)
        if not domain_key:
            return []

        document_ids = queries.fetch_course_document_ids(course_id)
        if not document_ids:
            return []

        signals = gap_store.list_active_signals(session, domain_key=domain_key)
        if not signals:
            return []

        relevant = sorted(
            {
                _text(s.get("document_id"))
                for s in signals
                if _text(s.get("document_id")) in document_ids
            }
        )
        if not relevant:
            return []
        titles = queries.fetch_document_titles(relevant)
        return build_provisional_nodes(
            signals,
            document_ids=document_ids,
            titles=titles,
            resolved_labels=resolved_labels,
        )
    except Exception:  # noqa: BLE001 — 暫定ノードのために個人地図を落とさない
        logger.debug(
            "personal map: provisional nodes unavailable for course=%s", course_id,
            exc_info=True,
        )
        return []
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                logger.debug("personal map: failed to close session", exc_info=True)


__all__ = [
    "MAX_PROVISIONAL_NODES",
    "PROVISIONAL_NODE_ID_PREFIX",
    "PROVISIONAL_SOURCE_LABEL",
    "build_provisional_nodes",
    "derive_provisional_nodes",
    "provisional_node_id",
    "resolve_course_map",
    "resolve_course_map_domain",
    "skeleton_labels",
]
