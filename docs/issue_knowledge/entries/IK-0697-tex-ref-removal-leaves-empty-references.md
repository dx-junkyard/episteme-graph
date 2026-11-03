---
id: IK-0697
title: "TeX 取り込みで \\ref・\\eqref・\\cite を空文字に置き換えるため、本文に「In , we」「Eq.,」「Table .」「by authors of ,」の跡が残り、学習者向けの抜粋が読めない文になる"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §15.9"
  - "docs/pipeline/overview.md"
feature_context:
  realizing: "TeX ソースの本文を平文化し、節・式・表・文献への参照を読める形のまま本文・主張・抜粋に残す"
  layers: [pipeline_a, url_material_fetch]
classification:
  axes:
    processing: [input_handling]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（backend/core/document_pipeline/tex_archive.py の _clean_text_fragment / _clean_text）: 地の文の平文化が
    \label・\ref・\eqref・\cite（citep / citet を含む）を引数ごと空文字に置き換えるため、参照の位置に何も残らない。
    処理軸 input_handling = 参照コマンドを「消してよい装飾」と同じ扱いにしている。構造軸 representation = TeX のラベル→
    印字番号（節番号・式番号・表番号）と文献キー→書誌の対応を本文に差し戻す受け皿が無い（数式側の参照は _replace_math_refs
    で別処理されるが、地の文には無い）。接続軸 information = 参照先を指す情報が変換の段で落ち、後段の抜粋・主張本文に届かない。
    統制軸 none = 順序・予算・担当の問題ではない。
generalization:
  level: repo_pattern
  general_form: "形式変換が参照・相互リンクのコマンドを装飾と同じく削除し、参照先の手掛かりの無い文が後段の表示に残る"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回 1 周目（arXiv 2610.20589・頭脳メモ seq 562〜646）で役割判定の入力を読み、引用・節参照・式参照・表参照の位置が空になった文が繰り返し現れることを見た。"
resolution:
  perspective: [representation_change, carry_through]
  note: "取り込み時に本文からラベル → (種類, 印字番号) の対応（_build_label_map）を決定論で数え、地の文の \\ref / \\eqref / \\cref / \\autoref を「式 (3)」「§2」「図 1」「表 2」に置く（直前が Eq. / Section / Fig. / Table なら番号だけ）。数えられない（\\numberwithin・付録の式・定理）参照はラベルを残す。\\cite 系は固定の目印 [引用] を 1 つ。数式内の参照も同じ対応で番号にし、式ブロックに raw.printed_numbers を載せた。"
  landed_in:
    - "backend/core/document_pipeline/tex_archive.py"
    - "backend/tests/test_latex_display.py"
    - "backend/tests/test_document_pipeline.py"
    - "docs/features/url_material_upload_design.md"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "再演は取り込み済みの tarball（2610.08908・PN 論文ほか 3 本）を MinIO から読み、構造化まで。頭脳が報告した「Eq. to any」「In we show」は PN 論文の古い artifact（resume）由来で、現行コードを通すと [引用] / 番号に置かれることを確かめた。解析の再実行・新しい artifact は確かめていない"
      - "引用の目印は 2026-10-16 に [引用] から言語に依らない [ref] へ変えた（\\citet と文頭の引用は the authors of [ref]）。既に取り込まれた教材は再解析まで [引用] のまま"
      - "\\numberwithin{equation}{section}（同梱 .sty・\\@addtoreset・\\thesection.\\arabic{equation} の再定義を含む）は数えるようにした。それ以外の独自の番号付け（\\counterwithin の派生パッケージ・独自カウンタ・cleveref の書式設定）は数えない"
      - "式の表示名（equation_display_label）への印字番号の配線（IK-0681 は未着手）"
related: [IK-0681, IK-0665, IK-0696]
view_of: []
history: []
---

## 課題

症状: 本文・主張・学習者向けの抜粋に「In , we …」「Eq., …」「Eqs. and」「Table .」「by authors of ,」「 .」が残る。観察の出所は
uxsim/runs/radar_loop/BRAIN_NOTES_A.md / BRAIN_NOTES_B.md（seq 570・576・581・597・616・627・629・639 ほか）。
原因: 地の文の平文化が参照コマンドを引数ごと空文字にし、参照先を表す情報を本文に差し戻す仕組みが無いため。

## 発見の観点

役割判定に渡る本文ブロックを実データで読んだ（data_inspection）。

## 解決の観点

取り込み時に作るラベル → 印字番号の対応で参照を置き換えた（受け皿を作る = representation_change）。対応が無いときも空にせずラベルを残し、引用は固定の目印にした（情報を落とさない = carry_through）。違う番号を出す危険がある番号付け（\numberwithin・付録の式）は数えず、ラベルのまま残す。


## 一般化

変換で「表示に要らない」と判断して消したコマンドが、実は文の成分（主語・目的語）だった、という型。確かめ方は、節・式・表・
文献の参照を含む TeX を通し、本文に参照先の目印か番号が残り、空の句読点の並びが現れないことを見る。
