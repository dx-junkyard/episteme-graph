"""ワーキングメモリ・レンズ（WMレンズ, 静かな計器, Phase 4 教員支援 v1 §3.2）。

正本: ``docs/features/teacher_triage_instruments_design.md`` §3.2 / §5 精査④。

原稿スタジオのスライドプレビュー（``POST /api/admin/lecture-studio/preview-split``）の
応答に相乗りし、スライドごとの**要素相互作用性**（相互依存する記号・数式の同時出現）を
段階ラベル化する。非LLM・読み時・決定論（同一入力には常に同一出力）。

- 相互作用性スコア = 突合できた distinct 記号数 + 数式件数（決定論）。
  記号候補は formulas の latex（+ display_text 中のギリシャ文字）から
  ``core/symbol_notation.py::normalize_symbol``（A層 symbol_registry と同一実装の seam）
  で正規化して抽出し、``document_id`` があれば symbol_registry artifact
  （``core/deliberation/refs.py::symbol_records``）の canonical / variants と
  textual 突合する。
- 段階ラベルは ``core/label_vocab.py`` の ``WM_INTERACTION_*``（固定閾値型の正本。
  独自辞書を作らない, TT2）。**最低段（few = 少ない）のときは wm 自体を返さない**
  （「平常時は視界に無い」）。
- ``document_id`` が無い / registry が引けないときは textual 照合のみに縮退し、
  ``degraded: true`` + 縮退の事実文を正直に付ける（§3.2。v2 で拡充）。
- 分割マーカー ``===`` の提案・自動挿入はしない（TT6: 挿すのは常に教員の手）。
- 学習者データ（学習履歴・痕跡）は一切参照しない（TT5: 入力は素材由来のみ）。

FastAPI / LLM は import しない（core の規律）。生値スコアは返却に含めない（TT2）。
"""

from __future__ import annotations

import logging
import re

from core.label_vocab import WM_INTERACTION_LABELS, WM_INTERACTION_LEVEL_SCALE
from core.symbol_notation import normalize_symbol

logger = logging.getLogger(__name__)

# 段階キー（正本は core/label_vocab.py の WM_INTERACTION_LEVEL_SCALE）。
WM_LEVEL_FEW = "few"

# fact 文の固定要素（設計書 §3.2 の逐語）。
WM_FACT_AUDIO_LIMIT = "読み上げ音声は添字・上付きを運べません。"

# textual 縮退の事実文（§3.2: トピック教材由来スライド等、記号照合が textual になる
# ケースを正直に表示する）。
WM_DEGRADED_NOTICE = "記号の登録情報と照合できないため、本文と数式の表記のみからの概算です。"

# fact 文に列挙する記号の上限（それ以上は「など」で正直に打ち切る）。
MAX_SYMBOLS_IN_FACT = 5

# LaTeX コマンドトークン（``\beta`` / ``\Omega_{m}`` 等。添字付きも1トークンで拾う）。
_COMMAND_TOKEN_RE = re.compile(r"\\[A-Za-z]+(?:_(?:\{[^{}]{1,24}\}|[A-Za-z0-9]))?")

# コマンド除去後の残りテキストから拾う記号トークン
# （ラテン1文字 or ギリシャ文字 + 任意の添字）。
_LETTER_TOKEN_RE = re.compile(
    r"(?:[A-Za-z]|[\u0370-\u03FF])(?:_(?:\{[^{}]{1,24}\}|[A-Za-z0-9]|[\u0370-\u03FF]))?"
)

# display_text からはギリシャ文字トークンのみ拾う（ラテン文字を拾うと通常の英単語が
# 全て記号候補になってしまうため。数式本体は formulas 側で拾う）。
_GREEK_TOKEN_RE = re.compile(
    r"[\u0370-\u03FF](?:_(?:\{[^{}]{1,24}\}|[A-Za-z0-9]|[\u0370-\u03FF]))?"
)


def _candidates_from_latex(latex: str) -> list[str]:
    """1つの latex 文字列から正規化済み記号候補を出現順に抽出する（決定論）。"""
    text = str(latex or "")
    if not text.strip():
        return []
    out: list[str] = []
    for match in _COMMAND_TOKEN_RE.finditer(text):
        normalized = normalize_symbol(match.group(0))
        # 正規化してもバックスラッシュが残るものは構造コマンド（\frac 等）— 記号ではない。
        if normalized and "\\" not in normalized:
            out.append(normalized)
    remainder = _COMMAND_TOKEN_RE.sub(" ", text)
    for match in _LETTER_TOKEN_RE.finditer(remainder):
        normalized = normalize_symbol(match.group(0))
        if normalized and "\\" not in normalized:
            out.append(normalized)
    return out


