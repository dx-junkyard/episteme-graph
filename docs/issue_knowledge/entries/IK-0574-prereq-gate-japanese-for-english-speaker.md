---
id: IK-0574
title: "前提ゲートの逆質問が英語で話す学習者にも日本語で出る"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-10-02
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.7
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [rag_chat]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
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
    確認: `routes/learning.py` の逆質問の組み立ては発話の言語を `_is_mostly_latin` で読んでいたが、その結果を
    「日本語の逆質問（services.check_prerequisites の固定文）に英語の 1 文を足す」ことにしか使わず、逆質問の本文・
    回答済みの目印「ご質問には上で答えました。」・選択肢ボタンの文言まで伝えていなかった（接続軸 condition = 判定した
    発話の言語という条件が、固定文を選ぶ段まで届かない）。英語の固定文の表は label_vocab に既にあり（IK-0424 / 0450）、
    語彙の欠落ではないので構造軸は none。処理軸は判定器そのもの（IK-0424 の基準）が正しく働いていたので none。
    統制軸は順序・予算・判断の割り当てに関わらないので none。
generalization:
  level: general
  general_form: "発話の言語を固定文の生成まで伝えない"
pattern: condition-not-propagated
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [carry_through, guardrail_fix]
  note: >-
    当該発話の言語（既存の `_is_mostly_latin` / `_is_kana_kanji_free` の和 = `_fixed_text_language`。判定器は新設しない）を
    逆質問の本文・回答済みの目印・選択肢ボタンまで運ぶ。英語の逆質問・目印・ボタンの正本は `core/label_vocab.py`
    （PREREQUISITE_GATE_FULL_EN / _MARKER_EN / _FIRST_EN / _ANSWERED_MARKER_EN / PREREQUISITE_CHOICE_*_EN）。保存文の形
    （本文 → `\n\n---\n\n` + 目印 + 逆質問）は不変で、履歴の判定器 3 本（`_prerequisite_gate_asked_in_history` /
    `_previous_turn_was_prerequisite_gate` / `_question_before_prerequisite_gate`）は `_is_prerequisite_gate_for_topic`
    経由で日英両方の逆質問を読む（4 本目の skipped notice は既に日英両対応）。日本語の発話の逆質問はバイト一致のまま。
    discuss / casual / 楽屋は従来どおりゲートを通らない。LLM 呼び出しは増えない。
  landed_in:
    - backend/api/routes/learning.py
    - backend/core/label_vocab.py
    - backend/core/learning_support_agent.py
    - backend/tests/test_ik0574_prerequisite_gate_language.py
    - backend/tests/test_ik0422_prerequisite_gate_once.py
    - docs/features/dialogue_response_shape_design.md
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（英語で話すペルソナの前提ゲート）"
      - "英語の発話に添える他の固定文（教材外の注意書き・HELP の footer・前提説明の LLM 失敗時の固定文）は日本語のまま"
      - "かな・漢字とラテン文字が混ざる短い発話の言語の読み"
related: [IK-0509]
view_of: []
history:
  - date: '2026-10-02'
    field: status
    from: 'open'
    to: 'resolved'
    reason: '発話の言語を逆質問の本文・目印・選択肢まで運び、履歴の判定器を日英両対応にした（ガードレールで確認・再演は次の周）。'
---

## 課題

前提ゲートの逆質問が英語で話す学習者にも日本語で出る。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

判定はあったが、その結果を固定文の選択まで運んでいなかった（IK-0424 の「英語の 1 文を足す」は日本語の逆質問を残した）。
判定器を増やさず、既存の 2 つの判定の和を逆質問・目印・選択肢の 3 か所に渡した。履歴の判定器は英語の目印も読むので、
英語の逆質問のあとの "Yes, I understand" / "No, please explain …" も日本語と同じ規則で扱われる。

## 一般化

発話の言語を固定文の生成まで伝えない。
