---
id: IK-0383
title: "前提確認の逆質問のあと学習者がボタンではなく「いいえ、DCF法から教えてください」と打つと同じ逆質問が返り（3 回続いた）、楽屋での「DCF法とは何ですか」も逆質問に吸い込まれ、学習者の問いに答えないままになる"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 前提知識が足りない学習者が、その前提の説明を受けてから元のトピックへ進む
  layers: [rag_chat, structure_descent]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸で、check_prerequisites（services.py）の説明要求の判定が「前提の名前全体（DCF法による磁場強度の
    推定）が発話の部分文字列か」しか見ず、略した名前（DCF法）・「とは」の問いに掛からなかった
    （input_handling）。接続軸で、①直前の往復が逆質問そのものだった事実 ②楽屋の往復である事実が
    前提ゲートに渡らず、ゲートが毎回初めての往復として動いた（condition）。加えて、説明ルート
    （LEARNING_ADVICE の前提確認）に入っても生成プロンプトがナビゲーターの案内（「具体的な解説はまだ
    行わない」）で、前提の説明を書かせていなかった。構造軸は none（medium: ゲートの状態を持たない設計は
    直前ターンの読みで足りた）。統制軸は none（medium: 判定順は正しく、状態の受け渡しの欠落）。
    確認: 第 8 周 st-06 の transcript seq 10 / 14（楽屋）が同一の逆質問文・content_grounding=None。
generalization:
  level: repo_pattern
  general_form: 会話の関門が自分の直前の問いかけと入口の種別を知らず、答えを関門の条件で読み直して同じ問いを返し続ける
pattern: condition-not-propagated
discovery:
  perspective: [reproduction]
  note: ペルソナ通し受講 第 8 周（st-06 の逆質問 3 連続・楽屋での同一逆質問）。
resolution:
  perspective: [carry_through, single_point_fix]
  note: >-
    ルート側に決定論の判定 _prerequisite_followup を置いた。直前の assistant ターンがこのトピックの逆質問
    （定型 PREREQUISITE_GATE_MARKER。services の文言との一致はテストで固定）なら逆質問を繰り返さない
    （check_prerequisites は呼び、肯定の記帳はそちらのまま = 否定形は記帳しない）。逆質問への「いいえ・教えて」と、
    前提名（全体か頭の語 = 3 文字以上）を挙げて説明を求める発話は、typed action が無ければ意図分類を経ずに
    前提の 3 段解決（是正 F4）へ流し、その前提 1 つの説明を書かせる（explain_prerequisite・LLM は 1 コールのまま）。
    楽屋は前提ゲートを通さない（回答は通常の RAG・記録は backstage_question のまま）。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_ik0383_prerequisite_followup.py
    - docs/backend/rag-chat.md
  verification:
    methods: [guardrail]
    unverified:
      - 砂場・ブラウザでの再操作（第 8 周の st-06 の往復で逆質問が繰り返されず説明が返るか）
      - 説明プロンプトの出力の質（実 LLM では未確認）
      - 前提名の頭の語が短い分野（2 文字の漢語など）では略称の言及を拾わない
related: []
view_of: []
history: []
---

## 課題

逆質問が繰り返され、前提についての問いに答えない。

## 発見の観点

同じ逆質問文が連続する transcript。

## 解決の観点

直前の逆質問と楽屋の事実をゲートへ運び、答えと問いを前提の説明へ流す。

## 一般化

関門が自分の問いかけを覚えていない。
