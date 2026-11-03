---
id: IK-0812
title: "inline 式候補の選別で、並列の項目の一部だけが式になる — 値の割り当て表「(n_p,m_p)=(1/2,1)」の先頭だけが独立して係数の対象が外に出る、対の定義 α∥ だけが候補で α⊥ が無い"
status: open
recorded_at: 2026-10-17
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "本文中の inline 数式から、定義・関係を担う式を候補として選び equation_semantics に回す"
  layers: [pipeline_a]
classification:
  axes:
    processing: [input_handling]
    structure: [decomposition]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: inline 候補の抽出が 1 文・1 ブロックあたりの候補数や最初の一致で打ち切るか、並列の数式（値の表・対の定義）を 1 つの単位として
    扱わない。構造軸 decomposition = 候補の単位が「意味のまとまり」（値の割り当て表全体、対の定義）でなく個々の $…$ になっている。
    処理軸 input_handling = 同じ文の後続の数式・対の片方を取りこぼす。接続軸 none / 統制軸 none。IK-0762（値指定が式になる）の是正は
    値の言及を候補から外す方向で、表の一部だけが残る本件とは別の症状。確定には inline 候補の抽出の打ち切り条件と、tex_b103・tex_b71 の
    候補の一覧を読む。
generalization:
  level: general
  general_form: "並列に並ぶ要素を 1 つずつの単位で選ぶため、まとまりの一部だけが独立して取り出され、残りと文脈が外に落ちる"
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 W2 が equation_semantics の inline 候補（2610.08751）を読み、seq 3278 で値の表の先頭「(n_p,m_p)=(1/2,1)」だけが候補で、同じ文の (1/2,2)/(1,4)/(1,2)/(1,0) と係数の対象（for b_1）が外にあること、seq 3275 で同じ脚注の対の定義のうち α∥ だけが候補で α⊥ が無いことに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md）。seq 3266 では「w=w_0.」と文末の句点が数式内に入る（TeX 原文どおりだが表示ラベルに句点が出る）。"
resolution:
  perspective: [pending]
  note: "未解決。値の割り当て表は表全体を 1 候補にするか候補から外し、対の定義は両方を候補にする。式の末尾の句読点は表示ラベルで落とす案。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0762, IK-0764]
view_of: []
history: []
---

## 課題

症状: tex_b103 の inline 候補が「(n_p,m_p)=(1/2,1)」だけで、どの係数か（for b_1）が式の外にあり、同じ文の (1/2,2)/(1,4)/(1,2)/(1,0) は候補にならない。
tex_b71 の脚注では α∥ だけが候補で対の α⊥ が無い。学習者には意味の取れない式・非対称な定義が出る恐れ。
原因（仮説）: 候補の単位が個々の $…$ で、並列のまとまりを扱っていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3266・3275・3278）。

追加の出所（頭脳 Z2）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3575・3596 — 添字の集合だけの ℓ={0,2,4} や句点込みの w=w_0. まで 1 式 1 LLM コールで意味付けされる。同じ脚注の CPL 式 w(a) 本体は Neighbor Equations に無く入力を張れない。seq 3581 — 分離可能性の第 2 の inline 式 Π(n|Θ)=Π(n) は別候補にならない）。

追加の出所（第 2 波 deploy 後の run・頭脳 Z0/Z1）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3594 — 同じ文の α⊥・α∥ の 2 式が別々の要求（2 コール））、uxsim/runs/radar_loop/BRAIN_NOTES_Z1.md（seq 3601 — (n_p,m_p)=(1/2,1) だけが式化され (1/2,2)・(1,4) は拾われない。seq 3598 — inline 候補が近傍式一覧に載らないため、Eq. (14) の参照先 A_AP の定義を結べない）。

## 発見の観点

inline 候補の一覧を、同じ文・同じ脚注の数式の並びと突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

並列の要素を 1 つずつ選ぶと、まとまりの一部だけが残る。辞書の型 unit-of-work-undefined。
