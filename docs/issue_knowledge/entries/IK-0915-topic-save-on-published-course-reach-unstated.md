---
id: IK-0915
title: "公開中のコースでトピックの授業用ドラフトを保存しても、保存の前後とも「受講者にいつから届くか」（版を発行していなければ即時に届く／発行済みなら版を発行するまで届かない）を言わず、教員は学生にいつ見えるのか分からない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/shared_versioning_design.md"
feature_context:
  realizing: "教員が公開中のコースのトピックを直し、受講者に届く時期を知って保存する"
  layers: [lecture_studio, versioning_v, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [completion]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 接続軸 condition: 学習者の読み取りは services._apply_course_version_view を通り、版の無いコースは live、版があれば有効な版の写しを読む。原稿スタジオの保存（backend/api/routes/lecture_studio/topics.py 542 行〜）の応答と画面はこの条件を言わない。統制軸 completion（medium）: 「保存した」と「受講者に届いた」が同じ表示で区別されない。6be229e1 に版があるかは確かめていないので hypothesis。
generalization:
  level: repo_pattern
  general_form: "保存が利用者に届くかどうかが別の層の条件（版・公開）で決まるのに、保存の画面がその条件を言わない"
pattern: disclosure-not-declared
discovery:
  perspective: [reproduction]
  note: >-
    te-01 が保存後に「受講している学生にはいつから見えるのか何も書かれていない」と記した。run: uxsim/runs/c-astro-r25-studio-r2/20261023T020312Z-65722（seq10・20）。
resolution:
  perspective: [carry_through]
  note: "course-structure と保存の応答に delivery（版が無ければ「すぐ受講者に届きます」、版があれば「版 N を見ています。届けるには新しい版を発行」）を返す。条件は V層の版解決と同じ（版の有無 + 公開・グループ共有）で、受講者のいない非公開コースでは何も言わない。保存の前から授業用ドラフトの下にも同じ文を出す。"
  landed_in:
    - "backend/core/lecture_studio_state.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "frontend/public/js/admin-lecture-studio.js"
  verification:
    methods: [guardrail]
    unverified:
      - "固定コースに版があるかは確かめていない（版あり・版なしの文は単体テストで確かめた）"
related: [IK-0877]
view_of: []
history: []
---

## 課題

症状: 保存の表示は「授業用ドラフトを保存しました」だけで、学生に届くかどうかが読めない。

原因（仮説）: 保存と配信の条件（版）が別の層にあり、画面に運ばれていない。

## 解決の観点

解決（2026-10-23・第 25 周 面 D 第 2 波）: course-structure と保存の応答に delivery（版が無ければ「すぐ受講者に届きます」、版があれば「版 N を見ています。届けるには新しい版を発行」）を返す。条件は V層の版解決と同じ（版の有無 + 公開・グループ共有）で、受講者のいない非公開コースでは何も言わない。保存の前から授業用ドラフトの下にも同じ文を出す。

当初の是正案（記録）:

- course-structure（または保存の応答）に配信の事実 1 つを返す（「このコースは版を発行していないので、保存はすぐ受講者に届きます」／「受講者は版 N を見ています。届けるには版を発行します」）。正本は versioning の resolver。
- 公開中でないコースでは何も言わない。

## 発見の観点

第 25 周 面 D 第 2 周（原稿スタジオ → 学生の受講）の通し受講で、ペルソナの観察と HTTP の生応答・製品コードを突き合わせた。

## 一般化

保存の画面は、その保存が誰にいつ届くかを言う。確かめ方は、版のあるコースと無いコースで保存し、届く時期の事実文が違うことを見ること。
