---
id: IK-0861
title: "説明バージョン（C層）が教員・学生の両側で開けない — 説明の一覧・追加・承認・引用の権限ゲートが部品の course_id（パイプラインの部品では空）でコースを引き、学生側は受講ゲートではなく教員の閲覧ゲートを使い、件の SQL も course_id 一致で絞るため、パイプライン由来の部品の説明はどの経路からも届かない"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/component_evidence_redesign.md"
  - "docs/features/graph_dialogue_review_design.md §11.1"
feature_context:
  realizing: "教員が部品の説明を書き・承認・共有し、学生がその部品の承認済みの説明を読む"
  layers: [endorsement_c, lecture_studio, frontend_learning_ui, auth_visibility]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [condition, target]
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
    接続軸 condition: 教員側 backend/api/routes/theory_components.py:4730（一覧）/ 4757（追加）は
    _ensure_viewable(component.course_id) を、承認・引用は _explanation_context の course_id（:4888 / :4997）を
    _ensure_viewable に渡す。パイプライン由来の部品は course_id が空（応答の "course_id": ""）なので
    get_viewable_course_data が引けず 404「Course not found」。コースの所有者（te-01）でも同じ。
    学生側 backend/api/routes/learning.py:8575 は get_viewable_course_data（所有者・グループ共有の判定 = services.py:1003
    user_can_view_course）を使い、受講ゲート get_accessible_course_data を使わないので、受講中の学生にも 404。
    接続軸 target: 同 :8590-8591 の SQL は e.course_id = :course_id で絞り、パイプライン部品の説明（course_id 空）には
    当たらない。CAST(:cid AS uuid) にチップの agent 側 ID（comp_003）が渡れば 5xx になる（ゲートで止まっているため
    今回は出ていない）。構造軸 responsibility: 部品は document 単位の権限（_ensure_component_editable :4078 は
    document → course の順で判定）に移ったのに、C層の説明だけがコース単位の判定を持ち続けている。統制軸は
    承認の手続そのものではなく到達の問題なので none（medium）。
generalization:
  level: repo_pattern
  general_form: "対象の所有単位を別の単位へ移したとき、一部の操作の権限判定と絞り込みだけが旧い単位のキーを読み続け、新しい単位で作られた対象には到達できない"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    学生 3 人が部品の説明で「説明を取得できませんでした」、教員 te-01 が原稿スタジオの説明で「読み込みに失敗しました」に
    当たった。応答の 404 detail と部品の course_id 空を突き合わせ、両側のゲートと SQL を辿った。run:
    uxsim/runs/c-astro-r25-share-r1/20261022T123418Z-31803 seq9・20261022T125310Z-41522 seq6・20261022T125633Z-43511 seq9。
resolution:
  perspective: [responsibility_move, required_argument]
  note: >-
    説明の一覧・追加・承認・承認一覧のゲートを部品の document 単位（_ensure_component_viewable / _ensure_explanation_viewable・document → course の順）に揃えた。学生側は受講ゲート get_accessible_course_data を通し、部品をコースの教材の document の中で UUID に解決してから引く（resolve_course_component_id・CAST しない）。届く範囲は _LEARNER_EXPLANATION_SCOPE_SQL の 1 断片（承認済みかつ 標準 / 共有 ON / 書いたコース）で、部品文脈の instance.explanation も同じ。course_id は出所として残し、追加の body の optional course_id で記録する。app.js は 404 と空（サーバの note）を分ける。
  landed_in:
    - "backend/api/routes/theory_components.py"
    - "backend/api/routes/learning.py"
    - "backend/core/component_context.py"
    - "frontend/public/js/app.js"
    - "frontend/public/js/admin-lecture-studio.js"
    - "docs/features/component_evidence_redesign.md"
    - "backend/tests/test_share_round25_fixes.py"
    - "backend/tests/test_component_context_api.py"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: "学生の範囲をコースの document 集合で部品を解決したうえで SQL の断片 1 つに置いた。"
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "説明の全経路を 1 つのゲート関数に寄せた。"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB での説明の一覧・承認・引用の端から端までの操作（砂場の第 2 周で確かめる）"
      - "sharing-dashboard は従来どおり course_id（出所）で集計する — パイプライン部品の説明で出所の無いものはどのコースの集計にも出ない"
related: []
view_of: [IK-0852]
history: []
---

## 課題

症状: 教員は原稿スタジオの「説明・承認の共有」で説明カードが 1 枚も出ず（読み込みに失敗しました）、学生は部品の説明で
「説明を取得できませんでした」になる。独自解釈の追加・承認・引用・学生への配信のどれにも届かない。原因: 部品の所有単位が
document に移った後も、C層の説明の権限と絞り込みだけがコース（部品の course_id）を読む。学生側は受講ではなく教員の
閲覧権で判定する。

## 発見の観点

ペルソナの通し受講で両側の失敗が同時に出た（reproduction）。404 の detail と部品の行を突き合わせてゲートを辿った（trace_walk）。

## 解決の観点

未解決。グラフ対話レビューで reject / approve だけを document 単位に是正した前例（_ensure_component_editable）を、説明の
経路にも 1 つの関数として適用する。面ごとの書き足しにしない（共通のゲート関数を経路が必ず通る）。

## 一般化

所有単位の移行で、規則を 1 箇所に置かず面ごとに直すと、直していない面が旧い単位を読み続ける。確かめ方は、course_id の空の
部品・受講だけの学生・document 共有だけの教員の 3 つで、説明の全経路（一覧・追加・承認・引用・学生の一覧・部品文脈の
instance.explanation）を開き、すべて届くか、閉じる理由が日本語の事実文で出ることを見ること。
