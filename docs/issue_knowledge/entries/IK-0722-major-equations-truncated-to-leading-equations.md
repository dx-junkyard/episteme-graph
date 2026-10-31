---
id: IK-0722
title: "thesis_reconstruction / dsl_linking の入力の式（major_equations）が論文先頭の 16〜24 式に限られ、2PN の結果式が入らず、headline_claim も主結果ではなく要旨の補助文になる"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/pipeline/agents.md"
  - "docs/pipeline/overview.md"
feature_context:
  realizing: "論文の中心命題と DSL グラフを再構成するために、論文の主要な式と主結果の主張を LLM の入力に選ぶ"
  layers: [pipeline_a]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定（コードで確認）: 入力 builder が「主要な式」を `equations.equations[:limit]` で論文の出現順に先頭から上限件数で切っており
    （sec_3〜sec_10 / Fokker 作用の入口まで）、重要度（主結果の式・中心命題の導出段）で選んでいなかった。headline_claim も要旨の先頭付近の文を採っている。処理軸 logic =
    「主要」を出現位置という代理で決める選択規則。接続軸 information = 後段（中心命題の支持構造・DSL の式接続）に主結果の式が
    届かない。構造軸 none（確信度 low: 打ち切りの事実を coverage として報告する受け皿はある — IK-0715 とは別の、上限内の選び方の問題）。
    統制軸 none（確信度 medium: 上限そのものは予算の統制だが、課題は上限内で何を残すか）。headline_claim の件は未確認のまま。
generalization:
  level: general
  general_form: "上限内に残す要素を重要度ではなく出現順で選び、文書後半にある主結果が要約・再構成の入力から外れる"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 L0 が thesis_reconstruction（seq 1962）・dsl_linking（seq 1968）・component_assembly（seq 1974）を応答する際、式が前半に偏り主結果の式を張れないことに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_L0.md）。"
resolution:
  perspective: [canonical_source, single_point_fix]
  note: "原因を確定: thesis_reconstruction / dsl_linking の input_builder が equations.equations[:limit] で先頭から切っていた（上限 16 / 24・role も節も見ない）。選び方の正本 src/episteme_graph/agents/equation_selection.py を新設し、主張（と thesis）が確定リンクで参照する式 → 結果系の節（skeleton の result / core_relation / diagnostic / conclusion）と equation_type・role_in_argument が result の式 → 残りを stratified_sampling の節単位層化、の順で選ぶ（文書順を保つ・式由来の合成主張は優先に数えない）。上限値と LLM 呼び出し回数は不変。thesis の coverage に選び方と取りこぼした節を載せた。headline_claim が要旨の補助文になる件は本エントリでは扱っていない（paper_skeleton 側の選び方・別件）。"
  landed_in:
    - "src/episteme_graph/agents/equation_selection.py"
    - "src/episteme_graph/agents/thesis_reconstruction/input_builder.py"
    - "src/episteme_graph/agents/dsl_linking/input_builder.py"
    - "src/tests/agents/thesis_reconstruction/test_equation_selection.py"
    - "src/tests/agents/dsl_linking/test_equation_selection.py"
    - "docs/pipeline/agents.md"
  verification:
    methods: [guardrail]
    unverified:
      - "実論文（2PN (A)dS・トライスペクトル EFTofLSS）の再解析で主結果の式が入力に入ったか（自律周回での再観察）"
      - "dsl_linking の式入力の取りこぼしは coverage に報告していない（orchestrator 未配線）"
      - "headline_claim が主結果でない件"
related: [IK-0715]
view_of: []
history: []
---

## 課題

症状: thesis_reconstruction の major_equations 16 件・dsl_linking の equations 24 件がいずれも論文前半の式で、フォッカー作用・2PN 運動方程式・
ハミルトニアンといった主結果の式が入らない。component_assembly の headline_claim は要旨の補助文（Fokker 作用）で主結果ではない。
原因（確定）: 入力 builder が `equations.equations[:limit]` で式を出現順の先頭から上限件数で切っていた。
出所はほかに uxsim/runs/radar_loop/BRAIN_NOTES_L1.md（seq 1966・2035）・BRAIN_NOTES_L2.md（seq 1958・2039）。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_L0.md（seq 1962・1968・1974）。

## 発見の観点

実際の入力の式の範囲と論文の構成を突き合わせた（data_inspection）。

## 解決の観点

上限内の選び方の正本（`equation_selection.py`）を 1 つ置き、主張・thesis が参照する式と結果系の式を優先し、残りを節単位で層化して配る
（canonical_source + single_point_fix）。thesis の coverage に選び方と取りこぼした節を残す。headline_claim の件は未対処。

## 一般化

「主要」を出現順で代理すると、後半に結果を置く文書で要の要素が落ちる。辞書の型 extent-decided-by-proxy-signal。
