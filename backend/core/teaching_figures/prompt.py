"""教材図スタジオのプロンプト定義（生成・修正・提案）。

出力物（title / caption / gap_reason / figure_brief）は教員と学習者が読む日本語なので、
指示文も日本語で書く（``core/tension/prompt.py`` 等の英語 instruction とは方針が違うが、
これは「出力言語＝指示言語」を揃える意図的な選択）。

不変条項の言語化がこのモジュールの主な仕事:

- **FG5 データを捏造しない**: ``data_plot_schematic``（模式グラフ）は定性的な概形のみ。
  実測値・具体的な数値目盛りの捏造を明示的に禁じる（:data:`DATA_PLOT_CONSTRAINT`）。
  図に描いてよいのは grounding（教材本文・数式・claim）に現れる関係のみ
  （:data:`GROUNDING_CONSTRAINT`）。
- **FG3 SVG-first・サニタイズ必須**: サニタイザの許可リストをプロンプト側にも書き、
  拒否ループを減らす（サニタイザが唯一の入口である事実は変わらない）。
- **FG8 学習者データを漏らさない**: 提案プロンプトへ渡せる学習者由来の情報は
  k-匿名通過後の**レンジ・段階ラベルの事実文だけ**（``signals.py`` が組み立てる）。
  このモジュールはその事実文を「参考情報」として添えるだけで、個人・生数値は扱わない。

LLM SDK も FastAPI も import しない純粋な文字列モジュール。
"""

from __future__ import annotations

from core.teaching_figures import schema

# ---------------------------------------------------------------------------
# 共通制約
# ---------------------------------------------------------------------------

# FG5（データを捏造しない）。**この文はガードレールテストが grep する** ——
# 文言を変えるときは backend/tests/ 側の期待も同時に直すこと。
DATA_PLOT_CONSTRAINT = (
    "模式グラフ（data_plot_schematic）を描く場合は、軸ラベル・概形・注目点だけの"
    "定性的な模式図に限定してください。実測値・具体的な数値目盛りを捏造してはならない。"
    "目盛りの数値、データ点、誤差棒、凡例の実測条件は描かないでください。"
)

# FG5（根拠のない関係を描かない）。
GROUNDING_CONSTRAINT = (
    "図に描いてよいのは、以下の参考資料（教材本文・数式・主張）に現れる関係のみです。"
    "資料に無い因果・依存・順序・数値を新しく作らないでください。"
    "描く根拠が資料に見つからない要素は、図に入れずに返答（reply）で教員に確認してください。"
)

# サニタイザ（core/teaching_figures/sanitizer.py）の許可リストの言語化。
SVG_CONSTRAINT = (
    "SVG は次の制約を必ず満たしてください（満たさないものは保存されません）:\n"
    "- ルートは <svg> で、viewBox 属性（例 viewBox=\"0 0 800 500\"）を必ず付ける\n"
    "- 使える要素は svg, g, path, rect, circle, ellipse, line, polyline, polygon, "
    "text, tspan, defs, marker, title, desc, linearGradient, radialGradient, stop, "
    "clipPath, use のみ\n"
    "- <script> / <style> / <image> / <foreignObject> / アニメーション要素は使わない\n"
    "- onclick 等のイベント属性、外部 URL の参照（href は #id の内部参照のみ）、"
    "style 属性内の url(...) / @import は使わない\n"
    "- コメント（<!-- -->）・DOCTYPE 宣言・処理命令（<? ?>）は入れない\n"
    "- 文字は <text> で描き、日本語ラベルはそのまま入れてよい"
    "（font-family は sans-serif 等の一般名を使う）\n"
    "- 図は単体で読めるようにし、色だけに意味を持たせない（線種・ラベルでも区別する）"
)

_FIGURE_KIND_VOCABULARY = "\n".join(
    f"- {kind}（{schema.figure_kind_label(kind)}）: {schema.FIGURE_KIND_GAP_HINTS.get(kind, '')}"
    for kind in schema.FIGURE_KINDS
)


# ---------------------------------------------------------------------------
# 対話生成・修正（§4.2 / §6.3）
# ---------------------------------------------------------------------------

_TURN_HEADER = (
    "あなたは大学院生向け教材の「教材図スタジオ」で、教員と対話しながら説明図（SVG）を"
    "作る補助です。教員の指示に従って図を作成・修正し、毎回**完全な SVG 全文**を返します"
    "（差分やパッチ形式では返さない）。\n\n"
    "返答（reply）は、何をどう変えたかを1〜3文の日本語で簡潔に書いてください。"
    "根拠が資料に見つからない部分があれば、そのことを reply に正直に書いてください。\n\n"
    "図を変更する必要がないターン（教員の質問に言葉で答えるだけのとき）は、"
    "svg_source を空文字列にしてください（前の版がそのまま維持されます）。\n\n"
    "title は教員向けの短い見出し、caption は学習者に見せる1文の説明にしてください。"
    "figure_kind は次の語彙から1つ選んでください:\n"
    f"{_FIGURE_KIND_VOCABULARY}"
)


def build_turn_instruction() -> str:
    """対話1ターンの指示文（grounding の直前に置くヘッダ）。"""
    return "\n\n".join(
        [_TURN_HEADER, GROUNDING_CONSTRAINT, DATA_PLOT_CONSTRAINT, SVG_CONSTRAINT]
    )


