---
id: IK-0452
title: "JSON を取り出す worker・単発経路（確認問題の並置 / structure_anchor の帰属 / tension の抽出ほか）が provider に response_format を渡さず、JSON はプロンプト文面でしか求めていなかった（JSON モードは vision 経路にだけ配線されていた）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: JSON を期待する LLM 呼び出しが、provider 側でも JSON オブジェクトとして返るよう要求される
  layers: [shared_infra, learner_experience_b, rag_chat]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 呼び出し側（BaseJSONLLMClient.complete_json と single_shot.json_call・structured_call のテキスト降格）は
    応答を JSON として読む契約を持つのに、core.llm.generate_text は response_format を受ける引数を持たず、provider との
    境界でその契約を要求していなかった（contract。medium: 片側を変えた型ではなく最初から配線が無い）。JSON モード自体は
    generate_json_with_image（vision）でだけ使われていた。処理軸 none: 取り出し（extract_json / parse_json_response）
    と修復ループは正しく、壊れていない。構造軸 none: 入口は既に一本化されていた。統制軸 none。確認: uxsim の mailbox
    （req-00018 / 00028 / 00048 / 00064 / 00074）で、これらの経路の要求が response_format: null であることを読んだ。
generalization:
  level: general
  general_form: 出力の形を守らせる手段が基盤にあるのに、その形を期待する呼び出しへ配線されず、文面の依頼だけで済ませている
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: 通し受講の砂場で product 要求を代行した brain が、mailbox の要求を読んで response_format が null であることを報告した。
resolution:
  perspective: [carry_through]
  note: >-
    core.llm.generate_text に optional な response_format を足し、openai 経路でだけ API へ渡す（他プロバイダは無視）。
    OpenAI の規則（messages に "JSON" の語が無い json_object は 400）に照らし、語が無ければ要求を落として従来の
    テキスト経路に戻す（json_mode_response_format）。共通入口 BaseJSONLLMClient.complete_json（worker 7 系統）・
    single_shot.json_call（既定 json_mode=True）・structured_call のテキスト降格が JSON_MODE を渡す。狭い署名の
    テスト用 fake には渡さない（accepts_response_format）。取り出し・修復ループ・U層計測・o1/o3 互換規則は不変。
  landed_in:
    - backend/core/llm.py
    - backend/core/llm_worker/single_shot.py
    - backend/core/llm_worker/client.py
    - backend/tests/test_llm_json_mode.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（mailbox の要求に json_object が載ることを修正後の通し受講で見ていない）
      - gemini / google 経路は JSON モードを要求しない（response_mime_type への写像は未実装）
related: []
view_of: []
history: []
---

## 課題

JSON を期待する経路が provider に JSON モードを要求せず、文面の依頼だけで済ませていた。

## 発見の観点

砂場の mailbox に残った実際の要求を読み、response_format が null であることを確かめた。

## 解決の観点

基盤の generate_text に JSON モードの口を足し、JSON を読む共通入口の3箇所から渡す。

## 一般化

出力の形を守らせる手段があるのに、それを期待する呼び出しへ配線していない型。
