---
id: IK-0725
title: "thesis_reconstruction のプロンプトの Output Schema に support_structure の各要素の形（text / claim_ids / equation_ids / support_type / reason / confidence）が書かれておらず、validator が要求する項目を LLM が推測するしかない"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "論文の中心命題と支持構造を LLM に構造化出力させ、validator で検証して後段（dsl_linking の thesis_nodes）へ渡す"
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    support_structure の要素の形は schema.py の SupportEntry と validator（support_type・claim_ids/equation_ids・confidence を読む）に
    あるのに、プロンプトの _OUTPUT_SCHEMA は各節を空配列 [] で示すだけで、形の正本と提示が別々に書かれていた。構造軸 responsibility =
    出力形の正本（dataclass）とプロンプトの例の担当が分かれ、例が正本から導かれていない。接続軸 contract = LLM に渡す出力契約が
    validator の要求と食い違う。処理軸 none（validator・repair の判定自体は正しい）。統制軸 none。
generalization:
  level: general
  general_form: "構造化出力のプロンプト例が検証側の必須項目と別に書かれ、入れ子要素の形を例に示さないため、生成側が形を推測して検証に落ちる"
pattern: duplicate-canonical-sources
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 L1 が thesis_reconstruction（seq 1966）に応答する際、Output Schema の support_structure が direct_supports: [] のみで項目の形が書かれていないことに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_L1.md。要求の実体は uxsim/runs/mailbox/req-01966.json / req-02035.json）。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: "プロンプトに _SUPPORT_ENTRY_EXAMPLE（SupportEntry と同じ 6 項目）を置き、support_structure の全 7 節をその例 1 件で示すようにした（節は SUPPORT_SECTIONS から生成）。制約にも 6 項目必須の 1 行を足した。契約テストで「例のキー = SupportEntry のフィールド」「全節が例を持つ」「例を埋めた出力が validator の error を出さない」「描画後のプロンプトに 6 項目が載る」を固定。LLM 呼び出し回数・validator は不変。"
  landed_in:
    - "src/episteme_graph/agents/thesis_reconstruction/prompt.py"
    - "src/tests/agents/thesis_reconstruction/test_prompt_contract.py"
    - "docs/pipeline/agents.md"
  verification:
    methods: [guardrail]
    unverified:
      - "実際の LLM（頭脳）の応答で support entry の項目欠落による repair が減ったか（自律周回での再観察）"
related: [IK-0711, IK-0722]
view_of: []
history: []
---

## 課題

症状: thesis_reconstruction の要求のプロンプトで、Output Schema の `support_structure` が `"direct_supports": []` のように空配列だけで、
各要素が持つべき text / claim_ids / equation_ids / support_type / reason / confidence が書かれていない。validator は direct_supports の
support_type（許容値）・claim_ids か equation_ids・confidence を検査し、dsl_linking は text を読むので、形を推測した応答は修復に回るか
後段で空になる。
原因: 要素の形の正本（`schema.py` の `SupportEntry`）とプロンプトの例が別々に書かれ、例が正本から導かれていなかった。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_L1.md（seq 1966）。要求の実体 uxsim/runs/mailbox/req-01966.json / req-02035.json。

## 発見の観点

頭脳が実際の要求 JSON のプロンプトを読み、validator の要求と突き合わせた（data_inspection）。

## 解決の観点

プロンプトの例を正本（SupportEntry）と一致させ、一致を契約テストで固定した（canonical_source + guardrail_fix）。

## 一般化

構造化出力の例を検証側と別に書くと、入れ子要素の形が抜けても誰も気付かない。辞書の型 duplicate-canonical-sources。
