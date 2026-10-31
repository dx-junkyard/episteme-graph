---
id: IK-0502
title: "再構成の問いの入力補完が、主張の行に保存された式の参照（equation.equation_ids）を読まず、式の種類から関係型を付ける経路も無かったため、予測（predict）の「関係型の式」の条件が構造的に一度も満たされなかった"
status: resolved
recorded_at: 2026-09-29
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17.10
  - docs/features/reconstruction_loop_design.md §4.2
feature_context:
  realizing: "学習者に量どうしの関係を予測させ、選択肢で構造照合する"
  layers: [reconstruction_r, knowledge_objects]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information, contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: 永続化は主張の equation 列に式の参照だけ（equation_ids / equation_stable_keys）を書き、
    式の本文は knowledge_equations に置く。入力補完はその列を「本文が無い＝式なし」と読み、式側の
    linked_claim_ids しか見ていなかった（主張が指す式が届かない）。contract: 予測の判定は
    equation.relation_type を要求するが、上流のどこもこのキーを書かず（equation_semantics が持つのは
    equation_type）、補完も常に空を入れていた — 後段の入力契約に対応する前段の出力が無い。
    構造軸 medium: 式の信頼度（PDF からそのまま抽出できたか）は agent_payload の confidence_policy にしか無い。
    処理・統制の軸には原因が無い。
generalization:
  level: general
  general_form: "後段の判定が要求するキーを前段のどこも書かず、しかも前段が別の形で保存した参照を後段が読まないため、判定の片方の枝が一度も成立しない"
pattern: available-but-unwired
discovery:
  perspective: [trace_walk, inventory]
  note: "predict の判定から relation_type の出所を全文走査し、書き手が再構成層の外に無いこと、主張の equation 列の実際の形（永続化の _build_claim_items）を突き合わせた。"
resolution:
  perspective: [carry_through, fail_closed]
  note: >-
    補完は主張自身の式の参照（無ければ親の参照）→ linked_claim_ids（source_scope.legacy_ids も含む）の順で
    knowledge_equations を解決する。関係型は、PDF からそのまま抽出できた式（confidence_policy が
    can_support_claim かつ must_not_treat_as_source_extracted でない）で、種類が関係型
    （relation / result / approximation / constraint）、記号が 2 つ以上のときだけ equation_type から付ける。
    それ以外は付けず、付けなかった理由を relation_withheld に残す（復元した式を答えキーにしない）。
  landed_in:
    - backend/core/reconstruction/claim_context.py
    - backend/core/reconstruction/worker.py
    - backend/tests/test_ik0502_recon_predict_inputs.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（砂場の承認済み主張の式が信頼の条件を満たすか・predict が実際に選ばれるかは見ていない）"
      - "confidence_policy を持たない古い式の行は信頼しない側に倒すので、再解析までは predict にならない"
related: [IK-0483, IK-0503]
view_of: []
history: []
---

## 課題

再構成の問いの入力補完が、主張の行に保存された式の参照を読まず、式の種類から関係型を付ける経路も無かったため、予測の「関係型の式」の条件が構造的に一度も満たされなかった。

## 発見の観点

predict の判定から relation_type の出所を全文走査し、主張の equation 列の実際の形と突き合わせた。

## 解決の観点

主張が指す式を knowledge_equations で解決し、PDF からそのまま抽出できた関係型の式にだけ関係型を付ける。付けなかった理由は残す。

## 一般化

後段の判定が要求するキーを前段のどこも書かず、前段が別の形で保存した参照を後段が読まないため、判定の片方の枝が一度も成立しない。
