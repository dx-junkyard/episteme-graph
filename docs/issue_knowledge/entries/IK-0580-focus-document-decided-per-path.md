---
id: IK-0580
title: "「いまどの論文の話か」が会話の状態として無く、5 つの経路が独自の規則で論文を選んで食い違っていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/root_cause_consolidation_2026-09-30.md §3
  - docs/features/focus_document_design.md
feature_context:
  realizing: "複数の論文を束ねたコースで、会話・操作の対象の論文を決めて検索結果・記号・式・範囲表示をその論文に合わせる"
  layers: [rag_chat, discuss, structure_descent, personal_network, tests_guardrails]
classification:
  axes:
    processing: [none]
    structure: [representation, responsibility]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造=representation: 焦点の論文が会話の状態として表現されておらず、各経路が手元の材料から推し量るしか
    なかった（焦点論文 §1 の旧来の規則の表で確認）。構造=responsibility: 「どの論文か」を決める判断が
    トピックの RAG・discuss・DIFF・前提の説明・構造帰属 worker の各経路に分散していた。接続=target: 経路間で
    対象の論文がずれ、記号・式の取り違えやコース横断の最初の 1 件の採用が起きた。統制=none: 実行順序・予算・
    手続の問題ではない（確信度 medium は、衝突時に推測で選ぶことを許していた点を手続の欠落と読む余地があるため）。
    処理=none: 各経路の条件式はその経路の規則の中では意図どおり。
generalization:
  level: general
  general_form: "操作の対象が状態として存在せず、複数の経路がそれぞれの規則で対象を推し量るため、経路ごとに対象がずれる"
pattern: rule-enforced-per-surface
discovery:
  perspective: [inventory, symptom_report]
  note: "別論文のチャンク混入・記号の論文違い・式 ⚓ の衝突・部品文脈の取り違え・議論中の論文が優先されない、の是正を並べると、どれも「どの論文か」を経路ごとに決めていることに行き着いた。"
resolution:
  perspective: [canonical_source, fail_closed]
  note: "焦点を決める解決関数を 1 つにし（段順 = 明示 → トピック → 直前の引用 → 画面の選択）、優先規則を 1 つ、衝突時は推測せず解決しない（fail-closed）とした。焦点は並べ替えと衝突解決にだけ使い、検索範囲は広げない。"
  landed_in:
    - backend/core/focus_document.py
    - backend/api/routes/learning.py
    - backend/core/descent/resolve.py
    - backend/core/personal_graph/nearby.py
    - backend/tests/test_focus_document_guardrails.py
    - docs/features/focus_document_design.md §5
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "対象の選択の派生形（段順の解決関数 1 つ + 衝突の fail-closed）。コース横断の LIMIT 1 を廃止した。痕跡への焦点の出所の記帳は見送った。"
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（ペルソナ通し受講の次の周）
      - ブラウザでの画面確認
      - 痕跡 payload への焦点の出所の記帳（見送り）
related: [IK-0513, IK-0552, IK-0553, IK-0554, IK-0556, IK-0558]
view_of: []
history: []
---

## 課題

トピックチャット・discuss・予想と並置・前提の説明・構造帰属 worker が、それぞれ別の規則で「どの論文か」を選び、
別論文のチャンクの混入、記号・式の論文違い、コース横断の最初の 1 件の採用が起きていた。原因は、焦点の論文が
会話の状態として無く、判断が経路ごとに分散していたこと。

## 発見の観点

経路ごとの是正を棚卸しで並べ、共通の欠落（焦点の状態の不在）に遡った。

## 解決の観点

解決関数と優先規則の一本化を主に、衝突時の fail-closed を従にした。経路ごとに優先条件を足す案は、
経路が増えるたびに規則が食い違うため採らなかった。

## 一般化

操作の対象を状態として持たない構造では、対象を推し量る経路が増えるたびにずれる（`rule-enforced-per-surface`）。
