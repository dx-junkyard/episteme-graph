---
id: IK-1070
title: "版ありのコースで保存すると、知らせが「新しい版を発行してください」と「いまの内容は版 2 と同じです（未発行の変更はありません）」を同時に言い、所有者は発行が要るか判断できない"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "教員が版を発行済みの公開コースのトピックを直し、直した内容を受講者に届ける（届けるのに発行が要るかを保存の画面で判断する）"
  layers: [lecture_studio, versioning_v, frontend_admin_ui]
classification:
  axes:
    processing: [logic]
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
    確定（コードと実データで読んだ）: `core/lecture_studio_state.py::topic_save_facts` は版ありのとき delivery.message を
    `TOPIC_DELIVERY_MESSAGES["release"]`（「…保存した内容を受講者に届けるには、新しい版を発行してください。」）で**無条件に**組み、
    その後で別に `unpublished_changes` から事実文 `TOPIC_UNPUBLISHED_CHANGES_NOTES[False]`（「…同じです（未発行の変更はありません）」）を足す。
    画面（`admin-lecture-studio.js` の保存の知らせ・`lsTopicDeliveryInnerHtml`）は両方をつなげて出す。接続軸 condition: 第 7 波で求めた
    「HEAD と版が同じか」の条件が、同じ dict の指示文まで運ばれていない。処理軸 logic: 指示文の分岐が無い。比較そのものは正しい
    （砂場 DB の shared_versions で版 2・版 3 と HEAD の snapshot の md5 が一致）。
generalization:
  level: repo_pattern
  general_form: "条件付きで意味を持つ指示文と、その条件の真偽を述べる事実文を別々に組み立て、条件が偽のときも指示文を出して両者が食い違う"
pattern: condition-not-propagated
discovery:
  perspective: [trace_walk, symptom_report]
  note: >-
    第 25 周 面 D 第 8 周。te-01 が版 2 のある 6be229e1 で t16 に区切りを入れて保存し（周の前提で外した区切りを入れ直した結果、HEAD が版 2 と
    同じに戻った）、続けて t17 を区切らずに保存した。両方の保存の応答で delivery.message（発行してください）と unpublished_changes=false
    の事実文が並んだ（PUT …/course-topics/t16・t17 の応答・course-structure）。保存の前は unpublished_changes=true だったので、頭脳は
    「7 枚を 2 枚に変えたのに『変更なし』」と読み、事実文の側を誤りと疑った。run: uxsim/runs/c-astro-r25-studio-r8/20261027T223243Z-92722 seq6・9・11・12。
resolution:
  perspective: [single_point_fix]
  note: >-
    第 25 周 第 8 波（2026-10-31）: `core/lecture_studio_state.py::topic_save_facts` が指示文の鍵を `unpublished_changes` から選ぶ
    （False → `TOPIC_DELIVERY_MESSAGES["release_current"]`「受講者は発行済みの版（版 N）を見ています。」で止める、True / None → 従来の
    「…新しい版を発行してください。」）。事実文 False も「いまの内容は版 N と同じです。発行は要りません。」にし、指示文と事実文を同じ値から作る。
    画面（届き方の文・保存の知らせ）はサーバの文を素通しのまま。マニュアルの届き方の段落を追随。
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: "条件（版と同じか）を文の組み立ての内側で受け取り、指示文を同じ値から作る（呼び出し側で消さない）。"
  landed_in:
    - "backend/core/lecture_studio_state.py"
    - "backend/tests/test_lecture_studio_round25_r8.py"
    - "backend/tests/test_lecture_studio_round25_r7.py"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
  verification:
    methods: [guardrail]
    unverified:
      - "版ありの固定コースで保存して版と同じに戻したとき、所有者の画面から指示文が消えることは実機（第 9 周 te-01）で見ていない"
related: [IK-1053, IK-0915]
view_of: []
history: []
---

## 課題

症状: 保存の知らせが「新しい版を発行してください」と「未発行の変更はありません」を同時に言う。保存の前は「版 2 の発行の後に保存した変更があります」と出ていたので、所有者は保存したのに「変更なし」に反転したと読み、発行が要るか判断できない（実際は HEAD が版 2 と同じに戻っており、事実文が正しく指示文が余計）。

原因: 指示文は版の有無だけで無条件に作られ、第 7 波で足した「HEAD と版が同じか」の条件を受け取っていない。

## 発見の観点

保存から受講者に届くまでを所有者の画面で歩き（trace_walk）、ペルソナが判断できないと記した（symptom_report）。DB の版の写しと HEAD を比べて事実文の側が正しいと確かめた。

## 解決の観点

第 8 波で是正（2026-10-31）。指示文と事実文を同じ値（unpublished_changes）から組む。

## 一般化

「〇〇してください」は、〇〇が要るという条件の真偽を述べる文と同じ値から作る。確かめ方: 条件が偽になる状態（版と同じに戻した保存）を作り、指示が消えることを見る。

## 第 9 周（第 25 周・面 D 第 9 周、2026-10-31・judge_studio_r9）

**GONE**（実機で両分岐を見た）。te-05 の開始時（版 4 = HEAD）の course-structure は「受講者は発行済みの版（版 4）を見ています。」+「いまの内容は版 4 と同じです。発行は要りません。」で指示文なし。te-01 の t16 の保存（付録の一括付け直しの未発行分が先にあった）は「…新しい版を発行してください。」+「版 4 の発行の後に保存した変更があります。」で、「同じです」は同時に出ない。場面の手で版 5 を発行した後、t17 を区切らずに保存すると「いまの内容は版 5 と同じです。発行は要りません。」だけ。砂場 DB で版 5 の写しの data と HEAD が一致。第 8 周と同条件（保存で版と同じに戻る）の前後比較ではない（未発行の変更が付け直しから先にあった）。残る欠けはコース単位の面に事実が出ないこと（IK-1079）。run: uxsim/runs/c-astro-r25-studio-r9/20261031T003210Z-30686 seq3、uxsim/runs/c-astro-r25-studio-r9/20261031T015111Z-37232 seq9・12・13・14。
