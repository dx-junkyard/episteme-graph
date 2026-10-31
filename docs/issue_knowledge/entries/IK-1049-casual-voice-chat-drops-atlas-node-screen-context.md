---
id: IK-1049
title: "音声中心の学生が分野の地図でノード（中性子星）を選んでから尋ねても、音声・casual の往復では画面文脈の解決が要素（element）に限られるため、地図の場所・パネルの検証の行・置かれた論文が AI に届かず、2 回とも「開いている地図の項目が見えない、注記の文を読み上げてほしい」と答え、出所の印は「教材に基づく」のまま"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-28
sources:
  - "docs/features/assistant_screen_adapter_design.md"
  - "docs/features/learning_chat_entry_unification_design.md"
feature_context:
  realizing: "音声で学ぶ学生が、地図で選んだ場所について画面に出ている検証の事実を AI と一緒に読む"
  layers: [screen_adapter_sa, rag_chat, field_atlas_s]
classification:
  axes:
    processing: [none]
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
    確認（コードと run）: backend/api/routes/learning.py の _learning_screen_context_block の呼び出しは kinds を
    elicit = ("view",) / casual = ("element",) / それ以外 = None（全 kind）にしている。地図の場所の事実（IK-1031 の是正 = view.atlas_node_id から
    サーバが解決する検証・承認の行と置かれた論文）は view 解決器が出すので、casual の往復では解決されない。st-07 は runner_voice
    （学習画面の音声ループと同じ intent_mode=casual + screen_mode=voice）で送り、同じ周の st-08（テキスト・日本語）は同じノードで論文名と
    パネルの行を AI が引いた。接続軸 condition = 「casual なら画面文脈を絞る」という条件が、地図の選択という別の状況にもそのまま掛かる。
    判定・構造・手続は設計どおり（casual で画面文脈を絞るのは短い会話調との衝突を避ける SA層 Phase 4 の判断）。
generalization:
  level: general
  general_form: "応答の調子に合わせて材料を絞る規則を伝達形式（音声）にも掛けると、その形式の利用者だけが画面で選んだものを AI に渡せなくなる"
pattern: available-but-unwired
discovery:
  perspective: [trace_walk, symptom_report]
  note: "st-07（音声）と st-08（テキスト）が同じ周に同じノード「中性子星」を選んで尋ね、回答と製品頭脳 B の grounding 所見（4263）・A（4264）を並べ、route の kinds の分岐を読んだ。run: uxsim/runs/c-r25-doubt-r7/20261027T173022Z-59619（seq8〜10）/ uxsim/runs/c-r25-doubt-r7/20261027T172411Z-57109（seq13・14）。"
resolution:
  perspective: [carry_through, deferred_decision]
  note: >-
    オーナー判断（2026-10-28・推奨どおり）: casual の「選択あり」の選択は要素に限らず利用者の明示の選択全般と読む。casual（elicit を除く）は
    kinds=("element",) のまま selected_atlas_node=True を渡し、_learning_screen_sources が include_atlas_node=True で地図の場所の材料
    （権限ゲート内の _screen_atlas_node_sources）を引き、新しい公開関数 resolvers/learning.py::resolve_selected_atlas_node（view 解決器と同じ
    _atlas_node_facts）が名前・札の文・詳細パネルの検証と承認の行・置かれた論文の題名（最大 3・出所ラベル付き）を事実文にする。kind は登録しない
    （登録 kind 集合は不変）・表示モードの事実と台帳・位置づけの射影は casual では渡さない・LLM 回数不変・範囲はコースだけ（DM1）。フロントは変更なし。
  landed_in:
    - "backend/core/assistant_context/resolvers/learning.py"
    - "backend/api/routes/learning.py"
    - "backend/tests/test_assistant_context_learning_route.py"
    - "backend/tests/test_doubt_round25_wave7.py"
    - "docs/features/assistant_screen_adapter_design.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "地図の場所の事実文を casual 用に別に組まず、view 解決器と同じ _atlas_node_facts 1 つを通した。固有の差分: モード（casual）はその関数だけを呼び、表示モードの行は呼ばない。"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面で地図の場所を開いたまま音声で尋ねる往復（第 8 周 st-07 の再演）"
      - "出所の印（教材に基づく）が回答の材料と一致しない点は別件として見ていない"
      - "音声で読み上げる回答の長さ（材料は最大 5 行）"
related: [IK-1031, IK-0907, IK-1043]
view_of: []
history: []
---

## 課題

症状: st-07（音声中心）が分野の地図で「中性子星」を選び、右パネルに「検証: 台帳に記帳なし。…」「承認: 承認記録なし」が出た状態で、
英語で「この項目の検証の注記は何を意味し、詳細はどこで見られるか」を 2 回尋ねた。AI は 2 回とも「開いている地図の項目もその注記も見えない」と言い、
学生に注記の文を読み上げるよう求めた。答えには表示中トピック（DCF 法）の教材 3 件が出典として付き「〔出所: 教材に基づく〕」と出たが、回答は教材を使っていない。
同じ周の st-08（テキスト）は同じノードで、置かれた論文の題名とパネルの行を AI が引いた（IK-1031 の是正はテキスト経路で効いている）。

原因: 学習チャットの画面文脈は casual の往復で element の解決だけを許し、地図の場所の事実を出す view 解決器を呼ばない。音声ループは casual で送る。

## 発見の観点

同じ周・同じノードの 2 人の学生（音声 / テキスト）の回答を並べ、ルートの kinds の分岐を読んだ。

## 解決の観点

未解決。「casual は画面文脈を絞る」を「利用者が選んだものは渡す」に分け、地図の選択を element と同じ扱いにする。

## 一般化

型 available-but-unwired。モードごとに材料を絞る規則を足すときは、利用者が明示的に選んだものを絞りの対象から外す。

第 8 周の再演: GONE（2026-10-28）。st-07 が音声（casual + voice）で地図の「中性子星」について 2 回尋ね、2 回とも「詳細に出ている検証の行は、確かめた記録がまだ無いという事実」「置かれている論文は中性子星の状態方程式をニューラルネットで推定するもの・置き方は AI の推定」「詳しくは出典タブの分野の中の位置づけ」と、選んだ場所・パネルの行・置かれた論文が音声の往復に届いた（耳で読める長さ・記号なし）。残り（軽・起票しない）: 出所の印が「出典を追えない AI の説明」、範囲の言い方が「このコースの教材の中に」/「このシステムの中に」で揺れる。AI に渡るパネルの行が学習者の語でない件は IK-1059。run: uxsim/runs/c-r25-doubt-r8/20261027T224734Z-1475（seq9・10）。
