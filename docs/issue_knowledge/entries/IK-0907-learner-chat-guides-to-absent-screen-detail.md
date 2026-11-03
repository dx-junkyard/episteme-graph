---
id: IK-0907
title: "学習チャットの AI が、材料に台帳の記帳が渡っていないことを正直に述べたうえで「わたしの地図でその項目を開き、記帳に書かれた条件（スコープ）を見てください」と案内するが、わたしの地図は検証の札（状態）だけでスコープの条件を出さない — AI が画面に無い操作・情報へ誘導する"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/architecture/persona_enactment_testing_design.md §22.8"
  - "docs/features/assistant_screen_adapter_design.md"
  - "docs/features/personal_map_nearby_design.md"
feature_context:
  realizing: "学生が地図で見た検証の札の意味を学習チャットで尋ねる"
  layers: [rag_chat, screen_adapter_sa, personal_network, doubt_d]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: 学習チャットの system 文脈に、学習者が見ている各画面で何が表示されるか（地図は状態の札のみ・条件は出典タブの一行）の事実が渡っていない。当該ターンは画面の要素を選んでいないので SA層の verification 解決器も働かず（設計どおり）、AI は手がかりの無いまま一般的な操作手順を作文した。処理軸 wording:
    生成された案内文が実在しない画面の情報を前提にしている。構造軸 none。確信度 medium は、根を『プロンプトに画面の事実が無い』と『モデルの作文』のどちらに置くかが確定していないため。
generalization:
  level: general
  general_form: "AI が利用者に画面上の操作を案内するとき、その画面に何が表示されるかの事実を材料に持たないため、もっともらしいが実在しない操作・情報へ誘導する"
pattern: fallback-fabricates-missing-link
discovery:
  perspective: [reproduction]
  note: >-
    st-03 と st-09 が地図の『直接検証の記帳あり』の条件を尋ね、AI が地図で条件を見るよう案内したが、直前の地図の画面には状態の札しか無かった。run: uxsim/runs/c-r25-doubt-r2/20261022T221137Z-82302（seq13・14）・uxsim/runs/c-r25-doubt-r2/20261022T221543Z-84621（seq13・14）。
resolution:
  perspective: [carry_through]
  note: >-
    第 2 波で是正（2026-10-23）。学習画面の画面文脈に view.personal_map_seen（この学習で「わたしの地図」を開いたか・真偽値）を載せ、SA層 view の解決器が LEARNING_PERSONAL_MAP_SCREEN_FACT（地図の検証の札は状態だけ・条件は出典タブの台帳の一行）を渡す。AI の画面の案内が画面の事実に従う。台帳の一行の解決は要素の論文で引く（IK-0899）。
  landed_in:
    - "backend/core/assistant_context/schema.py"
    - "backend/core/assistant_context/resolvers/learning.py"
    - "backend/api/routes/learning.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_doubt_ledger_document_key_round25.py"
  verification:
    methods: [guardrail]
    unverified:
      - "範囲表示のノードへ SA層 verification を引く経路と、地図の札から台帳の一行を開く操作は入れていない"
      - "LLM の実応答での案内は未確認（第 3 周 st-01 / st-04 で見る）"
related: [IK-0906, IK-0899]
view_of: []
history: []
---

## 課題

症状: AI は「材料に記帳の条件は含まれていません」と正直に答えるが、続けて「わたしの地図でその項目を開き、記帳に書かれた
条件を見てください」と案内する。地図には条件が出ないので、学生は案内どおりに確かめられない。

原因: 学習チャットの材料に、各画面が何を表示するかの事実が無い。画面の要素を選ばずに尋ねると、SA層の検証の解決器は
働かない（設計どおり）。

## 解決の観点

第 2 波で是正（2026-10-23。frontmatter の resolution）。以下は起票時の是正案。是正案（担当が選べる範囲）:

- 地図の範囲表示から尋ねた往復（`screen_context.view` が地図）では、SA層の `verification` を範囲のノードにも引く
  （学習者の地図にある段の台帳の事実文＋記帳のスコープ。IK-0899 の是正で document を必須にした後）。
- system 文脈に「検証の条件（スコープ）は出典タブの一行に出る・地図は状態の札だけ」の固定の事実を 1 行足し、画面の操作を
  案内するときはそれに従わせる（新しい固定文の継ぎ足しより、画面文脈の事実で渡す方を推奨）。
- 地図の札から記帳のスコープの事実文へ辿れるようにする（札を押すと台帳の一行を出す）。

