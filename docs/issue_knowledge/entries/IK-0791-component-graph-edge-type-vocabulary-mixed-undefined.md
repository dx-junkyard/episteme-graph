---
id: IK-0791
title: "component_graph の辺推論の入力で valid_edge_types が大文字の関係名 11 と小文字の operation 名 17（diagnoses / feeds / linearizes …）の混在で、後者の定義がプロンプトに無い"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-17
sources:
  - "docs/pipeline/theory-graph.md"
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "部品間の依存辺を、宣言された語彙の中から LLM に推論させて理論操作グラフを組む"
  layers: [pipeline_a, theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認前の仮説（起票時）: 辺推論の valid_edge_types が、規則の関係語彙（REQUIRES 等の大文字 11 語）と、classify_operation が operation から導く
    edge_type 語彙（defines / linearizes / diagnoses / feeds 等の小文字 17 語）を 1 つの一覧に連結して渡しており、プロンプトは前者の
    意味だけを定義している。構造軸 aggregation = 辺の種類の語彙の正本が 2 系統に分かれ、どちらがこの段の語彙かが一本化されていない。
    接続軸 contract = プロンプトが説明する語彙と validator が受理する語彙が一致しない（定義の無い語を LLM が推測で選ぶ）。
    処理軸 none / 統制軸 none。確定には component_graph の input_builder で valid_edge_types を組む箇所と、validator の受理集合を読む。
    確認済み（2026-10-17）: input_builder._valid_edge_types が VALID_EDGE_TYPES（関係 11 + operation 由来の小文字語）を丸ごと渡し、
    プロンプトは関係 11 語だけを定義していた。同根で、Material 2 の DSL 辺は core_predicate（10 語）を「edge_type に写せ」とだけ
    書いて写像表が無く、CONTAINS / CORRELATES / CAUSES の行き先も、DSL の INHIBITS（抑える・補正）と部品辺の INHIBITS（矛盾）の
    意味の違いも LLM の推測に委ねていた（X1 seq 3289）。
generalization:
  level: repo_pattern
  general_form: "2 系統の語彙を 1 つの許容一覧に連結して渡し、片方の定義しか示さないため、定義の無い語を LLM が推測で選ぶ"
pattern: contract-changed-one-side
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 T1 が component_graph の辺推論（seq 2938・26 部品・36 辺）の材料を読み、valid_edge_types の語彙の混在と小文字語の定義の欠落に気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md）。"
resolution:
  perspective: [vocabulary_table, guardrail_fix]
  note: "LLM に渡す語彙を schema.LLM_RELATION_EDGE_TYPES（プロンプトが定義する関係 11 語）に絞り、小文字の operation 由来語は normalizer 専用・validator だけが受理する形にした。DSL の core_predicate → edge_type の写像の正本 schema.CORE_PREDICATE_TO_EDGE_TYPE（REQUIRES / PRODUCES→PRODUCES_FOR / TRANSFORMS / DEFINES / MEASURES→CHECKED_BY（向き反転）/ INHIBITS→QUALIFIES）と edge_type_for_core_predicate を置き、Material 2 の各辺に suggested_edge_type と向きを載せる。対応の無い CONTAINS / CORRELATES / CAUSES / EQUIVALENT（と未知語）は RELATED_TO + mapping_note=no_specific_edge_type + warning で落とさない。プロンプトの定義一覧と語彙表の一致、写像表が両語彙に閉じていることをテストで固定した。"
  landed_in:
    - "docs/pipeline/theory-graph.md"
    - "docs/pipeline/agents.md"
    - "src/episteme_graph/agents/component_graph/schema.py"
    - "src/episteme_graph/agents/component_graph/input_builder.py"
    - "src/episteme_graph/agents/component_graph/prompt.py"
    - "src/tests/agents/component_graph/test_core_predicate_mapping.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実機での辺推論の再実行（2610.08751 の再解析）で INHIBITS が否定として出なくなったことの確認"
      - "LLM が suggested_edge_type を従う割合と、RELATED_TO に寄る辺が増えないかの実測"
related: [IK-0754, IK-0784]
view_of: []
history:
  - date: '2026-10-17'
    field: status
    from: open
    to: resolved
    reason: "X1 seq 3289 の DSL 述語写像の欠落を同根と確認し、語彙の絞り込みと写像表の正本化で解決"
---

## 課題

症状: component_graph の辺推論の入力で、valid_edge_types が大文字の関係名 11 と小文字の operation 名 17（diagnoses / feeds /
linearizes …）の混在で、後者の意味はプロンプトのどこにも書かれていない。LLM は小文字語を推測で使うしかない。
原因（仮説）: 関係語彙と operation 由来の edge_type 語彙を 1 つの許容一覧に連結し、片方しか定義していない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2938）。

追加の出所（頭脳 X1・是正前の run（seq 3289〜3310・2026-10-17 03:00〜03:11））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3289 — DSL の CONTAINS / CORRELATES / CAUSES に対応する edge_type が無く RELATED_TO / QUALIFIES に寄せた。INHIBITS（corrects）をそのまま写すと「Jeffreys 事前分布が Laplace 項を否定」と読め、補正は QUALIFIES が妥当。詳細は fixRejectFlow の起票）。

## 発見の観点

辺推論のプロンプトの語彙定義と、入力の valid_edge_types を突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

許容語彙を連結して片方だけ定義すると、プロンプトと validator の契約がずれる。辞書の型 contract-changed-one-side。
