---
id: IK-0764
title: "TeX 経路の教材でも equation_semantics の inline 式候補が inline_pdf_text_layer・partial 扱いで再構成必須になり、切り出しが添字・小数点の途中で切れる（`mathrm{halo}} = K_{ij}`・0.1 が「= 0」）"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/pipeline/agents.md"
  - "docs/pipeline/overview.md"
feature_context:
  realizing: "TeX ソースから取り込んだ本文の inline 数式を、原文どおりの式候補として equation_semantics に渡す"
  layers: [pipeline_a]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定: tex_archive は `$…$` を保持しており取り込み側は無関係（起票時の仮説「取り込み側だけ変わった」は訂正）。
    input_builder が出所（parser_source）を見ず、TeX 経路でも PDF 向け正規表現 `_INLINE_MATH_RE` だけで inline を拾い、
    build_llm_inputs が無条件に inline_pdf_text_layer を付けていた。接続軸 information = 原文の区切りという素材が候補抽出に
    配線されていなかった。処理軸 logic = 正規表現が右辺に `.` `^` を含まず左端の lookbehind も弱く、添字・小数点の途中で切れた。
generalization:
  level: general
  general_form: "入力が保持している区切りを後段の抽出が使わず、区切りの無い入力向けの推測で切り出し続けるため、区切りの途中で切れた断片を正しい値として後段に流す"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 Q0 が 2610.19001（TeX 経路）の equation_semantics 要求を読み、inline 候補が inline_pdf_text_layer・partial 扱いで再構成必須の方針が掛かること（seq 2514）、切り出しが \\mathrm{halo} の途中で切れ raw_text が『mathrm{halo}} = K_{ij}』になること（seq 2517）、off-axis の v_kick=0.1 が小数点で切れ『= 0』になること（seq 2544）に気付いた。出所: uxsim/runs/radar_loop/BRAIN_NOTES_Q0.md。"
resolution:
  perspective: [carry_through]
  note: "TeX ブロック（parser_source=tex_archive）は _tex_inline_candidates / _scan_inline_block が `$…$` / `\\(…\\)` の原文境界で切り出し、extraction_source=tex_source・needs_reconstruction=False にする。PDF 経路の _INLINE_MATH_RE は PDF 文字層にだけ使う。"
  landed_in:
    - "src/episteme_graph/agents/equation_semantics/input_builder.py"
    - "src/tests/agents/equation_semantics/test_inline_tex_source.py"
    - "backend/tests/test_latex_display.py"
    - "docs/pipeline/agents.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "上流の損失の少ない形式（TeX の原文境界）をそのまま使う上流の派生形を採った。全経路の横断検査は置いていない。設計前に原理は読んでいない。"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "実論文 2610.19001 の再解析（是正後の inline 候補の実物）"
      - "LLM 呼び出し回数の実測"
      - "後段（DSL・部品グラフ・二層説明）から偽の依存が消えたことの確認"
related: [IK-0762, IK-0757]
view_of: []
history:
  - date: '2026-10-16'
    field: classification
    from: pattern=contract-changed-one-side; connection=[contract]; cause_status=hypothesis
    to: pattern=available-but-unwired; connection=[information]; cause_status=confirmed
    reason: 是正で原因が確定し、取り込み側（tex_archive）は `$…$` を保持していて変わっていなかった。原文の区切りが候補抽出に配線されていなかったと分かったため
---

## 課題

症状: TeX 経路の教材でも inline 式候補が inline_pdf_text_layer・partial 扱いで再構成必須になる。切り出しが上付き添字や
小数点の途中で切れ、raw_text が `mathrm{halo}} = K_{ij}` のような断片や、0.1 が「= 0」に見える誤った値になる。
原因: input_builder が TeX 経路でも PDF 向け正規表現 `_INLINE_MATH_RE` だけで inline を拾い、`build_llm_inputs` が無条件に `extraction_source="inline_pdf_text_layer"` を付けていた。正規表現は右辺に `.` `^` を含まず左端の lookbehind も弱く、`0.1`→`0` や `\mathrm{halo}} = K_{ij}` の途中切れになった。値の言及を候補から除く判定は存在しなかった。（tex_archive は `$…$` を保持していたので取り込み側は無関係。起票時の仮説は訂正）

出所: uxsim/runs/radar_loop/BRAIN_NOTES_Q0.md（seq 2514 / 2517 / 2544）。

同根の再発: uxsim/runs/radar_loop/BRAIN_NOTES_Q1.md（seq 2515 / 2524 / 2530 / 2536）— extraction_source=inline_pdf_text_layer、括弧（'L > 2(R_h'）・バックスラッシュ（'mathrm{vir} > 10'・'vir}>10'）・'+' での切断、ブロック本文にも 'M_\[INLINE…]^{12}' と壊れた形が残る。

## 発見の観点

TeX 経路の原文と、equation_semantics に渡った inline 候補の raw_text を突き合わせた（data_inspection）。

## 解決の観点

段階間で運ぶ（carry_through）。TeX の原文境界を候補抽出まで運び、出所を tex_source として記録した。

解消の確認（reproduction_rerun・第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3573・3576・3579・3588 — inline 式の対象が本文中で [INLINE_MATH_TARGET]…[/INLINE_MATH_TARGET] で印付けされ、脚注も span になって渡る）。

解消の確認（頭脳 Z2・2609.35757 の再解析 run 54e85a2a と 2610.08751 の再解析 run の後段）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3575・3581・3587・3596 — [INLINE_MATH_TARGET] で対象の inline 式が一意に特定できる）。

解消の確認（第 2 波 deploy 後の run・頭脳 Z1）: uxsim/runs/radar_loop/BRAIN_NOTES_Z1.md（seq 3583・3586 — [INLINE_MATH_TARGET] で対象の inline 式の位置が読め、対象外の inline 式には印が付かない）。

## 一般化

上流（TeX 取り込み）は区切りを保持していたのに、下流の候補抽出がそれを使わず PDF 前提の推測で切り出していた。辞書の型 available-but-unwired（起票時の contract-changed-one-side から訂正・history に記録）。
IK-0762（値指定が式として母集合に入る）と同じ inline 候補の入口の問題で、本件は切り出しと出所の扱い。

## 第 11 周（第 25 周・面 D 第 11 周、2026-10-31・judge_studio_r11）

**STILL（再解析待ちの記録）**。コース 1bad7d9f の t2 の本文「論文では $Q = 0.5$」に〔AI復元〕の印（doubt 面 st-08）。Cep B の TeX 版 document 1771872a の記録 `eq_eqcand_inline_tex_b103_6_97f45e83` は `source_extraction.extraction_source = inline_pdf_text_layer`・`raw_text = "Q=0"`・`reconstruction.status = reconstructed_from_neighbors`（小数点で切れて復元）で、作った run は 2026-10-02（この課題の是正 10-16 の前）。印は記録どおりで、再解析（所有者の操作）まで変わらない。run: uxsim/runs/c-r25-doubt-r11/20261031T102036Z-2368 seq6。
