---
id: IK-1059
title: "学習者向けの「台帳」「記帳」の読み替え（IK-1018 の残り・第 7 波）が分野の地図のルートにだけ掛かり、学習チャットの画面文脈には地図パネルの元の行「検証: 台帳に記帳なし」が「その場所の詳細パネルに出ている行」として AI に渡り、出典タブの一行・検証の記録がない前提の一覧・わたしの地図の凡例にも「記帳」が残る"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-31
sources:
  - "docs/features/assistant_screen_adapter_design.md"
feature_context:
  realizing: "学生が地図・出典・わたしの地図・AI の答えのどこでも、検証の有無を同じ学習者の語で読む"
  layers: [screen_adapter_sa, field_atlas_s, doubt_d, personal_network]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [contract]
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
    確認（コード）: label_vocab.learner_atlas_wording（ATLAS_LEARNER_WORDING）は routes/atlas_view.py の学習者向けルートが読み時に通すだけ。
    学習チャットの routes/learning.py::_screen_atlas_node_sources は atlas_state.fetch_overlay_rows の verify_line をそのまま入れ、
    resolvers/learning.py::_atlas_node_panel_facts は LEARNING_ATLAS_PANEL_LINE_TEMPLATE「その場所の詳細パネルに出ている行: {line}」で渡す
    （endorse_line だけ learner_endorse_line を通る）。st-05・st-07 の画面のパネルは「検証: 検証の記録なし。…」（GONE した IK-1018 の形）なのに、
    製品頭脳 A の 4274/4276/4277/4278 は画面文脈に「検証: 台帳に記帳なし」を読んだ。学習者の出典タブの一行「この内容の検証スコープはまだ記帳されていません。」、
    出典タブの前提一覧の「（検証スコープの記帳なし）」、わたしの地図の凡例「枠線の実線 = 検証の記帳がある」も読み替えの外（st-05 seq9・11・16）。
    構造軸 responsibility = 読み替えが 1 つの面の責務として置かれ全学習者面の正本が無い。接続軸 contract = 画面文脈が「パネルに出ている行」と言いながら
    パネルと違う文を渡す。
generalization:
  level: general
  general_form: "利用者向けの語の読み替えを一つの画面の出口にだけ掛けると、同じ元の文を別の経路（AI への画面文脈・別の画面）が素のまま運び、同じ事実が場所ごとに違う語で出る"
pattern: rule-enforced-per-surface
discovery:
  perspective: [trace_walk, symptom_report]
  note: "st-05 の頭脳が出典タブ・前提一覧・わたしの地図の「記帳」を指摘し、製品頭脳 A が画面文脈の「台帳に記帳なし」を指摘した。パネルの投影（runner）と、画面文脈の材料を組む route・解決器の関数を突き合わせた。run: uxsim/runs/c-r25-doubt-r8/20261027T224211Z-98239（seq9・11・13・14・16・17）/ uxsim/runs/c-r25-doubt-r8/20261027T224734Z-1475（seq8・9）。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    学習者の語の正本を label_vocab.learner_atlas_wording（ATLAS_LEARNER_WORDING）1 つにし、地図ルート以外の出口も通した:
    ①routes/learning.py::_screen_atlas_node_sources は verify_line / endorse_line（learner_endorse_line の後）を読み替えてから
    画面文脈へ渡す（AI の材料 = パネルの文）②学習者向け台帳の一行 learner_ledger_line / _learner_unrecorded_line は
    _learner_wording で fact_line・support_fact_line・evidence_lines_fact・target_note・verification_status_label を読み替える
    （SA層 kind verification も同じ投影を通る）③app.js の前提一覧・スコープ併記と personal-map-home.js の凡例の固定文を
    「記録」に。教員の記帳の中身（スコープ本文）は書き換えない。atlas 波の learner_endorse_line（旧い承認行の置換）とは
    順に掛かるだけで衝突しない。
    第 9 波（再開分）: ④resolvers/learning.py::_atlas_node_facts の札の説明（ATLAS_PILL_NOTES）を learner_atlas_wording に通す
    ⑤personal_graph/nearby.py::_verification の札と条件の行を同じ表に通し、条件の頭 LEARNER_SCOPE_LINE_PREFIX を「記録された条件: 」・
    LEARNER_SCOPE_LINE_MORE を「…記録があります」に（学習者専用の文）⑥画面文脈の固定文 LEARNING_PERSONAL_MAP_SCREEN_FACT を「記録」に。
    教員の台帳画面の VERIFICATION_STATUS_LABELS_LEDGER は変えない（宛先差）。
  landed_in:
    - "backend/api/routes/learning.py"
    - "backend/api/routes/doubt.py"
    - "frontend/public/js/app.js"
    - "frontend/public/js/personal-map-home.js"
    - "backend/tests/test_doubt_round25_wave8.py"
    - "backend/core/assistant_context/resolvers/learning.py"
    - "backend/core/assistant_context/schema.py"
    - "backend/core/personal_graph/nearby.py"
    - "backend/core/doubt/schema.py"
    - "backend/tests/test_doubt_round25_wave9.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "面ごとに語を直すのをやめ、地図と同じ表を学習者向け台帳の投影 1 点と画面文脈の材料 1 点に通した。固有の差分: JS の固定文 3 つは素通しの元が無いので逐語で直した。第 9 波: 残った 2 出口（札の説明の AI 材料・わたしの地図の札と条件）も同じ表へ通し、LEARNING_* 固定文に語が無いことをガードレールで固定。"
  verification:
    methods: [guardrail]
    unverified:
      - "第 10 周 st-08 で英語の回答に ledger が出ないか（材料から「台帳」は消えた）を再演していない"
      - "学習者 DTO 全体の再帰走査ガードレールは無い（対象の欄と LEARNING_* 固定文を検査）"
