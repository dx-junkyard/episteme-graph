---
id: IK-0698
title: "TeX 取り込みで脚注の本文が段落に区切りなく合流し（theoremWe remind … / writeThe same）、「centre of inertia」が「node of inertia」に化ける"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §15.9"
  - "docs/pipeline/overview.md"
feature_context:
  realizing: "TeX ソースの本文を平文化し、脚注と本文の境界・著者の語をそのまま残して役割判定と抜粋に渡す"
  layers: [pipeline_a, url_material_fetch]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（2026-10-16）: 脚注の合流は仮説どおり _COMMAND_WITH_ARG_RE が \footnote{…} を中身に置き換えていたため。centre → node は
    tex_archive.py のどの置換規則からも起きない（_text_mode_macro_math・_expand_simple_macros は制御綴だけを展開し、素の語は
    書き換えない）ことをテストで固定した。残る候補は論文のプリアンブルの定義（例: 制御綴として書かれた語のマクロ）か原文の語で、
    当該論文の TeX ソースを読まないと確定しない。構造軸は区切りを入れるだけで足りた（別の単位の受け皿は要らなかった）ので none に改めた。
    起票時の仮説（上で確認済み）: 脚注の合流は backend/core/document_pipeline/tex_archive.py の _COMMAND_WITH_ARG_RE（コマンドを引数の中身に置き換える
    規則）が \footnote{…} を中身に置き換え、前後に区切りを入れないために起きると読める（「theorem\footnote{We …}」が
    「theoremWe …」になる形と一致）。「centre」が「node」になる件は、取り込んだ論文のプリアンブルの \newcommand / \def が地の文で
    展開されたか、別の置換規則が語の途中に当たったかのどちらかで、まだ確かめていない。確定には、当該論文の TeX ソースで
    \footnote と centre の出現箇所・マクロ定義を読み、_clean_text_fragment に通した前後を比べる。処理軸 input_handling =
    脚注の境界を地の文と区別しない。接続軸 information = 脚注である（本文の主張ではない）という情報が後段に届かない。
    構造軸 unknown = 脚注を本文と別の単位として表す受け皿が要るのか、区切りを入れるだけで足りるのかを判断していない。
    統制軸 none。
generalization:
  level: repo_pattern
  general_form: "形式変換が注釈・付記のコマンドを中身で置き換えて本文に溶かし、本文と付記の境界と語の綴りが失われる"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回 1 周目（arXiv 2610.20589・頭脳メモ seq 590・609・623・627・629・642）で役割判定の入力を読み、脚注が単語の途中に連結された文と、論文には無いはずの「node of inertia」を見た。"
resolution:
  perspective: [representation_change]
  note: "\\footnote{…}（\\footnotetext を含む）を数式の切り分けより先に「（脚注: …）」で区切り、直前の語へ密着させない（中の数式は残る）。centre → node は tex_archive.py の置換規則からは起きないことを確かめた（素の語は書き換えない・論文のマクロは制御綴だけを展開する）。当該論文の TeX ソースを外部から取らない方針のため、原因は特定していない。"
  landed_in:
    - "backend/core/document_pipeline/tex_archive.py"
    - "backend/tests/test_latex_display.py"
    - "backend/tests/test_document_pipeline.py"
    - "docs/features/url_material_upload_design.md"
  verification:
    methods: [guardrail]
    unverified:
      - "「centre of inertia」→「node of inertia」の原因（当該論文の TeX ソースのマクロ定義か原文の語か）— 未確定のまま残す"
      - "脚注を本文と別ブロックにするか（今回は段落内の括弧書きで境界を保つだけ）"
related: [IK-0696, IK-0697, IK-0665]
view_of: []
history: []
---

## 課題

症状: 本文ブロックに「The redshift factorThe factor …」「theoremWe remind …」「operator.To get」「writeThe same」のような脚注の合流と、
「node of inertia」（本来 centre of inertia と読める語）が現れる。役割判定の span 切り出しが難しくなり、抜粋が読めない。
観察の出所は uxsim/runs/radar_loop/BRAIN_NOTES_A.md / BRAIN_NOTES_B.md。
原因（仮説）: 脚注コマンドを中身で置き換える平文化の規則。centre → node は未確認。

## 発見の観点

役割判定に渡る本文ブロックを実データで読んだ（data_inspection）。

## 解決の観点

脚注は区切り（「（脚注: …）」）を入れて境界を保った。centre → node は変換の規則からは起きないことを確かめたので、規則は変えずに未確定のまま記録に残す（原文の TeX を読めば確定する）。


## 一般化

「コマンド → 中身」の一律の展開は、本文に属さない付記を本文の文に混ぜる。確かめ方は、脚注とプリアンブルのマクロを含む
TeX を通し、本文ブロックに脚注が単語の途中で連結されないこと・原文に無い語が現れないことを見る。
