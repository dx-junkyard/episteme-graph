---
id: IK-0976
title: "講義のスライドの自動ページ分割は段落を文字数の目安で貪欲に詰めるだけなので、見出しだけがスライドの末尾に残り本文が次のスライドへ泳ぐ（「## この章の見通し」「## TOV ネットワーク」）・式を導く文と式が別のスライドに分かれる・短いスライドと長いスライドが偏る"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/lecture_slide_sync_design.md"
feature_context:
  realizing: "長いトピック教材を、学生が 1 枚ずつ読める講義のスライドに自動で分ける"
  layers: [lecture_player, lecture_studio]
classification:
  axes:
    processing: [logic]
    structure: [decomposition]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 logic: backend/core/lecture.py::_group_units_by_size（745 行〜）は段落の表示長の和が目安を超えたところで切るだけで、
    切れ目の直前の段落が見出し行（# で始まる 1 行）か、直後が式だけの段落かを見ない。_paragraph_units（728 行〜）は空行で段落を
    分けるので、見出しは単独の段落になり、どのページの末尾にも来うる。構造軸 decomposition: 分割の単位が「空行で区切った塊」で、
    見出しとその本文・導入文と式という読みの単位を表していない。接続軸 none（境目は表示・音声・readiness で一致して届く）。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "長さの目安だけで連続した文書を区切り、見出しと本文・導入と本体のような離してはいけない組を区切りの両側に分ける"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction]
  note: >-
    学生 st-10 が固定コース 6be229e1 の t0 の講義で、スライド 1 の末尾が見出し「## 何で調べるのか」と導入 1 文、スライド 3 の末尾が
    見出し「## この章の見通し」だけになると記し、st-06 が t11 で「## TOV ネットワーク」「## TOV 残差による制約」が見出しだけ残り、
    「…損失にします。」と式 $L_{\mathrm{TOV}}$ が別のスライドに分かれ、次のスライドが式 + 段落 2 つ + ⚓ 2 つで長いと記した。
    run: uxsim/runs/c-astro-r25-studio-r4/20261024T234030Z-30759（seq8〜11）/ uxsim/runs/c-astro-r25-studio-r4/20261024T234308Z-52347（seq8〜12）。
resolution:
  perspective: [single_point_fix, representation_change]
  note: "core/lecture.py::_group_units_by_size が段落ではなく _reading_blocks（見出しだけの段落は次と・式だけの段落は直前と結ぶ）の読みの単位で貪欲に詰める。結んだ単位が目安の 2 倍を超えるときだけ単位の中を段落で切る。境目は段落境界のままなので表示と読み上げの index 対応は崩さない。表示・音声・readiness の 3 者が同じ関数を通るので一致は保たれ、境目が動いたトピックの音声は既存の保存・再生成の規則で作り直す。"
  landed_in:
    - "backend/core/lecture.py"
    - "backend/tests/test_lecture_studio_round25_r4.py"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
    - "backend/tests/test_lecture_studio_round25_r6.py"
    - "backend/tests/test_lecture_studio_round25_r7.py"
    - "backend/tests/test_lecture_studio_round25_r8.py"
  verification:
    methods: [guardrail]
    unverified:
      - "固定コースの t0 / t11 / t16 の講義の末尾に見出しだけが残らないかは実機で見ていない（第 5 周 st-09）"
      - "第 7 波の 3 形（2 文の導入と本体・式と直後の解説 2 文・箇条書きと直後の解説）が t0 / t1 / t11 の講義で結ばれるかは実機で見ていない（第 8 周 st-01 / st-07）"
      - "既存の音声キャッシュの境目は保存・再生成まで旧いまま（作り直しの告知は既存の規則）"
      - "第 8 波の 2 形（文中の式を含む 2 文の解説・⚓ の埋め込みだけの段落）が t0 / t11 の講義で結ばれるかは実機で見ていない（第 9 周 st-01 / st-08）"
related: [IK-0975, IK-0878]
view_of: []
history: []
---

## 課題

症状: 講義のスライドが見出しだけで終わり、その見出しの本文は次のスライドに出る。式を導く文と式が別のスライドに分かれる。
スライドの長さが偏る（数式の多いトピックで目立つ）。

原因: 自動ページ分割が段落の長さの和だけで区切り、見出しと本文・導入と式の組を見ない。

## 発見の観点

学生 2 人の通し受講（reproduction）で、講義を 1 枚ずつ送ったときに記された。

## 解決の観点

