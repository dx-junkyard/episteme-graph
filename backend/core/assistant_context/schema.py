"""画面文脈アダプター — 語彙・上限・正規化の正本。

設計: ``docs/features/assistant_screen_adapter_design.md``（SA1〜SA7）。

このモジュールは **純データ + 純関数だけ**を置く（FastAPI / sqlalchemy / LLM を
import しない）。規律は3つ:

- SA1 画面は参照だけを渡す: ``ScreenContext`` は ID・表示モード・短い題名しか
  持たない（描画テキスト・DTO 本体・数値を受け取らない）。
- SA4 数値・内部 ID 非表示: 解決器は事実文に ``confidence`` / ``weight`` /
  ``score``（``core.graph_paper_layer.schema.FORBIDDEN_KEYS``）の値も
  ``eq_op_`` / ``theory_op_`` / ``ev_`` / ``claim_`` のような内部 ID も書かない。
- SA7 プロンプト予算はコード定数: 上限は本モジュールの定数だけで、env で緩めない。

``normalize_screen_context`` は**例外を出さない**（画面の提供が壊れても対話を
止めない = §4.2 の「未知は 422 ではなく無視」）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# 画面 ID（登録済み語彙のみ。未知は無視 = None）
# ---------------------------------------------------------------------------

SCREEN_GRAPH_REVIEW = "graph_review"

KNOWN_SCREENS: tuple[str, ...] = (SCREEN_GRAPH_REVIEW,)

# ---------------------------------------------------------------------------
# 上限（SA1 / SA7）— すべてコード定数。env で緩めない。
# ---------------------------------------------------------------------------

#: ``selection`` / ``view`` / ``visible_entities`` の ID 系文字列の上限（§4.2）。
MAX_ID_CHARS = 160

#: ``visible_entities[].title`` の上限（§4.1「40字以内・本文の抜粋を入れない」）。
MAX_TITLE_CHARS = 40

#: ``visible_entities`` の件数上限。
MAX_VISIBLE_ENTITIES = 20

#: 解決結果ブロック全体の文字上限（SA7）。
MAX_BLOCK_CHARS = 2400

#: 解決結果ブロックの固定ヘッダ（SA7: 利用者の発話・既存 grounding と混ぜない）。
BLOCK_HEADER = (
    "[画面文脈 — 教員がいま画面で選んでいる対象を、サーバが解析結果から解決した事実。"
    "根拠ではなく範囲の手がかり]"
)

#: 予算超過で打ち切ったことを示す最終行（件数は書かない = SA4）。
TRUNCATION_LINE = "…（以下省略）"

# --- 解決器ごとの項目上限（§4.3）------------------------------------------

#: ノードに結ばれた式の上限。
MAX_EQUATION_ITEMS = 5

#: 逐語引用（evidence）の上限。
MAX_EVIDENCE_ITEMS = 3

#: 図・表を合わせた上限。
MAX_FIGURE_ITEMS = 3

#: 記号の上限。
MAX_SYMBOL_ITEMS = 5

#: 導出ステップの上限（チェーンを跨いだ合計）。
MAX_DERIVATION_STEPS = 3

#: 中心命題での役割の上限。§4.3 には明示が無いが、SA7（解決器ごとに項目上限を
#: 持つ）に従い他の部品と同じ扱いで上限を置く。
MAX_THESIS_ROLE_ITEMS = 3

#: 「章 → ノード」行の上限（§4.3 の ≤30行）。
MAX_SECTION_LINES = 30

#: 被覆（掛かっていない章・式・図）の列挙上限（各 ≤5。件数は書かない = SA4）。
MAX_COVERAGE_ITEMS = 5

#: 式の本文スニペット上限（latex は出さず plain_text のみ）。
MAX_PLAIN_TEXT_CHARS = 120

#: 説明本文・要約・caption など自由記述の上限（逐語引用には適用しない）。
MAX_TEXT_CHARS = 200

# ---------------------------------------------------------------------------
# 表示語彙
#
# いずれも「同じキー集合の表が黙って分裂する」ことを避けるため、既存表と
# キー集合が重ならない範囲でのみ定義する（test_label_vocab_guardrails）。
# ---------------------------------------------------------------------------

#: グラフ層 → 日本語（``admin-lecture-studio.js`` の層ラベルと同趣旨）。
GRAPH_LAYER_LABELS: dict[str, str] = {
    "main": "主グラフ",
    "equation_detail": "式の詳細",
    "debug": "デバッグ",
}

#: 表示中の層（画面の ``view.layer``）→ 日本語。
VIEW_LAYER_LABELS: dict[str, str] = {
    "main": "主グラフ",
    "detail": "式の詳細",
    "all": "すべて",
}

#: 式の役割（``graph_paper_layer`` の ``EQUATION_ROLES``）→ 日本語。
EQUATION_ROLE_LABELS: dict[str, str] = {
    "input": "入力",
    "intermediate": "中間",
    "output": "出力",
    "definition": "定義",
    "constraint": "制約",
    "linked": "関連",
}

#: 記号の役割（``graph_paper_layer`` の ``SYMBOL_ROLES``）→ 日本語。
SYMBOL_ROLE_LABELS: dict[str, str] = {
    "eliminated": "消去",
    "retained": "保持",
}

# contextual 説明の状態ラベルは**表にしない**。同じキー集合
# （approved / candidate）の表が `core/deliberation/dialogue.py` に既にあり、
# 表を増やすと「黙った分裂」になるため、個別の文字列定数として置く。
EXPLANATION_STATUS_APPROVED_LABEL = "承認済み"
EXPLANATION_STATUS_CANDIDATE_LABEL = "候補（未承認）"

# ---------------------------------------------------------------------------
# 事実文（固定文）
# ---------------------------------------------------------------------------

#: 章に結び付けられないノード（PL3 / PL8 の事実文をそのまま事実として渡す）。
FACT_NODE_UNLOCATED = (
    "論文上の位置は特定されていません（式・根拠・claim へのリンクがありません）"
)

#: 章にノードが掛かっていないときの行末（§4.3）。
FACT_SECTION_UNBOUND = "このフレームには掛かっていません"

#: 列挙を上限で打ち切ったことを示す語（件数は書かない = SA4）。
MORE_ITEMS_MARK = "ほか"


# ---------------------------------------------------------------------------
# ScreenContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScreenContext:
    """画面が渡した参照だけの文脈（SA1）。

    - ``screen``: 登録済み画面 ID（``KNOWN_SCREENS``）。
    - ``selection``: 選択対象の参照（``document_id`` / ``node_id`` など）。
    - ``view``: 表示モード（``mode`` / ``layer``）。
    - ``visible_entities``: 見えている項目の ``type`` / ``id`` / ``title``。
    """

    screen: str
    selection: dict[str, Any] = field(default_factory=dict)
    view: dict[str, Any] = field(default_factory=dict)
    visible_entities: list[dict[str, Any]] = field(default_factory=list)


def _as_mapping(raw: Any) -> Mapping[str, Any] | None:
    """dict / Pydantic モデル / それ以外 を Mapping か None に落とす。"""
    if isinstance(raw, Mapping):
        return raw
    dump = getattr(raw, "model_dump", None)
    if callable(dump):
        try:
            dumped = dump()
        except Exception:  # pragma: no cover - 防御的（例外を外へ出さない）
            return None
        if isinstance(dumped, Mapping):
            return dumped
    return None


def _clip_str(value: Any, limit: int) -> str | None:
    """文字列だけを受け取り上限で切る（それ以外は None = 落とす）。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


