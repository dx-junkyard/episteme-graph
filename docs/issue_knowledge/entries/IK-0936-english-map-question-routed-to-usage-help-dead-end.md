---
id: IK-0936
title: "英語で「地図のどこにこの論文があるか・札は何を意味するか」と尋ねた学生の問いが、意図分類で使い方（USAGE_HELP）に回り、英語のマニュアルが無いので「日本語の使い方ガイドに該当がありません。教材の質問なら送り直してください」の固定文で終わり、教材の内容にも地図の事実にも届かない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/features/manual_help_kb_design.md"
  - "docs/features/learning_chat_entry_unification_design.md"
feature_context:
  realizing: "英語で学ぶ学生が、地図の表示と論文の位置について学習チャットに尋ねる"
  layers: [rag_chat, help_kb, frontend_learning_ui]
classification:
  axes:
    processing: [unknown]
    structure: [responsibility]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: low
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 問いは 50 字を超えるので非LLM の pre-route（_is_usage_question）は通らず、_classify_intent の LLM 分類が USAGE_HELP を返したと読む（応答は HELP の英語の
    固定文・sources 空・content_grounding null）。接続軸 condition: 英語で問う利用者という条件が HELP の行き先（日本語のマニュアルだけ）に伝わらず、ヒットしないとき
    教材の検索へ戻さない。構造軸 responsibility: 「画面の要素の意味」と「教材の内容」の両方を含む問いの振り分けを分類器 1 回に任せ、取り違えたときの戻り道が無い。
    処理軸 unknown: 同じ趣旨の別の言い回しの英語の問い（再開後の run）は RAG に回ったので、分類の揺れか言い回しの差かは未確定。確かめるには、同じ文を複数回送り、
    分類の結果と英語のマニュアル検索の結果を記録する。
generalization:
  level: repo_pattern
  general_form: "内容と操作の両方を含む問いを一回の分類で片方の道に送り、送り先が利用者の言語で空のとき、もう片方の道へ戻さずに固定文で終える"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, boundary_walk]
  note: >-
    st-04（留学生・英語）が地図の位置と札の意味を英語で尋ね、HELP の固定文で終わった。同じ趣旨を日本語で尋ねた st-01 は RAG に回って論文の主題を返した。再開後の
    st-04 の別の言い回しは RAG に回った（是正ではなく揺れの可能性）。run: uxsim/runs/c-r25-doubt-r3/20261023T034010Z-73962（seq12）/
    uxsim/runs/c-r25-doubt-r3/20261023T033451Z-40732（seq12）/ uxsim/runs/c-r25-doubt-r3/20261023T070217Z-13811（seq12）。
resolution:
  perspective: [carry_through]
  note: >-
    学習チャットの分類経路（intent==USAGE_HELP・型付き usage_help ではない）で、問いがかな・漢字を含まない（英語）かつマニュアル・ベクトル補助層・開いているパネルのどれにも該当節が無いとき、_usage_help_response(return_none_if_undocumented=True) が固定文を返さず痕跡・履歴も残さず None を返し、通常の RAG 経路（DOMAIN_RAG）へ戻す。LLM 呼び出しは増えない（分類は済み・回答は 1 コール）。日本語の無ヒットは従来どおり固定文（G層の使い方の需要信号を残す）。再現は st-04 の中断 run の逐語の問いで、分類を USAGE_HELP に固定したテストで固定文 → 修正後に RAG の答えへ変わることを見た。分類が揺れか言い回しかは実機の反復送信では確かめていない（hypothesis のまま。戻り道は分類の結果によらず効く）。
  landed_in:
    - "backend/api/routes/learning.py"
    - "backend/tests/test_help_usage_route.py"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "実機で同じ英語の問いを反復送信したときの分類の揺れ"
      - "英語の問いが分類で LEARNING_ADVICE / CHIT_CHAT に回る場合"
related: [IK-0633, IK-0574, IK-0907]
view_of: []
history: []
---

## 課題

症状: 英語で地図のことを尋ねた学生に、「日本語の使い方ガイドに該当がありません。教材の質問なら送り直してください」という固定文だけが
返った。学生は論文の位置を尋ねていたので、使い方の案内として扱われたことが分からず止まった。

原因（仮説）: 内容と画面の両方を含む問いが分類で使い方に回り、英語では使い方の節が見つからず、教材の検索へ戻す道が無い。

## 発見の観点

同じ趣旨の問いを日本語と英語の学生に送らせ（再現）、行き先の違いを比べた（境界の歩き）。

## 解決の観点

分類の取り違えの戻り道（英語の HELP 無ヒット → RAG）を足した（carry_through）。（第 25 周 第 3 波・2026-10-23）

## 一般化

問いの振り分けを一回の分類に任せる系では、送り先が利用者の条件（言語）で空になるときの戻り道が無いと、利用者は固定文で止まる。
確かめ方は、各送り先が空になる条件の利用者で 1 件ずつ送り、内容の答えか事実文の次の一手に届くことを見ること。
