---
id: IK-0762
title: "inline の値指定（z=0・質量の閾値・Plummer 球のスケール半径など）が equation_semantics の inline 候補として「式」になり、展開式・有効音速・推定量の導出元に入り込んで偽の依存を作る"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/pipeline/agents.md"
  - "docs/pipeline/overview.md"
  - "docs/pipeline/theory-graph.md"
feature_context:
  realizing: "本文中の inline 数式を式の母集合に入れ、式どうしの導出関係（from_equations / derived_from）を DSL・部品グラフ・二層説明へ流す"
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [decomposition]
    connection: [meaning]
    governance: [budget]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: low
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定: equation_semantics の input_builder に値の言及（値の代入・パラメータ指定・範囲）を候補から除く判定が無く、
    PDF 向け正規表現で拾った inline をすべて式の母集合に入れていた。構造軸 decomposition = 「何を 1 つの式と数えるか」
    の単位が無く、値指定が関係式と同じ粒度で後段に流れた。接続軸 meaning = 後段は記号の一致で導出元と読み、偽の依存に
    なった。統制軸 budget = 二層説明の 1 回分が値指定で消費された（確信度は低い）。処理軸 none。
generalization:
  level: general
  general_form: "母集合に入れる単位の基準が無いため、関係ではない断片（値の指定）が関係と同じ扱いで後段に流れ、記号の一致だけで偽の依存として結ばれる"
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 Q2 が 2610.19001 の LLM 要求を読み、inline 候補が値指定ばかりであること（seq 2528〜2543）、dsl_linking の equations[].from_equations に Plummer 節の質量の値指定が展開式・有効音速・粘性推定量の入力として繰り返し現れること（seq 2546）、component_graph 修復要求でも同じ混入（seq 2552）、contextual_explanation の derived_from にスケール半径 = 5 が導出元として現れること（seq 2555 / 2558）、説明対象 8 件すべてが値指定で z=0 が 3 件重複したこと（seq 2561）に気付いた。seq 2534 では同ブロックに閾値が 3 つあり、断片だけでは位置が決まらない。出所: uxsim/runs/radar_loop/BRAIN_NOTES_Q2.md。"
resolution:
  perspective: [single_point_fix]
  note: "input_builder に classify_inline_relation を置き、値指定（z=0 / a=5 / \\epsilon=0.1 / M>10^{12}M_\\odot / 0<z<1）は候補から外して coverage.details.excluded_inline（reason=value_specification・列挙に上限あり）に残す（母集合に数えない・情報は落とさない）。記号どうしの関係式（R_h=3R_{\\rm vir} 等）は残る。"
  landed_in:
    - "src/episteme_graph/agents/equation_semantics/input_builder.py"
    - "src/tests/agents/equation_semantics/test_inline_tex_source.py"
    - "backend/tests/test_latex_display.py"
    - "docs/pipeline/agents.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "偽の依存を DSL・部品グラフ・二層説明の各面で読み替えて消さず、上流（式の母集合の入口）の条件を変えた（上流の派生形）。横断検査は置いていない。設計前に原理は読んでいない。"
  verification:
    methods: [guardrail]
    unverified:
      - "実論文 2610.19001 の再解析（是正後の inline 候補の実物）"
      - "LLM 呼び出し回数の実測"
      - "後段（DSL・部品グラフ・二層説明）から偽の依存が消えたことの確認"
related: [IK-0717, IK-0720, IK-0757, IK-0764]
view_of: []
history: []
---

## 課題

症状: equation_semantics の inline 候補に z=0・M_vir>10^11・Plummer 球の質量やスケール半径の値指定が「式」として入り、
dsl_linking の from_equations、component_graph の入力、contextual_explanation の lower_context.derived_from に展開式・
有効音速・推定量の導出元として現れる。二層説明では 1 回の 8 件が丸ごと値指定で、同じ z=0 が 3 要素に重複した。
原因: input_builder が TeX 経路でも PDF 向け正規表現 `_INLINE_MATH_RE` だけで inline を拾い、`build_llm_inputs` が無条件に `extraction_source="inline_pdf_text_layer"` を付けていた。正規表現は右辺に `.` `^` を含まず左端の lookbehind も弱く、`0.1`→`0` や `\mathrm{halo}} = K_{ij}` の途中切れになった。値の言及を候補から除く判定は存在しなかった。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_Q2.md（seq 2534 / 2546 / 2552 / 2555 / 2561）。

同根の再発: uxsim/runs/radar_loop/BRAIN_NOTES_Q1.md（seq 2518 / 2521 / 2545 / 2551 / 2560 / 2563）— 付録 Plummer 節の inline 候補（scale radius = 5・切断半径）が本論の展開式・ずり粘性推定量・LI 予測式の from_equations（thesis_reconstruction）、展開式・ずり粘性の入力（component_graph）、長波長展開・有効音速の部品の uses_equation（contextual_explanation）に入る。付録の設定値が本論へ流れる点も同じ母集合の誤りの下流として本件に畳む。

隣接の観察（頭脳 T0）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2910 — 自明な条件式 x_i=x_j も inline 式候補として 1 LLM コールを使う。値指定ではないが、定義・関係を担わない式が候補に入る同族の症状）。

## 発見の観点

inline 候補の中身と、後段の導出元の欄を実物で突き合わせた（data_inspection）。

## 解決の観点

単点修正（single_point_fix）。式の母集合の入口で値指定を外し、外した事実を coverage に残した。

## 一般化

「何を 1 つの式と数えるか」が無いため、値の指定が関係と同じ扱いで流れる。辞書の型 unit-of-work-undefined。
IK-0717（導出元の一律の誤配線）・IK-0720（再掲式の重複）と症状は重なるが、本件は母集合に入る単位の誤り。
