---
id: IK-1018
title: "学習者の分野の地図で「暗黙の前提」の概念を選ぶと、右のパネルの承認の行が「承認記録なし — 台帳のスコープ欄は空欄」と内部の欄名で言い、学生には何のことか分からない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/field_atlas_overlay_spec.md"
  - "docs/features/doubt_layer_issues.md"
feature_context:
  realizing: "学生が分野の地図で概念を選び、その概念が確かめられているか・承認されているかを右のパネルで読む"
  layers: [field_atlas_s, doubt_d, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [meaning]
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
    処理軸 wording: core/atlas_state.py が承認の行に固定文「承認: 承認記録なし — 台帳のスコープ欄は空欄」を返す（atlas-fixture.js にも同文）。
    接続軸 meaning: 「台帳」「スコープ欄」は D層（epistemic_ledger.verification_scopes）の内部の欄名で、学習者向けの語彙表（label_vocab）を通っていない。
    しかも承認（C層）の行に検証（D層）の欄の状態を書いており、行の主語と欄が食い違う。同じパネルの検証の行「記帳された直接検証なし。」は読める。
    構造・統制軸 none。
generalization:
  level: instance
  general_form: "学習者向けの説明行に、内部の記録の欄名をそのまま書き、行の主語と別の層の欄の状態を述べる"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: "素粒子から来た学生 st-03 が暗黒エネルギーを選び、『「台帳のスコープ欄は空欄」という言い方は何のことか分からない』と記した（審判 C が拾った）。文の出所を atlas_state.py で確かめた。run: uxsim/runs/c-astro-r25-atlas-r6/20261027T123201Z-9456 seq 8。"
resolution:
  perspective: [vocabulary_table, single_point_fix]
  note: >-
    「暗黙の前提」で承認の記録が無いときの承認の行を label_vocab.ATLAS_ENDORSE_NONE_ASSUMED_LINE「承認: このコーパスの中では、教員の承認の記録が
    ありません」（SL1 の閉世界）にし、承認の行から D層の欄の状態を外した（検証の範囲は検証の行が言う）。地図の状態キャッシュに残る旧文は
    atlas_state.learner_endorse_line が読み時に置き換える（GET /api/atlas と /node の 2 経路・キャッシュは書き換えない）。atlas-fixture.js も同文。
    学習者のパネルの文に出さない欄名は label_vocab.ATLAS_LEARNER_PANEL_DENYLIST（ガードレール）。help_kb の student denylist には足していない
    （マニュアルの検査とは別の面のため）。
    第 7 波（2026-10-28）残り: 学習者の地図の札・検証の行・札の説明・承認の行から「台帳」「記帳」を読み替える（label_vocab.ATLAS_LEARNER_WORDING / learner_atlas_wording を routes/atlas_view.py _apply_learner_wording が文のキーにだけ掛ける。正本の文・キャッシュは書き換えない）。atlas-panel.js の既定文・atlas-overlay.js の案内も学習者の語に。
  landed_in:
    - "backend/core/atlas_state.py"
    - "backend/core/label_vocab.py"
    - "backend/api/routes/atlas_view.py"
    - "frontend/public/js/atlas-fixture.js"
    - "backend/tests/test_atlas_review_round25_r6.py"
    - "backend/tests/core/test_atlas_state.py"
    - "frontend/public/js/atlas-panel.js"
    - "frontend/public/js/atlas-overlay.js"
    - "backend/tests/test_atlas_review_round25_r7.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の画面で暗黒エネルギーを選んだときの表示（次周の再演）"
related: [IK-0964, IK-0942]
view_of: []
history: []
---

## 課題

症状: 地図で「暗黙の前提」の概念（暗黒エネルギー・暗黒物質など）を選ぶと、右のパネルに「承認: 承認記録なし — 台帳のスコープ欄は空欄」と出る。

原因: 承認の行の固定文に D層の内部の欄名（台帳・スコープ欄）が入ったまま学習者向けに出ている。行の主語（承認）と述べている欄（検証の範囲）も別の層。

## 発見の観点

通し受講（reproduction）。学生の反応を製品の文の出所と突き合わせた。

## 解決の観点

第 6 波（2026-10-27）で解決。承認の行は承認だけを閉世界の文で言い、旧文は読み時に置き換える。

## 一般化

型 wording-mismatch。学習者向けの固定文は語彙表を通し、内部の欄名を denylist で検査する（help_kb の student denylist と同じ作法）。

面 B 第 6 周でも観察（2026-10-27）: st-06 が「暗黒エネルギー」を選ぶと承認の行が「承認記録なし — 台帳のスコープ欄は空欄」で、検証の行「記帳された直接検証なし。」と言い方が分かれ、学生は「スコープ欄が空欄というのがなぜ承認の行に付いているのか分からない」と記した。st-05 の「中性子星」は「検証: 台帳に記帳なし。分野の地図（教員レビュー済）の初期表示。」で『台帳』『初期表示』が未説明。run: uxsim/runs/c-r25-doubt-r6/20261027T123621Z-11841（seq8）/ uxsim/runs/c-r25-doubt-r6/20261027T123134Z-9179（seq13）。

## 第 7 周の再演（第 25 周・面 C 第 7 周、2026-10-28・campaign c-astro-r25-atlas-r7）

GONE。暗黒物質（暗黙の前提）の右のパネルの承認の行が「承認: このコーパスの中では、教員の承認の記録がありません」になった（内部の欄名が消えた）。残り（軽・起票しない）: 検証の行「記帳された直接検証なし。」と L1 の「暗黙の前提」の説明「…直接確かめた記録は台帳にありません」の『台帳』『記帳』は学生に初出の語のまま。パネルにその概念の関係（今回の版で加わった依存）が出ない。run: uxsim/runs/c-astro-r25-atlas-r7/20261027T172454Z-57525 seq 5 / 8。

## 第 7 波の是正（2026-10-28）

第 7 波（2026-10-28）残り: 学習者の地図の札・検証の行・札の説明・承認の行から「台帳」「記帳」を読み替える（label_vocab.ATLAS_LEARNER_WORDING / learner_atlas_wording を routes/atlas_view.py _apply_learner_wording が文のキーにだけ掛ける。正本の文・キャッシュは書き換えない）。atlas-panel.js の既定文・atlas-overlay.js の案内も学習者の語に。

## 第 8 周の再演（2026-10-28・judge_atlas_r8）

**GONE（地図の面）／ STILL（画面文脈の経路）**。学生 2 人の地図 L1・右のパネルに「台帳」「記帳」の語は出なかった（検証「直接確かめた記録なし。」「検証の記録なし。」）。残り（軽）: 検証の行の「分野の地図（教員レビュー済）の初期表示。」は学生に意味が取りにくい。承認の行の言い方が状態で揃わない件は NEW IK-1056。
経路の残り（読んで確かめた）: 学習チャットの画面文脈（SA層）の地図の場所の行は `routes/learning.py::_screen_atlas_node_sources` が状態キャッシュの `verify_line` を素のまま渡し（`endorse_line` だけ `learner_endorse_line` を通す）、`label_vocab.learner_atlas_wording` を通らない。同じ周の面 B のレーンで製品頭脳 A（4274・4276・4278）/ B（4277）が「検証: 台帳に記帳なし」が画面文脈に届くと記した。是正は地図の DTO と同じ読み替えを画面文脈の 2 行にも掛けること（面 B の審判はこのエントリを参照し、別エントリにしない）。

第 8 周（面 B）の観察（2026-10-28）: 学習者の地図のパネルは「検証: 検証の記録なし。…」・札の説明も「直接確かめた記録はありません」（第 7 周は「台帳に記帳なし」）— パネルの読み替えは GONE。同じ行が学習チャットの画面文脈には読み替え前で渡り、出典タブ・前提一覧・わたしの地図の凡例に「記帳」が残る件は IK-1059。run: uxsim/runs/c-r25-doubt-r8/20261027T224734Z-1475（seq8）。
