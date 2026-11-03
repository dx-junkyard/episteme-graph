---
id: IK-0696
title: "TeX 取り込みで環境名・レイアウトの残骸（center・tabular の列指定・subequations・itemize・1 文字の句読点）や前付け（著者行・ORCID）・表記表の 1 行ずつが独立した本文ブロックになり、役割判定の LLM コールを 1 件ずつ消費する"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §15.9"
  - "docs/pipeline/overview.md"
feature_context:
  realizing: "arXiv の TeX ソースから論文を取り込み、本文の段落だけを役割判定・主張採否の母集合にする"
  layers: [pipeline_a, url_material_fetch]
classification:
  axes:
    processing: [input_handling]
    structure: [decomposition]
    connection: [none]
    governance: [budget]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（backend/core/document_pipeline/tex_archive.py の _BLOCK_ENV_RE と _clean_text_fragment / _COMMAND_WITH_ARG_RE）:
    ブロックとして切り出す環境は数式・figure・table の閉じた集合だけで、それ以外の環境（center / tabular / itemize /
    subequations 等）は段落の平文化に回り、コマンドを引数に置き換える正規表現が \begin{center} を「center」、tabular の列指定を
    「tabular@p0.24p0.70@」のような文字列にして本文に残す。処理軸 input_handling = 平文化が環境の開始・終了・列指定を地の文と
    区別せず扱う。構造軸 decomposition = 「何を 1 つの本文ブロックと数えるか」（前付け・表記表の行・式の間のつなぎ語・
    空に近いブロック）が定義されておらず、粒度の違う断片が同じ母集合に入る。統制軸 budget = 断片ごとに役割判定の LLM が
    1 回ずつ呼ばれる（判定自体は reject で正しく終わるので処理の誤りではない）。接続軸 none = 文書構造が出したブロックを
    後段は契約どおり読んでおり、段階間で失われたものは無い（判断が境界に近いため medium）。
generalization:
  level: repo_pattern
  general_form: "入力形式の変換が閉じた集合以外の構文を地の文として素通しし、意味の無い断片が後段の 1 件 1 回の処理の母集合に入る"
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回 1 周目（arXiv 2610.20589 の TeX 取り込み・頭脳メモ seq 562〜646）で役割判定に渡る本文ブロックを 1 件ずつ読み、環境名だけ・列指定だけ・1 文字だけのブロックがそれぞれ LLM を 1 回消費していることを見た。"
resolution:
  perspective: [single_point_fix, first_class_state]
  note: "数式・図表以外の環境は開始・終了の印と幅・列指定だけを落として中身を残す（_strip_env_tokens）。tabular 系は「セル | セル」の行の表本体（raw.in_table・table 環境の中も）、itemize / enumerate / description は 1 項目 1 行の 1 ブロック、前付け（\\title・\\author・\\affiliation・ORCID・連絡先）は block_type unknown + raw.front_matter。\\ や \\[10pt] は語の区切りにし、記号だけの断片はブロックにしない。外したものの件数は metadata.structure_recovery に残す。"
  landed_in:
    - "backend/core/document_pipeline/tex_archive.py"
    - "backend/tests/test_latex_display.py"
    - "backend/tests/test_document_pipeline.py"
    - "docs/features/url_material_upload_design.md"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "実論文の TeX を通した役割判定の LLM 呼び出し回数の変化（取り込み済みの tarball を MinIO から読んで構造化までを再演 — 2610.08908 ほか 3 本。解析の再実行はしていない）"
      - "記法表がコマンドでなく段落の並び（\\noindent 行の連続）で書かれた論文"
      - "式の直前の 3 語以下・文末記号なしの断片だけを unknown（raw.equation_connective）にした。4 語以上のつなぎ（where we introduced the notation）は本文のまま"
      - "参照が \\paragraph の \\label を指すとき、LaTeX どおり直前の式の番号になる（著者の意図した節番号ではない）"
related: [IK-0347, IK-0665, IK-0699]
view_of: []
history: []
---

## 課題

症状: 役割判定の入力に「center \ \\」「tabular@p0.24p0.70@」「,」「subequations」「itemize」だけのブロックや、著者行・ORCID・
記法表の 1 行が独立して現れ、それぞれ LLM 1 回を消費する（判定はほぼ reject / section_meta）。観察の出所は
uxsim/runs/radar_loop/BRAIN_NOTES_A.md / BRAIN_NOTES_B.md（seq 562〜646）。
原因: TeX の平文化がブロック環境として扱う集合の外の環境を地の文として素通しし、コマンドを引数へ置き換える規則が
環境名・列指定を文字列として残すため。加えて「本文ブロック」の単位が定義されておらず、前付けや表の行も同じ母集合に入る。

追加の出所（頭脳 X1・全是正入りの再解析 run 40ecf8df（seq 3313 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3313 — paper_skeleton の Abstract 欄の先頭が「[tex_b1] APS/123-QED」で、REVTeX の \preprint の残骸が要旨扱いになる）。

追加の出所（頭脳 X2・是正前の run（seq 3290〜3314））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3314 — \preprint の雛形値「APS/123-QED」が本文ブロック tex_b1 になる）。

## 発見の観点

実データの入力（役割判定に渡ったブロック本文）を読んだ（data_inspection）。

## 解決の観点

変換の側で環境を認識し（開始・終了と列指定を落とす）、本文ブロックの単位を定めた（表本体・箇条書き・前付けを別の種別に分け、記号だけの断片は作らない）。外した件数は metadata.structure_recovery に残す。


## 一般化

形式変換の段が既知の構文の閉じた集合だけを構造化し、残りを地の文に流すと、後段の「1 件 = 1 回」の処理が残骸の数だけ
走る。確かめ方は、環境・前付け・表の多い TeX を通し、本文ブロックに環境名・列指定・記号だけの断片が現れないことと、
外した断片が coverage に理由付きで残ることを見る。
