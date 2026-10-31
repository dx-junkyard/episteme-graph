---
id: IK-0846
title: "同じ論文を PDF と TeX で二度取り込むと、分野の地図の「2 本以上の論文で反復した」候補（カテゴリギャップ・関係の共起）と論文の海の一覧が、1 本の論文を 2 本と数えて立ち・並ぶ"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/category_gap_candidates_design.md"
  - "docs/features/atlas_relation_edges_design.md"
feature_context:
  realizing: "論文の解析から、複数の論文に反復した主題・関係だけを教員のレビュー候補に浮上させ、学習者には閲覧できる論文の厚みを見せる"
  layers: [category_gaps, relation_edges_re, corpus_roaming, knowledge_landscape]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
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
    構造軸 representation: 反復の閾値は設計上「論文」の数（atlas_gaps/schema.py:135-137 MIN_DOCUMENTS_FOR_CANDIDATE・
    atlas_edges/schema.py:27-35 MIN_DOCUMENTS_FOR_EDGE「1論文だけの共起は…」）なのに、数えている単位は documents.id
    （atlas_gaps/store.py:582-587 の by_document、atlas_edges/derive.py:211-232 の by_node/shared、corpus_view.py の
    _visible_placements / list_corpus_documents）。同じ arXiv 論文の PDF 取り込み（題名 2605.26810v1・document f4bf2132）と
    TeX 取り込み（題名 Cepheus B…・document 1771872a）が別の行のまま 2 本と数えられ、gap「星間磁場」の 2 本の支持論文は
    同じ 1 本、辺候補は全件が 1 本の論文の 2 つの取り込み（14 番は同題が 2 行）、論文の海は 4 本が 8 行。
    「論文の同一性」を表す表現（arXiv ID 等の版を除いた識別子）が数える側に無い。処理軸 none（集計の式自体は正しい）。
    接続軸 none（medium: 同一性の材料 source_url / ファイル名は documents に既にあり、運ばれないとも読める）。統制軸 none。
    確認: run 20261022T123418Z-31802 の gap-candidates / edge-candidates 応答の document_id、run 20261022T124315Z-36565 の
    /api/learning/corpus/documents 応答、uxsim/sandbox/reingest_tex.py（旧 document を消さずに新しい document を作る）。
generalization:
  level: repo_pattern
  general_form: "「N 件以上の独立した出所で反復したら候補にする」規則が、出所の同一性ではなく取り込みの行の数で数え、同じ出所の重複取り込みが独立の証拠として閾値を満たす"
pattern: unit-of-work-undefined
discovery:
  perspective: [reproduction, data_inspection]
  note: "教員ペルソナが辺候補の支持論文 2 行を「同じ 1 本が 2 回数えられている」と読み、学生ペルソナが論文の海の 8 行を「実質 4 本」と数えた。応答の document_id と題名を突き合わせ、PDF 版と TeX 版の別行であることを確かめた。 run: uxsim/runs/c-astro-r25-atlas-r1/20261022T123418Z-31802・uxsim/runs/c-astro-r25-atlas-r1/20261022T124315Z-36565（頭脳の所見 uxsim/runs/round25/BRAIN_NOTES_atlas.md）。"
resolution:
  perspective: [canonical_source]
  note: >-
    論文の同一性の判定を core/document_refs.py の paper_identity_key / representative_document_ids の 1 箇所に置き（source_url の arXiv ID → ファイル名の arXiv ID → 題名の arXiv 番号 → documents.id。version 抜き）、カテゴリギャップの反復閾値（atlas_gaps/store.py derive_candidates）・辺の共起（atlas_edges/derive.py derive_co_occurrence_pairs）・論文の海の一覧と配置（corpus_view.py の _domain_paper_representatives）が同じキーで数える。同じ論文の重複取り込みは 1 行に畳み、読める題名を持つ取り込みを代表にする。地図の配置と一覧は同じ母集合から代表を選ぶので、アンカーの絞り込みが一覧の行と一致する。取り込み口で重複を拒むかは別の判断として残した。
  landed_in:
    - "backend/core/document_refs.py"
    - "backend/core/atlas_gaps/store.py"
    - "backend/core/atlas_edges/derive.py"
    - "backend/core/corpus_view.py"
    - "backend/tests/test_atlas_review_round25.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "数える 3 つの面（gap・辺・海）に判定を書き写さず、同一性キーの 1 関数を通す。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の実データ（2605.26810 の PDF 版と TeX 版）で候補が消えることは再演で未確認"
      - "題名にも URL にも arXiv ID の無い重複取り込みは畳めない（材料が無い）"
