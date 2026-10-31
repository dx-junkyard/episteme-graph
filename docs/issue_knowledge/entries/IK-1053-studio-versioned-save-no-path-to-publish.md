---
id: IK-1053
title: "版を発行済みのコースで所有者がトピックを保存すると「新しい版を発行してください」と言われるが、原稿スタジオに発行の場所への道も「未発行の変更がある」という事実も無く、学生に届けるまでの次の一歩がこの画面で閉じない"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-28
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/shared_versioning_design.md"
feature_context:
  realizing: "教員が版を発行済みの公開コースのトピックを直し、直した内容を受講者に届ける"
  layers: [lecture_studio, versioning_v, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [completion]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定（第 7 波で読んだ）: 接続軸 information: 保存の応答と course-structure の delivery（IK-0915 の是正）は「版 N を見ている・届けるには発行」という条件を
    言うが、発行の操作（コース管理の［版の管理］・V層 POST /api/admin/shared/course/{id}/releases）への導線と、HEAD が最新の版と違うか
    （未発行の変更があるか）の事実を原稿スタジオへ運んでいない。統制軸 completion: 「保存した」と「受講者に届いた」の間に発行という
    手続が挟まるのに、その手続の完了・未完了が保存の画面で読めない。version-state の update_available は購読者側の欄で、所有者の未発行の変更は
    表さないことを読んで確かめた（HEAD と版の差を表す欄はどこにも無かった）。構造軸は medium（表現の不足か導線の不足か）。
generalization:
  level: repo_pattern
  general_form: "保存が利用者に届くには別の画面の手続が要ると告げるが、その手続への道と、手続がまだ済んでいない事実を保存の画面に持たない"
pattern: last-mile-missing
discovery:
  perspective: [trace_walk, symptom_report]
  note: >-
    版を発行した固定コース 6be229e1 で、所有者 te-01 が t16（明示 2 枚）・t17（自動 5 枚）を保存した。保存の前後とも「受講者は発行済みの版
    （版 1）を見ています。…新しい版を発行してください。」が出た（IK-0915 の版あり経路の初観察 — GONE）が、頭脳は「新しい版を発行する場所
    への道がこの画面に無い」「次の一歩はこの画面だけでは閉じない」と記した。発行は指揮者が別経路（V層 API）で行った。
    run: uxsim/runs/c-astro-r25-studio-r7/20261027T171357Z-51101 seq6・9・12。
resolution:
  perspective: [carry_through, first_class_state]
  note: >-
    第 25 周 第 7 波（2026-10-28・オーナー判断: 発行は所有者の明示操作のまま）。`_topic_save_facts_for` が受講者の見ている版
    （active_release_id）の写しといまの working copy（`releases.build_course_snapshot`）を `course_has_unpublished_changes` で
    中身ごと比べ（時刻では代用しない・読めなければ言わない）、`topic_save_facts` が版ありの delivery に `unpublished_changes` +
    事実文 `unpublished_changes_note` を、所有者にだけ `versions_entry`（［版の管理を開く］）を載せる。原稿スタジオは届き方の文の下に
    それを描き、押すと V層 `Versioning.openModal`（任意の `options.onClose` を足し、閉じたら事実文を取り直す）。自動発行・督促・
    G層 To-Do は作らない。
  landed_in:
    - "backend/core/lecture_studio_state.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "frontend/public/js/versioning.js"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
    - "backend/tests/test_lecture_studio_round25_r7.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "届き方の判定と文は topic_save_facts の 1 箇所に置き、course-structure・保存の応答・付録の一括付け直しの応答が同じ値を受け取る。"
  verification:
    methods: [guardrail]
    unverified:
      - "版ありの固定コースで保存 → 未発行の文 → 版の管理で発行 → 閉じて「同じです」に変わるかは実機で見ていない（第 8 周 te-01）"
      - "版の写しと HEAD の比較が、発行直後に写しの JSON の並びだけ違うケースで偽の「変更あり」を出さないかは実データで見ていない（sort_keys で正規化している）"
related: [IK-0915, IK-0877]
view_of: []
history: []
---

## 課題

症状: 版ありのコースで保存すると「新しい版を発行してください」とだけ言われ、発行の場所が原稿スタジオから辿れない。保存の後に発行を済ませたかどうかも画面で分からない（発行後も文言は「版 2 を見ています…発行してください」と同じ形）。

原因（仮説）: 届く条件（版）は運んだが、届けるための手続（発行）への導線と、その手続が未完了という状態を運んでいない。

## 発見の観点

保存から受講者に届くまでを所有者の画面で歩いた（trace_walk）。ペルソナが次の一歩を見つけられないと記した（symptom_report）。

## 解決の観点

第 25 周 第 7 波で是正（2026-10-28）。発行は所有者の明示操作のまま、保存の画面に未発行の変更の事実（版の写しと HEAD の中身の比較）と［版の管理を開く］の入口だけを置いた（自動発行・督促なし）。

## 一般化

「別の画面で〇〇してください」と告げる表示は、その画面への道と、〇〇が済んだかどうかを伴う（last-mile-missing）。確かめ方: 版ありのコースで保存し、
原稿スタジオから発行に辿り着けること・発行の前後で表示が変わることを見る。

第 8 周の再演（2026-10-28）: **GONE（入口と事実文）**。te-01 の t16 を開くと届き方の文の下に「版 2 の発行の後に保存した変更があります。受講者はまだ版 2 を見ています。」+［版の管理を開く］（所有者だけ・editor の te-05 には出ない）。保存の応答・course-structure とも unpublished_changes が付き、HEAD と版の比較は正しかった（砂場 DB で版 2・版 3・HEAD の snapshot の md5 が一致）。ただし保存の知らせが「新しい版を発行してください」と「未発行の変更はありません」を同時に言う食い違いが出た → IK-1070。保存前の「変更あり」は周の前提（t16 の区切りを外す保存）の結果で、誰がいつ保存した変更かは画面に無い（所有者が自分の保存と取り違える — 記帳は見送り）。［版の管理を開く］を押す手は場面に無く、発行は指揮者の API（版 3 = 版 2 と同一の中身）。run: uxsim/runs/c-astro-r25-studio-r8/20261027T223243Z-92722 seq6・9・12。

## 第 9 周（第 25 周・面 D 第 9 周、2026-10-31・judge_studio_r9）

**GONE（トピックの保存の経路は実機で閉じた）**。te-01 が t16 に区切りを入れて保存 → 届き方の文 + 「版 4 の発行の後に保存した変更があります。」+［版の管理を開く］→ 版の管理モーダルで**場面の手で発行**（v5・「4名に通知」・履歴 5 行・メモはペルソナの文）→ 閉じると届き方の文が取り直され「いまの内容は版 5 と同じです。発行は要りません。」。8 周目で初めて、発行がペルソナの画面で行われた。残る欠け: コース単位の操作（付録の一括付け直し）の直後・コースの見出し・版の管理モーダルに未発行の事実が出ず、付け直しだけの段階では頭脳が発行を見送った（IK-1079）。run: uxsim/runs/c-astro-r25-studio-r9/20261031T015111Z-37232 seq6・9・11・12・13、uxsim/runs/c-astro-r25-studio-r9/20261031T012109Z-34801 seq7。
