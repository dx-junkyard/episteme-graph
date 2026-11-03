---
id: IK-1127
title: "版の管理モーダル（発行を決める画面）の冒頭が「発行した版だけが共有先に見え、共有先は同意するまで自分の版に留まります」と共有先（教員の購読）の規則だけを言い、受講者が発行で自動的に新しい版を見ることをモーダルとコースの見出しが言わないため、初任の教員は発行の前後とも「受講者にも同意が要るのか」を読めない"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/shared_versioning_design.md"
feature_context:
  realizing: "所有者が保存した変更を版として発行し、受講者に届いたかを発行の画面で確かめる（共有先の教員の版の同意とは別の宛先）"
  layers: [versioning_v, lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: low
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定（コードと生応答で読んだ）: frontend/public/js/versioning.js の `_introText("course")` は「発行した版だけが共有先に見え、共有先は同意するまで
    自分の版に留まります。」の 1 文だけで、宛先を共有先（V層の購読 = ピンを持つ教員）に限る。受講者は `services._apply_course_version_view` で
    有効な版（active_release_id）のスナップショットを読み、同意なしに発行の時点で新しい版へ移る。原稿スタジオのトピックの区画は
    `lecture_studio_state.TOPIC_DELIVERY_MESSAGES["release_current"]`「受講者は発行済みの版（版 N）を見ています。」を描くが、モーダルは所有者に
    `unpublished_changes_note`（発行の後は「いまの内容は版 N と同じです。発行は要りません。」）だけを描き、コースの見出しは未発行の変更があるときしか
    事実文を出さない。接続軸 target（受講者と共有先の 2 つの宛先を 1 文が片方だけで言う）。処理軸 wording（文言だけで閉じる）。
    統制軸は low で none（発行の手続そのものは成立している）。
generalization:
  level: repo_pattern
  general_form: "同じ操作が届く宛先が 2 種ある（同意して移る宛先と自動で移る宛先）のに、操作を決める画面の説明が片方の規則だけを言い、もう片方への届き方は別の画面にしか出ない"
pattern: wording-mismatch
discovery:
  perspective: [trace_walk, symptom_report]
  note: >-
    第 25 周 面 D 第 10 周。te-01 が［付録をまとめて付け直す］→ 見出しの［版の管理を開く］→ モーダルで「共有先は同意するまで自分の版に留まります」を読み、
    「受講者も同意しないと新しい版に移らないのか、同意が要るのは共有している先生だけなのか」と迷ったまま v6 を発行。発行後もモーダルは「いまの内容は版 6 と
    同じです。発行は要りません。」で、見出しの事実文は消え、受講者が版 6 を見ていると言い切る文は無かった（C 所見 2 件）。split-save でトピック t17 を
    開いた区画に「受講者は発行済みの版（版 7）を見ています。」が出て頭脳の迷いが解けた。
    run: uxsim/runs/c-astro-r25-studio-r10/20261031T052922Z-65584 seq7・8、uxsim/runs/c-astro-r25-studio-r10/20261031T053217Z-67272 seq11・12。
resolution:
  perspective: [canonical_source, carry_through]
  note: >-
    第 25 周 第 10 波（2026-10-31）: 宛先ごとの届き方の文を `label_vocab.VERSION_AUDIENCE_NOTE_COURSE`（受講者 = 発行の時点から新しい版・
    同意は要らない / 閲覧の共有先 = 取り込むまで自分の版 / 編集の共有先 = いまの内容）に置き、`lecture_studio_state.version_state_course_facts`
    が version-state（コース）で全員に `audience_note`、所有者には加えて `learner_version_note`（原稿スタジオのトピックの区画と同じ
    `TOPIC_DELIVERY_MESSAGES["release_current"]`）・未発行の変更の文（IK-1079 と同じ比較）・発行の後に変わったトピックの題名
    （`changed_topic_titles` = 写しと HEAD のトピック単位の比較・件数なし・studio 頭脳の所見 ①）を返す。`versioning.js` はコースの
    冒頭の静的な 1 文（「共有先は同意するまで…」）を言わず、サーバの文を素通しで描く（`data-vg-audience-note` / `data-vg-learner-version` /
    `data-vg-changed-topics`）。教材（document）の冒頭文は不変。
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "受講者の版の文は原稿スタジオと版の管理で同じ定数・同じ比較を通す（JS に宛先の文を焼き込まない）。"
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/core/lecture_studio_state.py"
    - "backend/api/routes/versioning.py"
    - "frontend/public/js/versioning.js"
    - "backend/tests/test_lecture_studio_round25_r10.py"
  verification:
    methods: [guardrail]
    unverified:
      - "モーダルを実機で開いて te-01 が受講者と共有先の届き方を読み分けられるかは第 11 周で見る"
      - "変わったトピックの題名は版の写しと HEAD のトピック dict 全体の比較で、音声・付録の付け直しだけの変更も「変わった」に数える"
related: [IK-1079, IK-1053, IK-0915]
view_of: []
history: []
---

## 課題

症状: 発行を決める画面の説明が共有先の教員の同意の規則だけを言い、初任の教員は受講者にも同意が要るのか、発行で受講者に届いたのかを読めない。

原因: モーダルの冒頭の文が宛先を共有先に限って書かれ、受講者への届き方の事実文はトピックの区画にしか描かれない。

## 発見の観点

付け直し → 見出し → 版の管理 → 発行を所有者の画面で歩き（trace_walk）、ペルソナが迷いを記した（symptom_report）。宛先ごとの版の解決をコードで確かめた。

## 解決の観点

未着手。宛先ごとに届き方を言い分け、受講者の版の事実文をモーダルでも同じ部品で描く。

## 一般化

型 wording-mismatch。確かめ方: 発行の前後でモーダルだけを読み、受講者と共有先の教員それぞれに何が起きるかが言えるかを見る。

## 第 11 周（第 25 周・面 D 第 11 周、2026-10-31・judge_studio_r11）

**GONE（実機）**。te-01 が t18 を区切って保存 → ［版の管理を開く］→ モーダルで「発行すると、受講者（学習者）はその時点から新しい版を見ます（受講者の同意は要りません）。共有先の教員のうち閲覧の共有先は…」・「受講者は発行済みの版（版 7）を見ています。」・未発行の文・「▸ 発行の後に変わったトピック: 主結果：CMB・BAO・SN・宇宙シアによる制約」を読み、「宛先ごとに書いてあって分かりやすい・変わったトピックも今さわった 1 件と一致」として v8 を発行。発行後はモーダルと原稿スタジオの両方が「受講者は発行済みの版（版 8）を見ています。いまの内容は版 8 と同じです。」。残り（小・記帳見送り）: 同じモーダルで「現在の最新版: v7」と「版 7」が混在。run: uxsim/runs/c-astro-r25-studio-r11/20261031T101202Z-98537 seq11・12。
