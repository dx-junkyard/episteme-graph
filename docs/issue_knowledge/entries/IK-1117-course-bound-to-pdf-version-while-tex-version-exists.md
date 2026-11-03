---
id: IK-1117
title: "固定コースが同じ論文の PDF 版の document を出典にしたまま凍結されているため、TeX 版が取り込まれていても教材の式の大半に［AI復元］の印が付く（印は出典の記録どおりで正しいが、より良い版があることを所有者も学習者も知らされない）"
status: open
recorded_at: 2026-10-31
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "TeX 版で取り込み直した論文を、既存コースの教材・講義・チャットに原文どおりの式として届ける"
  layers: [course_builder, versioning_v, learner_experience_b]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [version]
    governance: [completion]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: low
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定（砂場 DB の読み取りと、uxsim の api-server で `core.equation_reconstruction._load_records` を読み取り実行）:
    固定コース 6be229e1 の sources は PINN 論文の **PDF 版** document 7ef9e487（source_url arxiv.org/pdf/2605.31198）を指し、
    t11 の material_chunk_ids もこの document のチャンク。同じ論文の TeX 版 document ce667c63（arxiv.org/src）は別に取り込まれており、
    同名の別コース 1bad7d9f だけがそちらを出典にしている。7ef9e487 の生きた式の記録は 50 件中 45 件が `equation_latex_is_reconstructed`
    = True（PDF 経路の復元）、ce667c63 は 28 件中 0 件。よって 6be229e1 の L_TOV / N_TOV の［AI復元］は印の付与の誤りではなく
    出典の記録どおり（印の判定 `overlay_topic_equation_reconstruction` はトピックの論文の記録を引いている）。製品頭脳 A の
    「TeX 版でも復元印が多い」は、同名の 2 コース・2 版の取り違え（IK-0407 / IK-0956 と同族）。課題は、TeX 版が取り込まれても
    コースの出典を付け替える道・知らせが無く、再凍結（所有者の操作）を促す事実がどこにも出ないこと。接続軸 version（同じ論文の
    版違いの document の間に対応が無い）。統制軸 completion（「TeX で取り込み直した」が「受講者に原文の式が届く」に至る手続が見えない）。
generalization:
  level: repo_pattern
  general_form: "同じ資料のより良い版が取り込まれても、既に凍結した派生物は古い版を指したまま配信され、版の対応と付け替えの道が所有者に示されない"
pattern: same-name-different-referents
discovery:
  perspective: [data_inspection, symptom_report]
  note: >-
    第 25 周 面 D 第 9 周の製品頭脳 A（BRAIN_NOTES_product_A_r9.md 4299: 「教材の式 L_TOV・N_TOV がほぼ全て［AI復元］で、TeX 版でも
    復元印が多い疑い」）を、第 9 波の fixer が砂場 DB の読み取りで確かめた。
resolution:
  perspective: [pending]
  note: >-
    未着手（印の付与は正しいので印は変えない）。是正案: ①原稿スタジオのコース見出し（所有者）に「このコースの出典『…』には、同じ論文の
    別の版（TeX）が取り込まれています。式は出典の版の記録で表示しています（AI 復元を含む）。」の事実文と、コースビルダーで出典を付け替えて
    凍結し直す道案内（自動では付け替えない — 凍結した本文の ⚓ と式 ID が版で変わるため、オーナー判断の範囲）。同じ論文の判定は
    arXiv ID の正規化一致（`paper_discovery.schema.normalize_arxiv_id`）を使う。②uxsim の砂場マニフェストで、TeX 経路の検証には TeX 版を
    出典にしたコース（1bad7d9f）を使うと明記する。
  landed_in: []
related: [IK-0407, IK-0956, IK-1035, IK-0895]
view_of: []
history: []
---

## 課題

症状: TeX 版で取り込み直した論文なのに、固定コースの教材・講義・チャットで式の大半に［AI復元］の印が付く。

原因: コースは PDF 版の document を出典にしたまま凍結されている（印はその出典の記録どおり）。TeX 版の存在と付け替えの道がどこにも示されない。

## 発見の観点

製品頭脳の所見（symptom_report）を、砂場 DB の sources・チャンクの document・両版の式の記録の読み取りで確かめた（data_inspection）。

## 解決の観点

未着手。印は変えず、版の対応の事実と付け替えの道を所有者に出す。

## 一般化

より良い版の取り込みは、既存の派生物を自動では変えない。変えないなら、古い版を指していることと付け替えの道を派生物の所有者に示す。確かめ方: 同じ論文の 2 版がある状態で、古い版を出典にしたコースの所有者画面にその事実が出るかを見る。

## 第 11 周（第 25 周・面 D 第 11 周、2026-10-31・judge_studio_r11）

STILL。st-08 のチャットで「論文本文の同じ箇所にも同じ形の式が載っていますが、そこにも［AI復元］の印が付いている」（出典 7 = PDF 版のチャンク・製品頭脳 A 4332）。記号 N_c の使われ方が拾えない件（IK-1157）も PDF 版を出典にしたことの帰結の 1 つ。run: uxsim/runs/c-astro-r25-studio-r11/20261031T102933Z-6346 seq14。
