---
id: IK-0402
title: "違和感の候補抽出（tension）の修復プロンプトで「Your previous output」が空のまま渡され、Context blocks の components / chunks も常に空で target_refs が必ず空になっていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者の対話から違和感の候補を抽出し、どの部品・どの本文への引っかかりかを添えて本人に見せる（TensionMiningAgent）
  layers: [learner_experience_b, shared_infra]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information が 2 か所。①run_with_repair は JSON として読めなかった試行で previous_raw を空文字に戻していたため、
    修復プロンプトが「違反だけを直せ」と言いながら直す対象を渡していなかった（parse_json_response も生の応答を例外に載せていなかった）。
    ②worker は ConversationWindow の components / chunks に「現状は未収集」として常に空を渡しており、validator の allowed id 集合も
    空なので target_refs は空以外が書けなかった（ヒント痕跡の cited_chunk_ids という材料は同じ行にあった）。処理軸は medium で
    none: 判定ロジックの誤りではなく、値が次段へ運ばれていなかった。確認: 第 8 周の修復プロンプトの実物で両方が空だった。
generalization:
  level: repo_pattern
  general_form: 次段の入力を組み立てる材料が手元にあるのに、組み立て側が空の既定値を渡し続け、次段は材料なしで判断させられる
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: 修復ループに渡った実プロンプトを読み、previous output と Context blocks が空であることを見た。
resolution:
  perspective: [carry_through]
  note: >-
    ①parse_json_response は LLMOutputNotJSONError（ValueError の部分型）に生の応答を載せ、run_with_repair はそれを（6000 字まで）
    previous output として渡す。応答自体が空・取れないときは、それより前に読めた出力とその検証エラーを残す。読めた出力が一度も
    無いときは、tension の修復プロンプトは空の区画を出さずに「最初から JSON を出し直す」よう指示する。②worker はヒント痕跡の
    cited_chunk_ids を chunks として、その chunk を primary_chunk_id に持つ live component を components として集め、
    component が1件も無ければコースの出典論文の親 component に縮退する（DB 障害は空のまま・解析は止めない）。見出しは内部参照
    プレースホルダーを「（数式）」等に置き換えてから切り詰める。brief にあった「主張（claims）への縮退」は入れていない
    — ConversationWindow と validator の target_refs は components / chunks / topic / edges しか持たない。
  landed_in:
    - backend/core/llm_worker/repair.py
    - backend/core/llm_worker/client.py
    - backend/core/tension/worker.py
    - backend/core/tension/input_builder.py
    - backend/core/tension/prompt.py
    - backend/tests/test_wave6_tension_worker_inputs.py
  verification:
    methods: [guardrail]
    unverified:
      - 実 Postgres での context blocks の SQL（theory_components_live.primary_chunk_id / parent_agent_component_id の照合）
      - 砂場での再演（target_refs が実在 id で埋まるか・修復成功率が変わるか）
related: [IK-0401, IK-0403]
view_of: []
history: []
---

## 課題

修復プロンプトに直す対象が無く、帰属先の候補も無かった。

## 発見の観点

修復ループに渡った実プロンプトの中身を読んだ。

## 解決の観点

手元にある材料（生の応答・引用チャンク・その部品）を次段まで運ぶ。

## 一般化

「現状は未収集」の既定値は、材料が揃った時点で配線する。
