---
id: IK-0827
title: "TeX のダッシュの記法「--」「---」が本文に残り、「central--satellite」と表示され、役割判定の span が「---」で割れて次の span が「---we will omit …」とダッシュで始まる"
status: resolved
recorded_at: 2026-10-17
resolved_at: 2026-10-22
sources:
  - "docs/pipeline/overview.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "TeX ソースの本文を平文化し、記号と文境界を読める形で後段（役割判定・表示）に渡す"
  layers: [pipeline_a, url_material_fetch]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
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
    確定（2026-10-22・tex_archive を読んだ）: 地の文の平文化（_clean_text_fragment / _clean_text）に -- / --- を扱う箇所が無く、
    \\textemdash / \\textendash は命令の除去で字ごと消えていた。以下は起票時の仮説: TeX 取り込みの平文化が、TeX の合字の規則（-- = en ダッシュ、--- = em ダッシュ）を文字に置き換えず、記法のまま本文に残す。
    役割判定（LLM）は「---」を文の区切りと見て span を割り、次の span がダッシュで始まる。処理軸 input_handling = TeX の合字の記法の正規化が
    無い。構造軸 none / 接続軸 none / 統制軸 none。確定には tex_archive の平文化で -- / --- を扱う箇所が無いことを確かめる。
generalization:
  level: general
  general_form: "入力の書式の記法（合字の綴り）を文字へ正規化しないため、記法が本文に残り、表示と文境界の判断を乱す"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 AA2 が 2609.35757 の再解析 run 54e85a2a で「central--satellite」（seq 3839）と、tex_b61 が「---」で 2 span に割れ次の span が「---we will omit …」で始まること（seq 3860）に気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_AA2.md）。頭脳 Z2 も「---」が span 境界にかかること（seq 3599・3605）と「central--satellite」（seq 3710）を記している（uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md）。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: "地の文の平文化の 1 箇所（_clean_text_fragment と前付けの _clean_text）で tex_archive._tex_dashes を通し、--- を em ダッシュ（—）、-- を en ダッシュ（–）、\\textemdash / \\textendash を同じ字にする。数式の区間は通らないので減算・負号は不変。参照の範囲（\\ref{a}--\\ref{b}）は参照の置換の後に通るので範囲の判定は変わらず、出力は「B–F」になる。本文・節題・caption・表・脚注が同じ経路を通る。"
  landed_in:
    - "backend/core/document_pipeline/tex_archive.py"
    - "backend/tests/test_latex_display.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "記法の正規化を面ごとに置かず、全ての地の文が通る平文化の 1 箇所に置いた。"
  verification:
    methods: [guardrail]
    unverified:
      - "live re-ingest not run（2609.35757 の再取り込み・再解析で、役割判定が em ダッシュの位置で span を割らなくなるかは見ていない — 記法は消えるが、LLM が em ダッシュを文の区切りと読む可能性は残る）"
related: [IK-0696, IK-0697]
view_of: []
history: []
---

## 課題

症状: TeX の「--」「---」が本文に残り、「central--satellite」と表示される。役割判定では tex_b61 が「---」で 2 span に割れ、次の span が「---we will omit …」とダッシュで始まる。
原因（仮説）: TeX の合字の記法を文字に正規化していない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_AA2.md（seq 3839・3860）、uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3599・3605・3710）。

## 発見の観点

span の本文と境界を、TeX の原文の記法と突き合わせた（data_inspection）。

## 解決の観点

2026-10-22: 平文化の 1 箇所で合字の記法を字にした。本文・節題・数式の境界を実際の TeX 断片で回帰テスト（TestTexDashLigatures）に固定した。

## 一般化

書式の記法を正規化しないと、表示と境界の判断を乱す。辞書の型 information-dropped-as-unrepresentable の近傍（記法を文字として表していない）。
