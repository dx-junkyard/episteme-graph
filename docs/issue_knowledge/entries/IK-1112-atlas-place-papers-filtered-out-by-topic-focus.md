---
id: IK-1112
title: "分野の地図で場所（暗黒エネルギー）を開いて尋ねても、焦点の論文はトピックの論文だけで、地図の場所に置かれた論文の行が焦点の優先規則で落ち、検索の文脈がトピックの論文（Cep B）に固定される"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/focus_document_design.md"
feature_context:
  realizing: "学習者が分野の地図で開いた場所について、その場所に置かれた論文を根拠に答える"
  layers: [rag_chat, screen_adapter_sa]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    焦点の論文（core/focus_document.py FD1）は 明示 → トピック → 直前の引用 → 画面で選んだ論文 の段順で、画面の地図の場所（view.atlas_node_id）はどの段にも入っていなかった。地図の場所は
    SA層の事実文（_screen_atlas_node_sources）にだけ効き、RAG の並べ替えは prefer_focus がトピックの論文で FD3 を掛けるため、類似度がトピックの論文の最良より低い地図の論文の行は除かれた。接続軸
    condition（画面の明示状態が焦点へ渡らない）。
generalization:
  level: repo_pattern
  general_form: "画面で選んだ対象が文脈の事実には効くが、検索の優先・除外を決める焦点には渡らず、別の段の対象が検索を独占する"
pattern: condition-not-propagated
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    製品頭脳 B 4293（BRAIN_NOTES_product_B_r9.md）: 英語の地図の問いの RAG 文脈が Cep B の偏光・13CO だった。st-08（uxsim/runs/c-astro-r25-share-r9 seq
    9/10）の screen_context.view.atlas_node_id と chat core の resolve_focus_document の入力を突き合わせた。
resolution:
  perspective: [carry_through, canonical_source]
  note: >-
    焦点の正本に with_screen_place_documents を足し、chat core は resolve_focus_document
    の直後に、地図の場所に置かれた論文（learner_landscape_for_documents・コースの論文 ∩ スコープ）を焦点の後ろに加える（段とトピックの論文は変えない・discuss
    では足さない）。view.atlas_node_id は app.js の「最後に開いた場所」で地図を閉じても残るため、トピックの段を外さず、前提の逆質問のゲートも飛ばさない（地図の問いでゲートを出さない規律は ↗ の
    atlas_context のまま — 4295 で出たゲートは atlas_context の無い送信だった）。設計書 focus_document_design.md に FD-note 3 と pre-mortem を追補。
  landed_in:
    - "backend/core/focus_document.py"
    - "backend/api/routes/learning.py"
    - "docs/features/focus_document_design.md"
    - "backend/tests/test_round25_wave9_chat.py"
  verification:
    methods: [guardrail]
    unverified:
      - "地図の場所に置かれた論文が検索で上位に入ることの再演（第 10 周・砂場）"
      - "地図を閉じた後のトピックの問いで、地図の論文の行が混ざる度合い（並べ替えは焦点の内外だけを見る）"
      - "「地図を今開いている」を区別する画面の申告（app.js の atlas:opened / atlas:closed）は作っていない"
related: [IK-1031, IK-0907, IK-0552]
view_of: []
history: []
---

## 課題

地図の場所を尋ねても、トピックの論文だけが焦点で、地図の論文が文脈から落ちる。

## 発見の観点

製品頭脳 B 4293（BRAIN_NOTES_product_B_r9.md）: 英語の地図の問いの RAG 文脈が Cep B の偏光・13CO だった。st-08（uxsim/runs/c-astro-r25-share-r9 seq 9/10）の screen_context.view.atlas_node_id と chat core の resolve_focus_document の入力を突き合わせた。

## 解決の観点

焦点の正本に with_screen_place_documents を足し、chat core は resolve_focus_document の直後に、地図の場所に置かれた論文（learner_landscape_for_documents・コースの論文 ∩ スコープ）を焦点の後ろに加える（段とトピックの論文は変えない・discuss では足さない）。view.atlas_node_id は app.js の「最後に開いた場所」で地図を閉じても残るため、トピックの段を外さず、前提の逆質問のゲートも飛ばさない（地図の問いでゲートを出さない規律は ↗ の atlas_context のまま — 4295 で出たゲートは atlas_context の無い送信だった）。設計書 focus_document_design.md に FD-note 3 と pre-mortem を追補。

## 一般化

画面で選んだ対象が文脈の事実には効くが、検索の優先・除外を決める焦点には渡らず、別の段の対象が検索を独占する。型 condition-not-propagated。

第 10 周（2026-10-31・judge_doubt_r10・面 B の観察）: 地図の場所（暗黒エネルギー）を開いた英語の問いでも、検索はトピック t2 の論文（Cep B）を引き、構造の行まで材料に載る（製品頭脳 B 4312）。AI は「使っていない」と明言するのに、回答の下に Cep B の出典 3 件と「〔出所: 教材に基づく〕」が付く（IK-0511 の『引用ゼロでも出所を決めた出典を並べる』設計どおりの帰結だが、本文の言明と札が食い違う）。画面文脈の配置の行（どの論文がこの場所に置かれているか）は届いている。run: uxsim/runs/c-r25-doubt-r10/20261031T054204Z-72077（seq9・10）。

第 10 周（2026-10-31・製品頭脳 A 4307 / B 4312）: STILL。FD-note 3 の「加える」だけではトピックの論文が焦点に残り、地図の場所の問いでも
Cep B の出典・構造の行が先に載った。第 10 波で FD-note 4: 地図で場所を選んだ直後の 1 往復（と地図を開いている間）だけ app.js が
selection.atlas_node_id を載せ、chat core は with_screen_place_documents(..., selected_now=True) で置かれた論文だけを明示の焦点にする
（トピックの段を外す・表示中の教材の出典チャンクを足さない）。view だけの往復は FD-note 3 のまま。上の unverified の 3 点目は解消
（atlas:opened / atlas:closed と送信後に落とす印）。landed_in に test_round25_wave10_chat.py。

