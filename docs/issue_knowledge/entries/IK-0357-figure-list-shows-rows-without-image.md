---
id: IK-0357
title: 図の抽出が画像を作れなかった caption にも行を作り、前回までの抽出の行も区別なく残すため、図・画像一覧に絵の無い行と古い断片が並ぶ
status: resolved
recorded_at: 2026-09-25
resolved_at: 2026-09-25
sources:
  - docs/features/image_pipeline_knowledge_library_design.md §18
feature_context:
  realizing: PDF から切り出した図を教員に一覧で見せ、図ごとの検討・説明・コース利用の入口にする
  layers: [image_library_l, frontend_admin_ui, guidance_g]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [completion]
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
    構造軸: 抽出の行は「今回の抽出で作られた絵のある図」「保存に失敗した図」「前回の抽出で作られ今回は
    作られなかった図」を区別できず（status は extracted / failed の 2 値）、一覧は全行を出していた
    （representation）。確認手段: 実環境の fujimoto_d.pdf で、最新の run の artifact に載る figure_ids と
    document_figures の行を突き合わせ、failed の 5 行（bbox なし・p.1）と artifact に無い 30 行（7 月の
    run の部品画像の断片）を確認した。統制軸: 「画像を作れなかった」を行として残すことが完了の印に
    なっており、一覧に何を出すかの基準（絵があること）が無かった（completion）。処理軸: 個々の処理は
    仕様どおり（none）。接続軸: 一覧は受け取った行を正しく出していた（none。G層と二層説明の入力にも
    同じ基準が要ると分かったので medium）。
generalization:
  level: general
  general_form: 生成に失敗した項目と前回までの古い項目を、今回の成果と同じ状態で残すため、一覧に中身の無い項目や古い項目が並ぶ
pattern: stale-derivative-served
discovery:
  perspective: [symptom_report, data_inspection]
  note: >-
    オーナーが一覧に絵の無い図（Figure 5 / p1_i0 など）が並ぶと報告し、実 DB の document_figures と
    最新 run の artifact を突き合わせて、失敗行と前回の残りの 2 種類があると分かった。
resolution:
  perspective: [first_class_state, fail_closed]
  note: >-
    絵の無いものは行を作らず artifact の not_saved に理由を残す。抽出が最後まで通ったら今回作られなかった
    行を status='superseded' にする（migration 087、行は消さない）。一覧・検出要素・G層・二層説明の入力は
    is_listed_figure（extracted だけ）を通す。
  landed_in:
    - backend/core/document_pipeline/figure_images.py
    - backend/db/087_document_figures_superseded.sql
    - backend/api/routes/figure_presentation.py
    - backend/core/deliberation/inventory.py
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - docker 上での再解析と管理画面の表示
      - migration 087 の実 DB への適用
      - superseded にした行を参照しているコース・説明の表示
related: [IK-0355, IK-0358]
view_of: []
history: []
---

## 課題

**症状**: fujimoto_d.pdf の「図・画像」一覧に、絵の無い行（Figure 5 / p1_i0 / p1_i2 / p1_i3 / p1_i4、すべて p.1）と、
前回の抽出で作られた部品画像の断片が並んだ。

**原因**: 抽出は画像を作れなかった caption にも `status='failed'` の行を作っていた（P4「情報を落とさない」を
行で守っていた）。また upsert だけで、今回作られなかった前回までの行を区別する状態が無かった。一覧はどちらも
そのまま出していた。

## 発見の観点

症状報告（`symptom_report`）から、実 DB の行と最新 run の artifact の figure_ids を突き合わせた（`data_inspection`）。

## 解決の観点

「今回作られなかった」を一級の状態（`superseded`）にし（`first_class_state`）、一覧は絵のある最新の行だけを
出す側に倒した（`fail_closed`）。情報は落とさず、失敗の中身は artifact の not_saved、古い行は行ごと残す。

## 一般化

再生成する派生物を upsert だけで更新すると、今回作られなかった古い派生物と生成に失敗した項目が、今回の成果と
同じ顔で残る。辞書の `stale-derivative-served`。
