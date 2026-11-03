---
id: IK-0908
title: "原稿スタジオで editor として共有されたコースのトピックは、AI 書き換え（日次上限を 1 消費する LLM 呼び出し）までは通るのに保存は 403 で、画面は「保存に失敗しました」とだけ言う — 保存は所有者だけという規則が書き換えの前にも保存の後にも見えない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/teaching_figure_studio_design.md"
feature_context:
  realizing: "共有されたコースの教員が原稿スタジオでトピックを書き換えて保存する"
  layers: [lecture_studio, auth_visibility, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [condition]
    governance: [assignment]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 condition: rewrite（backend/api/routes/lecture_studio/topics.py 729〜737 行）は _course_data_for_studio_editable（editor 可）で通し、保存（同 542 行〜・583 行）は所有者か SYSTEM_ADMIN 以外を 403 "Forbidden" にする。course-structure は閲覧者が保存できるかを返さないので、画面（admin-lecture-studio.js の保存 catch）は理由の無い「保存に失敗しました」しか言えない。構造軸 responsibility（medium）: 権限の判定が経路ごとに別の関数にあり、画面に渡す 1 つの値が無い。統制軸 assignment: 保存は所有者に依頼する、という出口が誰にも示されない。
generalization:
  level: general
  general_form: "同じ編集画面の中で、下書きを作る操作と確定する操作の権限が違うのに、画面がその差を事前に言わず、確定で初めて理由なしに失敗する"
pattern: permission-and-affordance-asymmetric
discovery:
  perspective: [reproduction]
  note: >-
    te-02（ベテラン・editor 共有の 6be229e1）が t0 を AI で書き換え（変更は反映）→ 保存で 403、表示は「保存に失敗しました」のみ。設計時の疑い 6 の実測。run: uxsim/runs/c-astro-r25-studio-r2/20261023T021041Z-70961（seq11・12）。
resolution:
  perspective: [canonical_source, explicit_contract]
  note: "保存の可否を保存 API と同じ所有者判定から course-structure の can_save / save_notice として 1 つの値で返し（core/lecture_studio_state.topic_save_facts）、editor 共有では保存ボタンを無効にして所有者へ依頼する事実文を保存・書き換えの前から出す。書き換えは editor に許したまま、応答に save_notice を添えて結果が保存されないことを言う。保存の 403 は日本語の事実文（TOPIC_SAVE_FORBIDDEN_DETAIL）で、画面は detail を描く。"
  landed_in:
    - "backend/core/lecture_studio_state.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "保存の可否を保存 API と画面の 2 箇所で別々に判定せず、サーバの 1 つの判定（topic_save_facts）を画面が読むだけにした。"
  verification:
    methods: [guardrail]
    unverified:
      - "editor 共有の教員で実画面の保存ボタンが無効になることは砂場で見ていない（第 3 周で確かめる）"
      - "JS は node の無い環境で評価していない（静的検査のみ）"
related: [IK-0874, IK-0877]
view_of: []
history: []
---

## 課題

症状: 共有されたコースで AI 書き換えは成功し、保存だけが「保存に失敗しました」で止まる。教員は理由も、誰に頼めばよいかも分からない。書き換えに使った日次上限は戻らない。

原因: 書き換えは editor 可・保存は所有者のみ（教材図スタジオの採用と同じ水準）で、画面がその差を知る値を持たない。

## 解決の観点

解決（2026-10-23・第 25 周 面 D 第 2 波）: 保存の可否を保存 API と同じ所有者判定から course-structure の can_save / save_notice として 1 つの値で返し（core/lecture_studio_state.topic_save_facts）、editor 共有では保存ボタンを無効にして所有者へ依頼する事実文を保存・書き換えの前から出す。書き換えは editor に許したまま、応答に save_notice を添えて結果が保存されないことを言う。保存の 403 は日本語の事実文（TOPIC_SAVE_FORBIDDEN_DETAIL）で、画面は detail を描く。

当初の是正案（記録）:

- course-structure に閲覧者の権限（`can_save_topics` のような真偽値 1 つと事実文）を返し、editor では保存ボタンを無効にして「このコースのトピックの保存は所有者だけができます。変更は所有者に依頼してください」を近くに出す（書き換えボタンの近くにも同じ文）。
- 書き換えは editor にも許すか（下書きを所有者に渡す用途）、保存と同じ水準に揃えるかは担当が決める。許すなら書き換え結果を所有者に渡す手段（コピー・提案として保存）を用意する。
- 保存の 403 は detail を日本語の事実文にし、画面は detail を描く。

## 発見の観点

第 25 周 面 D 第 2 周（原稿スタジオ → 学生の受講）の通し受講で、ペルソナの観察と HTTP の生応答・製品コードを突き合わせた。

## 一般化

下書きと確定の権限が違う画面は、確定できないことを下書きの前に言う。確かめ方は、editor の教員で原稿スタジオを開き、書き換え・保存の前に保存できない事実文が出ること、保存ボタンが無効であることを見ること。
