---
id: IK-0897
title: "ナレッジライブラリで既にある別名・隠しラベルをもう一度足しても、応答が「新しく足した／既にあった／見送り済みのまま」を区別せず画面も一覧を読み直すだけなので、操作が効いたか分からず、見送り済みのラベルを足し直したときは黙って見送り済みのまま残る"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が共通部品のエントリに別名・隠しラベルを記録する"
  layers: [concept_registry, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
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
    接続軸 information: backend/core/library/registry.py:280-329 add_label は ON CONFLICT で既存行を返し（confirmed なら表示テキストを更新、dismissed なら SELECT で既存行をそのまま返す — 「見送り済みの行は復帰させない」は KR7 の意図どおり）、route（backend/api/routes/library.py:756-787）は常に 201 + {label} を返す。どの分岐を通ったかは応答に無い。画面（frontend/public/js/admin.js:3179-3188）は ok なら入力欄を空にしてラベル一覧を読み直すだけで、事実文を出さない。構造軸 representation: 「作った・既にあった・見送り済みで復帰させなかった」の 3 つの結果を 1 つの成功で表す。統制軸は none（判断の弁は正しく働いている — 伝わらないだけ）。
generalization:
  level: repo_pattern
  general_form: "冪等な追加の結果（新規・既存・意図して復帰させない）を 1 つの成功で返し、利用者に何が起きたかを伝えない"
pattern: failure-reported-as-success
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員 te-05 が既にある別名と隠しラベルを足し直し、2 回とも画面が変わらず「既にあったのか、追加されなかったのか書かれていない」と記した。応答と画面の扱いを突き合わせた。run: uxsim/runs/c-astro-r25-share-r2/20261022T220752Z-79835 seq7-8。
resolution:
  perspective: [first_class_state]
  note: >-
    core/library/registry.py の add_label が既存行の状態を先に読み、結果 outcome（created / existing / dismissed）を戻り値の dict に添える。create_relation も INSERT の成否と既存行の status から同じ 3 語彙を添える。routes/library.py は outcome を取り出し、新規だけ 201・それ以外は 200 で、固定文の facts（LABEL_OUTCOME_FACTS / RELATION_OUTCOME_FACTS）を返す。admin.js はラベル区画・関係区画の一覧の直前に facts を 1 行で描く。導出は既存の facts を描く形のまま。
  landed_in:
    - "backend/core/library/registry.py"
    - "backend/api/routes/library.py"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_share_round25_r2_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面でのラベル・関係の再追加（第 3 周の te-05 (c)）"
related: [IK-0864, IK-0867]
view_of: []
history: []
---

## 課題

症状: 既にある別名・隠しラベルを足しても画面が変わらず、効いたか分からない。見送り済みのラベルを足し直すと、黙って見送り済みの
まま残る。原因: 追加の 3 つの結果を 1 つの成功で返し、画面が事実文を出さない。

## 発見の観点

教員ペルソナの通し受講（reproduction）。応答と画面の扱いを辿った（trace_walk）。

## 解決の観点

第 2 波（2026-10-23）で是正。結果の種類を応答に持たせ、事実文で返す。

## 一般化

冪等な操作の結果を区別しない型（failure-reported-as-success の一形）。確かめ方は、新しい別名・既存の別名・見送り済みの別名を
1 つずつ足し、3 つの異なる事実文が出ることを見ること。