related: [IK-0366, IK-0419, IK-0664]
view_of: []
history: []
---

## 課題

症状: 分野の地図タブの「論文の解析から見つかった候補」の唯一の候補（星間磁場）の支持論文 2 本が、同じ論文の PDF 版
（題名が arXiv 番号）と TeX 版。関係（辺）の候補は全件「共起」で、各候補の支持論文 2 行がやはり同じ 1 本の 2 つの取り込み。
学習者の論文の海は 4 本の論文が 8 行で出る。原因: 反復の閾値と一覧が documents.id を単位に数え、同じ論文の
重複取り込みを独立した論文として扱う。論文の同一性を表すキーが数える側に無い。

## 発見の観点

ペルソナの通し受講（reproduction）で教員と学生がそれぞれ重複に気づき、API 応答の document_id と題名を読んで
別行であることを確かめた（data_inspection）。

## 解決の観点

論文の同一性キーを 1 関数に置き、反復の閾値・共起・論文の海の一覧と厚みがそのキーで数える。
件数バッジを出さない規律（LS5・CR3）はそのまま。

## 一般化

「独立した出所が N 件」の規則は、出所の同一性が表現されていないと、同じ出所の再取り込み・別形式の取り込みで満たされる。
確かめ方は、同じ論文を 2 形式で取り込んだ状態で候補が立たず・一覧が 1 行になることを見ること。

## 第 2 周の再演（2026-10-23・campaign c-astro-r25-atlas-r2）

- 消えた: カテゴリギャップの候補（星間磁場）は立たなくなり、辺の共起候補も同じ論文の 2 つの取り込みでは立たない。論文の海の
  一覧と学習者の論文の位置は、各論文が 1 回ずつ出る（run 20261022T220616Z-78776 seq 9〜10・20261022T220438Z-77657 seq 6〜7）。
- 第 2 周の再演: 残存（論文の海の「縁」）。縁の論文の題名に、同じ論文の PDF 版（題名が arXiv 番号）と TeX 版が別の行で並ぶ
  （「2606.02318v1」と「Neutron Star Equation of State …」、「2605.26810v1」と「Magnetic field Topology …」 — run
  20261022T220616Z-78776 seq 9）。学生は「同じ論文なのか別なのか分からない」と書いた。
- 是正者向けの差分: `backend/core/corpus_view.py` の `_visible_gap_signals` / `_build_fringe` が同一性キーを通っていない
  （SELECT が document の題名をそのまま返し、`_build_fringe` は題名の文字列で重複を落とすだけ）。`_domain_paper_representatives`
  と同じ代表の選び方（`document_refs.representative_document_ids`）で信号の document を代表へ寄せ、代表の題名で列挙する。
  あわせて、第 1 周の是正で候補から消えた「採用済み」の判断行が凍結を止める件は IK-0884 に分けた。

- 第 2 波で是正（2026-10-23）: `corpus_view._visible_gap_signals` が信号の `document_id` を返し、`_build_fringe(representatives=, rep_titles=)` が `_domain_paper_representatives` の代表へ寄せて代表の題名で列挙する（`test_atlas_review_round25_r2.py::test_fringe_folds_duplicate_imports_to_the_representative_title`）。再演は未確認。
