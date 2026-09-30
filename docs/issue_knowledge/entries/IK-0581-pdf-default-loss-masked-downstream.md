---
id: IK-0581
title: "取り込みの既定が PDF で、組版からの復元の損失が以降の全ステージに乗り、下流の各面が読み替えで補っていた（生成言語も run の条件として運ばれていなかった）"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/root_cause_consolidation_2026-09-30.md §4
  - docs/features/paper_radar_design.md §15.9
  - docs/features/url_material_upload_design.md §10
  - docs/features/discuss_opening_authoring_design.md §14
feature_context:
  realizing: "論文を取り込んで式・導出・記号・部品を構造化し、学習者の言語で生成文を作る"
  layers: [paper_discovery, url_material_fetch, pipeline_a, discuss]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [information, condition]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals:
    - kind: value
      target_axis: processing
      neighbor_of: [processing.input_handling, connection.information]
      statement: 同じ内容を損失の多い形式と少ない形式のどちらでも受けられるとき、損失の多い形式を既定に選んでいることを入力の取り扱いと区別する
      confidence: low
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理=input_handling: 同じ論文を TeX でも取れるのに、組版結果から式・導出を復元する PDF を既定の入力にしていた
    （同一論文で式チェーン PDF 0 本 / TeX 13 本 — 論文レーダー §15.9 の実測）。入力の選び方の性質で、単一の
    照合・分岐の誤りとは違うため確信度 medium（proposals に 1 件）。接続=information: 上流で失われた式・記号が
    記号レジストリ・部品・付録・ラベルへ欠落したまま流れ、下流は読み替え・除外で隠すだけだった。接続=condition:
    生成言語が run の条件として運ばれず、生成文が素材の言語（英語）のまま出た。構造=none: 保存形式や責務の置き場所は
    変えずに直せた（medium は、既定を持つ場所が API 層に無かった点を責務と読む余地があるため）。統制=none: 呼び出し
    上限・429 の扱いは解決側の統制で、原因ではない。
generalization:
  level: general
  general_form: "上流の段が損失の多い入力形式を既定に選び、失われた情報を下流の各面が読み替えで補うため、損失そのものは戻らず別の症状で現れ続ける"
pattern: upstream-loss-masked-downstream
discovery:
  perspective: [inventory, data_inspection]
  note: "記号レジストリの主記号欠落・代替品だけの部品・式の断片化・英語のままの生成文・無関係な式の付録・二重の主張・80 字で切れたラベルの是正を並べ、同一論文を TeX 経路で通した成果と比べて上流の同じ段に遡った。"
resolution:
  perspective: [carry_through, order_and_budget]
  note: "取り込みの既定を TeX にし、TeX が読めないときだけ同じリクエストで PDF へ倒す同期プリチェックを API 層に置いた（1 件あたりの取得は最大 2 回・HTTP 429 では 2 回目を取らない・出所は実際にバイト列を返した URL）。生成言語は run option として orchestrator まで運ぶ。"
  landed_in:
    - backend/api/source_resolution.py
    - backend/core/document_pipeline/orchestrator.py
    - backend/core/url_fetch.py
    - backend/tests/test_source_resolution.py
    - docs/features/paper_radar_design.md §15.9
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "上流の入力の派生形（損失の少ない形式を既定にし、倒し先への切り替えを受理前に同期で確かめ、呼び出しに上限を置く）。PDF しか無い論文の損失は残り、PDF 経路そのものの式復元の強化は次段。"
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（ペルソナ通し受講の次の周）
      - ブラウザでの画面確認
      - 実際の arXiv への取得（外部 API を叩かない方針）
      - バッチ取り込みキューでの生成言語（options 列が無く運べない）
      - PDF しか無い論文の式復元
related: [IK-0328, IK-0353, IK-0354, IK-0540, IK-0541, IK-0555, IK-0571]
view_of: []
history: []
---

## 課題

PDF 経路で取り込んだ教材では、記号レジストリに主記号が無い・部品が代替品だけ・式が断片化する・無関係な式が
付録に並ぶ・生成文が英語のまま、といった欠陥が面を変えて出続けた。下流の各面は読み替え・除外で補ったが、
原因は上流の入力形式の既定（PDF）にあり、損失そのものは戻らなかった。生成言語が run の条件として運ばれて
いなかったことも同じ根の一部。

## 発見の観点

下流の是正を並べる棚卸しと、同一論文を TeX 経路で通した成果の比較（データ実測）。

## 解決の観点

損失の少ない入力と生成条件を上流から運ぶことを主に、倒し先への切り替えと呼び出し上限の統制を従にした。
下流で読み替えを足し続ける案は、損失が戻らず面ごとに症状が出るため採らなかった。

## 一般化

同じ内容を複数の形式で受けられる入力で、損失の多い形式を既定にしていると、下流の是正はすべて隠蔽になる
（`upstream-loss-masked-downstream`）。