related: [IK-1018, IK-1031, IK-1049, IK-0907]
view_of: []
history: []
---

## 課題

症状: 第 7 波で地図のパネルは「検証の記録なし」と言うようになったが、同じ場所を開いて尋ねると AI には「詳細パネルに出ている行: 検証: 台帳に記帳なし」が渡る
（AI は言い換えて答えたが、材料と画面が食い違う）。出典タブの各カードの一行・「このコースの教材で、検証の記録がない前提を見る」の各行・わたしの地図の凡例は
「記帳」のまま。学生は「間接的な支持あり」（わたしの地図）と「まだ記帳されていません」（出典）を同じ問いの別の答えとして読み、混乱した。

## 発見の観点

同じ事実の文を、画面・AI の材料・別の画面の 3 経路で並べた。

## 解決の観点

未解決（上の resolution.note）。

## 一般化

型 rule-enforced-per-surface。語の読み替えは「どの面に掛けたか」ではなく「学習者に出る全経路のどこを通るか」で置き場所を決める。

第 8 波の是正（2026-10-31）: 学習者の語を 1 つの表に寄せて画面文脈・台帳の一行・JS 固定文に通した（上の resolution）。

第 9 周の再演（2026-10-31・固定コース 1bad7d9f＝前周と別のコース。語の比較は同じ一覧・同じ固定文なのでコースに依らない）: **部分 GONE・再開**。
GONE: 出典タブの各カード「この内容の検証スコープはまだ記録されていません。」・前提一覧「（検証スコープの記録なし）」・わたしの地図の凡例「枠線の実線 = 検証の記録がある」・
地図の検証 / 承認の行（AI の材料も「検証: 直接確かめた記録なし。」）。STILL（2 つの出口が表の外）:
①画面文脈の「その場所の札の説明として地図に出ている文」— `resolvers/learning.py::_atlas_node_facts` が `label_vocab.ATLAS_PILL_NOTES` を読み替えずに渡し、
st-08 の要求（mailbox req-04293）に「…直接確かめた記録は台帳にありません。」が入った。画面のパネルは「…記録はありません」。AI は英語で「the ledger has no record」と答えた。
②わたしの地図の札と条件の行 — `personal_graph` が `VERIFICATION_STATUS_LABELS_LEDGER`（「直接検証の記帳あり」）と `LEARNER_SCOPE_LINE_PREFIX`（「記帳された条件: 」）を
そのまま返す（凡例だけ JS で直したので、同じ区画で凡例は「記録」、札は「記帳」）。run: uxsim/runs/c-r25-doubt-r9/20261031T015856Z-41602（seq8・9）/ 20261031T015310Z-38509（seq9・11・16）。

第 8 波の検証の記録（再開に伴い本文へ移した）: 方法 guardrail。確かめていない範囲 = 学習者 DTO と画面文脈ブロック全体の走査ガードレールなし（対象の欄だけを検査）/ 第 9 周で AI の材料とパネルの語が揃うか — 第 9 周で札の説明の出口が残ることが分かった。

第 9 波の是正（2026-10-31）: 残った 2 出口と画面文脈の固定文を同じ表に通した（上の resolution ④〜⑥）。

## 第 10 周の再演（第 25 周・面 B 第 10 周、2026-10-31・judge_doubt_r10 — 第 9 周と同じコース・同じ項目・同じ英語の問い）

**GONE（第 9 周の 2 出口）**。①画面文脈の札の説明: 「この概念を前提として使う議論がありますが、その前提自体を直接確かめた記録はありません。」（mailbox req-04307 / 04312 — 第 9 周は「…台帳にありません」）。st-08 の英語の回答 2 回とも「there is no record that the premise itself has been checked directly」で ledger の語は出ない（第 9 周は「the ledger has no record」）。②わたしの地図: 札「直接検証の記録あり」・条件「記録された条件: 成立条件「状態方程式が単調に増える範囲で」」・凡例「検証の記録」で揃った（第 9 周は札が「記帳あり」「記帳された条件」）。気付き（軽・再開しない）: 学習者の出典タブの末尾「この内容の検証スコープはまだ記録されていません。」と前提一覧の行「（検証スコープの記録なし）」は、見出し「検証の記録がない前提」と別の語（「検証スコープ」は教員の台帳の語）で、st-05 は「『検証スコープ』って何？」と書いた — 第 8 波で採った学習者の語（「記録」）の範囲内だが、「スコープ」を学習者の語に残すかは語の表で決め直す候補（app.js の前提一覧は表を通さず JS に直書き）。run: uxsim/runs/c-r25-doubt-r10/20261031T054204Z-72077（seq9・10）/ uxsim/runs/c-r25-doubt-r10/20261031T053655Z-69809（seq9・11・16）。
