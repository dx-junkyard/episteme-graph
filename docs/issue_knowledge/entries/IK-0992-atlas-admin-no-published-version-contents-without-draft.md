---
id: IK-0992
title: "凍結して下書きが無くなると、分野の地図タブに公開中の版の項目・関係の一覧が出ない — 加えた項目と関係が公開版に入ったことを、候補から消えたことと履歴の一文からしか確かめられない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_binding_lifecycle_design.md"
feature_context:
  realizing: "教員が凍結した直後に、公開中の版に何が入ったかを分野の地図タブで確かめる"
  layers: [field_atlas_s, frontend_admin_ui]
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
    接続軸 information: admin.js renderState は下書きがあるときだけ editor に骨格を入れて renderPreview（領域・概念の図と IK-0886 の関係の節）を描き、下書きが無いと draftArea を隠す。公開中の版の骨格は state.frozen.skeleton として受け取っているのに、要約カードと来歴・履歴しか描かない。統制軸 review: 凍結は取り消せない確定で、確定の直後に結果を読み返す面が無い。構造・処理軸 none。
generalization:
  level: repo_pattern
  general_form: "確定の前は内容のプレビューがあるのに、確定の後は確定した内容そのものを読み返す面が無い"
pattern: last-mile-missing
discovery:
  perspective: [reproduction, trace_walk]
  note: "凍結の直後に教員が『公開中の 2026.2 の項目と関係の一覧が出ず、公開版に入ったことは候補から消えたことと履歴から推すしかない』と記した。admin.js renderState と renderPreview の条件を確かめた。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T070623Z-23509 seq 16、uxsim/runs/c-astro-r25-atlas-r5/20261027T071308Z-52116 seq 2。"
resolution:
  perspective: [carry_through]
  note: >-
    下書きが無いとき、分野の地図タブに「公開中の版（地図 版 X）の項目と関係を見る（読み取り専用）」の折りたたみを出し、既存の AtlasDraftPreview と関係の節（_appendDraftRelationsSection の published モード）で公開中の版を描く（admin.js renderPublishedView）。下書きがあるときは編集面のプレビューが同じ部品を使うので出さない。アンカー atlas.published-contents + マニュアル節 + ADMIN_UI_ANCHORS の 3 点。第 6 波（2026-10-27）で残りを是正: 下書きがある間も同じ折りたたみを出し、見出しを「公開中の版（地図 版 X）を見る（読み取り専用 — 下で編集している次版の下書きとは別です）」にして下書きの編集と区別する（閉じた状態・描画部品は同じ・ID を持たない部品なので下書きのプレビューと衝突しない）。
  landed_in:
    - "frontend/public/js/admin.js"
    - "frontend/public/admin.html"
    - "backend/core/help_kb/admin_ui_anchors.py"
    - "docs/manual/teacher/17-admin-atlas.md"
    - "backend/tests/test_admin_help_ui_anchors.py"
    - "backend/tests/test_atlas_review_round25_r5.py"
    - "backend/tests/test_atlas_review_round25_r6.py"
  verification:
    methods: [guardrail]
    unverified:
      - "管理画面での描画（折りたたみを開いたときの図と関係の節）はブラウザで確かめていない"
      - "前の版に無かった関係に『この版で加わった』を添えることはしていない（前の版の骨格を状態に持たないため）"
      - "第 6 波: 下書きがある間の折りたたみ（見出しで下書きと区別）もブラウザでは確かめていない"
related: [IK-0886, IK-0963]
view_of: []
history: []
---

## 課題

症状: 凍結 2026.2 の後、分野の地図タブには公開中の版の中身（領域・項目・関係）が無い。凍結の影響の画面で確かめた「次版で加わる項目 /
関係」が公開版に入ったことを、教員は一覧で読み返せない。

原因: 内容の描画が下書きの編集面にだけ結びついている。

## 発見の観点

凍結が初めて成立した周に教員が記した（reproduction）。描画の条件を確かめた（trace_walk）。

## 解決の観点

第 5 波（2026-10-27）で解決。公開中の版を同じ部品で読み取り専用に描く。

## 一般化

確定の後にも確定した内容を同じ部品で読み返せるようにする。確かめ方: 下書きの無い分野で地図タブを開き、公開中の版の項目と関係が読めることを見る。



## 第 6 周の再演（第 25 周・面 C 第 6 周、2026-10-27・campaign c-astro-r25-atlas-r6）

GONE（下書きが無いとき）。凍結直後と開き直しで「▸ 公開中の版（地図 版 2026.3）の項目と関係を見る（読み取り専用）」が出た（te-03 の下書きなしの画面にも 2026.2 で出た）。残り（軽）: 下書きがある間はこの折りたたみが出ず（是正の範囲どおり）、下書きと公開版を並べて読む手が無い。差分は凍結の影響の「下書きの領域・概念は現在の公開版と同じで、関係（辺）が変わります」「次版で加わる関係: …」で読めるので、起票はしない。run: uxsim/runs/c-astro-r25-atlas-r6/20261027T122716Z-6544 seq 2 / 18 / 19。

## 第 6 波の是正（第 25 周・面 C、2026-10-27）

第 6 周の残り（下書きがある間は折りたたみが出ない）を是正した。下書きがある間も「公開中の版（地図 版 X）を見る（読み取り専用 — 下で
編集している次版の下書きとは別です）」の折りたたみを出し、下書きと公開版を並べて読めるようにした（admin.js renderPublishedView・
`ATLAS_PUBLISHED_VIEW_WITH_DRAFT_TEXT`）。ガードレール `test_atlas_review_round25_r6.py::TestPublishedViewWithDraft`。

## 第 7 周の再演（第 25 周・面 C 第 7 周、2026-10-28・campaign c-astro-r25-atlas-r7）

GONE（残り）。下書きのある間も「▸ 公開中の版（地図 版 2026.3）を見る（読み取り専用 — 下で編集している次版の下書きとは別です）」が出て、下書きのプレビュー（領域・項目・関係の文）と並べて読めた。凍結で下書きが無くなると見出しは「…の項目と関係を見る（読み取り専用）」に戻る。run: uxsim/runs/c-astro-r25-atlas-r7/20261027T171656Z-52459 seq 2、uxsim/runs/c-astro-r25-atlas-r7/20261027T171933Z-54352 seq 2 / 12。
