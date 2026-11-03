---
id: IK-1126
title: "説明を別のコースへ引用したときの「引用先のコースの学生にも、この説明が届きます。」は、引用先コースの sources に部品の論文があるかだけで決まり、その部品の ⚓ がコースのどのトピックにも無い（学生が説明へ辿り着く画面の道が無い）ときも「届きます」と言う — 第 10 周の引用先 0d70b5d0 は PINN の TeX 版を sources に持つが 7 トピックとも暗黒エネルギーの内容で、非公開のコースでもある"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
feature_context:
  realizing: "教員が説明を別のコースへ引用し、その結果その学生の画面に何が出るのかを正しく知る"
  layers: [endorsement_c, lecture_studio, learner_experience_b]
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
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 condition: routes/theory_components.py _citing_course_has_component_document は list_course_source_document_ids（sources の document）
    に部品の document があるかで真偽を返し、core/explanation_governance.citation_delivery_fact はそれと査読状態だけで「届きます」を選ぶ。docstring
    自身が「学生は部品の ⚓（コースの教材の論文の部品）からしか説明を読めない」と書くが、判定はトピックの ⚓（content_blocks の component の埋め込み・
    出典タブの論理要素）を見ない。砂場 DB: 0d70b5d0 の sources に arXiv-2605.31198v1.tar.gz（ce667c63・部品 119657ae の document）がある・
    topics t0〜t6 のどれにも comp_003 / 119657ae の参照が無い・is_published=false。
generalization:
  level: repo_pattern
  general_form: "「届く」の判定を到達の必要条件（資料に含まれる）で代用し、実際に辿り着く画面の道（その要素が教材に置かれているか）を見ない"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    第 25 周 面 A 第 10 周 te-02 (b2) が江口の説明を自分のコース 0d70b5d0 へ引用し「…届きます。」（uxsim/runs/c-astro-r25-share-r10/20261031T053828Z-70459
    seq6）。IK-1061 の是正（sources の論文の有無）の run での初評価で、引用先のトピックを砂場 DB で読んだ。
resolution:
  perspective: [explicit_contract, canonical_source]
  note: >-
    「届く」の判定を sources（論文の有無）からトピックの ⚓ の粒度へ下げた。route の _citing_course_has_component_document（IK-1061 と同じ関数）が {has_part, anchored, visibility} を返し、anchored は core.course_content_builder.course_has_component_anchor（学習画面の ⚓ と同じ材料 = build_topic_evidence_items の component 項目・章ネストも走査・部品の DB UUID ∪ legacy_ids で突合し、⚓ が論文を持つときは論文も一致）。論文はあるが ⚓ が無いと CITATION_NO_ANCHOR_IN_COURSE_FACT「…トピックにはこの部品の ⚓ が置かれていないため、そのコースの学生の画面からはこの説明を開けません（引用の記録は残ります）。」、確かめられなければ CITATION_ANCHOR_UNCHECKED_FACT（届くと言わない）。公開（全体）でないコースは「引用先のコースの公開範囲は「非公開（自分のみ）」です。」を添える（語は label_vocab.SHARED_SCOPE_LABELS・受講者の有無は言わない）。
  landed_in:
    - "backend/core/course_content_builder.py"
    - "backend/core/explanation_governance.py"
    - "backend/api/routes/theory_components.py"
    - "backend/tests/test_share_round25_r10_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "⚓ のあるコースと無いコースへ同じ説明を引用した文の比較を砂場で（第 11 周 te-02 × (b2)）"
related: [IK-1061, IK-1023, IK-0920]
view_of: []
history: []
---

## 課題

症状: 部品の ⚓ が 1 つも無いコースへの引用で「引用先のコースの学生にも、この説明が届きます。」。学生がその説明を開く道は画面に無い。

原因: 届く判定が sources（論文の有無）の粒度で、トピック（⚓ の有無）の粒度でない。

## 発見の観点

再現: te-02 の (b2)。データ点検: 0d70b5d0 の sources・topics・公開状態を砂場 DB で読んだ。

## 解決の観点

第 25 周 第 10 波で是正（resolution.note）。

## 一般化

「届く」「見える」を言う事実文は、利用者が実際に辿る画面の道で判定する。型 extent-decided-by-proxy-signal。確かめ方は、部品の ⚓ があるコースと無いコースへ同じ説明を引用して文を比べること。

## 第 11 周の再演（第 25 周・面 A 第 11 周、2026-10-31・judge_share_r11）

GONE。te-02 が第 10 周と同じ江口の説明を同じ引用先 0d70b5d0 へ引用し、「引用しました。 ただし、引用先のコースのトピックにはこの部品の ⚓ が置かれていないため、そのコースの学生の画面からはこの説明を開けません（引用の記録は残ります）。 引用先のコースの公開範囲は「非公開（自分のみ）」です。」（uxsim/runs/c-astro-r25-share-r11/20261031T102323Z-3501 seq6）。⚓ のあるコースへの引用の文は今周は見ていない。
