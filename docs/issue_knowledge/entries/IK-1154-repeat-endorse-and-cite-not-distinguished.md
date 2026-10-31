---
id: IK-1154
title: "既に支持している説明にもう一度［支持する］を押すと「支持しました」とだけ出て人数は変わらず（実体は既存の支持の付け直し）、同じ説明を同じコースへもう一度［別コースで引用］すると「引用しました」で引用の一覧に同じ行（同じコース・同じ意図・同じ人・同じ日付）が二重に並ぶ — 新しい記録か既存の記録への作用かを言い分けない"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
feature_context:
  realizing: "教員が説明を支持・引用したとき、それが新しい記録になったのか、既にある記録を付け直したのかを知る"
  layers: [endorsement_c, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [none]
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
    処理軸 wording: api/routes/theory_components.py の endorse は ON CONFLICT (explanation_id, endorser_id) DO UPDATE で RETURNING (xmax = 0) から
    is_new_endorsement を得ているが、通知の判定にだけ使い応答の知らせに反映しない（画面の知らせは admin-lecture-studio.js / runner とも固定の
    「支持しました」）。構造軸 representation: cite_explanation は component_citations に INSERT するだけで、同じ説明・同じ引用先・同じ意図の行を一意に
    しない（重複の判定が表現に無い）。
generalization:
  level: repo_pattern
  general_form: "繰り返せる操作の結果を固定の成功文で返し、新しい記録か既存の記録の付け直しか（または重複の記録か）を言い分けない"
pattern: failure-reported-as-success
discovery:
  perspective: [reproduction]
  note: >-
    第 25 周 面 A 第 11 周（judge_share_r11）。te-02 が既に支持済みの江口の説明に暫定の支持 →「支持しました」・「暫定的に2名」のまま（uxsim/runs/c-astro-r25-share-r11/
    20261031T102323Z-3501 seq 4）。同じ説明を 0d70b5d0（第 10 周に引用済み）へ引用 → 引用の一覧に『宇宙論入門 補助教材（第 23 周・堀）』堀 正和・2026-10-31 が 2 行（seq 6）。
    頭脳「既に支持済みだったことを言わない」「既に引用済みだったことを言わない」。
resolution:
  perspective: [explicit_contract, representation_change]
  note: >-
    支持: upsert を WITH prior（同じ文のスナップショット = upsert 前の行）付きにして prior_revoked を返し、endorsement_outcome_fact が付け直し（「この説明は既に支持しています。支持を付け直しました（段階: …）。支持の記録は 1 つのままで、人数は変わりません。」）と取り消しの復帰（「取り消していた支持を付け直しました（段階: …）。」）を ExplanationOut.outcome_fact で返す（新規は空 = 画面の既定「支持しました」。段階の語は label_vocab.EXPLANATION_SUPPORT_LEVEL_LABELS・文は explanation_governance）。引用: 同じ説明・同じ引用先・同じ意図・同じ引用者の行があれば INSERT せず「この説明は、このコースへ同じ意図で既に引用しています。新しい引用の記録は作っていません（変更はありません）。」+ 届く範囲の文（監査・版固定もしない）。既存の二重行は消さず、一覧（_citations_for_explanations）が同じ 4 つ組を 1 行に畳む（最初の引用を見せる）。
  landed_in:
    - "backend/api/routes/theory_components.py"
    - "backend/core/explanation_governance.py"
    - "backend/tests/test_share_round25_r11_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "WITH prior … RETURNING (SELECT revoked FROM prior) を実 DB で走らせていない（scratch DB 未実施）"
      - "同じ支持・同じ引用を 2 度行った知らせと一覧の行数を砂場の画面で"
related: [IK-0897, IK-1126]
view_of: []
history: []
---

## 課題

支持の付け直しと引用の繰り返しを、新しい記録と同じ知らせで返す。引用は行が二重になる。

## 発見の観点

場面の教員の手（reproduction）。

## 解決の観点

未着手。新規か既存かを応答に載せ、引用は重複を作らない。

## 一般化

繰り返せる操作は、2 度目に何が起きたかを言い分ける。確かめ方: 同じ操作を 2 度行い、知らせと記録の数を見る。

## 第 11 波の是正（2026-10-31）

新規か既存かを応答に載せ、引用は重複を作らない（resolution.note）。既存の重複行は削除せず表示で畳む。
