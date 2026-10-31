---
id: IK-0332
title: GROBID の付録（back/annex）と body 直下の図表・見出し番号を読まず、付録・表・図キャプション・節階層が構造に入らない
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §3
  - docs/architecture/knowledge_structure_review_2026-09-12/A_fidelity.md
feature_context:
  realizing: PDF から論文の文書構造（節・段落・式・図表）を復元し、以降の全段階の母集合にする
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: TEI に存在する情報（annex の段落・figDesc・head@n）が blocks/sections に運ばれない
    （段階間で情報が落ちる）。構造軸: 表本体を表す block 種別が無く、節の level/parent を
    持てるのに埋めない表現の欠落。処理軸: パーサの各処理は仕様どおりで単一不良とは言い
    切れないため medium の none。統制軸: 要素なし。
generalization:
  level: general
  general_form: 入力形式の一部の容器（付録・直下要素）を走査しないため、その容器にだけ入る種類の内容がまるごと欠落し、下流は「無い」と正しく報告してしまう
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    原本の付録 A〜F・Table 1/2 の語句が blocks にも chunks にも 1 文字も無いことを原本照合で
    確認し、TEI 側に annex と figDesc が存在することから parser の走査範囲に遡った。
resolution:
  perspective: [carry_through, representation_change]
  note: >-
    back/annex を透過コンテナとして歩く。body/back 直下の figure/table を caption block に
    し、表本体は行テキストとして落とさない。head@n から level/parent を導出。REVTeX の
    ピリオド区切り caption と走り込み見出しを決定論で拾う。回収件数は metadata に残す。
  landed_in:
    - src/episteme_graph/agents/document_structure/grobid_parser.py
    - src/episteme_graph/agents/document_structure/agent.py
    - src/tests/agents/document_structure/test_grobid_structure_recovery.py
  verification:
    methods: [guardrail]
    unverified:
      - 是正後の再解析による実データでの効果の実測（LLM の live 呼び出しを行わない方針のため次回の解析待ち）
related: [IK-0329]
view_of: []
history: []
---

## 課題

**症状**: 付録の全文がどの層にも無い。図キャプションは PDF 10 本中 8 本で 0、表は 12 本
すべてで 0。全節が level=1。主結果の数値（表）が学習者にも教員にも届かない。

**原因**: `_parse_body` が `<body>` 直下の div しか歩かず、div の兄弟の `<figure>` と
`<back>` を見ない。head@n を section_id 生成にしか使わない。

## 発見の観点

原本との照合（`reproduction`）で欠落を確定し、TEI の実物（`data_inspection`）で情報が
入口に存在することを確かめた。

## 解決の観点

存在する情報を後段へ運ぶ（`carry_through`）。表と階層を表せる形にする
（`representation_change`）。表本体の意味付け（セル・単位）は非スコープ（§5.2）。

## 一般化

パーサは「走査していない容器」を知らない。下流の被覆報告は母集合が入口で欠けていると
「やることが無かった」と読める。入口の走査範囲は入力形式の全容器を列挙して固定する。
