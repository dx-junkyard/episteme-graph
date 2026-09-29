---
id: IK-0412
title: "分野の地図（GET /api/atlas）で、骨格の初期表示に由来するノードの ledger_status が verified のまま、同じノードの検証行は「台帳に記帳なし」と言い、パンくずには分野の内部キー（astrophysics）が出て、「暗黙の前提」のピルには意味の説明が無い"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が分野の地図を開き、各ノードが台帳でどう記帳されているかを読む（S層）
  layers: [field_atlas_s, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [meaning]
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
    処理軸: atlas_view.py が ledger_status に overlay 行の status をそのまま入れ、ピルも status から引いていた（wording）。構造軸: status 1 語が「骨格 seed の初期表示」と「台帳の記帳」の2つの意味を担い、seed 由来かどうかは status_source にしか無い（representation。medium: status_source 自体は行にあったので、表現の欠如より読み手の取り違えに近い）。接続軸: IK-0368 で検証行だけ直り、同じ行の ledger_status とピルは別の意味のまま届いた（meaning）。パンくずは skeleton.cartridge（内部キー）を表示名に使っていた。統制軸: 手続・順序の要素は無い。確認: 第 8 周の学生 st-01 が全ノードで ledger_status: verified と「検証: 台帳に記帳なし」の並びを見て「確かめられているのか分からない」と記録。
generalization:
  level: repo_pattern
  general_form: 1 つの状態語が由来の違う 2 つの意味（初期表示と記帳）を担い、片方の文だけを直すと同じ行の中で状態語と文が食い違う
pattern: same-name-different-referents
discovery:
  perspective: [reproduction]
  note: 学生ペルソナが地図のノードを順に読み、状態語と検証行の矛盾を記録した。
resolution:
  perspective: [explicit_contract, vocabulary_table]
  note: >-
    学習者に返す ledger_status を atlas_state.learner_ledger_status で導出し、seed 由来・コーパス由来の状態なし・引用 0 本の verified を unrecorded（語の正本 label_vocab.ATLAS_LEDGER_STATUS_UNRECORDED）に寄せた。表示状態 status とノードの色はフロント互換のため変えていない。seed の「原文に裏付け」ピルは「骨格の初期表示（台帳に記帳なし）」。各ノードに pill_note（label_vocab.ATLAS_PILL_NOTES。「暗黙の前提」の意味の1行）を足した。パンくずは atlas_state.domain_display_name（atlas_domain_meta.name → 同梱 domain.json）で、無ければ「名前が登録されていない分野」。トップレベルに domain_name を足し、cartridge は取得キーとして残した。
  landed_in:
    - backend/core/atlas_state.py
    - backend/api/routes/atlas_view.py
    - backend/core/label_vocab.py
    - backend/tests/api/test_atlas_view_api.py
    - backend/tests/core/test_atlas_state.py
    - frontend/public/js/atlas-overlay.js
    - backend/tests/test_learner_decision_notice_ui_static.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（第 8 周の学生の地図の読み）
      - atlas-overlay.js がピルの下に pill_note を出すことはブラウザで確かめていない（静的検査のみ）
      - seed が assumed / contested のノードのピルは骨格の宣言のまま残している（台帳の状態は unrecorded）
related: [IK-0368]
view_of: []
history: []
---

## 課題

地図のノードが「verified」を名乗りながら「台帳に記帳なし」と言う。

## 発見の観点

実ペルソナ（学生）の地図の読み（第 8 周）。

## 解決の観点

台帳の状態語を検証行と同じ根拠から導き、未記帳の語を持たせる。

## 一般化

状態語の由来が 2 つあるとき、片方の文だけ直すと同じ行で意味がずれる。
