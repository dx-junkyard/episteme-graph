---
id: IK-1056
title: "学習者の地図の右のパネルで、教員の承認の記録が無いことを状態によって言い分ける（暗黙の前提は「このコーパスの中では、教員の承認の記録がありません」・それ以外は「承認記録なし」）"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-28
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/field_atlas_overlay_spec.md"
feature_context:
  realizing: "学生が分野の地図で概念を選び、右のパネルの承認の行を読む"
  layers: [field_atlas_s, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 wording: core/atlas_state.py endorse_line_for は、承認の記録が無いとき status が assumed なら label_vocab.ATLAS_ENDORSE_NONE_ASSUMED_LINE
    （閉世界の言い方）を、それ以外では「承認: 承認記録なし」を返す。同じ事実（このコーパスの中に教員の承認の記録が無い）が状態で 2 つの言い方になり、
    後者は閉世界（SL1）の「このコーパスの中では」を欠く。IK-1018 の是正が assumed の枝だけを置き換えた。構造軸 none・接続軸 none（入力の取り違えは
    無い）・統制軸 none。
generalization:
  level: repo_pattern
  general_form: "同じ不在の事実を言う文を、条件分岐の一つの枝だけで直し、他の枝が旧い言い方のまま残る"
pattern: rule-enforced-per-surface
discovery:
  perspective: [symptom_report, trace_walk]
  note: "st-03 が暗黒エネルギー（暗黙の前提）を選んだ右のパネル（run uxsim/runs/c-astro-r25-atlas-r8/20261027T224229Z-98423 seq 8）と、st-01 が分子雲（未訪問）を選んだパネル（同 20261027T224409Z-99225 seq 8）を並べ、atlas_state.endorse_line_for の枝と突き合わせた。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    core/atlas_state.endorse_line_for の記録なしの枝を状態に依らず label_vocab.ATLAS_ENDORSE_NONE_LINE（閉世界の 1 文・
    ATLAS_ENDORSE_NONE_ASSUMED_LINE は互換の別名）に寄せ、状態キャッシュに残る旧い「承認: 承認記録なし」は learner_endorse_line が
    読み時に置き換える（地図の右パネル・/node・学習チャットの画面文脈の 3 経路が同じ関数を通る）。ガードレールは atlas_state に
    旧い単独の文が残らないこと・各状態で同じ文になることを固定する。
  landed_in:
    - "backend/core/atlas_state.py"
    - "backend/core/label_vocab.py"
    - "backend/tests/test_atlas_review_round25_r8.py"
    - "backend/tests/test_atlas_review_round25_r9.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場で状態の違う概念（暗黙の前提・未訪問）を選び、承認の行が同じ文であること（次周の再演）"
related: [IK-1018]
view_of: []
history: []
---

## 課題

症状: 暗黙の前提の概念のパネルは「承認: このコーパスの中では、教員の承認の記録がありません」、未訪問の概念（分子雲）のパネルは
「承認: 承認記録なし」。どちらも C層の承認が 0 件という同じ事実。学生には言い方の違いが状態の違いの意味を持つように読める。

原因: IK-1018（第 6 波）が承認の行から内部の欄名を外すとき、assumed の枝だけを閉世界の文に置き換え、最後の既定の枝は旧い短い文のまま残った。

## 発見の観点

第 8 周の 2 人の学生が別の状態の概念を選んだパネルを並べて読んだ。

## 解決の観点

記録なしの枝を 1 つの閉世界の定数に寄せ、旧い文は読み時に置き換える（第 8 波）。`endorse_line_for` の記録なしの枝を 1 つの定数（閉世界の言い方）に寄せ、ガードレールで「承認記録なし」の単独の言い方が学習者に出ないことを固定する。

## 一般化

型 rule-enforced-per-surface: 言い方の規則を分岐の一枝にだけ当てる。確かめ方: 承認の記録の無い概念を状態ごとに 1 つずつ選び、承認の行が同じ文であること。

## 第 9 周の再演（2026-10-31・judge_atlas_r9）

**GONE（同じ概念で前後比較）**。st-01 が選んだ分子雲（未訪問・run uxsim/runs/c-astro-r25-atlas-r9/20261031T015432Z-39319 seq 8）の承認の行は「承認: このコーパスの中では、教員の承認の記録がありません」。第 8 周の同じ学生・同じ概念は「承認: 承認記録なし」。st-03 の宇宙膨張（未訪問・20261031T015252Z-38248 seq 8）も同じ文で、/api/atlas の nodes の endorse（応答の先頭）も未訪問の各概念が同じ文。場面が 1 概念で終わるため「暗黙の前提」の概念との同時比較は今周も無いが、第 8 周に暗黙の前提（暗黒エネルギー）が出していた文と、今周の未訪問の文が一致した。
