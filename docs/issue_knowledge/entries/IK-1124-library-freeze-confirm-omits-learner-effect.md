---
id: IK-1124
title: "ナレッジライブラリのエントリの［凍結（版発行）］の確認文は「凍結版はパイプラインの解析に使われます。取り消せません（修正は次版で行います）。」だけで、凍結すると確定済みの結び付きが学生の画面（部品の「共通部品として」・記号の吹き出しの概念名）に出るようになることを言わない — 詳細の各区画の注記はそれを言っているのに、取り消せない操作の確認でだけ学生側の効果が抜ける"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "教員が取り消せない凍結を、学生に何が届くようになるかを知ったうえで決める"
  layers: [concept_registry, frontend_admin_ui, learner_experience_b]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [review]
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
    処理軸 wording: admin.js の凍結の確認（message「凍結版はパイプラインの解析に使われます。取り消せません…」）は解析側の効果だけを書く。
    接続軸 information: 凍結が学生の画面に届く条件であること（詳細の「結びついた教材の部品」「結びついた記号」「この概念の扱い」の注記が言う）が、
    確認の文に運ばれていない。統制軸 review: 取り消せない操作の確認で、判断に要る効果の片方が抜ける。
generalization:
  level: repo_pattern
  general_form: "取り消せない操作の確認文が内部処理側の効果だけを書き、利用者の別の当事者（学生）に届く効果を書かない"
pattern: disclosure-not-declared
discovery:
  perspective: [reproduction, invariant_audit]
  note: >-
    第 25 周 面 A 第 10 周 te-03 (c) が凍結の確認を読んでキャンセルした（uxsim/runs/c-astro-r25-share-r10/20261031T052922Z-65582 seq9）。
    頭脳ノート「確認の文に学生に届くことが書かれていない」。詳細の注記と確認文を突き合わせた。
resolution:
  perspective: [vocabulary_table]
  note: >-
    凍結の確認文に学生の画面への効果の 1 文を足した（正本 label_vocab.LIBRARY_FREEZE_LEARNER_EFFECT_NOTE・admin.js は逐語ミラーで一致をテストで固定）。マニュアル #detail-freeze にも同じ事実を書いた。
  landed_in:
    - "backend/core/label_vocab.py"
    - "frontend/public/js/admin.js"
    - "docs/manual/teacher/19-admin-knowledge-library.md"
    - "backend/tests/test_share_round25_r10_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "凍結の確認ダイアログを砂場の画面で"
related: [IK-0983, IK-0954]
view_of: []
history: []
---

## 課題

症状: 凍結の確認が「パイプラインの解析に使われます」だけを言い、学生への効果を言わない。te-03 は記号・地図の判断が未了なので凍結を見送ったが、確認文からは学生に何が変わるかを読めなかった。

原因: 確認の文言が L層の解析用途の時代のまま、概念レジストリで凍結が学生への可視化の弁になった後も更新されていない。

## 発見の観点

再現: te-03 の (c) で［凍結（版発行）］を押し、確認を読んでキャンセルした。原則照合: 取り消せない操作の確認に判断の材料が揃っているか。

## 解決の観点

第 25 周 第 10 波で是正（resolution.note）。

## 一般化

操作の意味が増えたとき（ここでは学生への可視化の弁）は、取り消せない操作の確認文も見直す。型 disclosure-not-declared。確かめ方は、確定済みの結び付きがあるエントリとないエントリで確認文を読むこと。

## 第 11 周の再演（第 25 周・面 A 第 11 周、2026-10-31・judge_share_r11）

GONE。te-03 の凍結の確認文に「凍結すると、この共通部品に確定済みで結び付いた教材の部品・記号が、学生の画面（部品の文脈の「共通部品として」・記号の吹き出しの概念名）に出るようになります。」が入り、頭脳はそれを読んで凍結した（uxsim/runs/c-astro-r25-share-r11/20261031T101202Z-98535 seq11）。
