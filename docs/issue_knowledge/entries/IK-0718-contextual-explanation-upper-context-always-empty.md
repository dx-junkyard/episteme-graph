---
id: IK-0718
title: "二層説明（contextual_explanation）の材料で式の upper_context が全件空になり、その式が論文のどの主張を支えるかが説明の材料に入らない"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/features/hierarchical_context_explanation_design.md"
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "式ごとの文脈的な説明で、その式が論文中で何を支えるか（上位の文脈）を材料として LLM に渡す"
  layers: [hierarchical_explanation, pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [unknown]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 主張 → 式のリンクが claim_object_builder / equation_semantics の linked_claim_ids に載っていないか、載っていても
    upper_context を組み立てる突合の粒度（agent ID と UUID・ブロック単位と span 単位）が合わずに全件外れている。
    同じ教材の 6 バッチ（本論・付録とも）で upper_context が一度も埋まっていない。接続軸 information = 上位の文脈となる
    主張の情報が材料組み立ての段まで届いていない。構造軸 unknown = リンクの受け皿が無い（表現の欠如）のか、受け皿はあるが
    渡していないのかを見ていない。処理軸 none / 統制軸 none。確定には、該当教材の equation_semantics / claim_object_builder
    artifact で式に linked_claim_ids があるか、upper_context の組み立てコードがどのキーで引くかを確認する。
generalization:
  level: repo_pattern
  general_form: "下位から上位への逆参照（どの主張がこの要素を使うか）を前段が持たないか後段が引けず、文脈材料の上位側が常に空になる"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 L0 が contextual_explanation の要求（seq 1986〜2028）を応答する際、upper_context が全バッチで空であることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_L0.md）。"
resolution:
  perspective: [pending]
  note: "未解決。まず linked_claim_ids の有無と突合のキーを確かめ、受け皿が無ければ主張 → 式の参照を artifact から逆引きして運ぶ（carry_through）、あれば引き方を直す。"
  landed_in:
    - "docs/features/hierarchical_context_explanation_design.md"
related: [IK-0717]
view_of: []
history: []
---

## 課題

症状: contextual_explanation の材料で `upper_context` が全件空。付録の式を含め、各式が論文のどの主張を支えるかが説明の材料に無い。
原因（仮説）: 主張 → 式のリンクが claim_object_builder / equation_semantics の `linked_claim_ids` に無いか、突合の粒度が合わない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_L0.md（seq 1986〜2028）。

追加の出所（2609.35757・頭脳 T0）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2943・2946 — 式要素の upper_context が全件空。claim 要素の upper_context も節名だけ — seq 2958）。

追加の出所（頭脳 T2）: uxsim/runs/radar_loop/BRAIN_NOTES_T2.md（seq 2942〜2951 — 式要素の upper_context が全件空で、式が何を支えるかを書けない）。

追加の出所（2610.08751・頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3293・応答は後任が採用 — 8 式とも upper_context が空で、thesis_reconstruction で式 ID を支持構造に張ったのに反映されない。lower_context の derived_from は式の要約文で式 ID が無く出所を辿れない）。

追加の出所（頭脳 X1・是正前の run（seq 3289〜3310・2026-10-17 03:00〜03:11））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3292 — 式 8 件とも upper_context が空）。

追加の出所（頭脳 X0・是正前の run（seq 3288〜3312））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3291・3294 — 式 8 件とも upper_context が空）。

追加の出所（頭脳 X2・是正前の run（seq 3290〜3314））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3293 — 式 8 件とも upper_context が空で、グラフでは main ノードに結ばれている eq_multipoles 等も説明層に届かない。seq 3296 — comp_004 の子 6 件の upper_context が合成主張 1 件だけで中心命題の主張に繋がらない）。

追加の出所（第 2 波 deploy 後の run・頭脳 Z0/Z1）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3645・3651 — 式 8 件とも upper_context が空）、uxsim/runs/radar_loop/BRAIN_NOTES_Z1.md（seq 3649 — 同じく空。seq 3616 で equations[].linked_claim_ids が全 24 式で空・claims 側の equation_ids も空で、式と主張の結線が材料に届いていない）。

## 発見の観点

実際の材料の中身を読んだ（data_inspection）。

## 解決の観点

未解決。リンクの有無（表現）と引き方（伝達）のどちらで落ちているかを切り分けてから直す。

## 一般化

逆参照を持たない構造では、上位の文脈を求める後段が常に空を受け取る。辞書の型 information-dropped-as-unrepresentable。