def build_turn_content(
    *,
    grounding: str,
    current_svg: str,
    user_instruction: str,
) -> str:
    """最初の user メッセージに注入する本文（指示 + 参考資料 + 現在の図 + 教員の指示）。"""
    sections: list[str] = [build_turn_instruction()]
    if grounding.strip():
        sections.append("[参考資料]\n" + grounding.strip())
    else:
        sections.append(
            "[参考資料]\n（参考資料が供給されていません。"
            "資料に基づかない関係を描かず、必要なら教員に確認してください。）"
        )
    if current_svg.strip():
        sections.append(
            "[現在の図（この SVG を土台に修正する）]\n" + current_svg.strip()
        )
    sections.append("[教員の指示]\n" + user_instruction.strip())
    return "\n\n".join(sections)


def build_repair_instruction(reason: str) -> str:
    """サニタイズで拒否された SVG を作り直させるための追加指示（同一コール内で1回だけ）。"""
    return (
        "直前の SVG は保存時の検査を通りませんでした。理由は次のとおりです:\n"
        f"{reason}\n\n"
        "同じ図の意図を保ったまま、この理由を解消した完全な SVG を作り直してください"
        "（制約は最初の指示のとおり）。"
    )


# ---------------------------------------------------------------------------
# ギャップ検出・図タイプ提案（§5.1(b)）
# ---------------------------------------------------------------------------

_SUGGEST_HEADER = (
    "あなたは大学院生向け教材のレビュー補助です。以下のトピック本文を読み、"
    "**文章や数式だけでは理解しづらく、説明図があると理解が進む箇所**を挙げてください。\n\n"
    "各項目には次を含めます:\n"
    "- anchor_excerpt: その箇所を特定するための**本文からの逐語引用**"
    "（本文に現れる文字列をそのままコピーする。要約・翻訳・言い換えは禁止。"
    "20〜120文字程度）\n"
    "- gap_reason: なぜそこが分かりづらいかの事実文（1〜2文。学習者を評価する表現は使わない）\n"
    "- figure_kind: 下の語彙から1つ\n"
    "- figure_brief: 何をどう描くかの一文（図の中身の指示になる具体性で書く）\n"
    "- confidence: 0.0〜1.0\n"
    "- uses_learner_signal: 参考情報（学習者のつまずきの集計）を根拠に含めたら true\n\n"
    f"figure_kind の語彙:\n{_FIGURE_KIND_VOCABULARY}"
)

_SUGGEST_RULES = (
    "守ること:\n"
    "- 提案は最大 {max_items} 件。無理に数を揃えず、根拠のあるものだけを挙げる"
    "（0件も正しい答えです）。\n"
    "- anchor_excerpt は本文の逐語部分文字列でなければなりません"
    "（一致しない項目は破棄されます）。\n"
    "- 本文に書かれていない内容を「分かりづらい箇所」として作らないでください。\n"
    "- 参考情報として渡される学習者のつまずきは、人数のレンジや段階ラベルだけの集計です。"
    "個々の学習者について推測・言及しないでください。\n"
    "- 出力は指定された JSON スキーマのみ。"
)


def build_suggestion_content(
    *,
    topic_title: str,
    source_text: str,
    equations: list[str] | None = None,
    claims: list[str] | None = None,
    signal_lines: list[str] | None = None,
    max_items: int = 4,
) -> str:
    """ギャップ提案1コール分の user メッセージ本文を組み立てる（純粋関数）。"""
    sections: list[str] = [
        _SUGGEST_HEADER,
        _SUGGEST_RULES.format(max_items=max(1, int(max_items))),
    ]
    if topic_title.strip():
        sections.append(f"[トピック] {topic_title.strip()}")
    sections.append("[トピック本文]\n" + (source_text.strip() or "（本文が空です）"))
    if equations:
        sections.append("[関係する数式]\n" + "\n".join(f"- {e}" for e in equations if e))
    if claims:
        sections.append("[関係する主張]\n" + "\n".join(f"- {c}" for c in claims if c))
    if signal_lines:
        sections.append(
            "[参考情報: 学習者のつまずき（k-匿名の集計。人数はレンジ表示）]\n"
            + "\n".join(f"- {line}" for line in signal_lines if line)
        )
    return "\n\n".join(sections)


def build_suggestion_repair(previous_raw: str, errors: list[str]) -> str:
    """提案出力の検証に失敗したときの修復指示（1回だけ再試行する）。"""
    error_text = "\n".join(f"- {e}" for e in errors if e) or "- 検証に失敗しました"
    return (
        "直前の出力は検証を通りませんでした。問題点は次のとおりです:\n"
        f"{error_text}\n\n"
        "特に anchor_excerpt は本文に現れる文字列をそのままコピーしてください"
        "（言い換え・要約・省略記号の追加は不可）。"
        "問題を解消した項目だけを含めて、同じ JSON スキーマで出し直してください。\n\n"
        f"[直前の出力]\n{previous_raw[:2000]}"
    )


__all__ = [
    "DATA_PLOT_CONSTRAINT",
    "GROUNDING_CONSTRAINT",
    "SVG_CONSTRAINT",
    "build_repair_instruction",
    "build_suggestion_content",
    "build_suggestion_repair",
    "build_turn_content",
    "build_turn_instruction",
]
