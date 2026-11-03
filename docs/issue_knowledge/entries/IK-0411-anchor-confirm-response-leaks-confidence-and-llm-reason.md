---
id: IK-0411
title: "構造帰属の確定（POST /api/learning/anchors/{trace_id}/confirm）の応答が保存した structure_anchor を丸ごと返し、confidence の生の数値・内部の claim ID を含む英語の LLM の理由文・anchor_id・detector_version が学習者に届いていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が「この疑問は◯◯についてでしたか？」の候補を確定・却下する
  layers: [learner_experience_b, rag_chat]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 保存用の structure_anchor（AI 候補の confidence・reason・detector_version
    を含む）から学習者向け応答へ移るところで、学習者に出してよい範囲（数値非表示 P7・内部 ID 非表示
    KO10）の条件が掛かっていなかった（condition）。ダイジェスト（get_anchor_digest）はラベルだけを投影しており、同じ行の確定応答だけが射影を通っていなかった。構造軸は
    none（ラベルの語彙は揃っていた）。確認: 3 名のペルソナの confirm 応答に confidence 0.55 / 0.88 / 0.6・英語の reason・anchor_id の
    UUID・detector_version が載っていた（routes/learning.py の confirm_anchor_route が {ok, **result} を返していた）。
generalization:
  level: general
  general_form: 保存用の行をそのまま応答に展開し、利用者に出してよい範囲の射影を通さない
pattern: condition-not-propagated
discovery:
  perspective: [reproduction, invariant_audit]
  note: ペルソナの応答本文を数値非表示・内部 ID 非表示の原則と照合した。
resolution:
  perspective: [fail_closed, guardrail_fix]
  note: >-
    confirm / dismiss の応答を許可リストの射影 _learner_anchor_decision_dto に通す（{ok, trace_id, status, notice,
    anchor_label, anchor_type_label, doubt_type, doubt_type_label, related_assumption:
    {statement}}。ラベルはダイジェストと同じ語彙表）。事実文は label_vocab.ANCHOR_CONFIRMED_NOTICE /
    ANCHOR_DISMISSED_NOTICE。tension の confirm / dismiss / connect も {ok, trace_id, status}
    の許可リストにした（現状は漏れていないが、payload のキーが増えても応答に漏れないように）。フロントは ok と related_assumption.statement しか読まないので互換。
  landed_in:
    - backend/api/routes/learning.py
    - backend/core/label_vocab.py
    - backend/tests/test_learning_chat_wave6_turns.py
    - docs/backend/api.md
    - frontend/public/js/app.js
    - frontend/public/js/discuss.js
    - backend/tests/test_learner_decision_notice_ui_static.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 学習画面（進捗タブの帰属カード）・discuss の着地画面が notice を描くことはブラウザで確かめていない（静的検査のみ）
      - 他の学習者向け書き込み API（map-exclude / reflection 等）の応答の射影は見ていない
related: []
view_of: []
history: []
---

## 課題

帰属の確定応答に AI 候補の数値・理由文・内部 ID が載る。

## 発見の観点

応答本文と原則の照合。

## 解決の観点

応答を許可リストで射影し、何が起きたかは事実文で言う。

## 一般化

保存用の行をそのまま応答に展開する型。
