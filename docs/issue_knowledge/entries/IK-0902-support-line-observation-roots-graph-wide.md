---
id: IK-0902
title: "独立支持経路の observation_roots が「対象へ届く経路の根」ではなくグラフ中で観測系の主張を参照するノードの全体から作られ、支持線ゼロ（level none）のときも返るため、台帳詳細に「支持線は見つかりません」と「観測からの支持根: 5 件」が並び、別の対象でも同じ根の列が出る（根は主張の内部 ID のまま）"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/architecture/persona_enactment_testing_design.md §22.8"
  - "docs/features/stakes_ledger_design.md"
feature_context:
  realizing: "教員が台帳詳細で、この対象を観測からの支持線がどう支えているかを読む"
  layers: [stakes_ledger_sl, doubt_d, frontend_admin_ui]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
    governance: [none]
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
    処理軸 logic: core/doubt/support_paths.py は roots（ソース）を『観測系 claim を参照し sink でも対象の言い直しでもない全ノード』で作り、observation_roots をその contributing_claims から作って、flow の値にかかわらず全分岐で返す。roots
    は最大流のソース候補の全体であって、対象へ実際に届いた経路の根ではない。接続軸 meaning: doubt-atlas.js は observation_roots を『観測からの支持根』という見出しで、level none のときも描く — 候補の全体が『この対象の支持の根』という意味で読まれる。label は observation_targets の label がそのまま
    claim_span_* の agent ID になる経路がある。構造軸 none。
generalization:
  level: general
  general_form: "最大流などの計算で使った入力候補の全体を、計算結果（その対象に届いた経路）の説明として返すため、結果がゼロのときも候補が根として表示され、対象を変えても同じ列が出る"
pattern: projection-mistaken-for-source
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    te-04 の eq_dcf と eq_mod_dcf、te-01 の eq_op_0037（支持線なし）と主グラフのノードで、観測からの支持根の列が一字一句同じだった。support_paths の戻り値の組み立てを辿った。run:
    uxsim/runs/c-r25-doubt-r2/20261022T215750Z-73306（seq10・14・26）・uxsim/runs/c-r25-doubt-r2/20261022T220507Z-77990（seq16・22）。
resolution:
  perspective: [single_point_fix]
  note: >-
    第 2 波で是正（2026-10-23）。support_paths の observation_roots を、対象（sink）へ有向辺で辿り着ける根ノードを持つ観測系主張だけに限った（_nodes_reaching）。支持線ゼロ（none）では空。本文の引けない観測系主張は id を名前に使わず「名前を引けない対象」。
  landed_in:
    - "backend/core/doubt/support_paths.py"
    - "backend/core/doubt/observation_targets.py"
    - "backend/tests/test_doubt_ledger_document_key_round25.py"
  verification:
    methods: [guardrail]
    unverified:
      - "根を主張の本文の抜粋で出す（safe_text）は入れていない（観測系主張の本文 120 字まで）"
related: [IK-0899, IK-0901]
view_of: []
history: []
---

## 課題

症状: 台帳詳細で「この対象への、観測記録からの支持線はこのコーパスの中では見つかりません。」の直下に「観測からの支持根」
が並び、矛盾して読める。別の式・別のノードでも同じ根が並び、対象ごとに求めたものに見えない。根は `claim_span_001_142` の
ような内部 ID で、何の観測かが読めない。

原因: `observation_roots` が最大流のソース候補の全体（グラフ中の観測系主張の参照）で、対象に届いた経路の根ではない。
level に関係なく返し、画面も level に関係なく描く。

## 解決の観点

第 2 波で是正（2026-10-23。frontmatter の resolution）。以下は起票時の是正案。是正案:

- `observation_roots` は、残余グラフで対象（sink）へ届いた経路のソースに限る（`s_reachable` と flow の分解から求める）。
  level none のときは空にする。
- 画面は level が none なら根の行を描かない。根の名前は主張の本文（`learner_context_common.safe_text` を通した抜粋）で出す。
- 対象ごとに別の根になることをテストで固定する（2 つの対象で同じグラフを使い、届く根が違う fixture）。

## 発見の観点

同じ論文の別の対象で根の列を比べ、全部一致したので戻り値の組み立てを読んだ。

## 一般化

計算の入力候補を結果の説明として返すと、結果がゼロでも説明が出て矛盾する。確かめ方は、結果がゼロの対象と、
根の違う 2 つの対象を用意して、説明の欄が結果に追随することを見ること。

第 3 周の再演: 残存（2026-10-23・名前の部分）。支持の根は対象へ届く経路の根に絞られた（level single で根が出る）。ただし根の主張の claim_id は agent ID
（claim_span_001_142 等）で、表示名の正本が UUID しか引かないため、根は全件「名前を引けない対象」の反復になる（te-02 のノード台帳）。原因は IK-0930。
run: uxsim/runs/c-r25-doubt-r3/20261023T033238Z-24957（seq8・9）。

第 3 波で是正（2026-10-23）: 根の名前は observation_claim_targets が主張参照の解決の正本（core/knowledge_objects/claim_refs.py）で本文を引くようにした（IK-0930）。DB に行が無い局所 ID は claim_object_builder の本文で名前だけ出す。実データでの見え方は未確認。
