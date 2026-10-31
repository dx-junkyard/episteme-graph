---
id: IK-1031
title: "学生が分野の地図でノードを選んでから尋ねると、AI に届く画面文脈は名前と札の意味の 1 文だけで、右パネルに出ている「検証」の行（「台帳に記帳なし」「記帳された直接検証なし」）とそのノードに結びつく論文（［根拠を見る］の先）が渡らないため、AI は「検証の行の中身は手元に無い」「どの論文が前提にしているかは含まれていない」と答え、「どこで見られるか」に画面の場所を言えない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/features/stakes_ledger_design.md"
  - "docs/features/assistant_screen_adapter_design.md"
  - "docs/features/field_atlas_overlay_spec.md"
feature_context:
  realizing: "学生が地図で選んだ場所について、画面に出ている検証の事実とそこから辿れる論文を AI と一緒に読む"
  layers: [screen_adapter_sa, rag_chat, field_atlas_s]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
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
    確認（コードと run）: app.js getScreenContext は view.atlas_node_label / atlas_node_status / atlas_node_ledger_status だけを控え、
    core/assistant_context/resolvers/learning.py の _atlas_node_facts は名前 1 行と ATLAS_PILL_NOTES の札の文 1 行だけを事実にする。
    右パネルの検証の行・承認の行・結びつく論文（GET /api/atlas の同じノードの ledger 事実と根拠の論文）は画面にあるのに画面文脈へ配線されていない。
    接続軸 information = 画面に出ている事実が AI への入力に届かない。他の軸 none（判定・構造・手続は設計どおり）。
generalization:
  level: general
  general_form: "画面の選択を AI に渡すとき名前と分類だけを渡し、同じ画面に並ぶ事実を渡さないと、AI は利用者に見えている事実を「手元に無い」と言い、画面へ送り返すだけになる"
pattern: available-but-unwired
discovery:
  perspective: [trace_walk, symptom_report]
  note: "st-05（中性子星）と st-06（暗黒エネルギー・日本語 2 回）がノードを選んでから尋ね（harness H36 の是正後で初めて atlas_node_label が送られた）、AI が札の文を逐語で引く一方でパネルの検証の行と論文を持たないことを、回答と製品頭脳 A の grounding 所見（4243・4245・4247）で確かめた。run: uxsim/runs/c-r25-doubt-r6/20261027T123134Z-9179（seq13・14）/ uxsim/runs/c-r25-doubt-r6/20261027T123621Z-11841（seq8〜10）。"
resolution:
  perspective: [carry_through]
  note: >-
    学習画面は分野の地図で開いた場所の参照 view.atlas_node_id だけを足し（SA1）、学習ルートの _screen_atlas_node_sources が受講ゲート済みのコースから分野を導いて凍結骨格の状態キャッシュ（GET /api/atlas/node と同じ行）の検証・承認の行と、learner_landscape_for_documents（grounding_document_ids の範囲 = DM1）で引いたその場所に置かれた論文の題名（最大 3・出所ラベル付き）を sources[atlas_node] にし、view 解決器が事実文にする（読める場所 = 出典タブの「分野の中の位置づけ」）。LLM 回数不変・elicit では引かない。
  landed_in:
    - "frontend/public/js/app.js"
    - "frontend/public/index.html"
    - "backend/api/routes/learning.py"
    - "backend/core/assistant_context/resolvers/learning.py"
    - "backend/core/assistant_context/schema.py"
    - "backend/tests/test_doubt_round25_wave6.py"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: "論文の母集合は受講ゲート済みの grounding_document_ids だけで、画面の参照が範囲を広げない。"
  verification:
    methods: [guardrail]
    unverified:
      - "学生が地図でノードを選んで「どこで見られるか」を尋ね、回答が論文名で答えることを実機（砂場）で確かめていない（第 7 周 st-07）"
      - "どの論文のどの主張がその場所の根拠かまでは渡していない"
related: [IK-0907, IK-0986, IK-1018]
view_of: []
history: []
---

## 課題

症状: st-05 が地図で「中性子星」を選んで「どこまで確かめられているの？」と尋ねると、AI は論文側の確かめ方を答えたあと「地図の『中性子星』という場所そのものの
検証の記録（「検証」の行の中身）は、いまの手元の情報には含まれていない」と言った。右パネルには「検証: 台帳に記帳なし。」と出ていた。st-06 の「暗黒エネルギー」でも
AI は札の文を逐語で引いた（名前と札は届いた — IK-0907 の是正は GONE）が、「渡っているのは札のこの一文だけ」「どの論文のどの議論が前提にしているかは含まれていない」と言い、
「詳しくはどこで見られますか」に画面の具体的な入口を答えられなかった。答えは正直（捏造しない）。

原因: 画面文脈の地図ノードは名前と状態コードだけで、パネルの他の事実は配線されていない。

## 発見の観点

操作列の追跡（選択 → 問い → grounding）と利用者の不満（画面と AI で見えているものが違う）。

## 解決の観点

未解決。参照（node_id）を渡してサーバが解決する SA層の規約どおりに足す。

## 一般化

型 available-but-unwired。画面に事実がある選択を AI に渡すときは、名前ではなく参照を渡してその事実を解決する。

## 第 6 波の是正（2026-10-27）

参照（node_id）を渡してサーバが検証・承認の行と置かれた論文を解決する。設計書 §16.9。

第 7 周の再演: GONE（テキスト経路）・音声経路は IK-1049（2026-10-28）。st-08 が地図で「中性子星」を選んでから日本語で尋ねると、AI は置かれた論文『Neutron Star Equation of State via Physics Informed Neural Network』（AI による推定・未確認）とパネルの「検証: 台帳に記帳なし」「承認: 承認記録なし」を引き、出典タブ「分野の中の位置づけ」へ案内した（第 6 周に答えられなかった「どこで見られるか」に答えた）。同じ周の st-07（音声 = casual）は同じノードで何も届かず（IK-1049）。付記（起票しない）: 部品を選ばずに「この部品は…」と尋ねた往復で台帳の記帳が会話に届かないのは、トピック単位の事実（SA層 4-c）が保留の設計どおり。run: uxsim/runs/c-r25-doubt-r7/20261027T172411Z-57109（seq13・14）/ uxsim/runs/c-r25-doubt-r7/20261027T173022Z-59619（seq9・10）。