第 25 周 第 4 波で是正（2026-10-25）。core/lecture.py::_group_units_by_size が段落ではなく _reading_blocks（見出しだけの段落は次と・式だけの段落は直前と結ぶ）の読みの単位で貪欲に詰める。

## 一般化

長さの代理指標で区切るとき、区切りの両側に分けてはいけない組を単位として先に結ぶ。確かめ方: 見出しの多いトピック・式の多いトピックの
スライドの末尾が見出しだけにならないこと、式の直前の文と式が同じスライドにあることを全トピックで走査する。

第 5 周の再演（2026-10-27）: **GONE**（自動ページ分割の経路）。固定コース 6be229e1 の講義 88 枚（全 20 トピック）に、見出しで終わるスライドと式だけで始まるスライドは無かった（読み取りの API で走査）。st-09 の t17（5 枚）・t18（4 枚）の 1 枚目は見出し + 段落。教員が手で入れた区切り（te-01 の t16・見出しの直後）は対象外で、見出しだけの区画を作れるが警告は無い — 教員の明示の分割なので記帳しない。run: uxsim/runs/c-astro-r25-studio-r5/20261027T072111Z-9659 seq12・14。

第 6 周の再演（2026-10-27）: **一部 STILL**。見出しだけがスライドの末尾に残る形は無い（講義の全トピックを読み取りで確認）。残る形は 2 つ: ①見出し + 導入 1 文でスライドが終わり、その節の本体（箇条書き・表）が次のスライドへ行く（t0 の 1 枚目「## 何で調べるのか … 組み合わせます。」→ 2 枚目は箇条書き 2 行だけ・t1 の 1 枚目「## 2. 向きはランダムではない」+ 1 文 → 表は次の枚 — 起票時の t0 の症状と同じ）。②式で終わり、その式の解説 1 文だけのスライドができる（t11 の 2 枚目が式 N_TOV で終わり 3 枚目が「大事なのは…微分できることです。」1 文）。_reading_blocks は「見出しは次と・式は直前と」結ぶが、導入文（「…組み合わせます。」）と本体、式と直後の解説を結ばない。run: uxsim/runs/c-astro-r25-studio-r6/20261027T123457Z-10833 seq8〜10・15、uxsim/runs/c-astro-r25-studio-r6/20261027T123926Z-13979 seq9・10。

第 6 波で残り 2 形を是正（2026-10-27）: 第 6 周（judge_studio_r6 §2）で「見出し + 導入 1 文で切れて本体（箇条書き・表）が次の枚」（t0・t1）と「式の直後の解説 1 文だけの枚」（t11）が残った。`_reading_blocks` は見出しの直後の導入 1 文（1 文・160 字以内の散文）と「：」で終わる導入の段落を次の本体と（次の見出しは引かない）、式だけの段落の直後の解説 1 文を式と結ぶ。結んだ単位が目安の 2 倍を超えれば従来どおり段落で切る。境目は段落の境界のまま（表示・音声・readiness の一致は不変）。テスト `test_lecture_studio_round25_r6.py::TestReadingBlocks`。

第 7 周の再演（2026-10-28）: **一部 STILL（第 6 波の規則が 1 文に限られる）**。t0 の「見出し + 導入 1 文」形は消えた（1 枚目が節の終わりで切れ、2 枚目が見出し + 導入 + 箇条書き）。残る形: ①t1 の 1 枚目が「## 2. 向きはランダムではない」+ 導入 **2 文**で終わり、表は次の枚 ②t11 の 2 枚目が式で終わり、3 枚目が式の直後の解説 **2 文**だけ／4 枚目が式で終わり、5 枚目が解説 2 文で始まる ③t0 の 3 枚目が箇条書きの直後の解説（「ここで「J=3–2」は…」）で始まる。`core/lecture.py::_is_single_sentence_unit`（`len(_sentence_units(text)) <= 1`・160 字以内）が 2 文の導入・解説を結ばない。読み取りの学生 API（講義 sequence）で確かめた。run: uxsim/runs/c-astro-r25-studio-r7/20261027T172130Z-55411 seq10・17、uxsim/runs/c-astro-r25-studio-r7/20261027T173357Z-61213 seq9〜12。

