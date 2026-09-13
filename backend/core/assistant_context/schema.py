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

#: 学習チャット（Phase 4 = §11）。教員側と**別のヘッダ・別の予算**を持つ。
SCREEN_LEARNING = "learning"

KNOWN_SCREENS: tuple[str, ...] = (SCREEN_GRAPH_REVIEW, SCREEN_LEARNING)

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
# 学習チャット（Phase 4 = 設計書 §11）
#
# 教員側（Phase 1）の定数を**再利用しない**: ヘッダは「教員がいま画面で選んでいる
# 対象」と書いてあり学習側では嘘になる（§11.3）。予算も既存プロンプト（トピック本文
# 5000字 + 最大8チャンク）に対しては 2400 は過大なので別に持つ。
# ---------------------------------------------------------------------------

#: 学習側の解決結果ブロックの文字上限（§11.3。SA7: env で緩めない）。
MAX_BLOCK_CHARS_LEARNING = 1200

#: 学習側の固定ヘッダ（§11.3 の文言）。
BLOCK_HEADER_LEARNING = (
    "[画面文脈 — 学習者がいま画面で見ている対象について、サーバが解析結果から解決した事実。"
    "根拠ではなく範囲の手がかり]"
)

# --- 選択箇所ブロック（§11.4）---------------------------------------------
#
# ``selection_text`` は**参照ではなく逐語テキスト**なので、``screen_context``
# （参照だけ = SA1）と同じ袋に入れず第2ブロックとして注入する。一致検査の結果を
# 必ず併記して、クライアント申告を根拠と区別できる状態のまま渡す。

SELECTION_BLOCK_HEADER = (
    "[学習者が選択した箇所 — 学習者が教材上で範囲選択した逐語。"
    "ここについての質問である可能性が高い]"
)

#: 表示中教材の本文に逐語が見つかったとき。
SELECTION_MATCH_CONFIRMED = "（表示中の教材と一致を確認済み）"

#: 見つからなかったとき（**そのまま載せたうえで**正直に書く）。
SELECTION_MATCH_UNCONFIRMED = "（本文との一致は確認できていません）"

#: 選択逐語の文字上限（§11.4）。
MAX_SELECTION_TEXT_CHARS = 600

# --- 出力側の拘束（§11.13-2 のオーナー判断）---------------------------------
#
# SL1 の閉世界語彙は「サーバが書く文字列」への denylist で守られており、出力側には
# 掛かっていない。台帳の事実文を渡す以上、LLM がそれを分野レベルの言明へ言い換える
# 余地が生まれるので、``out_of_source_guard_instruction()`` と同型の固定指示文を
# system 側に1本足す（route が使う）。
#
# **禁止語の例示を書かない**という判断（2026-09-12）:
#   ① 本モジュール一式は SL1 denylist（語彙の正本は
#      ``tests/test_stakes_ledger_guardrails.py`` の ``BANNED``）の走査対象で、
#      例示を書くと定数自身が違反語を持つ。「禁止の文脈なら除外」という carve-out を
#      作ると、denylist 検査は「文脈を読む」テストに劣化し、他所での本物の違反も
#      見逃しやすくなる。
#   ② 禁止したい表層形を指示文に書くこと自体が、その表層形のプライミングになる。
# そのため**肯定形（どこまでなら言えるか）だけ**を書き、越えてはならない外側は
# 「コーパスの外」という一般化した言い方で示す。
LEARNING_VERIFICATION_OUTPUT_CONSTRAINT = (
    "検証記録の不在について言えるのは「このコーパスの中では検証記録がありません」までです。"
    "コーパスの外（分野全体・学界・世界）で確かめられているかどうかは、"
    "この文脈からは分からないので述べないでください。"
    "検証記録が無いことを、価値・新規性・優先度の主張に言い換えないでください。"
)

# --- 学習側の解決器の項目上限（§11.3 の表）---------------------------------

#: 選択要素1件から出す事実文の上限（§11.3「1件・事実6行」）。
MAX_LEARNING_ELEMENT_FACTS = 6

#: 前提 / 入力 / 出力など supports 1行あたりの列挙上限。
MAX_LEARNING_SUPPORT_ITEMS = 3

