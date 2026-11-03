---
id: IK-1011
title: "原稿スタジオの AI 書き換えの申し送りが、前の版の注意点（「偏光と磁場・分子線と運動の対応は原文に無い」）を「材料の論理ブロックの要約に記述があったため取り下げ」と言うが、その要約は教員の画面に出ない — 人が書いた注意点を AI が外した根拠を、教員が確かめられない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "教員が AI の書き換えの案を受け取り、何が変わったかとその根拠を確かめてから保存（または所有者に渡す）する"
  layers: [lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [review]
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
    接続軸 information: 書き換えの grounding（論理ブロックの要約）は LLM には渡るが、教員の画面（右ペインの根拠リンク・申し送り）には
    同じ文が出ず、申し送りが参照する根拠を教員側から引けない。統制軸 review: 注意点の取り下げは学習者に届く内容を減らす変更で、人の確認が
    要る種類なのに、確認の材料が画面に無い。処理軸 none（申し送り自体は理由付きで出ている）。構造軸 none。
generalization:
  level: repo_pattern
  general_form: "AI が人の書いた記述を外した理由として、人の画面に出ない材料を挙げる"
pattern: ai-decides-instead-of-human
discovery:
  perspective: [reproduction]
  note: >-
    共有教員 te-03 が t0 で書き換えを頼み、申し送りの取り下げの根拠を「その要約は私の画面には見えない…原文の抜粋で照らすまでは信じきれない」と記した。
    run: uxsim/runs/c-astro-r25-studio-r5/20261027T071438Z-61813 seq6・8。
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: "書き換えの規則 TOPIC_DRAFT_HANDOFF_RULE に、外す・取り下げる根拠は現在の下書きか根拠候補の見出し・要約・式からの逐語の引用で添え、画面に出ない材料（論理ブロックの要約など）を挙げないことを足した。応答は lecture_studio_state.qualify_handoff_basis が決定論で検査し（照合先 teacher_visible_texts = 前の版の下書き・題名・概要・build_topic_evidence_items の見出し・要約・式・図題）、照合できない引用・引用の無い取り下げの項目には HANDOFF_UNVERIFIED_BASIS_NOTE を付ける（項目は落とさない）。"
  landed_in:
    - "backend/core/course_content_builder.py"
    - "backend/core/lecture_studio_state.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
    - "backend/tests/test_lecture_studio_round25_r5.py"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: "照合の条件をプロンプトの善意に任せず、応答を受けた側で画面に出る材料の集合 1 つ（teacher_visible_texts）と必ず照らし、照合できないときは「根拠の表示なし」と閉じる側に倒した。固有の差分: 項目は落とさず事実文を添える（取り下げの判断は教員）。"
  verification:
    methods: [guardrail]
    unverified:
      - "実モデルが規則どおり逐語の引用を添えるかは見ていない"
      - "取り下げを言う語の検出は語の一覧による — 一覧に無い言い方の取り下げは検査に掛からない"
      - "右ペインに出ない根拠リンクの項目（表示の上限より後ろ）も照合先に入る"
related: [IK-0948, IK-0978]
view_of: []
history: []
---

## 課題

症状: 申し送りが注意点を取り下げた理由として、画面に無い材料を挙げる。教員は根拠を確かめられないまま案を受け取る。

原因: 書き換えの grounding の材料と、教員の画面に出す根拠の一覧が一致していない。

## 発見の観点

教員の通し受講（reproduction）で、申し送りの文と右ペインの中身を突き合わせた。

## 解決の観点

解決済み（2026-10-27・第 5 波）。申し送りに挙げてよい根拠を教員の画面で読める材料に限り、照合できない項目には「根拠の表示なし」を添える。

（起票時の案）案: 申し送りが注意点・本文を外す理由に材料を挙げるときは、その材料の逐語の抜粋（と出所）を申し送りに添えるようプロンプトと検証で求める
（逐語でなければその項目を「確認が要る取り下げ」と書く）。または注意点の取り下げを案に入れず、申し送りの提案に留める。

## 一般化

AI が人の記述を減らす変更は、確かめられる根拠を同じ画面に出す。確かめ方: 注意点を外す書き換えを起こし、申し送りの根拠が画面の文と逐語で照合できることを見る。

第 6 周の再演（2026-10-27）: **GONE**。共有教員 te-04 の書き換えの申し送りで、教員の画面の引用が無い取り下げの項目に「（根拠の表示なし: 教員の画面で読める箇所の引用がありません）」が付き、頭脳は「根拠の無いものが分かる」と記した。run: uxsim/runs/c-astro-r25-studio-r6/20261027T122747Z-6889 seq6。
