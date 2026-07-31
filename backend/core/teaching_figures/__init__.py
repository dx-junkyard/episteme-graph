"""教材図スタジオ（Teaching Figure Studio）core パッケージ。

話（テキスト）や数式だけではわかりづらい箇所に、教員が AI と対話しながら作った
説明図（SVG）を挿入するための層。設計の正本は
``docs/features/teaching_figure_studio_design.md``（不変条項 FG1〜FG9）。

各モジュール:
    schema     — 図タイプ / 状態 / 段階ラベル / MinIO キー規約の語彙の正本（§5.2・§3）
    sanitizer  — SVG サニタイザ。**生成図の唯一の保存入口**（§4.1・FG3）
    prompt     — 生成・修正・提案のプロンプト（§4.2・§5.1(b)）
    generator  — 対話1ターンの実行（generate_conversation_turn + サニタイズ + 1回修復）
    suggest    — ギャップ検出 + 図タイプ提案（逐語検査つき。DB 書き込みはしない）
    signals    — 既存 k-匿名集約（R層 stumble / D層 naive_signal）の読み出しアダプタ
    store      — ``course_teaching_figures`` / ``teaching_figure_suggestions`` の DB プリミティブ

規約:

- **開発ルール2**: 本パッケージは FastAPI を import しない。権限ゲート
  （``_require_teacher`` / コース編集権限）・CostGate・監査記帳は API 層
  （``api/routes/teaching_figures.py``）の責務。
- **FG1 A層非改変**: ``document_figures`` / 解析パイプラインには触らない
  （``src/episteme_graph/agents/`` は読みもしない）。
- **FG2 確定は人間**: AI 出力（提案・生成 SVG）は常に candidate / draft。学習者に届くのは
  教員が「採用して挿入」し、かつトピックを保存した図のみ（二重ゲート）。
- **store のセッション規約**: 各関数は第1引数に session を受け取り commit / close しない
  （採用時のトピック側登録と同一トランザクションにするため。§7.1b）。
"""

from core.teaching_figures.sanitizer import SanitizedSvg, SvgRejected, sanitize_svg
from core.teaching_figures.schema import (
    FIGURE_KINDS,
    FIGURE_KIND_LABELS,
    FIGURE_STATUSES,
    SIGNAL_BASES,
    SUGGESTION_STATUSES,
    confidence_label,
    figure_kind_label,
    minio_key_for,
    new_figure_id,
)

__all__ = [
    "FIGURE_KINDS",
    "FIGURE_KIND_LABELS",
    "FIGURE_STATUSES",
    "SIGNAL_BASES",
    "SUGGESTION_STATUSES",
    "SanitizedSvg",
    "SvgRejected",
    "confidence_label",
    "figure_kind_label",
    "minio_key_for",
    "new_figure_id",
    "sanitize_svg",
]
