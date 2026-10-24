---
id: IK-0926
title: "説明の著者が自分の説明を支持（承認する）でき「暫定的に1名が承認」の 1 名に数えられ、同じ著者が自分で査読承認して共有 ON にすれば、他の教員の目を通さずに同じ論文を使う別のコースの学生にも届く"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員の独自解釈を、他の教員の支持と査読を経て、コースをまたいで学生に届ける"
  layers: [endorsement_c, learner_experience_b, indicator_catalog]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [review]
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
    統制軸 review: routes/theory_components.py:4947-5010 endorse_explanation は著者本人の支持を拒まず（通知だけを自己承認で省く :5000-5002）、集計ビュー
    component_explanation_endorsement_summary は著者の支持も数える。patch_explanation（:4866-4879）は著者に review_status='teacher_approved'
    を許す。学生への配信条件（routes/learning.py:8593-8599）は「査読承認 かつ（標準 or 共有 ON or 書いたコース）」で、査読者が著者かを見ない。接続軸 condition:
    「他の教員が確かめた」の条件が配信条件と支持の数のどちらにも入っていない。改訂原則 4（制度指標は定義を公開）に照らすと、学生向けの段階ラベルの定義（誰を数えるか）が宣言されていない。
generalization:
  level: repo_pattern
  general_form: "作成者自身の承認が承認の数と配信の弁の両方を満たし、第三者の確認を経たことを意味するはずの表示と条件が自己確認で成立する"
pattern: gate-position-wrong
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員 te-02 が自分の説明に査読承認と［承認する］を押し「暫定的に1名が承認」になった（uxsim/runs/c-astro-r25-share-r3/20261023T033629Z-52380 seq5-6）。続く te-05
    の支持で「2名」になったが、うち 1 名が著者であることは画面に出なかった（uxsim/runs/c-astro-r25-share-r3/20261023T033729Z-59479 seq4・seq6）。配信条件の SQL
    を突き合わせた。
resolution:
  perspective: [required_argument]
  note: >-
    指揮者の判断（推奨どおり）で実装した。① 支持の人数から著者本人と取り消し済みを除く（行は書く・記録は残す）— 正本 core/explanation_governance.py::COUNTED_ENDORSEMENT_SUMMARY_SQL、集計ビューを読んでいた 5 箇所（theory_components / learning / atlas_state / deliberation/positioning / doubt/ledger_builder）をこの JOIN に置き換え。② 共有 ON の説明が書いたコース以外の学生に届く条件に「最新の査読承認の記帳者（theory_review_events.changed_by）が著者でない」を足す（記帳が無いときは届けない側）。書いたコースと標準の説明の条件は不変。③ 制度指標カタログに explanation-support-label（著者を除く・取り消しを除く・宛先・非利用）を宣言し、学習者向け説明一覧に indicator_id、学生マニュアルに節 {#reader-labels}。
  landed_in:
    - "backend/core/explanation_governance.py"
    - "backend/api/routes/learning.py"
    - "backend/api/routes/theory_components.py"
    - "backend/core/atlas_state.py"
    - "backend/core/deliberation/positioning.py"
    - "backend/core/doubt/ledger_builder.py"
    - "backend/core/indicator_catalog.py"
    - "backend/tests/test_share_round25_explanations.py"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: "「著者以外が確かめた」の条件を配信の問い合わせの内側（SQL の正本 1 つ）で強制し、査読者が分からないときは届けない側に倒した。固有の差分: 書いたコースの学生への配信は著者の査読のままにした（コースの担当の弁）。"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB での相関サブクエリ（NON_AUTHOR_REVIEWED_SQL）"
      - "査読の PATCH は今も著者か SYSTEM_ADMIN だけで、普通の教員は他人の説明を査読できない — コースをまたいで届くのは実際には SYSTEM_ADMIN が査読した説明だけ（他の教員が査読する経路を作るかは未決）"
      - "査読の後に著者が本文を書き換えても査読は失効しない"
related: [IK-0924, IK-0861]
view_of: []
history: []
---

## 課題

症状: 著者が自分の説明を支持して 1 名と数えられ、自分で査読承認して共有すれば別のコースの学生に届く。原因: 支持の集計と配信の条件が支持者・査読者が著者かを見ない。

## 発見の観点

教員ペルソナ 2 人の通し受講（reproduction）と配信条件の点検（trace_walk）。

## 解決の観点

是正済み（第 3 波・指揮者の判断に従う）。支持の人数から著者を除き、コースをまたぐ配信に著者以外の査読承認を要件にし、学生向けの段階ラベルの定義を制度指標カタログに宣言した。

## 一般化

自己確認で弁が通る型。確かめ方は、著者が支持・査読した説明が別のコースの学生に届かず、支持の段階ラベルが著者を数えないことを、著者・他の教員・学生の 3 人で砂場で見ること。
