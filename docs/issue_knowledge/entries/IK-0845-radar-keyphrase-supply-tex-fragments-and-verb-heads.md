---
id: IK-0845
title: "論文レーダーの供給語に、中心命題の TeX 数式の断片（rm Mpc・CDM）と題名の動詞を頭にした断片（adopting Mpc）、スラッシュで割れた語（h units）が入り、網を一般語に寄せる"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-22
sources:
  - "docs/features/paper_radar_design.md §25"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "起点の論文の題名・中心命題・要旨から、arXiv の検索語になる名詞句を機械供給する"
  layers: [paper_radar, paper_discovery]
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
    確定（関数の再実行・実物の中心命題）: 中心命題（thesis_reconstruction の central_thesis.text）は日本語の文に
    `${\rm Mpc}/h$` `$\Lambda$CDM` のような TeX の数式区画を含む。供給の段④ title_thesis_keyphrases は TeX を落とさず
    英字の連なりを取るため、命令名 `rm` と数式に隣接した断片 `CDM` が句になった。題名は `by adopting Mpc over Mpc/h units`
    で、連なりの頭の動名詞 `adopting` を外す規則が 1 語のときにしか無く「adopting Mpc」が句になり、区切り文字に `/` が
    含まれて「Mpc/h」が「Mpc」と「h units」に割れた。処理軸 input_handling = 材料の記法（TeX・スラッシュ・動詞の頭）の正規化の漏れ。
    供給の経路は 1 本（title_thesis_keyphrases）で、他の軸は none。
generalization:
  level: general
  general_form: "文から検索語を機械的に切り出すとき、記法の区画（数式）と記号の結合（スラッシュ）と語の品詞（動詞の頭）を見ずに英字の連なりだけで句を作り、断片が検索語になって網を一般語に寄せる"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [symptom_report, data_inspection]
  note: "オーナーがブラウザで 2610.08751 を起点に開いたレーダー画面のスクリーンショット（uxsim/runs/radar_loop/screenshot-2026-10-22-1811.png）に `rm Mpc` `CDM` `adopting Mpc` `h units` のチップが並び、CDM が網を広げていた。中心命題の原文を document_analysis_artifacts で読んで TeX の由来を確かめた。"
resolution:
  perspective: [canonical_source]
  note: "供給の入口（title_thesis_keyphrases）で strip_tex_for_phrases を通し、`$…$` の数式区画とそれに隣接した英字の断片・制御綴り・波括弧を句の材料から外す（表示・保存の本文は変えない）。語の正規表現で `/` を語の内側に認め（`Mpc/h` は 1 語）、区切り文字から `/` を外す。連なりの頭の一般動詞の動名詞（_GENERIC_GERUNDS。adopting / mitigating 等を追加）は句に含めず次の語から始める（分野の名詞 clustering は対象外）。実物の題名・中心命題で `prior-volume effects` / `galaxy clustering` / `Mpc/h units` / `PVE` だけが残ることを確かめた。"
  landed_in:
    - "backend/core/paper_discovery/radar.py"
    - "backend/tests/test_paper_radar_core.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "供給の面（チップ・検索式・当たり外れ）に手を入れず、句を作る 1 箇所の入口で材料を正規化する。"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "是正後の供給で arXiv を検索し直した網は未確認（arXiv 呼び出しの予算で止めた — P-0007）"
      - "日本語の中心命題から英字の固有名（PVE）だけが残るのは意図どおりだが、英語の中心命題での副作用（数式に隣接した正当な語の脱落）は実データで見ていない"
related: [IK-0760, IK-0800, IK-0802]
view_of: []
history: []
---

## 課題

症状: レーダーのキーフレーズに `rm Mpc` `CDM` `adopting Mpc` `h units` が並び、`CDM` だけに当たる候補が網の大半を占めた
（2610.08751・2026-10-22 のスクリーンショット）。
原因: 供給の段④が TeX の数式区画・スラッシュ・動詞の頭を見ずに英字の連なりから句を作っていた。

## 発見の観点

画面のチップを中心命題の原文（TeX 入り）と突き合わせた（symptom_report → data_inspection）。

## 解決の観点

句を作る 1 箇所の入口で材料を正規化する（canonical_source）。落とすのは検索語の材料だけで、表示・保存の本文は変えない。

## 一般化

文から検索語を機械的に切り出すとき、記法の区画と記号の結合と語の品詞を見ずに英字の連なりだけで句を作ると、断片が検索語になる。
