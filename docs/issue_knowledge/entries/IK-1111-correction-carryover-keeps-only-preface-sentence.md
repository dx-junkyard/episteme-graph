---
id: IK-1111
title: "直前の往復の訂正を次の往復の system へ持ち越すとき、訂正の語を含む文だけを切り出すため「一つ訂正させてください。」の前置きだけが渡り、訂正の中身（次の文）が落ちる"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/backend/rag-chat.md"
feature_context:
  realizing: "前の往復で行った訂正を、次の往復の回答が黙って覆さないよう、訂正の中身を AI に渡す"
  layers: [rag_chat]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    core/dialogue_shape.py previous_correction_fact は訂正の語（訂正 / より正確です /
    誤解）を含む文だけを連結する。「一つ訂正させてください。」は訂正の語を含むが中身を持たない前置きで、中身は次の文にある。製品頭脳 B 4296 が受け取った [前の往復での訂正] は前置き 1 文だけだった。処理軸
    logic（文の選び方の条件）、接続軸 information（次の往復へ運ぶ値が中身を失う）。
generalization:
  level: repo_pattern
  general_form: "範囲を語の出現で決める抽出が、語を含むが中身を持たない前置きの文だけを拾い、中身を含む隣の文を落とす"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    第 25 周 第 9 周の製品頭脳 B 4296（uxsim/runs/round25/BRAIN_NOTES_product_B_r9.md）が [前の往復での訂正]
    に前置きだけが渡ったと記した。previous_correction_fact の切り出しを読んで確かめた。
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    前置きの形（文頭 12 字以内 + 「訂正させてください / します / があります」等で文が終わる）を _CORRECTION_PREFACE_RE で見分け、前置きのときは続く 1 文を逐語で添える（200
    字の上限は従来どおり）。前置きでない訂正文の切り出しは変えない。
  landed_in:
    - "backend/core/dialogue_shape.py"
    - "backend/tests/test_round25_wave9_chat.py"
  verification:
    methods: [guardrail]
    unverified:
      - "前置きと中身が同じ文に無く 2 文以上離れる形"
      - "4296 の往復の再演で [前の往復での訂正] に中身が載ること（第 10 周）"
related: [IK-0576]
view_of: []
history: []
---

## 課題

訂正の持ち越しが前置きの 1 文だけになり、次の往復の AI は何を訂正したかを知らない。

## 発見の観点

第 25 周 第 9 周の製品頭脳 B 4296（uxsim/runs/round25/BRAIN_NOTES_product_B_r9.md）が [前の往復での訂正] に前置きだけが渡ったと記した。previous_correction_fact の切り出しを読んで確かめた。

## 解決の観点

前置きの形（文頭 12 字以内 + 「訂正させてください / します / があります」等で文が終わる）を _CORRECTION_PREFACE_RE で見分け、前置きのときは続く 1 文を逐語で添える（200 字の上限は従来どおり）。前置きでない訂正文の切り出しは変えない。

## 一般化

範囲を語の出現で決める抽出が、語を含むが中身を持たない前置きの文だけを拾い、中身を含む隣の文を落とす。型 extent-decided-by-proxy-signal。
