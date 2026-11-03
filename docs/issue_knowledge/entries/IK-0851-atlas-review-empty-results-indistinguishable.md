---
id: IK-0851
title: "分野の地図のレビュー画面で「該当なし」と「調べていない／調べられなかった」が同じ見た目になる — 別表記の注記はベクトル索引が空（索引済み 0）でも黙って出ず、見送り済みの表示をオンにして該当ゼロでも一文が無く、凍結の影響がゼロのときは影響が無いという肯定の事実文が無い"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_vector_anchoring_design.md"
  - "docs/features/atlas_binding_lifecycle_design.md"
feature_context:
  realizing: "教員がカテゴリギャップ候補を既存項目の別名として登録する／見送った候補を探す／凍結の影響を確かめる"
  layers: [vector_anchoring_va, category_gaps, field_atlas_s, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
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
    構造軸 representation: 候補の near_anchor は「近い項目がある」ときだけキーが付き（core/atlas_vectors/annotate.py:61-105。
    アンカーが無い・版が違う・例外のときは入力をそのまま返す）、「索引が無いので照合していない」を表す値が無い。砂場の
    vectors/status は embedded_nodes 0（built_at なし）で、gap カードには[別名として登録]も理由も出ない（admin.js
    _gapNearAnchorHtml / _gapCandidateCardHtml）。接続軸 information: 見送り済みの表示（include_dismissed=true）で見送り済みが
    ゼロのとき renderGapCandidates は候補が 1 件以上あれば何も足さず、空のときの GAP_EMPTY_TEXT しか持たない。凍結の
    影響プレビュー（freeze-impact）は影響ゼロで facts: [] を返し、admin.js:8553-8590 は影響ゼロなら影響の一文なしで公開前
    チェックへ進む（下書きが現行版と同一かも言わない）。処理軸 none（各分岐は宣言どおり）。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "結果が空のとき、「調べて該当なし」と「調べる前提が無い・調べなかった」を同じ空で返し、利用者が次の一手（索引を作る・そのまま進む）を選べない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, data_inspection]
  note: "教員ペルソナ 2 人が「注記が無いのか描かれないのか」「見送り済みが無いのか表示に失敗したのか」「影響が無いのか確認できなかったのか」を判別できないと記した。vectors/status と gap-candidates / freeze-impact の応答を読み比べた。 run: uxsim/runs/c-astro-r25-atlas-r1/20261022T123912Z-34807・uxsim/runs/c-astro-r25-atlas-r1/20261022T123418Z-31802（頭脳の所見 uxsim/runs/round25/BRAIN_NOTES_atlas.md）。"
resolution:
  perspective: [first_class_state]
  note: >-
    ①atlas_vectors/annotate.py が照合の状態（checked / no_index / unavailable）を report に書き、gap-candidates が near_anchor_check {state, note} を返す（索引が無いときは「ベクトル索引」区画への導線の事実文）。②見送り済みの表示で見送り済みがゼロなら dismissed_note（gap・辺とも）。③freeze-impact は消える項目が無いとき「この凍結で削除される概念・対応が外れるコースはありません。」、項目構成が同じなら「下書きの領域・概念は、現在の公開版と同じです。」を facts に足し、既存の公開前チェックへそのまま流れる。文言はすべてサーバ側の定数で、admin.js は素通しする。
  landed_in:
    - "backend/core/atlas_vectors/annotate.py"
    - "backend/core/atlas_review_display.py"
    - "backend/core/atlas_lifecycle.py"
    - "backend/api/routes/atlas_gaps.py"
    - "backend/api/routes/atlas_edges.py"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_atlas_review_round25.py"
    - "backend/tests/test_atlas_gaps_api.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "空の種類の事実文はサーバの 1 モジュールに置き、画面ごとに文言を持たない。"
  verification:
    methods: [guardrail]
    unverified:
      - "ブラウザでの表示位置は未確認"
      - "同梱の基準地図の分野で索引を再構築した後に checked の文が出ることは再演で未確認"
related: [IK-0848]
view_of: []
history: []
---

## 課題

症状: 別名登録の場面で、gap 候補に別表記の注記も[別名として登録]も出ず、その理由も無い（実際はベクトル索引が未構築）。
見送り済みを表示しても一覧が変わらず、見送り済みゼロの一文が無い。凍結の影響がゼロだと影響の確認が出ないまま公開前
チェックへ進み、「影響なし」の肯定の一文が無い。原因: 空の理由を表す値・文言が無く、該当なしと未照合が同じ空になる。

## 発見の観点

ペルソナの通し受講（reproduction）と応答の読み取り（data_inspection）。

## 解決の観点

照合の状態・見送り済みゼロ・凍結の影響ゼロをサーバの事実文で言い分ける。

## 一般化

fail-soft の縮退が「何も付けない」で表されると、縮退と該当なしが区別できない。確かめ方は、索引なし・見送りゼロ・影響ゼロの
それぞれで画面に別々の事実文が出ることを見ること。

## 第 2 周の再演（2026-10-23・campaign c-astro-r25-atlas-r2）

- 消えた: 凍結の影響が無いときの肯定の一文（「この凍結で削除される概念・対応が外れるコースはありません。」）が公開前チェックに
  出た（run 20261022T220051Z-74929 seq 18）。
- 未観測: 別表記の照合状態（near_anchor_check）と見送り済みゼロの一文は、ギャップ候補がゼロ・辺の見送りが 1 件あったため
  画面に現れる条件が無かった。
- 第 2 周の再演: 残存（候補ゼロの理由）。「いまレビューする候補はありません。」だけで、ゼロの理由（論文由来の信号が無い /
  信号はあるが 2 本以上の論文で反復していない / すべて判断済み）が区別されない。新任の教員は「何をすれば候補が出てくるのか
  画面に書いていない」で止まった（run 20261022T215750Z-73308 seq 3〜4）。
- 是正者向けの差分: `derive_candidates` が閾値に届かなかった active 信号の有無を返せるようにし（件数でなく真偽）、
  gap-candidates に空の理由の事実文（`atlas_review_display` の定数）を 1 キー足す。「同じ論文の重複取り込みは 1 本と数えます」
  も理由の一つとして言う（IK-0846 の是正で候補が消えた教員に、消えた理由が読める）。

- 第 2 波で是正（2026-10-23）: `derive_candidates(report=)` が理由の真偽（信号あり / 反復に届かない / 重複を畳んで届かない / 現行版で解消 / 判断済み）を書き、gap-candidates は候補ゼロのとき `empty_notes`（`atlas_review_display.EMPTY_*`）を返す。admin.js は「いまレビューする候補はありません。」の下に並べる。再演は未確認。
