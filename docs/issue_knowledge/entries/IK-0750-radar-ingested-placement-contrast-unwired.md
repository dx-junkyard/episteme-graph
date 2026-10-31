---
id: IK-0750
title: "論文レーダーの取り込み済み候補で、起点と候補の分野の地図への配置は両方とも読めるのに、その対比（重なり・片側だけの面）がどの画面にも組まれていない"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §21"
feature_context:
  realizing: "教員が近い論文を取り込んだあと、起点の論文との関係を研究判断に使える形で読む"
  layers: [paper_radar, knowledge_landscape]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（審判 deep-05 と radar の検索経路）: 両教材の live 配置は landscape の API で読めるが、レーダーの取り込み済みカードは
    導線（位置づけ・グラフレビュー）を出すだけで、起点との対比を組む処理がどこにも無かった。審判が両画面の配置を手で並べて初めて
    「大規模構造形成で重なり、起点だけが重力、候補だけが初期宇宙」と読めた。接続軸 information = 素材はあり、それを必要な面へ渡す配線が
    無い。処理・構造・統制は none（既存の読み口と出所ラベルで足りた）。
generalization:
  level: general
  general_form: "2 つの対象それぞれの属性は別々の画面で読めるが、判断に効く両者の差分を組む処理がどこにも無く、人が手で突き合わせている"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "深い段の審判が deep-05.json の両教材の配置を手で並べ、判定を動かしたのが配置の重なりと差だったことを記録した（出所: uxsim/runs/radar_loop/judge-deep-05.md）。"
resolution:
  perspective: [carry_through]
  note: "検索経路で取り込み済み候補に placement_contrast（shared / seed_only / candidate_only のノード名 + 出所ラベル・骨格版）を読み時導出で付け、片側に配置が無いときは事実文を付ける。読み口は landscape の既存 list_for_documents を 1 回。数値・weight は載せない。"
  landed_in:
    - backend/core/paper_discovery/radar.py
    - frontend/public/js/admin-paper-radar.js
    - "docs/features/paper_radar_design.md §21"
    - "docs/features/paper_radar_design.md §22"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB の配置を持つ 2 教材での表示（ブラウザ）"
      - "複数分野にまたがる配置での骨格版の書き方"
related: [IK-0703, IK-0751, IK-0778]
view_of: []
history:
  - date: '2026-10-16'
    field: resolution.landed_in
    from: "paper_radar_design.md §21（node 単位の対比）"
    to: "paper_radar_design.md §22 C（各項目に perspective_label / perspective_labels / layer）"
    reason: "審判 deep-06 §④-4: 対比が観点と粒度（領域 / 概念）を落とし「方法の面での分かれ」が読めなかった。観点は正本 core.landscape.schema.perspective_label、JS は粒度で分けて観点ごとに並べる。"
---

## 課題

症状: 取り込み済みの近い論文について、起点との関係を研究判断に使うには、分野の地図への配置を 2 つの画面で別々に開いて手で
並べるしかなかった。審判の判定を動かしたのはこの対比だった。原因: 対比を組む処理がどの面にも無かった（素材は既存）。

出所: uxsim/runs/radar_loop/judge-deep-05.md §③ / §④-1。

## 発見の観点

深い段の記録を読み、判定を動かした材料が既存の配置の突き合わせだったことを確かめた（data_inspection）。

## 解決の観点

既存の読み口から対比を組み、取り込み済みカードまで運んだ（carry_through）。重なりは両側とも確認済みのときだけ「教員確認済み」。

## 一般化

別々に読める 2 つの属性の差分が判断材料なら、差分を組む処理をどこに置くかを決める。確かめ方は、呼び出し側の操作列（検索 → 取り込み済み
カード）を辿り、差分が画面に現れることを見ること。
