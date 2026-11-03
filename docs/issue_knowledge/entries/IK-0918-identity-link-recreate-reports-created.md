---
id: IK-0918
title: "同じ部品と同じ共通部品の同一性リンクをもう一度作ると、サーバは既にある（確定済みの）行を返すだけなのに、画面は「候補を作成しました。上の一覧から確定・却下できます。」と言い、作られなかったことも既に確定済みであることも伝わらない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が共通部品への結び付けを作り直す・別の版の部品で作る"
  layers: [deliberation_w, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: core/deliberation/identity_links.py:155-200 create_candidate は ON CONFLICT DO NOTHING の後に既存行を SELECT
    して返し、route（routes/deliberation.py:585-630 create_identity_link）は常に同じ形で 200 を返す。どの分岐を通ったかは応答に無い。deliberation.js:1941
    は成功時に固定の「候補を作成しました」を出す。構造軸 representation: 新規・既存（候補のまま）・既存（確定済み・却下済み）の結果を 1 つの成功で表す。IK-0897
    でラベルと関係は直したが、同一性リンクの追加は同じ形のまま残った。
generalization:
  level: repo_pattern
  general_form: "冪等な追加の結果（新規・既存・判断済み）を 1 つの成功で返し、利用者に何が起きたかを伝えない"
pattern: failure-reported-as-success
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員 te-02 が 2 回目の「結びつける」を押し、一覧に行が増えないのに「候補を作成しました」と出たと記した。応答の id が 1 回目と同じ行で status が confirmed であることを生応答で確かめた。run:
    uxsim/runs/c-astro-r25-share-r3/20261023T032742Z-99528 seq10。2 回目が TeX 版の同じ部品に向いたのはハーネスの誤り（harness_fixes_share.md
    H24）だが、製品の文言の食い違いは独立に再現する。
resolution:
  perspective: [first_class_state]
  note: >-
    identity_links.create_candidate が結果の種類 outcome（created / existing_candidate / existing_confirmed / existing_rejected。既存行はその status で言い分ける）を戻り値に添え、事実文の正本 LINK_OUTCOME_FACTS を置く。routes/deliberation.py の create_identity_link は新規だけ 201 + 監査、既存は 200・監査なしで outcome / facts を返す。deliberation.js は固定文を捨て facts を素通しで描く（IK-0897 と同じ作法）。
  landed_in:
    - "backend/core/deliberation/identity_links.py"
    - "backend/api/routes/deliberation.py"
    - "frontend/public/js/deliberation.js"
    - "backend/tests/test_share_round25_identity_links.py"
    - "backend/tests/test_deliberation_ui_static.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB で ON CONFLICT 後の既存行の読み直しと outcome の組"
      - "実画面での作り直しの文言"
related: [IK-0897, IK-0916]
view_of: []
history: []
---

## 課題

症状: 2 回目の作成で行は増えず確定済みの 1 行のままなのに「候補を作成しました」と出る。原因: 既存行を返す分岐が応答に現れず、画面が固定文を出す。

## 発見の観点

教員ペルソナの通し受講（reproduction）。生応答と画面の文を突き合わせた（trace_walk）。

## 解決の観点

是正済み（第 3 波）。作成の結果を新規・既存（候補・確定済み・却下済み）で言い分ける outcome と事実文をサーバが返し、画面はそれを描く。

## 一般化

IK-0897 と同じ型の別の追加経路。確かめ方は、新規・候補のまま・確定済み・却下済みの各状態で同じ結び付きを作り、4 つの異なる事実文が出ることを画面で見ること。
