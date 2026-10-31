---
id: IK-0857
title: "D層のコース単位 API の大半（前提の地図・前提一覧・未検証合意リスト・台帳の要約・負荷再計算・コーパス監査・前提マイニング・スコープ候補と覆る条件の候補の再生成）が TEACHER であることしか見ずコースの編集権限を確かめず、学生の台帳 GET は受講ゲートだけで対象がそのコースの教材に属するかを確かめない"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/stakes_ledger_design.md"
feature_context:
  realizing: "教員が自分のコースの前提の地図を操作する／学生が受講コースの出典の検証状態を読む"
  layers: [doubt_d, stakes_ledger_sl, auth_visibility]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
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
    接続軸 condition: `backend/api/routes/doubt.py` の `/courses/{course_id}/...` の admin ルートのうち、
    `_require_editable_course_or_404` を通すのは observation-targets（1405）と naive-signals（1429）だけ。ledger-summary（933-）・
    scope-candidates/refresh（974-・LLM）・falsification-candidates/refresh（1382-・LLM）・load/recompute（1439-）・assumptions（1492-）・
    assumption-mining/run（1688-・LLM）・corpus-audit/run（1698-）・assumption-atlas（1718-）・open-assumptions（2380-）は
    `Depends(_require_teacher)` のみで、任意の教員が任意のコース id で読み・LLM の日次枠を消費できる。学生向け
    `get_learner_ledger_line`（2820-）は `get_accessible_course_data` で受講を確かめた後、`learner_ledger_line` が target_id だけで
    台帳行を引き、対象の document がコースの教材に含まれるかを見ない。返す `support_fact_line` には支持線の切断点の主張文が
    引用されるので、受講中のコースの URL で別の（閲覧できない）論文の台帳の一行を読める。式の target_id は `eq_dcf` のような
    推測できる形を含む。構造軸 none（権限判定の正本 `_require_editable_course_or_404` / `list_course_source_document_ids` はある）。
    砂場の run では 2 人の教員とも自分のコースだけを操作したので審判 DB4 は発火していない — 現物の確認はコードの読み取りによる。
generalization:
  level: general
  general_form: "同じ束の下に並ぶ経路の一部にだけ所属・編集権限のゲートを置き、残りは役割だけで通すため、束の id を差し替えると他人の束を読め・操作でき、子の対象 id を差し替えると束の外の対象を読める"
pattern: scope-widened-silently
discovery:
  perspective: [adversarial_review, inventory]
  note: "設計時の疑い（§22.2 の「権限ゲート不揃い」「学習者台帳 GET のコース範囲」）を、doubt.py のコース単位ルートを全件列挙してゲートの有無を突き合わせて確かめた。run の上では発火していない（te-02 / te-03 は所有コースのみ）。 run: uxsim/runs/c-r25-doubt-r1/20261022T124129Z-35838・uxsim/runs/c-r25-doubt-r1/20261022T125655Z-43791（学生の台帳 GET の経路 seq8）。"
resolution:
  perspective: [fail_closed]
  note: >-
    routes/doubt.py の /courses/{course_id}/... の admin ルートはすべて先頭で _course_scope_document_ids（_require_editable_course_or_404 → 教材の document 集合）を通す（ledger-summary・scope/falsification 再生成・observation-targets・load・assumptions・mining・corpus-audit・atlas・open-assumptions。naive-signals は既存のゲート）。反実仮想の course_id 指定も同じゲート、document_id 指定は閲覧権限。学生の台帳 GET はコースの教材 ∩ 対象の document（_target_document_ids と部品の写し先）で閉じ、外なら不在と同じ 404。ガードレールはコース単位ルートの先頭の文がゲートであることを AST で固定する。読み取り系を共有 viewer に開く判断はしていない（編集権限に揃えた）。
  landed_in:
    - "backend/api/routes/doubt.py"
    - "backend/tests/test_doubt_course_scope_round25.py"
    - "backend/tests/test_object_scope_authorization.py"
    - "backend/tests/test_stakes_ledger_api.py"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: "ゲートの戻り値から範囲を求めるので、範囲を引く経路がゲートを迂回できない。"
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "ルートごとにゲートを書く代わりに 1 関数を通し、全ルートを AST で走査して固定した。"
  verification:
    methods: [guardrail]
    unverified:
      - "他人のコースの id で叩いたときの 404 は単体テストのみ（砂場の API では未確認）"
      - "閲覧のみ共有の教員が前提の地図を読めなくなる（編集権限に揃えた）影響は運用で未確認"
related: [IK-0852, IK-0119]
view_of: []
history: []
---

## 課題

症状（コードの読み取りで確認）: 教員が他人のコース id で前提の地図・未検証合意リストを読み、LLM の再生成を起動できる。
学生は受講中のコースの URL で、コース外の論文の台帳の一行を target id で読める。原因: コース単位ルートのゲートが不揃いで、
学生の台帳 GET は対象の所属を確かめない。

## 発見の観点

ルートの全件棚卸し（inventory）と、壊すつもりで経路を読む（adversarial_review）。

## 解決の観点

解決済み（2026-10-23）。ゲートを全ルートに揃え、学生側は対象 ∩ コースの教材で閉じる（原理 enforce-condition-in-query-with-required-argument）。

## 一般化

同じ親 id の下に経路を足すたびにゲートを書くと、書き忘れた経路から範囲が広がる。確かめ方は、他人のコース id・コース外の
対象 id を渡して全ルートが 404 になることを見ること。
