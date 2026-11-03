---
id: IK-1004
title: "前提の逆質問の直後に学生が別のことを尋ねると、問いに「〜ってどういう意味」「とは」などの説明を求める語が含まれるだけで逆質問への答え（前提の説明の依頼）と読まれ、地図の札「暗黙の前提」の意味を尋ねた問いに無関係な前提「学習損失と 4 段階の交互最適化」の説明が回答の後半を占める"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/features/stakes_ledger_design.md §16.8"
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
feature_context:
  realizing: "学生が前提の逆質問を受けた後、別の画面（分野の地図）の表示について学習チャットに尋ねる"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 logic: routes/learning.py の _prerequisite_followup は、直前の往復が逆質問（gate_first）なら、発話に _PREREQ_TEACH_MARKERS（「とは」「って何」「ってどういう」「わからない」ほか）があるだけで kind='explain'・target=gate_first を返し、_prejudged='LEARNING_ADVICE' で前提の説明へ流す。発話が前提名に触れているか（mentioned / overlap）を条件にしていない。接続軸 condition: 「直前が逆質問」という会話の状態だけで、次の発話の意図を決めている。製品頭脳 A（4058）も同じ所見。
generalization:
  level: repo_pattern
  general_form: "直前に問いを返した会話の状態だけで次の発話をその問いへの答えと読み、別の話題の問いを取り違える"
pattern: condition-not-propagated
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    st-10 が t13 で前提の逆質問（学習損失…を理解していますか）を受けた次の往復で「地図で暗黒エネルギーに付いている『暗黙の前提』ってどういう意味ですか？」と尋ねると、回答の後半が「ここからは、求められた前提知識『学習損失と 4 段階の交互最適化』の説明です」だった。run: uxsim/runs/c-r25-doubt-r5/20261027T071839Z-88666（seq13）。製品頭脳 A 4058（uxsim/runs/round25/BRAIN_NOTES_product_A_r5.md）。
resolution:
  perspective: [single_point_fix]
  note: >-
    _prerequisite_followup の逆質問の直後の分岐で、前提の説明へ流すのを ①前提名に触れる（mentioned / overlap）②別の語を主語に立てない否定・説明要求（_names_other_subject が偽 — 「いいえ」「それってどういう意味」）に限った。「『未訪問』ってどういう意味」「What does unvisited mean?」は after_gate（通常の経路・逆質問は保留）。
  landed_in:
    - "backend/api/routes/learning.py"
    - "backend/tests/test_doubt_round25_wave5.py"
  verification:
    methods: [guardrail]
    unverified:
      - "主語の判定は内容語（漢字・カタカナ・英字）と鉤括弧の決定論で、ひらがなだけの主語は前提の答えと読む"
      - "学習チャットの実機で逆質問の直後に別の問いを送っていない"
related: [IK-0509, IK-0383, IK-0423, IK-0907]
view_of: []
history: []
---

## 課題

症状: 逆質問の次に別の問いをすると、その問いに答える前提の説明が付く。問いへの答えが薄まり、学生は頼んでいない説明を読まされる。

原因: 逆質問の直後の発話に説明を求める語があれば、前提名に触れていなくても前提の説明の依頼と読む。

## 発見の観点

再現と経路の追跡（_prerequisite_followup の分岐）。

## 解決の観点

第 25 周 面 B 第 5 波（2026-10-27・docs/features/stakes_ledger_design.md §16.8）で解決。_prerequisite_followup の逆質問の直後の分岐で、前提の説明へ流すのを ①前提名に触れる（mentioned / overlap）②別の語を主語に立てない否定・説明要求（_names_other_subject が偽 — 「いいえ」「それってどういう意味」）に限った。「『未訪問』ってどういう意味」「What does unvisited mean?」は after_gate（通常の経路・逆質問は保留）。

起票時の是正案: 是正案: 逆質問の直後でも、kind='explain' にするのは ①否定の答え（いいえ・分からない）単独 ②前提名に触れる（mentioned / overlap）発話 のときだけにする。説明を求める語があっても前提名に触れない問いは新しい問いとして通常の経路へ流し、逆質問は保留のまま（IK-0509 の一度きりの記録は変えない）。

## 一般化

会話の状態で発話の意味を決める規則は、状態に関係ない発話を取り込まない条件（話題の一致）を持つ。確かめ方は、問いを返した直後に無関係の問いを送り、その問いだけに答えることを見ること。

第 6 周の再演: GONE（2026-10-27）。st-05 の 1 回目の回答の後ろに前提の逆質問が付いた直後の「いま地図で選んでいる「中性子星」は、結局どこまで確かめられているの？一言で。」は前提の説明に取られず通常の回答になった。st-06 も同じ。run: uxsim/runs/c-r25-doubt-r6/20261027T123134Z-9179（seq7・14）/ uxsim/runs/c-r25-doubt-r6/20261027T123621Z-11841（seq9・10）。
