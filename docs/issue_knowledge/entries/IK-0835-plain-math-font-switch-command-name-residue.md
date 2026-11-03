---
id: IK-0835
title: "近い構造・理論モジュールのラベルで、書体の切り替え命令 \\cal と添字の印 \\star が命令名の字として残り「cal L_star」と表示される"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-22
sources:
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "理論モジュールのラベル（$…$ の LaTeX）を、近い構造の対・照合の名指し・グラフレビューのノード名に読める平文で出す"
  layers: [graph_review, paper_radar]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
    governance: [none]
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
    確定（関数の再実行）: 表示用の平文化の正本 core.theory_modules.schema.plain_math は、囲む形の書体命令（\mathcal{L}）と
    一部の切り替え命令（\rm \bf \it \bm）だけを外し、\cal / \sf / \tt などの切り替え命令は最後の「\命令名 → 命令名」の規則で
    字として残した。\star も記号の表に無く「star」になった（\cal L_\star → 「cal L_star」）。引数を波括弧で囲まない書体命令が
    ギリシャ文字の前に来る形（\boldsymbol\theta）も「boldsymbolθ」になった。照合キー symbol_match_key も同じく切り替え命令と
    囲まない書体命令を外さず（「call_star」「mathcall_*」「textbfx」）、同じ記号の書き方の違いが別の記号になっていた。
    処理軸 input_handling = 入力の記法の正規化の漏れ。近い構造の名指しは related._display_label → plain_math の 1 経路を
    通っており（IK-0786 で正本化済み）、経路ではなく正本の規則の穴。他の軸は none。
generalization:
  level: repo_pattern
  general_form: "記法を平文へ落とす正本が、命令の一部の書き方（囲む形）だけを外し、別の書き方（切り替え形・囲まない形・未登録の記号）を命令名の字として残す"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回の深い段の審判が、近い構造のラベルに `cal L_star`（\\cal の命令名の残骸の可能性）を見つけた（出所: uxsim/runs/radar_loop/judge-deep-09.md の所見 2・3）。同系統の textbfx は IK-0786 で是正済み。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: "正本 plain_math に書体の切り替え命令の表 FONT_SWITCH_RE（\\rm \\bf \\it \\bm \\cal \\sf \\tt \\sl \\sc \\mit \\boldmath と \\displaystyle 系）を置き、命令ごと外す。書体は記号の同一性に関わらないので \\mathcal{L} も \\cal L も「L」にそろえる。添字の印 \\star / \\ast は落とさず「⋆」「∗」にする（\\mathcal{L}_\\star → 「L_⋆」）。囲まない書体命令がギリシャ文字などの前に来る形も中身を残す。照合キー symbol_match_key は同じ表で切り替え命令を外し、囲む形・囲まない形の書体命令（\\textbf 系・\\mathbb 系を追加）を外し、\\star と * を同じ印として読む（L_\\star と L_* は同じキー）。アクセント（\\hat 等）は照合キーでは外さない（推定量 x̂ と x を同一視しない）。表示では従来どおり外す（既存の契約 n·k）。"
  landed_in:
    - "backend/core/theory_modules/schema.py"
    - "backend/tests/test_theory_module_core.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "近い構造・照合の名指し・保存行・ノード名はすでに plain_math 1 つを通る（IK-0786）。面に足さず正本の規則を直し、照合キーも同じ切り替え命令の表を使う。"
  verification:
    methods: [guardrail]
    unverified:
      - "live re-ingest not run（judge-deep-09 の近い構造の対を、是正後のレーダー画面で読み直していない）"
      - "保存行の visual_label は再解析まで旧規則の値のまま（related は $ 付きの label から読み時に作り直すので表示には出ない見込み・未確認）"
      - "照合キーの変更で、既存の理論モジュールの入出力記号の結び付きが変わる範囲（\\rm を含む記号のキーが変わる）は実データで見ていない"
related: [IK-0786]
view_of: []
history: []
---

## 課題

症状: 近い構造のラベルに `cal L_star` が出る（`\cal L_\star` / `\mathcal{L}_\star` の命令名の残骸）。
原因: 平文化の正本 plain_math が書体の切り替え命令と `\star` を知らず、最後の「命令名を字にする」規則で残した。照合キーも同じ穴。

出所: uxsim/runs/radar_loop/judge-deep-09.md（所見 2「近い構造のラベルに命令名の残骸（`cal L_star`）」・所見 3）。

## 発見の観点

画面に出るラベルを、TeX の記法と突き合わせた（data_inspection）。

## 解決の観点

正本の規則の穴を埋めた（切り替え命令の表を 1 つ置き、表示と照合キーの両方が使う）。`\mathcal{L}` → 「L」、`_\star` は「⋆」として残す（印は落とさない）。

## 一般化

記法を平文に落とす規則は、命令の書き方の変種（囲む形 / 切り替え形 / 囲まない形）ごとに抜けが出る。IK-0786（textbfx）と同じ正本の、別の書き方の穴。
