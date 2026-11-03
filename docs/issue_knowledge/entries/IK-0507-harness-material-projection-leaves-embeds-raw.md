---
id: IK-0507
title: ペルソナへの教材の投影が [[FORMULA_N]] だけを画面と同じ規則で解決し、![[kind:id]] 埋め込み（根拠チップ・図・数式・出典カード）を生のまま見せていたため、画面では描かれる要素を「埋め込みが残っている」と報告させうる
status: resolved
recorded_at: 2026-09-29
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17.10
feature_context:
  realizing: ペルソナ通し受講テストで、学習画面のトピック教材をペルソナが読む文に投影する（learning.topic.open）
  layers: [cycle_verification, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: 教材の描画規則の正本は画面の `frontend/public/js/app.js::renderMaterialChunk`（`![[kind:id]]` を
    chunk の evidence_items / formulas / figures で ⚓ チップ・数式・図カード・出典カード・未解決カードに描く）。
    ハーネスの `uxsim/runner/state.py` は `[[FORMULA_N]]` の解決だけを部分的に写しており、同じ事実（教材の見え方）の
    実装が 2 つあって片方だけが育っていた（aggregation）。接続軸: 画面では「⚓ 主張の題」や「未解決 source:… — 取得できません
    でした」と描かれるものが投影では `![[claim:c1]]` の生記法のまま渡り、ペルソナと審判にとって意味が変わる（meaning）。
    処理軸: 投影の個々の処理は書いた範囲では正しい（none・medium）。統制軸: 手続の欠陥ではない（none）。
generalization:
  level: repo_pattern
  general_form: 画面の描画規則をハーネス側で部分的に写すと、写していない記法だけが生のまま観測者に渡り、画面には無い症状を観測者が報告する
pattern: duplicate-canonical-sources
discovery:
  perspective: [boundary_walk, trace_walk]
  note: >-
    第 12 周の「埋め込みがまだ残っている」報告を IK-0506 と並べて調べる途中で、投影（state.py）と画面（app.js）の
    教材描画を突き合わせ、`![[kind:id]]` の扱いだけが写されていないことを確かめた。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    画面の `renderMaterialChunk` を正本とし、投影に同じ規則の文字版 `render_material_text` を置いた（ID 正規化・
    "kind:id" 優先で同一 ID の別 kind へフォールバック・component/claim は ⚓ チップ・equation は formulas →
    evidence の順で無ければ「数式は準備中です」・figure は画像なしカード・source 等は種別付きカード・引けないものは
    画面と同じく kind:id を見せる未解決カード・`[[X]]` は formulas → figures で引けなければ残す・
    `drop_unresolved_embeds`）。種別ラベルは element-vocab.js の KIND_LABELS の逐語ミラー。製品側の未解決を隠さない
    ことをテストで固定した。
  landed_in:
    - uxsim/runner/state.py
    - uxsim/tests/test_pinned_course_and_embeds.py
  verification:
    methods: [guardrail]
    unverified:
      - 実際の砂場の教材 DTO での投影（砂場停止中）
      - KIND_LABELS ミラーと element-vocab.js の一致検査（逐語で写したが一致テストは置いていない）
      - 過去 run の replay（投影が変わるためキャッシュ鍵が変わり、replay は分岐して live に切り替わる）
related: [IK-0506]
view_of: []
history: []
---

## 課題

`learning.topic.open` の投影は、画面と同じく `[[FORMULA_N]]` を chunk の formulas で解決していたが、`![[component:id]]` /
`![[claim:id]]` / `![[source:id]]` / `![[equation:id]]` / `![[figure:id]]` と `[[FIGURE_N]]` は生の記法のまま渡していた。
画面はこれらを ⚓ チップ・数式・図カード・出典カードに描き、引けないものだけを「未解決」カードにする。投影との差のため、
ペルソナは画面に無い「生の埋め込み記法が残っている」を読みうる状態だった。

## 発見の観点

IK-0506 の調査で、ペルソナが報告した記法が画面ではどう見えるかを app.js の `renderMaterialChunk` で確かめ、投影の
実装と境界を突き合わせた（boundary_walk / trace_walk）。

## 解決の観点

画面の描画規則を正本とし、投影はその文字版として同じ分岐を持たせた（canonical_source）。引けない埋め込みは画面と同じく
kind:id を見せる（製品側の未解決を審判から隠さない）。規則の各分岐をテストで固定した（guardrail_fix）。

## 一般化

観測者（ペルソナ・審判・スクリーンリーダー試験など）へ渡す投影が画面の描画を部分的に写すと、写していない記法だけが
生で漏れて、画面には無い症状が報告される。型は `duplicate-canonical-sources`。
