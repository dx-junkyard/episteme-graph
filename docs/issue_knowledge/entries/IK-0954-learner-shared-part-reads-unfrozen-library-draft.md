---
id: IK-0954
title: "学生の「共通部品として」は凍結していないエントリの下書き（名前・要約）をそのまま読み、どの教員の下書き編集も凍結を経ずに学生へ届くが、画面と KB は「解析が参照するのは凍結した版だけ」とだけ言う"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "共通部品（ナレッジライブラリのエントリ）の内容を学生の部品の文脈に出す"
  layers: [concept_registry, image_library_l, learner_experience_b]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [version]
    governance: [review]
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
    接続軸 version: backend/core/component_context.py:652-670 の _build_shared_part は library_store.get_entry（下書きの行）の
    name / summary / aliases を読み、凍結版（library_entry_versions）を見ない。te-05 が結んだエントリは
    version_line「まだ凍結していません」・latest_version_no 0 のまま、学生 st-08・st-01 の「共通部品として」に名前と要約が出た。パイプラインの retrieval
    は凍結版だけを読む（L層の規律）ので、同じエントリの読み手どうしで版がずれている。統制軸 review:
    下書きは編集した教員の保存で即座に変わり、凍結（レビューの弁）を通らずに学生に届く。画面の文（review_status_note「解析が参照するのは凍結した版だけ」）と KB（library.md#what-
    students-see「凍結は学生の画面には直接は出ない」）は、凍結していなくても学生に届くことを言わない。
generalization:
  level: repo_pattern
  general_form: "同じ正本の読み手のうち一部は凍結版だけを、別の一部は下書きを読み、下書きの編集がレビューの弁を通らずに一部の読み手へ届く"
pattern: stale-derivative-served
discovery:
  perspective: [trace_walk, reproduction]
  note: >-
    te-05 がライブラリの詳細で「まだ凍結していません」「解析が参照するのは凍結した版だけ」を読んで「学生に見えるのか」と迷い（uxsim/runs/c-astro-r25-share-
    r4/20261024T232812Z-98848 seq13-14）、学生 st-08
    の部品の文脈にそのエントリの名前と要約が出た（uxsim/runs/c-astro-r25-share-r4/20261024T233212Z-21482 seq11）。_build_shared_part
    の読み先を追った。
resolution:
  perspective: [single_point_fix]
  note: >-
    学生の読み口を凍結版に揃えた。library store に get_frozen_entry_for_learner（active かつ凍結版がある エントリの最新凍結版の content だけを返す・無ければ None）を足し、component_context._build_shared_part はこれだけを読む（凍結版が無いエントリは共通部品の面ごと出さない = fail-closed）。記号の概念参照（symbol_lookup._load_concept_ref）も凍結版との JOIN で凍結版の無いエントリを落とし、名前は凍結版の name。ライブラリの「この概念の扱い」の補足と KB library.md#what-students-see に「学生に出るのは凍結した版だけ・凍結しないうちは出ない」を書いた。オーナー判断の推奨（学生も凍結版だけ）で実装。
  landed_in:
    - "backend/core/library/store.py"
    - "backend/core/component_context.py"
    - "backend/core/symbol_lookup.py"
    - "backend/api/routes/library.py"
    - "docs/admin_operations/library.md"
    - "backend/tests/test_share_round25_identity_consumers.py"
    - "frontend/public/js/admin.js"
    - "docs/manual/teacher/19-admin-knowledge-library.md"
    - "backend/tests/test_share_round25_r6_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "凍結版の無いエントリに結ばれた確定リンクを持つ実データでの学生画面"
      - "オーナー判断（学生に届く共通部品の版）の確定"
related: [IK-0920, IK-0953]
view_of: []
history: []
---

## 課題

症状: 凍結していない共通部品が、確定リンク 1 本で学生の画面に名前・要約ごと出る。教員はライブラリの画面からそのことを読み取れず、Copilot も答えられなかった。原因: 学生側の読み手（component_context）が下書きの行を読み、凍結版を読む解析パイプラインと版の扱いが揃っていない。

## 発見の観点

経路の追跡（確定リンク → component_context → app.js）と再現。

## 解決の観点

是正済み（第 25 周・面 A 第 4 波・2026-10-25）。

旧案: どちらに揃えるかはオーナー判断の候補（judge_share_r4.md §5）: (a) 学生は凍結版だけを読む（凍結版が無ければ共通部品の面を出さないか名前だけ）。(b) 下書きを読むことを正とし、ライブラリの「この概念の扱い」と KB に「確定したエントリは凍結していなくても、確定した結び付きを通じて学生の画面に名前と要約が出ます」と書く。推奨は (a)（L層の「読まれるのは凍結した版」に学生も揃え、どの教員の下書き編集も凍結を経て届くようにする）。

## 一般化

派生物・読み手ごとに版がずれる型。確かめ方は、凍結版と下書きで要約を変え、学生の部品の文脈がどちらを出すかを砂場で見ること。

## 第 5 周の再演（第 25 周・面 A 第 5 周、2026-10-27）

第 5 周の再演: 消滅（画面の文）。エントリの確定後に「学生の画面…に出るのも凍結した版だけで、凍結版が無いうちは学生には出ません」と出た。学生側は確定リンクが無く shared_part は null（凍結版の読み分けそのものは未評価）。run: uxsim/runs/c-astro-r25-share-r5/20261027T071135Z-43804 seq5、uxsim/runs/c-astro-r25-share-r5/20261027T071307Z-52079 seq11

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: 学生側は規則どおり（未凍結の確定リンクは出ない — st-08）。残り: エントリ詳細の「結びついた教材の部品」の区画注記が「確定した結び付きは学生の「共通部品として」に出ます。」と凍結の条件を言わず、同じ画面の「この概念の扱い」（凍結版が無いうちは学生には出ません）と Copilot の答えより粗い。いま確定したリンクが学生に出るのかが区画から読めない。run: uxsim/runs/c-astro-r25-share-r6/20261027T122225Z-3503 seq4、uxsim/runs/c-astro-r25-share-r6/20261027T122544Z-5649 seq3

## 第 6 波の是正（第 25 周・面 A 第 6 波、2026-10-27）

ライブラリ詳細の「結びついた教材の部品」の区画注記に凍結の条件を足した:「確定した結び付きは、この共通部品に凍結した版があるとき学生の「共通部品として」に出ます」（凍結していないうちは出ない）。記号の区画注記・「この概念の扱い」・Copilot と同じ条件を言う。

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演: 区画注記は GONE。詳細の「結びついた教材の部品」に「確定した結び付きは、この共通部品に凍結した版があるとき学生の「共通部品として」に出ます（凍結した版が無いうちは学生には出ません）」（uxsim/runs/c-astro-r25-share-r7/20261027T171357Z-51091 seq5）。学生側（凍結版のある共通部品が出るか）は、⚓ が開けず未評価（IK-1051）。観察（記帳しない）: 凍結版 2 のエントリに別名を足したとき、その別名が凍結版に入るのか再凍結が要るのかを画面が言わない（te-02 seq4）。
