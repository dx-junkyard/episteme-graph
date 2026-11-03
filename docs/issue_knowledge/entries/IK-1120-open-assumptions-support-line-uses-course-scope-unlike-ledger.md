---
id: IK-1120
title: "未検証合意リストの行の「観測からの支持線」がコースの教材全体（4 本）を範囲に計算されるため、同じ主張の台帳（対象の論文 1 本が範囲）が「単一の支持線…『診断・応用』が同時に崩れると」と言う主張を、リストの行は「複数の支持線」と言う（同じ欄名で範囲が違う）。同じ行の「到達可能性: 記録なし」も覆る条件の到達可能性で、台帳の検証提案の「到達可能性: 次世代の装置・観測なら可能」とは別の量"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/features/stakes_ledger_design.md"
  - "docs/architecture/persona_enactment_testing_design.md"
feature_context:
  realizing: "教員が未検証合意リストの 1 行と、その行から開いた台帳を見比べて、主張がどれだけ観測に支えられ、確かめに行けるかを判断する"
  layers: [stakes_ledger_sl, doubt_d, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target, meaning]
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
    確認（コード + 読み取り）: 台帳 GET（routes/doubt.py の台帳組み立て・SL-3）は compute_support_lines(document_ids=[対象の document]) で、
    コメントに「範囲は対象の document（IK-0852）」とある。未検証合意リスト（GET .../open-assumptions → core/doubt/open_assumptions.py
    compile_open_assumptions）は build_support_context(document_ids=scope_ids) をコースの範囲（_course_scope_document_ids）で 1 度作り
    compute_support_lines_from_context で各行を引く。砂場（episteme-uxsim api コンテナ・読み取りのみ）で主張 42c4e2e8（Cepheus TeX）を
    両方の範囲で計算: document 1 本 = single「この対象は単一の支持線に立っています。『診断・応用』が同時に崩れると…」/ コース 1bad7d9f の
    sources 4 本 = several「この対象は複数の独立した支持線に支えられています。」。別の論文の観測が Cepheus の主張の支持線に数えられている。
    到達可能性: リストの reachability_summary は覆る条件（falsification_conditions）の到達可能性の要約で、検証提案の reachability は読まない。
    台帳の提案カードは「到達可能性: 次世代の装置・観測なら可能」、リストの同じ行は「到達可能性: 記録なし」。
    接続軸 target = 同じ欄の計算対象（document の範囲）が経路でずれる。接続軸 meaning = 同じ語「到達可能性」が別の量を指す。
    構造軸 none = 範囲を渡す引数は既にある（渡す値が違うだけ）。処理軸 none = 各計算は自分の範囲では正しい。
generalization:
  level: repo_pattern
  general_form: "同じ欄名の量を一覧と詳細で別々の範囲・別の正本から計算すると、一覧の行から開いた詳細が反対のことを言い、どちらを信じるか利用者が判断できない"
pattern: scope-widened-silently
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    te-03 が提案の完了を記録した後に前提の地図を再読込し（seq14）、合意リストの同じ主張の行（「…観測からの支持線: 複数の支持線」「到達可能性: 記録なし」）を
    直前の台帳（seq6・10「単一の支持線」「到達可能性: 次世代の装置・観測なら可能」）と見比べて気付いた。審判が砂場で範囲を変えて 1 本ずつ計算した。
    run: uxsim/runs/c-r25-doubt-r10/20261031T053250Z-67645（seq6・10・14）/ uxsim/runs/c-r25-doubt-r10/20261031T052922Z-65583（seq3・11 の 7 行目）。
resolution:
  perspective: [canonical_source, vocabulary_table]
  note: >-
    第 25 周 第 10 波: compile_open_assumptions は支持線の共有文脈を行の対象の document ごとに 1 度だけ作る（_support_ctx_for。
    行の document が引けない旧行だけコースの範囲に戻す）。範囲の正本は台帳と同じ「対象の document」（IK-0852）で、一覧と詳細が
    同じ主張で同じ段階を返す。欄名は label_vocab.LEDGER_FIELD_LABELS に falsification_reachability「覆る条件の到達可能性」と
    proposal_reachability「検証提案の到達可能性」を足し、合意リストの行は前者・台帳の提案カードは後者で言い分ける。一覧の上に
    範囲の事実文 OPEN_ASSUMPTIONS_SUPPORT_SCOPE_NOTE（support_scope_note）。数値は増やしていない。
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: "範囲は呼び出し側の都合（一覧の一括計算）で広げず、行の document を必ず渡す形にした。固有の差分: 範囲そのものは既に必須引数で、渡す値（コース全体 → 行の論文）を揃えた。"
  landed_in:
    - "backend/core/doubt/open_assumptions.py"
    - "backend/core/label_vocab.py"
    - "backend/api/routes/doubt.py"
    - "frontend/public/js/doubt-atlas.js"
    - "backend/tests/test_doubt_round25_wave10.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の主張 42c4e2e8 で一覧の行と台帳が同じ「単一」になることを画面で確かめていない（第 11 周 te-03）"
      - "論文の数だけ支持線の文脈を作るので、教材の多いコースでの一覧の応答時間は測っていない"
related: [IK-0852, IK-1002, IK-1057]
view_of: []
history: []
---

## 課題

症状: 同じ主張 42c4e2e8 について、台帳は「単一の支持線」、未検証合意リストの行は「複数の支持線」。同じ行の「到達可能性: 記録なし」は、台帳の検証提案の
「次世代の装置・観測なら可能」と食い違って読める。

原因: リストは支持線をコースの教材 4 本の範囲で計算し、台帳は対象の論文 1 本で計算する。リストの到達可能性は覆る条件の量で、提案の量ではない。

## 発見の観点

ペルソナが一覧と詳細を見比べ、審判が範囲だけを変えて同じ関数を 2 回呼んだ（再現 + 実データ）。

## 解決の観点

未着手。範囲の正本（対象の document）に一覧を揃え、同じ語が別の量を指す欄は欄名で言い分ける。

## 一般化

型 scope-widened-silently。一覧で一括計算の都合で範囲を広げると、詳細と同じ欄名のまま別の答えになる。一覧と詳細が同じ欄名を持つなら、範囲も同じ正本から取る。

第 10 波（2026-10-31）: 一覧の支持線を行の論文の範囲に揃え、到達可能性の欄名を 2 つに分けた（上の resolution）。

## 第 11 周の再演（第 25 周・面 B 第 11 周、2026-10-31・judge_doubt_r11 — 固定コース 1bad7d9f・3 周連続の同じ対象）

GONE（画面で確認）。te-03 の前提の地図の未検証合意リスト 7 行目（主張 42c4e2e8「$\ddot I$ depends on …」）が「観測からの支持線: 単一の支持線」、同じ主張の台帳が「単一の支持線…『診断・応用』」で同じ段階（第 10 周はリスト「複数」）。リストの頭に範囲の事実文「観測からの支持線は、各行の対象が載っている論文の中で読んでいます（台帳と同じ範囲です）。」、行の欄名は「覆る条件の到達可能性: 記録なし」、台帳の提案カードは「検証提案の到達可能性: 次世代の装置・観測なら可能」で言い分けられた。残り（軽・別件）: 検証提案の到達可能性の語彙が観測の語だけ（IK-1148）。run: uxsim/runs/c-r25-doubt-r11/20261031T101505Z-99946（seq3・6）/ 20261031T101202Z-98534（seq3）。
