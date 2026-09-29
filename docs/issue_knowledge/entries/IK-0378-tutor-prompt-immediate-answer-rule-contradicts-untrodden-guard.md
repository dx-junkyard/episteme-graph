---
id: IK-0378
title: "学習チャットは未踏ガード（「教材では確認できない」と先に述べて予想を求める）と ⚠️ 注意書きを、最弱集約の根拠の格（overall_tier == out_of_source）で付けていたため、類似度 0.30〜0.45 の出典が1件混じるだけで 8〜14 出典の回答にも付き、チューターの即答規則・discuss の規則と衝突した。注意書きは回答本文として履歴に焼き込まれ、次の往復でモデルに戻っていた"
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習チャットが根拠の有無に応じて応答の姿勢と出所の注意書きを決める
  layers: [rag_chat]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: ガードと注意書きの付与条件が overall_tier == out_of_source だった（HEAD の routes/learning.py
    の RAG 本経路）。overall_tier は採用出典の tier の最弱集約で、出典の採用は score >= 0.30、tier が source
    になるのは score >= 0.45（core/learning_experience.py の _SOURCE_SCORE_THRESHOLD）なので、弱い出典が
    1 件混じるだけで out_of_source になる（logic）。接続軸: tier の最弱集約（格の表示）を「採用した根拠が
    無い」の意味で読んでいた（meaning。medium: 閾値の二重化とも読める）。ガードと即答規則の間に優先順位の
    宣言も無かった。注意書き（表示の飾り）は回答本文に前置したまま保存され、クライアントが送り返す履歴から
    次の往復のプロンプトに戻っていた。構造軸は none（出所 content_grounding の 3 値で「根拠ゼロ」は表せて
    いた。medium）。統制軸は none。確認: 第 8 周（埋め込みあり）で出典 8〜14 件の回にもガードが付いた
    （起票時の仮説 = 砂場の検索が空だった可能性 は外れた）。
generalization:
  level: repo_pattern
  general_form: 集約した格（最弱値）を「根拠が無い」の代わりに使い、根拠があるときにも根拠が無いときの振る舞いをさせる
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection, reproduction]
  note: 肩代わり頭脳が製品のプロンプト本文を読み（第 3 周）、第 8 周で出典件数とガードの有無を突き合わせて確認した。
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    ガードと注意書きは content_grounding == model_generated（採用した根拠が 1 つも無い。IK-0382 の
    _topic_material_engages_message の後）のときだけ付ける。ガードを付けるときは優先順位の一文
    （_OUT_OF_SOURCE_GUARD_PRECEDENCE: 出典・表示中の教材で裏づけられない主張にだけ適用し、裏づけられる内容は
    即答の規則どおりに答える）を並べる。overall_tier の値（UI の格表示）は変えない。注意書きは応答の answer に
    だけ前置し、履歴には保存しない。クライアントが送り返す履歴に付いていても _history_without_out_of_source_notice
    が剥がしてから LLM に渡す。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_learning_chat_wave6_turns.py
    - docs/backend/rag-chat.md
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（出典付きの回答からガードと注意書きが消え、出典ゼロの回答には付くか）
      - 旧データの履歴に焼き込まれた注意書き（保存済みの行は書き換えない。再注入時に剥がすだけ）
      - 出典ゼロで表示中の教材が問いに関わるとき（course_material になりガードは付かない — IK-0382 の判定に依存）
related: [IK-0382, IK-0394]
view_of: []
history:
  - date: 2026-09-28
    field: status
    from: open
    to: resolved
    reason: 第 8 周で原因を確認し是正（付与条件を model_generated に限定・優先順位の一文・注意書きを履歴に保存しない）
  - date: 2026-09-28
    field: cause_status
    from: hypothesis
    to: confirmed
    reason: 出典 8〜14 件の回にもガードが付くことを第 8 周で確認し、付与条件が最弱集約の tier だったことをコードで確認
  - date: 2026-09-28
    field: pattern
    from: contract-changed-one-side
    to: extent-decided-by-proxy-signal
    reason: 原因は指示の片側変更ではなく、集約した格を「根拠ゼロ」の代わりに使ったこと
  - date: 2026-09-28
    field: axes
    from: "processing none / connection meaning+condition"
    to: "processing logic / connection meaning"
    reason: 付与条件の条件式そのものの誤りと確認した（condition = 段階間で条件が伝わらない、ではなかった）
---

## 課題

出典が 8〜14 件ある回答にも未踏ガードと ⚠️ 注意書きが付き、チューターの即答規則と衝突した。
原因は、付与条件が採用出典の tier の最弱集約（overall_tier == out_of_source）で、類似度 0.30〜0.45 の
出典が 1 件混じるだけで満たされたこと。注意書きは回答本文として履歴に保存され、次の往復に戻っていた。

## 発見の観点

製品のプロンプトの実物を読み（第 3 周）、第 8 周で出典件数とガードの有無を突き合わせた。

## 解決の観点

付与条件を「採用した根拠が 1 つも無い」（content_grounding == model_generated）に限定し、優先順位を
明文化し、注意書きは表示にだけ付けて履歴に残さない。

## 一般化

集約した格を別の意味（根拠ゼロ）の代わりに使う型。
