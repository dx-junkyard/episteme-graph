"""discuss モード（「論文と話す」）の非LLM・読み取り専用投影。

正本設計書: ``docs/features/discussion_mode_design.md``。Phase 0/1（可視性フィルタ・
intent_mode='discuss' の分岐）は ``backend/api/services.py`` / ``backend/api/routes/learning.py``
に実装済み。本パッケージは Phase 2（§3.3 開幕画面 / §6.3）のバックエンドを持つ。

- A層（``src/episteme_graph/agents/``）・D層（``core/doubt/``）・W層（``core/deliberation/``）は
  読むだけで改変しない。
- LLM 呼び出しを一切行わない（DM8: 開幕画面は同期パス・非LLM）。
- FastAPI を import しない（開発ルール2 / core/ 共通ルール）。
"""
