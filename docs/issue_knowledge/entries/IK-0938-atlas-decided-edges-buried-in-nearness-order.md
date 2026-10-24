---
id: IK-0938
title: "関係（辺）の候補の一覧で、採用済み・下書きに反映済みの関係が近さ順の並びの中ほどに混ざり、凍結を止め得る自分の採用や反映した関係を探しても先頭に無く、反映直後の一文も何を足したかを言わない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_relation_edges_design.md"
feature_context:
  realizing: "教員が採用した関係を次版の下書きへ反映し、反映できたことと凍結を止める残りを一覧で確かめる"
  layers: [relation_edges_re, field_atlas_s, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [review]
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
    構造軸 representation: 一覧は derive_edge_candidates の「共起 → 近さの順位」の並びに判断をマージするだけで
    （routes/atlas_edges.py list_atlas_edge_candidates・admin.js renderEdgeCandidates は受け取った順に描く）、判断の
    状態（採用済み・未反映 / 下書きに入っている）が並びに効かない。cap_for_review は判断済みを畳まないが、近さの位置の
    まま残すので、近さだけの候補が長く続く中ほどに出る。反映直後の状態文は固定の「次版の下書きに追加しました」
    （admin.js setEdgesStatus）で、つなぐ項目を言わない。統制軸 review: 凍結を止めるのは「採用済み・未反映」の行なのに、
    それを一覧の先頭で確かめる入口が無い（カテゴリギャップ側は IK-0884 で区画を分けたが、辺の導出内の判断には同じ扱いが
    無い）。接続軸 none・処理軸 none（並べ方の規則は自分の定義どおり）。
    確認: run 20261023T070217Z-13812 seq 2 / 9 / 11 / 12（ブラックホール — 重力波は応答の candidates に判断と
    in_draft 付きで載るが位置は近さ順の中ほど）、run 20261023T032742Z-99530 seq 9 / 10。
    注: ハーネスの観察上限が中ほどのカードを題名の列挙に畳んだため、頭脳には「採用の印・下書きの印が無い」と見えた
    （harness_fixes_atlas.md H18）。印が無いのは投影の側で、製品は印を持つ。製品の課題は並び順と反映直後の一文。
generalization:
  level: repo_pattern
  general_form: "判断の状態（採用済み・反映済み）を持つ項目を、未判断の候補と同じ推定の並び順で一覧に混ぜ、人が自分の判断と残りの仕事を探す手間を並び順に預ける"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: "精読型の教員ペルソナが前の周に採用した関係を探して見つけられず、反映後も一覧で確かめられなかった。応答の candidates の順と判断、admin.js の描画順を突き合わせた。 run: uxsim/runs/c-astro-r25-atlas-r3/20261023T070217Z-13812・20261023T032742Z-99530。"
resolution:
  perspective: [first_class_state]
  note: >-
    判断の状態を並び順に効かせる 1 箇所を atlas_review_display.order_decided_first に置いた（帯 = 採用済み・未反映 → 下書きに入っている →
    それ以外・帯の中は導出の順の安定ソート・近さの値は使わない）。辺は cap_for_review の前に並べ替え（畳みは入力の順を保つ）、ギャップも同じ関数を通す。採用済み・未反映には
    pending_note（凍結を止める一文）を付ける。mark-incorporated の応答に result_line（「『A — B』を次版の下書きに追加しました。」）を足し、admin.js
    の結果文はそれを描く。
  landed_in:
    - "backend/core/atlas_review_display.py"
    - "backend/api/routes/atlas_edges.py"
    - "backend/api/routes/atlas_gaps.py"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_atlas_review_round25_r3.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "並び順の規則を辺とギャップが同じ 1 関数に通す（面ごとに書かない）。"
  verification:
    methods: [guardrail]
    unverified:
      - "近さだけの候補が多い分野で、採用した関係が一覧の先頭に出ることは再演で未確認"
related: [IK-0884, IK-0886, IK-0887]
view_of: []
history: []
---

## 課題

症状: 前の周に採用した「ブラックホール — 重力波」を一覧で探すと、先頭には別の教員の採用だけがあり、自分の採用は
近さ順の長い並びの後ろに混ざる。下書きへ反映した後も一覧の並びは同じで、状態文は「次版の下書きに追加しました」
だけ。反映できたことは凍結の影響の「次版で加わる関係」で初めて確かめられた。

原因: 判断の状態が並び順に効かず、反映直後の文が項目を名指さない。

## 発見の観点

通し受講（reproduction）と応答・描画順の突き合わせ（trace_walk）。ハーネスの畳みが症状を強めた分は H18 に分けた。

## 解決の観点

第 3 波（2026-10-23）で是正。判断の状態を並び順に効かせる 1 箇所を atlas_review_display.order_decided_first に置いた（帯 = 採用済み・未反映 → 下書きに入っている → それ以外・帯の中は導出の順の安定ソート・近さの値は使わない）。

## 一般化

推定の候補と人の判断を同じ一覧に置く画面（説明レビューキュー・ギャップ候補）で同じ形が出る。確かめ方: 近さだけの候補が
多い分野で採用し、一覧の先頭に出ること・反映後の文に項目名が出ることを見る。
