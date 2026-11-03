---
id: IK-0414
title: "論文の海の論文一覧（GET /api/learning/corpus/documents）は分野の地図に関係づけられた論文（配置 ∪ gap 信号）だけを返し、学習中のコースの論文のように閲覧できるのに関係づけの無い論文が一覧に無いことを、事実文で1つも言わない"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が論文の海で、分野に関係する閲覧可能な論文を一覧する（コーパス回遊層 Phase A）
  layers: [corpus_roaming]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: 一覧の DTO は documents だけで、「母集合から外れた閲覧可能な論文」の事実を置く場所が無かった（representation）。接続軸: 可視集合には学習中の論文が入っているのに、その存在が応答に届かない（information）。処理軸: 母集合を配置 ∪ gap 信号に限るのは CR3 / LS1 の設計どおりで、条件式の誤りではない（none。medium）。確認: 第 8 周の学生が「自分の Cep B の論文も暗黒エネルギーの論文も一覧に出てこない」と記録。
generalization:
  level: repo_pattern
  general_form: 閉世界の一覧で母集合の外側を黙って落とし、利用者は「無い」と「関係づけられていない」を区別できない
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: 学生ペルソナが論文の海を開き、自分の論文が一覧に無いことを記録した。
resolution:
  perspective: [representation_change]
  note: >-
    応答に optional キー facts を足し、一覧に出ない閲覧可能な論文を題名の列挙1行で言うようにした（corpus_view.list_unrelated_visible_titles / unrelated_documents_fact。上限8題名 +「ほか」・件数なし・「この分野の論文ではない」とは言わず「関係づけが記録されていない」と言う = CR4）。地図の位置には置かない（LS1）。取得に失敗したら事実文だけ落とす。フロント（corpus-sea.js）は facts をまだ描いていない。
  landed_in:
    - backend/core/corpus_view.py
    - backend/api/routes/corpus.py
    - backend/tests/test_corpus_roaming_core.py
    - backend/tests/test_corpus_roaming_api.py
    - frontend/public/js/corpus-sea.js
    - backend/tests/test_learner_decision_notice_ui_static.py
  verification:
    methods: [guardrail]
    unverified:
      - 実 Postgres での NOT EXISTS 句の実行（docker 未達）
      - 砂場での再演
      - corpus-sea.js が一覧の下に facts を出すことはブラウザで確かめていない（静的検査のみ）
related: [IK-0386, IK-0410]
view_of: []
history: []
---

## 課題

一覧の外側にある自分の論文について、何も言われない。

## 発見の観点

実ペルソナの論文の海の読み（第 8 周）。

## 解決の観点

母集合の外側を事実文の受け皿に載せる。

## 一般化

閉世界の一覧は、外側の存在を言う場所を持たないと「無い」と読まれる。
