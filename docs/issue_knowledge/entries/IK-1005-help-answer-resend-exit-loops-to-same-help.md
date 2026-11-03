---
id: IK-1005
title: "学習チャットが使い方（USAGE_HELP）に振り分けた回答の末尾に「教材の内容についての質問なら、そのまま送り直してください。」と添えるが、同じ文を送り直しても同じ分類で同じ使い方の回答が返り、案内した出口が行き止まりになる — 画面の事実文（分野の地図の札の文）の中身を尋ねる問いは、意図分類に画面の文脈が渡らず使い方に回る"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/features/stakes_ledger_design.md §16.8"
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
feature_context:
  realizing: "学生が分野の地図の札に付いた検証の記録の文が何を確かめた記録かを学習チャットで尋ねる"
  layers: [rag_chat, help_kb, screen_adapter_sa]
classification:
  axes:
    processing: [none]
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
    接続軸 information: routes/learning.py は分類が USAGE_HELP のとき _usage_help_response の本文に _USAGE_HELP_FOOTER（「教材の内容についての質問なら、そのまま送り直してください」）を足す。分類（_classify_intent）は当該発話だけを入力にする（LC4）ので、同じ文を送り直せば同じ分類になる — 案内した出口は機能しない。IK-0936 の戻り道（無ヒットの英語は RAG へ）は日本語でマニュアルに該当節がある場合には働かない。製品頭脳 B（4061）は、画面の事実文の意味を問う問いの行き先が未定義と指摘。
generalization:
  level: repo_pattern
  general_form: "振り分けの誤りを利用者に直させる案内が、同じ入力に同じ振り分けを返す仕組みでは効かず、利用者を同じ場所へ戻す"
pattern: fallback-fabricates-missing-link
discovery:
  perspective: [reproduction]
  note: >-
    st-05 が同じ日本語の問いを 2 回送り、2 回とも同じマニュアル節（地図の項目を押したときにできること）が返った。2 回目は 1 回目の末尾の案内に従った送り直しで、学生は諦めた。run: uxsim/runs/c-r25-doubt-r5/20261027T072425Z-13857（seq8・9）。製品頭脳 B 4061（uxsim/runs/round25/BRAIN_NOTES_product_B_r5.md）。
resolution:
  perspective: [single_point_fix, carry_through]
  note: >-
    使い方に見える発話を HELP に回さず RAG へ流す判定を _usage_help_goes_to_rag の 1 箇所に集め（実行結果の言葉・使い方の回答の直後の同じ発話の送り直し・地図で開いた場所の名前/札/地図に触れる問い）、pre-route と分類の USAGE_HELP の両方が通る。案内の文言「そのまま送り直してください」は不変で、案内どおりに送り直すと RAG に届く。型付きの usage_help（明示）は従来どおり HELP。
  landed_in:
    - "backend/api/routes/learning.py"
    - "backend/tests/test_doubt_round25_wave5.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "HELP から RAG への戻り道を _usage_help_goes_to_rag の 1 箇所にし、pre-route と分類の両経路が同じ述語を通る（ガードレールで呼び出し 2 箇所を固定）。"
  verification:
    methods: [guardrail]
    unverified:
      - "送り直しの判定はクライアントが送る history の直前 2 ターンに依る（履歴を送らない経路では働かない）"
      - "地図の場所を選んだ状態の問いは run で当てていない（H36）"
related: [IK-0936, IK-0907]
view_of: []
history: []
---

## 課題

症状: 使い方の回答の末尾の「そのまま送り直してください」に従うと、同じ使い方の回答が返る。札の文が何を確かめた記録かには答えない。

原因: 分類は当該発話だけで決まり、送り直しは同じ入力。案内に対応する経路（内容の質問として通す）が無い。

## 発見の観点

再現（同じ問いを 2 回）。

## 解決の観点

第 25 周 面 B 第 5 波（2026-10-27・docs/features/stakes_ledger_design.md §16.8）で解決。使い方に見える発話を HELP に回さず RAG へ流す判定を _usage_help_goes_to_rag の 1 箇所に集め（実行結果の言葉・使い方の回答の直後の同じ発話の送り直し・地図で開いた場所の名前/札/地図に触れる問い）、pre-route と分類の USAGE_HELP の両方が通る。案内の文言「そのまま送り直してください」は不変で、案内どおりに送り直すと RAG に届く。型付きの usage_help（明示）は従来どおり HELP。

起票時の是正案: 是正案: ①末尾の案内を操作に変える — 使い方の回答に［教材の内容として聞き直す］（typed action で分類をバイパスして DOMAIN_RAG へ）を付け、文の案内は削る。②画面文脈に地図の場所（IK-0907 第 4 波の view.atlas_node_*）があるときは、札の文の意味の問いを使い方に回さず、SA層の事実文（札の説明と台帳の状態）で答える。送り直しで行き先が変わる約束はしない。

## 一般化

利用者に『もう一度』を促す案内は、もう一度で違う結果が出る経路を伴う。確かめ方は、案内どおりに操作して違う行き先に届くことを見ること。

第 6 周の再演: GONE（2026-10-27）。st-06 が地図のノードを選んでから同じ日本語の問いを 2 回送ると、2 回とも使い方に回らず内容の回答（2 回目は「前の答えを変えずに…結論は撤回していません」）。st-05 の地図の問いも RAG。画面の場所を答えられないのは IK-1031。run: uxsim/runs/c-r25-doubt-r6/20261027T123621Z-11841（seq9・10）。
