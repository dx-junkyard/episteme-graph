---
id: IK-1153
title: "［記号の候補を探す］が新しい候補を作らなかったとき、理由を問わず「閲覧できる教材の記号の定義の文に、この概念の名前・別名は語として現れませんでした」と言う — 名前が現れた記号が既に取り消し・見送りの行として残っている（だから新しく作らなかった）ときも同じ文で、同じ画面の取り消し済みの行を戻すと「定義の文に、概念の名前（または別名）が語として現れました」と出て食い違う"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "教員が概念に結びそうな記号を探し、新しい候補が無い理由（一致が無いのか、一致は既に判断済みなのか）を知って次の手を選ぶ"
  layers: [concept_registry, frontend_admin_ui]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 logic: api/routes/library.py derive_entry_symbol_candidates は stats["links_created"] だけで 2 つの文を選ぶ。core/library/symbol_concepts.py は
    名前が語として一致した記号にも create_candidate を呼ぶが、既存のリンク（取り消し済み・見送り）があると outcome が created にならず数えない。
    一致の有無と新規作成の有無が 1 つの数に畳まれている。接続軸 information: 「一致したが判断済み」の事実が応答に載らない。
generalization:
  level: repo_pattern
  general_form: "結果の文を「新しく作った件数」だけで選び、0 件の理由（一致なし／一致したが既に判断済み）を区別しないまま「一致なし」と言う"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    第 25 周 面 A 第 11 周（judge_share_r11）。te-03 が 67727cb0 で［記号の候補を探す］→「…語として現れませんでした。別名を足すと、その語でも探します。」
    （uxsim/runs/c-astro-r25-share-r11/20261031T101202Z-98535 seq 8）。直後に取り消し済みの L_TOV を戻すと行に「記号「L_TOV」の定義の文に、概念「TOV residual
    loss at collocation points」の名前（または別名）が語として現れました。」（seq 9）。頭脳「その別名で探したのかは文から読めない」。route と derive の数え方を読んだ。
resolution:
  perspective: [first_class_state, explicit_contract]
  note: >-
    derive_symbol_concept_candidates が、名前が語として一致したが既に行がある組（create_candidate の existing_* の結果）を existing_matches（記号名と状態の列・数値は画面に出さない）として返す。route の _symbol_derive_fact は新規作成なし・判断済み（見送り・取り消し）ありなら「判断済みの一致: L_TOV（取り消し済み）・…。…［候補に戻す］から判断し直せます。」、候補・確定だけなら「すでに下の「結びついた記号」にあります: …」、一致なしのときだけ従来の「現れませんでした」を返す。状態の語は identity_links.DECISION_MARKS（一覧の札と同じ）。
  landed_in:
    - "backend/core/library/symbol_concepts.py"
    - "backend/api/routes/library.py"
    - "backend/tests/test_share_round25_r11_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "取り消し済みの記号だけが一致するエントリで［記号の候補を探す］を押した文を砂場の画面で"
      - "同じ論文の別版への写し（carry-over）の既存行は existing_matches に数えていない"
related: [IK-0983, IK-1062]
view_of: []
history: []
---

## 課題

新しい候補が 0 のとき、一致が無いのか、一致は判断済みなのかを言い分けず「現れませんでした」と言う。同じ画面の行と矛盾する。

## 発見の観点

場面の手順（reproduction）と、route → derive の件数の流れ（trace_walk）。

## 解決の観点

未着手。0 件の理由を分けて返す。

## 一般化

型 extent-decided-by-proxy-signal: 「無い」を新規作成の件数で代用する。確かめ方: 判断済みの一致だけがある状態で操作し、文が事実と合うか。

## 第 11 波の是正（2026-10-31）

0 件の理由を分けて返す（resolution.note）。
