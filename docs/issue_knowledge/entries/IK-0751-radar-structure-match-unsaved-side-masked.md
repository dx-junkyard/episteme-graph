---
id: IK-0751
title: "起点の論文の理論モジュールが未保存なのに、取り込み済み候補の構造照合は「同じ構造の論文は見つかっていません」と閉世界の不在だけを言い、照合できなかった側を名指ししない"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §21"
feature_context:
  realizing: "取り込んだ近い論文と起点の論文の理論の構造が同じかを確かめる"
  layers: [paper_radar, graph_review]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract]
    governance: [completion]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（審判 deep-05 と theory_modules/related.py）: 候補側の related は自分の保存行があれば照合を走らせ、相手が 0 件なら
    FACT_RELATED_NONE を返す。相手の論文（起点）が保存行を持たない（既存教材にバックフィルなし）ことは母集合から黙って落ち、
    「照合したが無かった」と「照合できる相手がいなかった」が同じ文になった。統制軸 completion = 照合が走ったことを照合が成立したことの
    代理にした。接続軸 contract = 名指しされた相手の保存の有無が照合の入力に渡っていなかった。
generalization:
  level: general
  general_form: "比較の片側が前段で欠けているのに、下流は比較が走ったものとして閉世界の「無い」を言い、欠けた側を名指ししない"
pattern: upstream-loss-masked-downstream
discovery:
  perspective: [data_inspection]
  note: "深い段の審判が起点と候補の related の事実文を両方読み、2 つを並べないと「照合できなかった」と分からないことを記録した（出所: uxsim/runs/radar_loop/judge-deep-05.md）。"
resolution:
  perspective: [explicit_contract]
  note: "related に照合の相手（peer_document_id・閲覧できるものだけ）を受ける口を足し、相手が未保存なら「見つかっていません」の前に題名付きで名指しする。レーダーの取り込み済みカードには module_match_note（起点 / この論文のどちらが未保存か）を 1 行。再解析は代行しない。"
  landed_in:
    - backend/core/theory_modules/related.py
    - backend/core/library/structural_matches.py
    - backend/api/routes/theory_components.py
    - backend/core/paper_discovery/radar.py
    - frontend/public/js/admin-paper-radar.js
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "保存の有無の判定は structural_matches.documents_with_saved_modules の 1 箇所に置き、related とレーダーの両方がそれを使う。"
  verification:
    methods: [guardrail]
    unverified:
      - "グラフレビュー側から peer_document_id を渡す呼び出し元（未配線・API の口だけ）"
      - "ブラウザでの表示"
related: [IK-0750]
view_of: []
history: []
---

## 課題

症状: 起点の理論モジュールが未保存のまま候補を取り込むと、候補側の構造照合は「このコーパスの中では、同じ構造のモジュールを持つ
他の論文は見つかっていません」と言い、「構造の似た論文は無い」と読まれ得た。原因: 照合の相手側の保存の有無が照合に渡っておらず、
欠けた側が閉世界の不在に畳まれた。

出所: uxsim/runs/radar_loop/judge-deep-05.md §0 / §④-2。

## 発見の観点

起点側と候補側の事実文を並べて読み、欠けた側が片方の文にしか現れないことを確かめた（data_inspection）。

## 解決の観点

照合の相手を明示の入力にし（explicit_contract）、未保存なら名指しする事実文を「見つかっていません」の前に置く。

## 一般化

閉世界の「無い」を言う前に、比較の片側が土俵に乗っているかを入力として受け取る。確かめ方は、片側だけ保存行の無い 2 教材で
両方の画面の文を読むこと。
