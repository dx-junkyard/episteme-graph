---
id: IK-1064
title: "⚓ の部品の文脈を開いたまま「この部品の説明はどの先生が書いたか・このコースの先生の説明はあるか」と尋ねると、意図分類が使い方（USAGE_HELP）に回し、マニュアル「コースを受講する」の節が返る — app.js の画面文脈は開いた部品の element_id だけを送り element_label を送らないので、第 6 波の分類の手がかり（選んだ要素の名前）が要素では一度も働かない"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/assistant_screen_adapter_design.md §11"
  - "docs/features/manual_help_kb_design.md"
feature_context:
  realizing: "学生が開いている部品の説明の出所・有無を学習チャットで尋ね、画面に出た事実に基づいて答えてもらう"
  layers: [rag_chat, screen_adapter_sa, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: routes/learning.py _intent_screen_hint は screen_context.selection.element_label があれば
    「学習者は教材の「…」を選んでいます」を分類に添えるが、app.js getScreenContext は openContextElement から element_id / element_type
    だけを selection に入れ、element_label を入れない（state.openContextElement も名前を持たない）。分類のプロンプト（製品頭脳 4279）に
    画面の 1 行が無かった。加えて「説明の帰属を問う」発話に分類の行き先が無く、迷えば DOMAIN_RAG の規則にもかかわらず USAGE_HELP になった。
    統制軸 none: 判定の順序・ゲートの位置は規則どおり。
generalization:
  level: repo_pattern
  general_form: "受け手が参照する欄を、送り手の画面が一度も埋めず、受け手側のテストだけが通って経路が死んでいる"
pattern: available-but-unwired
discovery:
  perspective: [trace_walk, reproduction]
  note: >-
    第 25 周 面 A 第 8 周 st-10 が ⚓ の部品の文脈を開き隣の部品へ移った後に区画を選んで尋ねた（uxsim/runs/c-astro-r25-share-r8/20261027T224858Z-2266 seq11）。
    応答はマニュアルの受講の節。分類のプロンプト（uxsim/runs/mailbox/req-04279.json）に画面の行が無く USAGE_HELP。
    app.js の selection の組み立てと _intent_screen_hint の読む欄を突き合わせた。ハーネスも隣へ移ったときに名前を控えていなかった（H51）。
resolution:
  perspective: [carry_through]
  note: >-
    app.js が部品の文脈を開くとき部品名（instance.component.label・40 字）を openContextElement に控え、getScreenContext が selection.element_label で送る（SA1 = 参照と名前だけ）。routes/learning._is_screen_fact_question は要素を選んでいて「この部品」等を指し、説明・書いた・先生・承認・検証などを問う発話を RAG へ回す（pre-route と分類の USAGE_HELP の両方に効く）。分類のプロンプトに「画面に出ている要素の事実（説明の有無・誰が書いたか・承認や検証の記録）を問う質問も DOMAIN_RAG」を 1 文。SA層の要素の解決器は、説明が無い事実と、説明を書いた教員の名前（学習者の画面に出ている本人登録の名前）を事実文に足した。HELP の無関係な節を無ヒット扱いにする案は入れていない。
  landed_in:
    - "frontend/public/js/app.js"
    - "frontend/public/index.html"
    - "backend/api/routes/learning.py"
    - "backend/core/assistant_context/resolvers/learning.py"
    - "backend/tests/test_share_round25_r8_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "ブラウザで部品を開いて問い、分類のプロンプトに画面の 1 行が載るか（第 9 周 st-07 × (d)）"
related: [IK-1043, IK-1005, IK-0936]
view_of: []
history: []
---

## 課題

症状: 部品の説明の出所を尋ねた学生に、受講登録の手順が返った。直前の画面は「この部品には、承認済みの説明がまだありません。」と言っていたが、その事実は答えに入らない。

原因: 分類器に「この部品」が何かが渡らない（送り手の画面が名前を送らない）。説明の帰属の問いに行き先が無い。

## 発見の観点

経路の追跡: app.js の screen_context → _intent_screen_hint → 分類のプロンプト。再現: 部品を開いたまま問う。

## 解決の観点

送り手の画面に部品名を送らせ、画面の事実を問う発話を RAG へ回した。 元の見立て: 送り手の画面を受け手の読む欄に合わせる。分類の語彙に画面の事実の意味を問う行き先を足す。

## 一般化

受け手の欄を足したら送り手の画面がそれを埋めるかを通しで確かめる。型 available-but-unwired。確かめ方は、ブラウザの画面で部品を開いて問い、分類のプロンプトに画面の 1 行が載るかを見ること。

## 第 9 周の再演（第 25 周・面 A 第 9 周、2026-10-31・judge_share_r9）

未評価。st-07 の (d) は ⚓ が開かない t11 で組まれ（harness H55）、選択質問の枠が 2 つで 3 問目「この部品の説明は誰が書いたか」は送られなかった。2 問目「先生が書いた説明と論文の書き方で違うところ」は DOMAIN_RAG に回り（USAGE_HELP ではない）、教材本文を「先生の説明」として比べた。run: uxsim/runs/c-astro-r25-share-r9/20261031T015336Z-38765（seq10・11）。

## 第 10 周の再演（第 25 周・面 A 第 10 周、2026-10-31・judge_share_r10）

3 問目が初めて送られた。「この部品の説明は誰が書いたの？」は DOMAIN_RAG に回り（USAGE_HELP ではない）、「書き手・承認の記録は材料に入っていません…推測で言うことはしません」と答え、「⚓ を開いたまま同じ質問を」と案内した。ただしこのトピック（t11）の ⚓ は置き換わりの 1 行しか出さず案内は行き止まり（IK-1051）。⚓ を開いた経路の評価は未了。run: uxsim/runs/c-astro-r25-share-r10/20261031T054227Z-72202（seq13）。

## 第 11 周の再演（第 25 周・面 A 第 11 周、2026-10-31・judge_share_r11）

分類は OK（DOMAIN_RAG）・「⚓ を開いたまま」は未再現。st-05 は ⚓ を開いた（seq7〜9）後に「この部品の説明は、誰が書いたもの？」と尋ね、「分かりません…⚓ を開いて出所の表示を見るのが確実」と答えた。runner は開いている ⚓ の element_id を学習チャットの画面文脈に送らない（app.js getScreenContext は送る — harness H67）ので、直前の「承認済みの説明がまだありません」が AI に届かない形は製品の欠陥と数えない（uxsim/runs/c-astro-r25-share-r11/20261031T102623Z-4737 seq13）。
