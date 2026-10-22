---
id: IK-0801
title: "TeX 取り込みの題名抽出が論文マクロを展開せず未知の命令として消すため、documents.title が「… by adopting over units」になり、原題の「$\\Mpc$ over $\\Mpch$」（Mpc over Mpc/h）が落ちる"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/pipeline/overview.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "arXiv の TeX ソースから論文の題名を取り出し、教材一覧・レーダー・比較の表示名に使う"
  layers: [pipeline_a, url_material_fetch, paper_radar]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
    governance: [review]
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
    確定（原本 tarball で確認）: 原文は \title{… by adopting $\Mpc$ over $\Mpch$ units}（\newcommand{\Mpc}{{\rm Mpc}} /
    \newcommand{\Mpch}{{\rm Mpc}/h}）。題名の経路 _clean_text は $ を外すだけで消してはいなかったが、論文マクロを展開しないため
    \Mpc / \Mpch を未知の命令として _COMMAND_RE で消していた。「Mpc」が 2 つとも落ちたのはどちらもマクロだったから（起票時の
    仮説「$…$ を中身ごと除去」は誤り）。本文の経路 _clean_prose はマクロを展開して数式を保つ（IK-0764 系の是正）が、題名の経路に
    及んでいなかった。処理軸 input_handling = 題名の TeX の命令の扱いが誤り。統制軸 review（low）= 同じ規則を面ごとに直し題名の面の
    検査が無かった。構造軸 none / 接続軸 none。
generalization:
  level: general
  general_form: "同じ入力から複数の面へ文字列を取り出すとき、数式の扱いの是正を一方の面にだけ入れるため、他方の面では中身ごと消えて意味が欠ける"
pattern: rule-enforced-per-surface
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で 2610.08751 を TeX 経路で取り込んだ直後（取り込み決定は uxsim/runs/radar_loop/scan-09.decision.json）、実 DB の documents 行（document 1471ff7f）の title が「Mitigating prior-volume effects in galaxy clustering by adopting over units」で、原題の「Mpc over $h^{-1}$Mpc」が欠けていた。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    題名は本文と同じ _clean_prose（論文マクロ展開・数式保持）を通し、その $…$ 区間だけを数式 → 平文の正本
    core.theory_modules.schema.plain_math で記号を残した平文に落とす（_clean_title）。documents.title は一覧・レーダー・比較・
    ファイル名代替で平文として読まれるので列は 1 つのまま平文とし、title_tex 列（migration）は足さない — マクロ展開後の平文で
    情報は落ちず（「adopting Mpc over Mpc/h units」）、題名を数式描画する読み手が無いため。既存 document 1471ff7f の title は
    未修正（再解析か題名だけの UPDATE はオーナー操作）。
  landed_in:
    - "backend/core/document_pipeline/tex_archive.py"
    - "backend/tests/test_document_pipeline.py"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "既存 document 1471ff7f の documents.title の書き換え（再取り込み・UPDATE は未実行）"
      - "docker で組み上げた実機での再取り込みと教材一覧・レーダーの表示"
related: [IK-0764, IK-0696, IK-0707]
view_of: []
history: []
---

## 課題

症状: 2610.08751 を TeX 経路で取り込むと documents.title が「Mitigating prior-volume effects in galaxy clustering by adopting over units」になり、
原題の「Mpc over $h^{-1}$Mpc」の数式部分と直前の Mpc が落ちる。題名は教材一覧・レーダーの起点・比較の表示名に出るため、意味の欠けた
題名が広く表示される。
原因（確定）: 題名の経路が論文マクロ（\\Mpc / \\Mpch）を展開せず未知の命令として消していた。本文の経路のマクロ展開・数式保持が題名の経路に及んでいなかった。

出所: uxsim/runs/radar_loop/scan-09.decision.json（取り込みの決定）と実 DB の documents 行（document 1471ff7f）。

追加の出所（頭脳 U1）: uxsim/runs/radar_loop/BRAIN_NOTES_U1.md（seq 3019 — paper_skeleton の入力でも題名が「… adopting over units」と語が欠けたまま）。

是正前の出所（頭脳 X2・是正前の run（seq 3290〜3314））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3302 — landscape_placement の材料の paper.title が「… adopting over units」）。

## 発見の観点

取り込んだ document の title を arXiv の原題と突き合わせた（data_inspection）。

## 解決の観点

題名を本文と同じ整形（_clean_prose）に通し、数式区間だけを plain_math で平文化した（正本の再利用）。原本 4 本（本件 + c795b2cf / a8d731be / 842bd9cb）を新しい抽出で読み直し、本件だけが「Mitigating prior-volume effects in galaxy clustering by adopting Mpc over Mpc/h units」に変わり他 3 本は DB の題名と一致した。回帰テスト 2 件を追加。

実機の確認（reproduction_rerun）: 2026-10-17 03:49 の全再解析（document 1471ff7f・run 40ecf8df）のあと、documents.title は「… adopting Mpc over Mpc/h units」で数式部分が保たれた。verification の unverified に挙げた「既存 document の title の書き換え」は、この再解析で起きたことを確かめた（unverified の欄は書き換えずに残す）。

## 一般化

数式の扱いを面ごとに直すと、直していない面で意味が欠ける。辞書の型 rule-enforced-per-surface。IK-0696 族（TeX の残骸が本文に残る）とは逆向きの、
必要な部分が落ちる害なので別エントリにした。
