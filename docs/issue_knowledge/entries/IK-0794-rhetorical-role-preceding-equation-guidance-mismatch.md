---
id: IK-0794
title: "役割判定の文脈で、直前ブロックが本文なのに Preceding Equation が 2 つ前の式を指し、「current block may continue from it」と式からの続きとして案内される"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-17
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "本文ブロックの役割判定で、直前の表示式から続く文かどうかを LLM に手掛かりとして示す"
  layers: [pipeline_a]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: low
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認前の仮説（起票時）: rhetorical_role の input_builder が「直前の式」を直前ブロックが式かどうかに関係なく遡って探し、見つかった式に固定の
    案内文「current block may continue from it」を付ける。処理軸 logic = 続きの案内を出す条件が「直前ブロックが式である」ことに
    なっていない。接続軸 meaning = 間に本文が挟まる式を、続きの元として後段の LLM に読ませる（low）。構造軸 none（low）/
    統制軸 none。確定には Preceding Equation を選ぶ条件と案内文を付ける条件を読む。同じ式が Previous Context と Preceding
    Equation に二重に載る冗長（seq 2983）も同じ箇所で起きている可能性がある。
    確認済み（2026-10-17）: _preceding_equation が「直前 2 文脈ブロック以内・同じ節」の式を返し、本文の書き出しを見ていなかった
    （X1 seq 3334 の tex_b54・3361 の tex_b98 はいずれも間に本文ブロックがあり、新しい話題で始まる）。seq 3376 の Previous Context は
    順序の誤りではなく、直前段落が前の小節（sec_8）の末尾で付録 D に言及していたもの — 節境界をまたぐことが見出しに出ていなかった。
generalization:
  level: general
  general_form: "手掛かりの対象を遡って探すとき、隣接していることを条件にしないまま隣接を前提にした案内文を付ける"
pattern: wording-mismatch
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 T1 が rhetorical_role（再取り込みの document・seq 2977・3013）の材料を読み、Previous Context（本文）と Preceding Equation（その前の式）が別ブロックなのに式からの続きとして案内されていることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md）。seq 2983 では直前が式のとき Previous Context と Preceding Equation に同じ式が二重に載る。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: "preceding_equation は直前の文脈ブロックが同じ小節の式ブロックで、かつ本文が続き語（小文字始まり・where / which / i.e. / This is because / In the second line …、input_builder.starts_as_continuation）で始まるときだけ渡す。案内文は「the block right before this one; the current block starts as its continuation」に変え、TeX 由来で式本文が載るときは Previous Context に同じ式を重ねない。前後の文脈が別の節のものなら見出しに明記する（prev_text_other_section / next_text_other_section）。"
  landed_in:
    - "docs/pipeline/agents.md"
    - "src/episteme_graph/agents/rhetorical_role/input_builder.py"
    - "src/episteme_graph/agents/rhetorical_role/prompt.py"
    - "src/episteme_graph/agents/rhetorical_role/schema.py"
    - "src/tests/agents/rhetorical_role/test_math_boundary_and_preceding_equation.py"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "実機での再解析（2610.08751）で seq 3334 / 3361 相当のブロックに Preceding Equation が出なくなったことの確認"
      - "続き語の一覧で拾えない続き（大文字始まりの記号・数式で始まる段落）の取りこぼしの実測"
related: [IK-0714]
view_of: []
history:
  - date: '2026-10-17'
    field: status
    from: open
    to: resolved
    reason: "隣接と続き語を条件にし、案内文と二重掲載・節境界の見出しを是正"
---

## 課題

症状: tex_b6 の判定で Previous Context は本文 tex_b5、Preceding Equation は式 tex_b4 なのに「current block may continue from it」と
案内される。tex_b69 でも Previous Context（tex_b68）と Preceding Equation（tex_b67）が別ブロックで同じ案内が付く。
原因（仮説）: 直前の式を遡って探し、隣接を条件にせず案内文を付けている。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2977・2983・3013）。

追加の出所（頭脳 T2）: uxsim/runs/radar_loop/BRAIN_NOTES_T2.md（seq 2984 — Preceding Equation に tex_b17 が渡るが間に本文 tex_b18 があり、現ブロックは式の続きではない）。

追加の出所（頭脳 X1・全是正入りの再解析 run 40ecf8df（seq 3313 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3334・3361 — Preceding Equation が前の話題の式（Jeffreys 事前分布・対数尤度）で、このブロックはその続きではないのに「may continue from it」と誘導される。seq 3376 — Previous Context が付録 D の話題で直前段落ではない。詳細は fixRejectFlow の起票。指示では IK-0714 宛てだったが、案内文の誤帰属は本エントリの症状なのでここに記録）。

## 発見の観点

役割判定の Previous Context と Preceding Equation のブロック番号を、案内文の前提と突き合わせた（data_inspection）。

## 解決の観点

案内の条件を「直前の文脈ブロックが同じ小節の式で、本文が続き語で始まる」に絞り、案内文を条件に合わせた（single_point_fix）。
3334 / 3361 の並びと続き語の判定、節境界の見出し、二重掲載の不在をテストで固定した（guardrail_fix）。

解消の確認（reproduction_rerun・第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3582・3585 — 節をまたぐ前文脈に「(from the previous section, not part of this section)」の注記が付く）。

解消の確認（頭脳 Z2・2609.35757 の再解析 run 54e85a2a と 2610.08751 の再解析 run の後段）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3611・3650・3662 — 「(from the previous section …)」「(from the next section …)」の節境界の注記が前後とも効く）。

解消の確認（第 2 波 deploy 後の run・頭脳 Z0/Z1）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3597・3618・3633・3642 — 継続文の前の式が Preceding Equation として渡り、「This is because」のような where 以外の継続にも付く。seq 3633 — 後文脈にも節境界の注記）、uxsim/runs/radar_loop/BRAIN_NOTES_Z1.md（seq 3580・3622）。残る観察: 直前が式でも Preceding Equation でなく Previous Context に式が入る例がある（Z1 seq 3580・3610・3715、Z0 seq 3648 — 継続の判定が文頭語に依存する）。

## 一般化

隣接を前提にした案内を、隣接を確かめずに付けると後段が誤った続きを読む。辞書の型 wording-mismatch の近傍（文言が条件と食い違う）。
