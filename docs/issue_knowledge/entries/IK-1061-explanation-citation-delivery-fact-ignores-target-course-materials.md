---
id: IK-1061
title: "説明を別コースへ引用すると「引用先のコースの学生にも、この説明が届きます。」と言うが、届くかの判定は説明の公開条件だけを見て、引用先のコースの教材がその部品（の論文）を含むかを見ない — 部品を含まないコースへの引用でも届くと言い、そのコースの学生の ⚓ からは説明が読めない"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/hierarchical_context_explanation_design.md"
feature_context:
  realizing: "教員が他の教員の説明を自分のコースへ引用し、そのコースの学生に部品の説明として届ける"
  layers: [endorsement_c, lecture_studio, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
    governance: [completion]
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
    接続軸 target: routes/theory_components.py cite_explanation は _ensure_editable(citing_course_id) だけを見て引用を受け、
    届くかの判定 _explanation_delivered_to_course は LEARNER_EXPLANATION_SCOPE_SQL（承認・共有・著者以外の査読）だけを評価する。
    共有かつ著者以外の査読済みの説明は、どのコースを渡しても真になり、引用先のコースの sources にその部品の論文があるかを
    見ない。学生は部品の ⚓ から説明を読むので、部品を含まないコースでは届かない。統制軸 completion: 「届きます」という
    完了の文が、学生が実際に読める状態ではなく公開条件という代理で決まっている。処理軸・構造軸 none: 判定の SQL と
    文の受け皿はあり、引用先の教材の範囲を条件に入れていないだけ。
generalization:
  level: repo_pattern
  general_form: "届く先への到達を、届ける物の公開条件だけで判定し、届く先がその物を参照する経路を持つかを確かめずに「届きます」と言う"
pattern: completion-defined-by-proxy
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    第 25 周 面 A 第 8 周、te-02 が te-04 の説明（TeX 版の部品「TOV residual loss at collocation points」・コース 1bad7d9f）を
    コース 6be229e1 へ引用し「引用しました。 引用先のコースの学生にも、この説明が届きます。」を受けた（uxsim/runs/c-astro-r25-share-r8/20261027T224703Z-1148 seq5）。
    直後に 6be229e1 の学生 st-10 が ⚓ の部品の説明を開くと「この部品には、承認済みの説明がまだありません。」（uxsim/runs/c-astro-r25-share-r8/20261027T224858Z-2266 seq9）。
    引用前の st-07（uxsim/runs/c-astro-r25-share-r8/20261027T224047Z-97606 seq9）と同じ。DB で 6be229e1 の sources は PDF 版の論文だけで、説明の部品は TeX 版の論文にある。
resolution:
  perspective: [carry_through, fail_closed]
  note: >-
    引用の応答の届く判定に「引用先のコースの sources（list_course_source_document_ids — 学生の説明 API が部品を解決する集合と同じ）が部品の論文を含むか」を足した（theory_components._citing_course_has_component_document）。含まなければ CITATION_NOT_IN_COURSE_FACT「ただし、引用先のコースの教材にはこの部品の論文が含まれていないため、そのコースの学生の画面にはこの説明は出ません。」、確かめられなければ CITATION_COURSE_UNCHECKED_FACT（届くとは言わない）。公開条件の判定（LEARNER_EXPLANATION_SCOPE_SQL）は不変。確定リンクで結ばれた別の論文の部品は学生の説明 API が辿らないので「届く」に数えない。引用先の選択肢に部品を含むかを添える案は入れていない。
  landed_in:
    - "backend/core/explanation_governance.py"
    - "backend/api/routes/theory_components.py"
    - "backend/tests/test_share_round25_r8_fixes.py"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: "届く判定を学生側の解決と同じ集合（コースの sources）で評価し、取れなければ閉じる。固有の差分: 条件は SQL ではなく route の集合の積で、未確認を第 3 の文で言う。"
  verification:
    methods: [guardrail]
    unverified:
      - "部品を含むコースと含まないコースへ引用して outcome_fact を読み分ける通し（第 9 周 te-04 × (b2)）"
      - "版ピン中の学生が見るスナップショットの sources と HEAD の sources の差は見ていない"
related: [IK-1023, IK-0924, IK-0407, IK-0920]
view_of: []
history: []
---

## 課題

症状: 2 人目の査読が済んだ説明を別コース 6be229e1 へ引用すると「引用先のコースの学生にも、この説明が届きます。」と出るが、
そのコースの学生が ⚓ の部品の説明を開くと「承認済みの説明がまだありません。」。引用の前後で学生の画面は変わらない。

原因: 引用の受付と「届く」の判定が、引用先のコースが部品を含むかを見ていない。

## 発見の観点

再現: 同じコース・同じトピックの ⚓ を、引用の前（st-07）と後（st-10）に開いた。データの確認: 説明の部品の論文と引用先のコースの sources を突き合わせた。

## 解決の観点

引用先のコースの sources に部品の論文が含まれるかを届く判定に足した（含まない・不明は届くと言わない）。 元の見立て: 届く先が届ける物を参照する経路（sources・同一性リンク）を持つかを判定に入れる。

## 一般化

「届きます」の文は届く先の側の経路で決める。公開条件は必要条件にすぎない。型 completion-defined-by-proxy。確かめ方は、部品を含まないコースへ引用し、その学生の画面を開くこと。

## 第 9 周の再演（第 25 周・面 A 第 9 周、2026-10-31・judge_share_r9）

run では未評価。te-04 は唯一の教員の説明の著者で、共有 OFF の標準の説明を引用しようとして 403（NEW IK-1077）— 引用自体が成立しなかった。引用先も 6be229e1 のまま（1bad7d9f は引用元なので選べない — harness H56）。コード（routes/theory_components.py cite_explanation → _citing_course_has_component_document → explanation_governance.citation_delivery_fact の course_has_part の分岐）は入っていることを読んで確かめた。run: uxsim/runs/c-astro-r25-share-r9/20261031T015134Z-37470（seq6）。

## 第 10 周の再演（第 25 周・面 A 第 10 周、2026-10-31・judge_share_r10）

run で初評価・形を変えて STILL → IK-1126 に切り出し。te-02 が共有 ON・査読済みの江口の説明を 0d70b5d0 へ引用し「引用しました。 引用先のコースの学生にも、この説明が届きます。」。0d70b5d0 の sources には部品の論文（PINN TeX 版）があるので判定は真だが、7 トピックのどれにも部品の ⚓ が無い（砂場 DB）。run: uxsim/runs/c-astro-r25-share-r10/20261031T053828Z-70459（seq6）。
