---
id: IK-0889
title: "分野の地図タブのベクトル索引の区画が、作成時刻をマイクロ秒付きの UTC 生値で出し、索引が何に使われるか・作り直す必要があるかを言わない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_vector_anchoring_design.md"
feature_context:
  realizing: "教員がベクトル索引の状態を見て、作り直すかどうかを決める"
  layers: [vector_anchoring_va, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [none]
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
    処理軸 wording: admin.js renderVectorStatus が built_at を「最後に作成: 2026-10-22T21:56:52.616957+00:00」と生値で出し、
    件数行の単位を「ノード」と書く（教員向けの他の区画は「項目」）。接続軸 information: 状態 API は stale を返し
    VECTOR_STALE_TEXT もあるが、stale でないときに「索引は現在の版と一致しています（作り直す必要はありません）」に当たる
    文が無く、索引が候補の近さ（別表記の注記・関係の候補）に使われることも区画に書かれない。作り直しの結果も
    「新しく索引した項目: （件数） / そのまま使った項目: （件数）」と件数だけで、何も変わらなかったことを文で言わない。
    構造軸・統制軸 none。確認: run 20261022T220051Z-74929 seq 9〜10、教員ペルソナ「数字が並んでいるだけで、作り直す必要が
    あるのかは分からない」。
generalization:
  level: instance
  general_form: "運用の状態を示す区画が保存値（時刻・件数）をそのまま並べ、利用者が次に取るべき操作の判断に要る一文を持たない"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, symptom_report]
  note: "教員ペルソナが索引の状態を読んで判断できず、とりあえず作り直した。 run: uxsim/runs/c-astro-r25-atlas-r2/20261022T220051Z-74929。"
resolution:
  perspective: [canonical_source]
  note: >-
    索引の区画の表示語をサーバ定数に寄せた。vectors/status が facts（用途の一文 → 最新 / 一部欠け / 未構築 / 版ずれの要否の一文。atlas_review_display.VECTOR_INDEX_*）と built_at_label（「YYYY-MM-DD HH:MM（UTC）」）を返し、refresh は fact（全件再利用なら「変更はありませんでした」）を返す。admin.js renderVectorStatus / vectorRefreshFactText はそれを素通しし、件数と ISO 時刻を描かない。
  landed_in:
    - "backend/api/routes/atlas_vectors.py"
    - "backend/core/atlas_review_display.py"
    - "frontend/public/js/admin.js"
    - "docs/manual/teacher/17-admin-atlas.md"
    - "backend/tests/test_atlas_vectors_api.py"
  verification:
    methods: [guardrail]
    unverified:
      - "ブラウザ未確認"
related: [IK-0851, IK-0850]
view_of: []
history: []
---

## 課題

症状: 「索引済み: （件数）/（件数） ノード（骨格 版…）／最後に作成: 2026-10-22T21:56:52.616957+00:00」とだけ出る。作り直しても
「新しく索引した項目: （件数） / そのまま使った項目: （件数）」。教員は作り直す必要があったのかを判断できない。

原因: 区画が状態 API の保存値をそのまま描き、用途・要否の一文を持たない。

## 発見の観点

再演（reproduction）での教員の所見（symptom_report）。

## 解決の観点

第 2 波（2026-10-23）で是正。表示語をサーバ側の定数に寄せ、用途と要否の一文を足す。

## 一般化

運用状態の区画（索引・キャッシュ・同期）で、保存値をそのまま出すと次の操作が決められない。確かめ方: 状態ごと（最新 / 古い /
未構築）に区画の一文が次の操作を言うことを見る。