第 7 波で残り 3 形を是正（2026-10-28）: 第 7 周（judge_studio_r7 §2）の残り「見出し + 導入 2 文で切れて表が次の枚」（t1）・「式の直後の解説 2 文だけの枚」（t11）・「箇条書きの直後の解説で枚が始まる」（t0）。`_is_single_sentence_unit`（1 文・160 字）を `_is_short_lead_unit`（2 文まで・240 字）にし、箇条書きの段落の直後の短い解説（2 文まで）も箇条書きと結ぶ（`_reading_blocks`）。結んだ単位が目安の 2 倍を超えれば従来どおり段落で切る。境目は段落の境界のまま。テスト `test_lecture_studio_round25_r7.py::TestReadingBlocksTwoSentences`。

第 8 周の再演（2026-10-28）: **形を変えて STILL**。t1 の「見出し + 導入 2 文 → 表は次の枚」は消えた（読み取りの計算で 2 枚目 = 見出し + 導入 + 表 + 解説。st-01 は t1 の 1 枚目まで）。残る 3 か所はどれも「2 文まで・240 字」の判定が**本文中の小さな式の置き換えを 60 字に数える**ために外れた: ①t11 2 枚目が式 N_TOV で終わり 3 枚目が解説 2 文だけ（解説は 2 文だが `$r$`・`$dm/dr$` 等 4 つの [[FORMULA_N]] で `_display_length` = 312 > 240）②t0 3 枚目が箇条書きの直後の解説「ここで「J=3–2」は…」で始まる（2 文・式 4 つで 321 > 240）。`_display_length` は表示式の見積り（1 個 60 字）を、文中の記号にもそのまま当てている（threshold-reused-across-regimes 型の別の現れ）。③**⚓ の埋め込みだけの段落**（`![[component:…]]`・`![[claim:…]]`）が読みの単位として扱われず、直前の段落と結ばれないため、t11 の ⚓ 2 つが「TOV 残差による制約」の枚から外れて次の枚（5 / 6）の先頭に回る（教材では節の末尾）。是正案: 短い導入・解説の判定は文中の式を素の文字数（または小さい換算）で数え、埋め込みだけの段落は式だけの段落と同じく直前の段落と結ぶ。run: uxsim/runs/c-astro-r25-studio-r8/20261027T224552Z-353 seq9〜11、uxsim/runs/c-astro-r25-studio-r8/20261027T224929Z-2551 seq9・10・12。審判の計算は砂場 DB の HEAD（= 版 2・版 3 と同一）を `build_topic_slides` にかけた読み取り。

第 8 波で残り 2 形を是正（2026-10-31）: ①`core/lecture.py::formula_length_map` が文中の小さな式（`is_display` が偽の `[[FORMULA_N]]`）を式の実文字数で数え、`_display_length` / `_is_short_lead_unit` / `_reading_blocks` / `_group_units_by_size` / `_balance_units_into_n` に渡す（表示式・`is_display` 不明は従来どおり 60 字）。正本は `auto_paginate_slides` の 1 箇所で、表示・音声・readiness・プレビューが同じ値を通る。②⚓ の埋め込みだけの段落（`![[component:…]]` / `![[claim:…]]` / `![[source:…]]`）は `_is_anchor_embed_only_unit` で式だけの段落と同じく直前の段落と結ぶ。`===` の明示分割の優先は不変。文中の式の多いトピックは枚数が減ることがある。テスト `test_lecture_studio_round25_r8.py::TestInlineFormulaLength`（r4 の preview テストは表示式の fixture に変更）。

第 9 周の再演（2026-10-31・judge_studio_r9）: **GONE**（残り 2 形とも）。t0 は 3 枚（1 = 問い + なぜ Cep B / 2 = 何で調べるか（箇条書き + 「ここで『J=3–2』は…」の解説 2 文）+ 2 つがどうつながるか / 3 = 見通し + 次へ + 図）で、箇条書きと文中の式を含む解説が同じ枚。t11 は 3 枚で、TOV ネットワークの式と直後の解説が 1 枚目、⚓ 2 つが「TOV 残差による制約」の節と同じ 2 枚目に残る（第 8 周の 6 枚 → 3 枚）。t1 は 2 枚（表と解説が同じ枚・GONE 継続）。学生の講義（st-01 の t0 3 / 3・t1 2 / 2・t2 2 / 2、st-08 の t11 3 / 3）と審判の読み取りの計算（版 5 の写しを `build_topic_slides` にかけた — t0 3・t1 2・t2 2・t11 3・t16 2・t17 3）が一致。2 つ目のトピックでも ▶ が選べた（H38 GONE）。run: uxsim/runs/c-astro-r25-studio-r9/20261031T015559Z-39886 seq8〜11・14〜16・18〜19、uxsim/runs/c-astro-r25-studio-r9/20261031T020200Z-43015 seq8〜11。