## 発見の観点

学生に地図の札の意味を尋ねさせ、回答の案内と直前の画面の観察を突き合わせた。

## 一般化

AI に画面の案内をさせるなら、画面に何が出るかの事実を材料に持たせる。確かめ方は、案内に出てくる画面の操作を実際に
たどり、案内どおりの情報に届くことを見ること。

第 3 周の再演: 残存（2026-10-23）。わたしの地図を開いた後の問いに view.personal_map_seen は載ったが、AI の案内は日本語で「記帳を付けた担当の先生に尋ねる」、
英語で「The tag on your map is the place to check」（地図の札に条件は無い）だった。加えて、渡している事実文「条件は出典タブの台帳の一行に出ます」は
ノード（主グラフの段）の記帳には当たらない（出典タブは主張・式の一行だけ）。学習者がノードのスコープへ届く面が無いことが根で、IK-0932 に起票した。
run: uxsim/runs/c-r25-doubt-r3/20261023T033451Z-40732（seq14・15）/ uxsim/runs/c-r25-doubt-r3/20261023T070217Z-13811（seq14・15）。

第 3 波で是正（2026-10-23）: 根の IK-0932 を直した — 地図の札（直接検証・間接的な支持・反証）の下に記帳された条件（成立条件・領域）の 1 行を出し、AI に渡す画面の事実文を「札の下に条件が 1 行で出る・根拠の引用・記帳した人・精度は出ない」に合わせた（出典タブへ案内しない）。LLM の実応答での案内は未確認（第 4 周 st-07 / st-10）。

第 4 周の再演: 残存（2026-10-25・形を変えて）。地図 → AI → 地図の強い循環は出なかった（st-07 の音声では AI が「地図の配置は手元に無い」と正直に言い先生へ向けた）。ただし
英語の st-04 が分野の地図の札（暗黒エネルギー〔暗黙の前提〕）の意味を 2 回尋ねると、2 回とも「地図そのものの説明・使い方ガイド・先生」へ戻した — 学生が読めなかったのは
その地図の説明そのもの。分野の地図の札（pill と pill_note）は SA層の画面文脈に入っておらず、AI は札の文を知らない。札の語の軸のずれ自体は IK-0964。
run: uxsim/runs/c-r25-doubt-r4/20261024T233257Z-31520（seq13）/ uxsim/runs/c-r25-doubt-r4/20261024T233659Z-74988（seq8・9）。

第 4 波で是正（2026-10-25）: 分野の地図で最後に開いた場所を、画面文脈の view に名前（40 字）と状態の enum だけで載せ（app.js の atlas:nodeselect の購読）、SA層 view 解決器が「学習者は分野の地図で「…」を開きました」と札の説明の文（label_vocab.ATLAS_PILL_NOTES — 詳細パネルと同じ正本）の 2 行を AI に渡す。topic / visible の解決器は作らない。LLM の実応答（英語の st-04 の問いで地図の札を説明できるか）は未確認。

第 5 周の再演: 未確認（2026-10-27）。st-10・st-05 とも分野の地図を開いて札を読んだが、ノードを選ぶ手（learning.atlas.select_node）を経ずに尋ねたので、第 4 波の view.atlas_node_label は送られていない（app.js も atlas:nodeselect でだけ控える — 投影は製品どおり）。場面 s-db-map-ask-en に選ぶ手が無いのが原因（harness H36）。観察: st-10 の問いは前提の逆質問の取り違えで前提の説明へ（IK-1004）、st-05 の問いは使い方に回って送り直しが行き止まり（IK-1005）。run: uxsim/runs/c-r25-doubt-r5/20261027T071839Z-88666（seq12・13）/ uxsim/runs/c-r25-doubt-r5/20261027T072425Z-13857（seq7〜9）。

第 6 周の再演: GONE（2026-10-27・名前と札）。harness H36 の是正で学生がノードを選んでから尋ね、view.atlas_node_label が初めて送られた。st-06 は項目名を書かずに尋ねたが AI は「地図の『暗黒エネルギー』の札」と札の文を逐語で引き、分野として未検証とは言わない閉世界の言い方も正しかった。残り: パネルの検証の行と結びつく論文が渡らず「どこで見られるか」に答えられない — IK-1031。run: uxsim/runs/c-r25-doubt-r6/20261027T123621Z-11841（seq8〜10）/ uxsim/runs/c-r25-doubt-r6/20261027T123134Z-9179（seq13・14）。
