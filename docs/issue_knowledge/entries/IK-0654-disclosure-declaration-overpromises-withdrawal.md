---
id: IK-0654
title: "可視性の宣言とマニュアルが、取り消し・見送りの後に起きることを現物より多く約束し、マニュアルの節どうしも食い違う"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-09
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/disclosure_axes_design.md"
  - "docs/features/trace_registry_sovereignty_ledger_design.md"
feature_context:
  realizing: "学習者が自分の記録を取り消し・見送り・地図から外すとき、その結果を宣言とマニュアルで確かめる"
  layers: [disclosure_axes, trace_registry, docs]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [review]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 宣言は「書き直し・削除をすると、そこから後のやりとりは記録から外れます」「候補は却下できます。どちらも…後から戻せます」と書いていたが、痕跡は状態を変えて残り、見送った候補を戻す API は無い（routes/learning.py は confirm / dismiss のみ）。マニュアルの節はわたしの記録から地図の除外ができると書き、別の節は一覧と持ち出しだけと書いていた。処理軸 wording = 挙動と違う文。統制軸 review = DA1（現物から確認できることだけ）を照合する手続が文の内容に及んでいない。構造軸 none。接続軸 none。
generalization:
  level: repo_pattern
  general_form: "当事者への宣言を現物を読まずに書き、できない操作をできると約束する"
pattern: doc-drifts-from-code
discovery:
  perspective: [invariant_audit, doc_code_diff]
  note: "第 23 周の机上レビューで、DA1 と宣言の文、宣言とマニュアル、マニュアルの節どうしを読み合わせて見えた。"
resolution:
  perspective: [doc_correction]
  note: "宣言の取り消しと見送りの文を現物どおりに直し（痕跡は状態を変えて残る・見送った候補は戻せない・答えられなかった質問の一覧からは外れない）、マニュアルの該当節を同じ内容に揃えた。"
  landed_in:
    - "backend/core/disclosure_axes.py"
    - "docs/manual/student/02-student.md"
  verification:
    methods: [guardrail]
    unverified:
      - "宣言の文と現物の挙動を機械で照合する検査は無い（basis のファイル実在の検査だけ）"
related: [IK-0653, IK-0658, IK-0660]
view_of: []
history: []
---

## 課題

症状: 宣言とマニュアルが「記録から外れます」「後から戻せます」「わたしの記録から地図に反映しないにできる」と書くが、どれも現物と違う。原因: 宣言の文が現物の取り消し・見送りの経路を読まずに書かれていた。

## 発見の観点

DA1 × 宣言の文 × 現物の API の読み合わせ（invariant_audit）と、宣言とマニュアルの突合（doc_code_diff）。

## 解決の観点

文を現物に合わせた（doc_correction）。戻す操作や一覧から外す操作を新しく作るかは別の課題（見送りの戻し・答えられなかった質問）として分けた。

## 一般化

宣言は約束であり、現物より多くを書くと当事者の判断を誤らせる。確かめ方は、宣言の各文について対応する API・状態遷移を列挙し、無い操作を約束していないことを見ること。