#: 主張本文の先頭抜粋の上限（決定論的な切り出し。要約しない = 原則7）。
MAX_LEARNING_CLAIM_EXCERPT_CHARS = 80

#: 台帳由来の事実文の上限（§11.3「3件」）。
MAX_LEARNING_VERIFICATION_FACTS = 3

#: 分野内の位置づけの事実文の上限（§11.3「2件」）。
MAX_LEARNING_PLACEMENT_FACTS = 2

#: 学習側で解決対象にする要素型（学習者向け射影が存在する型だけ = fail-closed）。
LEARNING_ELEMENT_TYPES: tuple[str, ...] = ("component", "claim", "equation", "figure")

#: 選択要素の型 → 学習者向けの呼び名。既存の ``_GENERIC_ITEM_LABELS``
#: （「関連する主張」等のレーン相手用）とは用途が違うので流用しない。
LEARNING_ELEMENT_TYPE_LABELS: dict[str, str] = {
    "component": "論理要素",
    "claim": "主張",
    "equation": "式",
    "figure": "図",
}

#: ``selection.kind`` の語彙（§11.2）。
LEARNING_SELECTION_KINDS: tuple[str, ...] = (
    "topic",
    "segment",
    "chunk",
    "element",
    "document",
)

#: 表示モード（既存 ``screen_mode`` をそのまま写す。新語彙を作らない = §11.2）。
LEARNING_VIEW_MODE_LABELS: dict[str, str] = {
    "chat": "通常のチャット画面",
    "lecture": "レクチャー再生画面",
    "voice": "音声会話",
}

#: discuss の検索範囲（既存 ``discuss_scope`` の2語彙）。
LEARNING_DISCUSS_SCOPE_LABELS: dict[str, str] = {
    "course_sources": "このコースのソース論文",
    "all_visible": "閲覧できる資料全体",
}

# ---------------------------------------------------------------------------
# 検索で当たった箇所の構造（知識の転用層 P4-2 = ``knowledge_transfer_design.md`` §5）
#
# 画面の申告ではなく**回答に採用した出典**が入口なので、``screen_context`` が無い
# ターンでも働く。ヘッダは画面文脈ブロックと**別定数**にする（同じヘッダだと
# 「学習者が見ている対象」と「検索が当てた箇所」が混ざり、どちらの由来か復元できない）。
# ---------------------------------------------------------------------------

#: 検索由来の構造ブロックの固定ヘッダ。
BLOCK_HEADER_RETRIEVED = (
    "[検索で当たった箇所の構造 — 回答に採用した出典の箇所について、"
    "サーバが解析結果から解決した事実。根拠ではなく構造の手がかり]"
)

#: 検索由来の構造ブロック全体の事実文の上限（§5「全体上限 8 行」）。
MAX_LEARNING_RETRIEVED_FACTS = 8

#: 1つの出典から出す主張の上限（§5「出典ごと最大 2 主張」）。
MAX_LEARNING_RETRIEVED_CLAIMS_PER_SOURCE = 2

#: 検索由来の主張本文の先頭抜粋の上限（§5「120 字」。決定論的な切り出し・要約しない）。
MAX_LEARNING_RETRIEVED_CLAIM_CHARS = 120


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


def infer_selection_kind(selection: Mapping[str, Any] | None) -> str:
    """``selection`` の種別を決める（§11.2「画面の申告を必須にしない」）。

    ``kind`` が語彙内ならそれを使い、無ければ
    ``element_id`` → ``chunk_id`` → ``segment_id`` → ``topic_id`` の順に推定する。
    どれも無ければ ``"document"``（最も粗い粒度へ縮退する）。純関数・例外を出さない。
    """
    mapping = _as_mapping(selection) or {}
    declared = _clip_str(mapping.get("kind"), MAX_ID_CHARS)
    if declared in LEARNING_SELECTION_KINDS:
        return declared
    for key, kind in (
        ("element_id", "element"),
        ("chunk_id", "chunk"),
        ("segment_id", "segment"),
        ("topic_id", "topic"),
    ):
        if _clip_str(mapping.get(key), MAX_ID_CHARS):
            return kind
    return "document"
