---
id: IK-0923
title: "部品の「標準の説明」は初めて説明一覧を開いたときの部品の要約の写しで、その後の再解析で要約が直っても更新されないため、旧い機械文（「Reusable theory unit (define_relation) within … covers 2 equation step(s).」）が承認の対象として残り続ける"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が部品の標準の説明を読んで承認し、学生に届ける"
  layers: [endorsement_c, knowledge_objects, lecture_studio]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [version]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 version: routes/theory_components.py:4703-4748 _ensure_standard_explanation は kind='standard' の行が無いときだけ summary を
    body に写し、行があれば何もしない。知識オブジェクト層の再解析（sync_live_rows）は同じ UUID の部品の summary を更新するが、説明の行は写しのまま残る。砂場では IK-0755
    の是正（機械文を作らない）より前の要約が残っており、第 3 周の当日に初めて開いたことで旧い機械文が説明の行に固定された（created_at が周の当日）。
generalization:
  level: repo_pattern
  general_form: "正本（要約）から最初の参照時に作った写しを、正本の更新後も更新せずに配る"
pattern: stale-derivative-served
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    教員 te-02 が原稿スタジオで説明一覧を開くと標準の説明が英語の機械文で、te-05 も同じ文を読んだ。生応答の created_at と _ensure_standard_explanation を突き合わせた。run:
    uxsim/runs/c-astro-r25-share-r3/20261023T033629Z-52380 seq2・uxsim/runs/c-astro-r25-share-r3/20261023T033729Z-59479 seq3。
resolution:
  perspective: [state_transition]
  note: >-
    routes/theory_components.py の _ensure_standard_explanation は、標準の説明の行が既にあって本文が部品の現在の要約と違えば _refresh_standard_explanation_body で本文と題名を追随させ、旧本文・旧題名は監査行（action=standard_body_refreshed・metadata.previous_body / previous_title）に残す（P4）。査読の状態と支持は消さず、更新より前の有効な支持があるか更新後に査読承認が無いときは一覧の行に body_note「…既にある査読・支持は前の本文に対するものです。」を付ける。人が本文を書き換えた行（action=body_edit）は追随させない。
  landed_in:
    - "backend/api/routes/theory_components.py"
    - "backend/tests/test_share_round25_explanations.py"
  principles:
    - principle: sync-by-stable-key-and-protect-human-decisions
      use: consulted
      note: "派生物（標準の説明）を正本（部品の要約）に追随させつつ、人の判断（査読・支持）と人が書き換えた本文は保護した。固有の差分: 旧本文は行ではなく監査に残し、判断が前の本文に対するものであることを事実文で示す。"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB での本文更新の時刻比較"
      - "砂場の旧い機械文の要約は再解析（IK-0755）か snapshot の取り直しで直す — 表示側の追随だけでは要約そのものは直らない"
related: [IK-0755, IK-0891]
view_of: []
history: []
---

## 課題

症状: 標準の説明が「Reusable theory unit … covers 2 equation step(s).」のままで、教員が読めず承認できない。原因: 説明の行は初回参照時の要約の写しで、要約の更新に追随しない。

## 発見の観点

教員ペルソナの通し受講（reproduction）と生応答の時刻の点検（data_inspection）。

## 解決の観点

是正済み（第 3 波）。標準の説明は読み時に部品の現在の要約へ追随し、旧本文は監査に残し、既存の査読・支持は前の本文に対するものだと事実文で示す。

## 一般化

派生物が正本に追随しない型。確かめ方は、説明を開いた後に部品を再解析して要約を変え、説明一覧を開き直して標準の説明が新しい要約になることを砂場で見ること。