def _normalize_flat(raw: Any) -> dict[str, Any]:
    """``selection`` / ``view`` の正規化（文字列と真偽値だけを通す）。"""
    mapping = _as_mapping(raw)
    if mapping is None:
        return {}
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, bool):
            out[key] = value
            continue
        text = _clip_str(value, MAX_ID_CHARS)
        if text is not None:
            out[key] = text
    return out


def _normalize_entities(raw: Any) -> list[dict[str, Any]]:
    """``visible_entities`` の正規化（≤20件・title ≤40字）。"""
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if len(out) >= MAX_VISIBLE_ENTITIES:
            break
        mapping = _as_mapping(item)
        if mapping is None:
            continue
        entity: dict[str, Any] = {}
        for key, value in mapping.items():
            if not isinstance(key, str):
                continue
            limit = MAX_TITLE_CHARS if key == "title" else MAX_ID_CHARS
            text = _clip_str(value, limit)
            if text is not None:
                entity[key] = text
        if entity:
            out.append(entity)
    return out


def normalize_screen_context(raw: Any) -> ScreenContext | None:
    """画面が渡した生の文脈を ``ScreenContext`` に正規化する（SA1 / §4.2）。

    - 未知の ``screen`` / 型不正 / ``None`` は ``None``（無視して従来動作へ）。
    - ID は 160字・``title`` は40字・``visible_entities`` は20件で切る。
    - **例外を出さない**（画面の提供が壊れても対話を止めない）。
    """
    try:
        mapping = _as_mapping(raw)
        if mapping is None:
            return None
        screen = _clip_str(mapping.get("screen"), MAX_ID_CHARS)
        if screen is None or screen not in KNOWN_SCREENS:
            return None
        return ScreenContext(
            screen=screen,
            selection=_normalize_flat(mapping.get("selection")),
            view=_normalize_flat(mapping.get("view")),
            visible_entities=_normalize_entities(mapping.get("visible_entities")),
        )
    except Exception:  # pragma: no cover - 防御的（SA2 fail-soft）
        return None
