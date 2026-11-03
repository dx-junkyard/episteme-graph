---
id: IK-0905
title: "台帳の記帳面に内部の語が出る — スコープの必須エラーが「condition / domain / precision / system のうち 1 つ以上」と英語の欄名、記帳者が users の UUID のまま、同じ内容のスコープを二度記帳しても重複の事実文も畳みも無い、同じ検証状態を PUT しても画面が一字も変わらず事実文が無い"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/architecture/persona_enactment_testing_design.md §22.8"
  - "docs/features/doubt_layer_issues.md"
feature_context:
  realizing: "教員が台帳にスコープと検証状態を記帳し、記帳したこと・誰が記帳したかを確かめる"
  layers: [doubt_d, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
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
    処理軸 wording: routes/doubt.py の 422 の detail が API の欄名（condition / domain / precision / system / reason / evidence_quote）で、画面の欄の日本語名と対応しない。接続軸 information: _scope_out は recorded_by を users.id
    のまま返し、doubt-atlas.js 285 行は表示名を引かずそのまま描く（帰属の情報は DB にあるが画面まで名前として届かない）。スコープの追記は重複を検査せず、verification-status の PUT は旧状態と同じでも ok を返し、画面は状態行を更新しない。構造軸 none。
generalization:
  level: repo_pattern
  general_form: "帰属・検証の記帳面で、API の欄名と利用者の識別子をそのまま画面に流し、同じ記帳の繰り返しも黙って受けるため、利用者が自分の操作の結果と記帳者を読めない"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: >-
    te-04 と te-01 の記帳で、422 の英語の欄名・記帳者 UUID・同一内容の重複行・同じ状態の PUT の無反応を観察した。run:
    uxsim/runs/c-r25-doubt-r2/20261022T215750Z-73306（seq16・17・18・20）・uxsim/runs/c-r25-doubt-r2/20261022T220507Z-77990（seq12・13・14・15）。
resolution:
  perspective: [vocabulary_table]
  note: >-
    第 2 波で是正（2026-10-23）。スコープの入力検査は画面の語（label_vocab.LEDGER_SCOPE_AXIS_LABELS）で事実文を返す。記帳者は recorded_by_label（users.display_name・引けなければ「記帳者を引けません」）。同じ 4 軸・同じ根拠のスコープは受けたうえで duplicate + note。検証状態の PUT は changed + note（記帳しました / すでにこの状態です）。画面は直前の note を台帳区画の先頭に出し、記帳フォームの英語の欄名を外した。
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/core/doubt/scope.py"
    - "backend/api/routes/doubt.py"
    - "frontend/public/js/doubt-atlas.js"
    - "backend/tests/test_doubt_ledger_document_key_round25.py"
  verification:
    methods: [guardrail]
    unverified:
      - "JS 側に欄名の逐語ミラー検査は置いていない（JS は静的な見出しだけを持ち、事実文はサーバの文をそのまま描く）"
related: [IK-0900]
view_of: []
history: []
---

## 課題

症状: スコープの記帳で軸を空にすると「condition / domain / precision / system のうち 1 つ以上が必要です」と英語の欄名で
止められ、画面のどの欄か分からない。記帳者は `1b2424a6-…` のような UUID で、自分か他人かが読めない。他の教員と
一字一句同じスコープを記帳すると 2 行並び、重複である事実も出ない。検証状態を同じ値で記帳し直しても、画面は一字も
変わらず「記帳しました」「同じ状態です」のどちらも出ない。

## 解決の観点

第 2 波で是正（2026-10-23。frontmatter の resolution）。以下は起票時の是正案。是正案:

- 422 の detail を画面の欄名（条件・領域・精度・系 等。`doubt-atlas.js` の欄見出しと同じ語）で書く。語の正本は
  `core/label_vocab.py` に 1 表置き、JS は逐語ミラーで固定する。
- `_scope_out` に `recorded_by_label`（`users.display_name`・取れなければ「記帳者を引けません」）を足し、JS はそれを描く。
  アカウントの墓標化（AL1）の後も名前の解決は同じ関数を通す。
- 同じ対象に同じ 4 軸・同じ根拠のスコープがあるときは 409 で止めるか、受けたうえで「同じ内容のスコープが既にあります」
  の事実文を返す（推奨は事実文付きで受ける — 別の教員の独立の判断として残す意味がある）。
- verification-status の PUT は `changed: bool` を返し、画面は「記帳しました」/「すでにこの状態です」を状態行に出す。

## 発見の観点

記帳の各操作を 2 人の教員で繰り返し、画面の文と応答を照らした。

## 一般化

記帳面に API の欄名と内部の識別子をそのまま流すと、利用者は自分の記帳を確かめられない。確かめ方は、入力検査の失敗・
他人の記帳・重複・無変更の 4 つの場面を作り、どれも利用者の語の事実文になることを見ること。
