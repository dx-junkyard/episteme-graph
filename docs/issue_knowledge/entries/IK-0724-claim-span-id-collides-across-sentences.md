---
id: IK-0724
title: "採択された主張の claim_span ID（claim_span_002_59・claim_span_002_82）が同じ節の別々の 2 文に重複して付き、後段で別の主張に結ばれる危険がある"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/features/knowledge_objects_design.md"
feature_context:
  realizing: "採択された主張を一意な識別子で後段（thesis_reconstruction 以降）へ渡し、参照を張らせる"
  layers: [pipeline_a, knowledge_objects]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: low
    structure: medium
    connection: low
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: claim_span_{span}_{連番} の材料（span の番号と連番）が文書全体では一意にならないスコープ（ブロック内・節内の数え直し）で
    作られ、同じ節の別ブロックの 2 文が同じ ID を受け取る。例: claim_span_002_59 が sec_11 の別々の 2 文（relation / method_choice）、
    claim_span_002_82 が sec_17 の 2 文（comparison / definition）に付く。構造軸 representation = 主張を一意に指す識別子が表せていない。
    処理軸 logic = 採番の材料の選び方（確信度 low）。接続軸 none（確信度 low: 後段で実際に取り違えたかは見ていない）。統制軸 none。
    型は名前空間の混同（IK-0719）ではなく、局所でのみ一意な番号を全体のキーに流用したものと読んで id-unique-only-within-inner-scope とした。
    確定には、claim_object_builder の claim_id 採番（span 番号と連番の由来）を読み、該当教材（a8d731be）の artifact で重複 ID の 2 文の
    block_id を確かめる。
generalization:
  level: general
  general_form: "内側のスコープで数え直す番号から全体の識別子を作り、別の対象が同じ識別子を受け取る"
pattern: id-unique-only-within-inner-scope
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 L2 が thesis_reconstruction（seq 2039・トライスペクトル論文）の accepted_claims を読み、同じ ID が別の 2 文に付いていることに気付き、使わずに review_notes に明記した（出所: uxsim/runs/radar_loop/BRAIN_NOTES_L2.md と uxsim/runs/radar_loop/brainL2_2039.json）。"
resolution:
  perspective: [pending]
  note: "未解決。採番の材料に block_id など文書内で一意な外側の文脈を足すか、内容由来の stable_key と同じ作法で一意化し、重複を検出したら後段に渡す前に止める。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0719]
view_of: []
history: []
---

## 課題

症状: thesis_reconstruction の入力で claim_span_002_59 が sec_11 の別々の 2 文に、claim_span_002_82 が sec_17 の別々の 2 文に付いている。
頭脳は取り違えを避けるためその主張を使わなかった。
原因（仮説）: 採番の材料がブロック内・節内の数え直しの番号で、文書全体で一意にならない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_L2.md（seq 2039）。

同根の再発: uxsim/runs/radar_loop/BRAIN_NOTES_Q1.md（seq 2545）— 同じ claim_span_004_32 が tex_b45:span_004 と tex_b55:span_004 の 2 主張に付き、頭脳は旧形式 ID で参照した。

追加の出所（2609.35757・TeX 経路・頭脳 T1）: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2932 — accepted_claims の claim_span_003_62 が tex_b145:span_003 と tex_b148:span_003 の 2 主張に付く）。

追加の出所（頭脳 T0）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2931 — claim_span_003_62 が tex_b145（方法選択）と tex_b148（解釈）の 2 主張に付く。T1 seq 2932 と同じ衝突）。

追加の出所（2610.08751・頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3281 — claim_span_002_54 が tex_b110:span_002 と tex_b111:span_002 の別主張に重複。legacy_claim_id は別で一意。seq 3284 — claim_span_001_sub01 も block を含まず文書内で一意か疑わしい）。

追加の出所（2610.08751・頭脳 Z2）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3608 — claim_span_003_65 が tex_b110:span_003（KCDM で 1〜2σ）と tex_b111:span_003（Mpc/h の強い制約は人工的）の 2 主張に重複。legacy_claim_id は別）。

同根の観察（頭脳 AA2・2609.35757 の再解析 run 54e85a2a）: uxsim/runs/radar_loop/BRAIN_NOTES_AA2.md（seq 3842 ほか equation_semantics の全件 — candidate_text_spans の span_id がブロックをまたいで重複（span_001 が tex_b3 と tex_b5 の両方）。span_id がブロック内でしか一意でない仕様のため、(block_id, span_id) の組で引かない下流は誤リンクする。主張 ID の衝突と同じ根）。

## 発見の観点

実際の後段入力の ID と本文を突き合わせた（data_inspection）。

## 解決の観点

未解決。外側の文脈を採番に足すか内容由来キーで一意化し、重複を検出して止める。

## 一般化

局所の番号を全体のキーにすると別の対象が衝突する。辞書の型 id-unique-only-within-inner-scope（IK-0719 の名前空間の混在とは別の原因）。
