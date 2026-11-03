---
id: IK-0719
title: "後段 agent の入力で claim_id が二系統（claim:tex_bN:span_k と claim_span_k_NN）混在し、同じ文の主張が親と原子化子の両方で accepted として並ぶ"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/features/knowledge_objects_design.md"
feature_context:
  realizing: "採択された主張を一意な識別子で後段（thesis_reconstruction・dsl_linking・component_assembly）へ渡し、参照を張らせる"
  layers: [pipeline_a, knowledge_objects]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: claim_qualification の span 由来の ID（claim:tex_bN:span_k）と claim_object_builder の claim object ID（claim_span_k_NN）が
    同じ accepted_claims の列に並び、親 span と、それを原子化した子（同文）が両方採択扱いで後段に渡っている。
    例: claim:tex_b2:span_002 と claim_span_002 / claim:tex_b224:span_001 と claim_span_001_29 が同文。構造軸 representation = 同じ主張を
    1 つと数える同一性の表現（どちらの名前空間が正か・親子の関係）が入力に無い。接続軸 target = 後段が同じ対象を 2 つとして参照し、
    参照先がずれる（主結果 claim_span_001_40 が dsl_linking の入力に無い一方で thesis 側にはある）。処理軸 none / 統制軸 none。
    確定には、後段入力を組み立てるコードで accepted_claims の母集合（span / claim object / 親子の扱い）を確認する。
generalization:
  level: general
  general_form: "同じ対象を指す 2 つの名前空間の識別子が 1 つの列に並び、重複と参照の取り違えが後段に流れる"
pattern: id-namespace-conflated
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 L0 が thesis_reconstruction（seq 1962）と dsl_linking（seq 1968）の入力を読み、ID 形式の混在と同文 claim の重複に気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_L0.md）。"
resolution:
  perspective: [pending]
  note: "未解決。後段に渡す主張の母集合を 1 つの名前空間に揃え、原子化された親は子に置き換える（または親子関係を明示して片方だけを採択扱いにする）。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0724, IK-0733]
view_of: []
history: []
---

## 課題

症状: thesis_reconstruction / dsl_linking の入力で claim_id が `claim:tex_bN:span_k` と `claim_span_k_NN` の二系統で混在し、同文の主張が
親と原子化子の両方で accepted になっている。頭脳は同じ主張を 2 つの参照として扱わされる。
原因（仮説）: span 由来の ID と claim object の ID が同じ列に並び、親子の関係が入力に表れていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_L0.md（seq 1962・1968）。

同根の再発: uxsim/runs/radar_loop/BRAIN_NOTES_Q1.md（seq 2545 / 2551 / 2569）— thesis 入力で 'claim:tex_bN:span_M' と 'claim_span_M_K' が混在し、component_graph の材料（claim_span_003_31 等）と landscape_placement の材料（claim_span_001 / 002）がそれぞれ別系統で、同じ形の ID が段ごとに別の主張を指す。

追加の出所（2609.35757・TeX 経路・頭脳 T1）: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2932 — claim_id が claim_span_005 / claim_span_001_11 / claim:tex_bNN:span_NNN の 3 系統で混在。seq 2938 — DSL 辺の claim は claim:tex_bNN 形式、Material 側は claim_span_* 形式で同じ主張が別 ID）。

追加の出所（2610.08751・頭脳 W0）: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md（seq 3282 — thesis_nodes が参照する claim_span_005_52 / claim_span_003_79 / claim_span_002_60 が accepted_claims に無い。dsl_linking の入力が親主張である件は IK-0807）。

追加の出所（頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3283・3289 — 旧形式 claim:tex_bN:span_M と claim_span_N_M の二系統が component_assembly と component_graph の入力でも食い違う。詳細は IK-0733）。

追加の出所（頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3281 — thesis_reconstruction の accepted_claims でも claim:tex_bN:span_M と claim_span_M_K の 2 系統が混在）。

追加の出所（頭脳 Z2）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3674 — 同じ主張が landscape では claim_span_002_sub01、thesis（seq 3608）では claim:tex_b23:span_002 と段ごとに別の ID 体系）。

同根の観察（頭脳 AA2）: uxsim/runs/radar_loop/BRAIN_NOTES_AA2.md（seq 3842 — candidate_text_spans の span_id がブロックをまたいで重複。ID の名前空間がブロック内に閉じている件は IK-0724）。

追加の出所（第 25 周 第 6 周の前・PINN TeX 再解析 ce667c63・製品頭脳 A / B）: uxsim/runs/round25/BRAIN_NOTES_product_B_r6pre.md（4225 — claim_centered_plan と thesis_nodes の claim ID は claim:tex_b10:span_001 形、available_claims は claim_span_001_sub01 形で、headline_claim_ids が available に無く突き合わせが空になる。available_derivation_ids の step_001 が derivation 名で修飾されず重複。4233 — 主張 8 件すべて upper_context が空で、同根の疑い）、BRAIN_NOTES_product_A_r6pre.md（4224 — thesis の claim_ids に旧形 claim:tex_b18:span_002 が混在）。IK-0733（refiner の headline 主張 ID）と同じ二系統。

## 発見の観点

実際の後段入力を読んだ（data_inspection）。

## 解決の観点

未解決。名前空間を 1 つに揃え、原子化の親子を明示する。

## 一般化

複数の採番体系が 1 本の列に混ざると、重複も参照ずれも後段で区別できない。辞書の型 id-namespace-conflated。
