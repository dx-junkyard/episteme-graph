---
id: IK-0444
title: "出典番号の固定（IK-0432）が第 10 周でも崩れ、同じチャンクが [出典31] → [出典50] のように往復ごとに新しい番号を受け取った（保存本文はドリルダウンの目印込みで、送り返された本文と照合できず、保存でクライアントの sources の無い履歴に全体が上書きされた）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が会話の前の回答に付いた出典番号を、あとの往復でも同じ資料として参照できる
  layers: [rag_chat, discuss]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理・接続軸: persist_chat_history はどの経路でも body.history（クライアントの写し）+ 新しい往復で行全体を書き直す。IK-0432 の
    _rehydrate_history_sources は本文一致で保存済みの sources を戻すが、保存本文は extract_inline_actions の前の生の回答（[〇〇について詳しく聞く]
    の目印込み）で、応答で返し送り返される本文は目印を抜いた形なので一致せず、戻せなかったターンの sources が保存で失われた。uxsim の runner は直近の窓（HISTORY_WINDOW）だけを
    {role, content} で送るので、窓の外の往復も保存から消えた（logic / information）。構造軸: 対応表（チャンク → 番号）が各ターンの sources
    に散らばり、会話全体の正本を持たなかった（representation）。確認: 78722 の step 30 → 31 → 34 で 459b679e が 31 → （不在）→ 50
    となり、直前の往復に無い番号は必ず続きから振り直されていた。
generalization:
  level: general
  general_form: サーバが付けた描画メタを、クライアントが送り返す写しで行ごと上書きし、次の処理がその写ししか読めない
pattern: full-update-clobbers-unrelated-fields
discovery:
  perspective: [trace_walk, data_inspection]
  note: 第 10 周（c-astro-verify-wave456）の transcript の往復ごとの sources と mailbox の実プロンプト（req-*.json）を並べた。
resolution:
  perspective: [representation_change, carry_through]
  note: >-
    会話全体の「チャンク → 番号」をサーバが累積の対応表（citation_map）として持つ。採番器 _SessionCitationNumbers は保存済み履歴の citation_map と sources
    から最初の番号を引き、mapping() を本体 RAG と前提の説明の assistant_meta に焼き込む。どの経路の保存も body.history を書き直すので、受け取った履歴の最後の assistant
    ターンにも対応表の控えを載せる（_carry_citation_map）。本文照合の鍵からドリルダウンの目印を抜く。学習者向けの履歴 GET からは citation_map を外す。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_ik0444_0451_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（第 10 周の対話オラクルを修正後に再実行していない）
      - 保存行そのものがクライアントの窓の長さに縮む挙動は変えていない（番号の対応だけを運ぶ）
related: [IK-0432]
view_of: []
history: []
---

## 課題

出典番号が往復ごとに新しくなり、前の回答の番号が後で別の番号になる。

## 発見の観点

往復ごとの sources を並べ、直前の往復に無いチャンクだけが振り直されることを見た。

## 解決の観点

対応表を会話の正本としてサーバが持ち、どの保存経路でも運ぶ。

## 一般化

クライアントの写しで行ごと上書きし、サーバのメタを失う型。
