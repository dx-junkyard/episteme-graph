"""D層（Doubt Layer）— 認識的地位台帳・暗黙前提・疑義・反実仮想。

A層（構造化）・B層（学習）・C層（承認）に続く第四の層。A層成果物
（theory_claims / theory_component_graphs 等）を **読む側** として実装し、
台帳・前提・疑義・検証提案を新規テーブルに積む。A層コードは変更しない。

不変条項（docs/features/doubt_layer_issues.md §0）:
  - AIに疑わせない: LLM 出力は常に candidate。確定の主体はすべて人間。
  - 帰属必須・匿名疑義なし: 全書き込みを theory_review_events に監査記録。
  - 情報を落とさない (P4): dismiss / withdraw は status 遷移で保持。削除しない。
  - 煽らない・数値を見せない: load_score / confidence の生数値を API/UI に出さない。
  - 同期パスに LLM を入れない (P6): LLM 補助は非同期バッチ worker のみ。
  - 学習者を監視しない (P3): 学習者由来シグナルは k-匿名集約のみ。
"""
