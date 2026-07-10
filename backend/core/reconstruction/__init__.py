"""再構成ループ（Reconstruction Loop, R層）コア。

学習者に理論の再構成（予測 / 言い直し）をさせ、A層が生成した精密な構造
（theory_claims）を答えキー（ground truth）として構造照合し、ズレを事実として
返す閉ループ。tension / structure_anchor / doubt と同型の独立モジュール群。

設計原則（docs/features/reconstruction_loop_design.md）:
  - A層非改変（src/episteme_graph/agents/ を読むだけ）。
  - 実行時の判定（DIFF）は非LLM・同期。LLM を使うのは item オーサリング worker のみ（P6）。
  - 判定は「仮説」、権威は出典リビール、点数は出さない（P1/P7）。
  - item は削除せず auto → flagged → retired の状態遷移（P4）。
  - core/ に FastAPI を import しない（テスタビリティ確保）。
"""