def extract_symbol_candidates(display_text: str, formulas: list[dict]) -> list[str]:
    """スライドの記号候補（正規化済み・出現順・重複なし）。

    formulas の latex を順に走査し、その後 display_text 中のギリシャ文字を拾う。
    """
    seen: set[str] = set()
    ordered: list[str] = []

    def _add(candidate: str) -> None:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)

    for formula in formulas or []:
        if not isinstance(formula, dict):
            continue
        for candidate in _candidates_from_latex(str(formula.get("latex") or "")):
            _add(candidate)
    for match in _GREEK_TOKEN_RE.finditer(str(display_text or "")):
        _add(normalize_symbol(match.group(0)))
    return ordered


def build_symbol_lookup(document_id: str | None) -> set[str] | None:
    """symbol_registry artifact から正規化済み記号集合を組む。

    引けない（document_id なし / artifact なし / 例外）ときは ``None``
    （= textual 照合への縮退。呼び出し側が ``degraded`` を立てる）。fail-soft:
    ここでの失敗はプレビュー応答を止めない（TT4）。
    """
    if not str(document_id or "").strip():
        return None
    try:
        from core.deliberation.refs import symbol_records

        records = symbol_records(str(document_id))
    except Exception:  # noqa: BLE001 — 計器の失敗でプレビューを止めない（TT4）
        logger.warning(
            "lecture_wm: symbol registry lookup failed for document %s; "
            "falling back to textual matching",
            document_id,
            exc_info=True,
        )
        return None
    known: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        raw_values = [record.get("canonical_symbol")]
        variants = record.get("notation_variants")
        if isinstance(variants, list):
            raw_values.extend(variants)
        for raw in raw_values:
            normalized = normalize_symbol(str(raw or ""))
            if normalized:
                known.add(normalized)
    return known or None


def _fact_line(symbols: list[str], formula_count: int, *, degraded: bool) -> str:
    """事実文（設計書 §3.2 の型）。生値フィールドは返さず、件数は日本語文中の事実として書く。"""
    shown = symbols[:MAX_SYMBOLS_IN_FACT]
    symbol_text = "・".join(shown)
    if len(symbols) > MAX_SYMBOLS_IN_FACT:
        symbol_text += " など"
    if shown and formula_count > 0:
        fact = (
            f"このスライドには相互に依存する記号 {symbol_text} と"
            f"数式{formula_count}件が同時に現れます。"
        )
    elif shown:
        fact = f"このスライドには相互に依存する記号 {symbol_text} が同時に現れます。"
    else:
        fact = f"このスライドには数式{formula_count}件が同時に現れます。"
    fact += WM_FACT_AUDIO_LIMIT
    if degraded:
        fact += WM_DEGRADED_NOTICE
    return fact


def wm_for_slide(
    display_text: str,
    formulas: list[dict],
    *,
    known_symbols: set[str] | None = None,
) -> dict | None:
    """1スライド分の WM 注釈。最低段（few）は ``None``（wm キー自体を省略する契約）。

    - ``known_symbols`` あり: registry と突合できた distinct 記号のみ数える。
    - ``known_symbols`` なし: textual 照合のみ（抽出候補をそのまま数え、
      ``degraded: true`` + 縮退の事実文を付ける）。
    """
    degraded = known_symbols is None
    candidates = extract_symbol_candidates(display_text, formulas or [])
    if known_symbols is None:
        matched = candidates
    else:
        matched = [c for c in candidates if c in known_symbols]
    formula_count = sum(1 for f in (formulas or []) if isinstance(f, dict))
    score = len(matched) + formula_count

    level = WM_INTERACTION_LEVEL_SCALE.label_for(score)
    if level == WM_LEVEL_FEW:
        return None

    wm: dict = {
        "level": level,
        "level_label": WM_INTERACTION_LABELS.get(level, level),
        "fact": _fact_line(matched, formula_count, degraded=degraded),
    }
    if degraded:
        wm["degraded"] = True
    return wm


def annotate_slides(slides: list[dict], *, document_id: str | None = None) -> list[dict]:
    """スライド dict 配列（``display_text`` / ``formulas`` キー）に ``wm`` を相乗りさせる。

    最低段のスライドには ``wm`` キーを付けない（「平常時は視界に無い」）。
    入力リスト自体を変更して返す（プレビュー応答の組み立て用）。
    """
    known_symbols = build_symbol_lookup(document_id)
    for slide in slides or []:
        if not isinstance(slide, dict):
            continue
        wm = wm_for_slide(
            str(slide.get("display_text") or ""),
            slide.get("formulas") or [],
            known_symbols=known_symbols,
        )
        if wm:
            slide["wm"] = wm
    return slides
