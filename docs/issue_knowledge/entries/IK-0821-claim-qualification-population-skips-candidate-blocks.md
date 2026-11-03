---
id: IK-0821
title: "再解析 run で claim_qualification に回る span のブロック列が飛び、役割判定で主張候補とされたブロック（tex_b30・b77・b83・b87）が主張採否に 1 件も来ない"
status: open
recorded_at: 2026-10-17
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "役割判定で主張候補になった span を、主張採否の母集合として LLM に回す"
  layers: [pipeline_a]
classification:
  axes:
    processing: [unknown]
    structure: [unknown]
    connection: [information]
    governance: [unknown]
  axis_confidence:
    processing: low
    structure: low
    connection: low
    governance: low
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 主張候補の span が claim_qualification の母集合に入る前に落ちているか、母集合には入っているが処理の順序が文書順でなく後回しに
    なっている。どちらかは材料から判別できない（頭脳 Y0 も「取りこぼしか後回しか要確認」）。接続軸 information（low）= 役割判定の結果が
    主張採否に届かない可能性。処理軸・構造軸・統制軸は unknown（打ち切り・層化サンプリング・母集合の選別・順序のどれが原因かを見ていない）。
    確定には run 40ecf8df の claim_qualification artifact の coverage（母集合・処理済み・打ち切り）と、tex_b30・b77・b83・b87 の span が
    母集合にあるかを読む。決定論 reject（IK-0803）で外れたなら role_labels が section_meta 系のはずで、役割判定の結果（X0 seq 3321・3348・
    3351・3354 で主張候補あり）と食い違う。
generalization:
  level: general
  general_form: "前段で候補になった項目が後段の母集合に現れず、落ちたのか後回しなのかが記録から読めない"
pattern: upstream-loss-masked-downstream
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 Y0 が再解析 run 40ecf8df の claim_qualification（2610.08751・seq 3405〜3456）のブロック列を追い、b23・b25・b26・b27・b36・b38・b40 の後で tex_b30 が抜け、tex_b74 の次が tex_b90 で tex_b77・b83・b87 が来ていないことに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_Y0.md。役割判定の側は頭脳 X0 が主張候補ありと記録: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md）。"
resolution:
  perspective: [pending]
  note: "未解決。まず coverage と artifact で落ちた理由（打ち切り・選別・順序）を確かめる。落ちていたなら理由を coverage に残し、後回しなら記録の順序を文書順にそろえる。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0803, IK-0815]
view_of: []
history: []
---

## 課題

症状: 再解析 run 40ecf8df で、claim_qualification に回る span のブロック列が tex_b27 → tex_b36（tex_b30 が抜ける）、tex_b74 → tex_b90（tex_b77・b83・b87 が抜ける）と飛ぶ。
これらのブロックは役割判定で主張候補ありと判定されていた。
原因（仮説）: 母集合に入る前に落ちたか、処理が後回しになっている。どちらかは未確認。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_Y0.md（seq 3423・3456）、uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3321・3348・3351・3354）。

追加の観察（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3573 — tex_b30 の 4 span が equation_semantics の candidate_text_spans に並び、少なくとも役割判定の結果としては保持されている。claim_qualification の母集合に入ったかは未確認のまま）。

## 発見の観点

claim_qualification に来たブロック列を、役割判定で主張候補になったブロックと突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

前段の候補が後段に現れないとき、落ちたのか後回しなのかが記録から読めない。辞書の型 upstream-loss-masked-downstream（原因未確定）。
