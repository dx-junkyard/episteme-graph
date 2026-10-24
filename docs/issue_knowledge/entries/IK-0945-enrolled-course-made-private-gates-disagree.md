---
id: IK-0945
title: "受講中のコースを所有者が非公開にすると、マイコースに残り・開く・教材・講義・チャットは受講ゲート（get_course_data = 受講の行）で通るのに、受講開始は 403、記号の吹き出し・部品の文脈・帰還の扉は可視性ゲート（get_accessible_course_data）で 404「Course not found」になり、同じコースの中で画面ごとに入れる／入れないが食い違う"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "学習者が受講中のコースを開き、記号・⚓ から文脈を辿って読む"
  layers: [auth_visibility, learner_experience_b, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
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
    接続軸 condition: backend/api/services.py の get_course_data（255 行）は所有者か learning_states の行があれば可視性を見ずに返し、get_accessible_course_data
    （692 行）は所有者・public かつ published かつ template・group メンバーだけを通す。routes/learning.py の get_topic_material（4731 行）・講義・チャットは前者、
    symbols/lookup（7874 行〜）・components/{id}/context（8821 行）・cycle の return-door は後者、enroll_course（1280〜1299 行）は可視性で 403。
    構造軸 aggregation: 「この学習者はこのコースを読めるか」の判定が 2 つの関数に分かれ、受講済みで非公開になった状態でだけ両者が食い違う。処理軸 none。
    統制軸 none（medium: 非公開化した後に既存の受講者へ何を許すかは方針が書かれていない）。
generalization:
  level: repo_pattern
  general_form: "同じ利用者・同じ対象への入場判定が 2 つの関数に分かれ、片方だけが変わる状態（後から閉じた等）で、同じ画面の中の経路ごとに入れる／入れないが食い違う"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, boundary_walk]
  note: >-
    st-04 がマイコースの 1bad7d9f（周の前の pin_course で非公開化）で受講開始すると 403、開くと通り講義も読めたが、記号 P の吹き出しは何も出ず
    （lookup は 404）、⚓ は「HTTP 404 Course not found」。return-door も 404。run: uxsim/runs/c-astro-r25-studio-r3/20261023T072502Z-354（seq4・5・9・10）。
    同じ状態は st-01 の一覧（seq2）にも出る。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: "学習者の入場判定を services.resolve_course_entry の 1 関数にし、get_course_data / get_accessible_course_data を委譲させた。方針は指揮者の判断（推奨どおり）で、所有者・受講の行がある利用者は非公開化・開示範囲の変更の後も読み続けられ、止まるのは新規の受講開始だけ（403 は COURSE_ENROLLMENT_CLOSED_NOTICE）。マイコースの行に enrollment_notice（COURSE_ENROLLED_BUT_CLOSED_NOTICE）、入れないときの 404 は日本語の COURSE_NOT_AVAILABLE_NOTICE。方針は auth-visibility.md §3 に書き、受講者の継続アクセスを変えるならオーナー判断として書き換える。"
  landed_in:
    - "backend/api/services.py"
    - "backend/api/routes/learning.py"
    - "backend/api/routes/cycle.py"
    - "backend/api/schemas.py"
    - "backend/core/label_vocab.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_course_entry_gate.py"
    - "docs/features/auth-visibility.md"
  verification:
    methods: [guardrail]
    unverified:
      - "非公開化した固定コースで学生が記号・⚓・扉を開く流れは実機で見ていない（第 4 周の再演）"
      - "継続アクセスの方針はオーナーに確かめていない（推奨どおりに実装）"
related: [IK-0857, IK-0861, IK-0407]
view_of: []
history: []
---

## 課題

症状: 非公開になった受講中のコースで、教材と講義は読めるのに、受講開始は「権限がありません」、記号は無反応、⚓ は英語の 404。学生はコースに入れて
いるのか入れていないのか分からない。

原因: 学習者の入場判定が「受講の行がある」と「いま公開されている」の 2 関数に分かれ、ルートごとにどちらかを使っている。

## 発見の観点

固定コースの封じ（非公開化）で生じた状態を学生が歩き、ルートごとの応答を並べた。状態はハーネスが作ったが、所有者が受講者のいるコースを非公開に
することは製品の通常の操作。

## 解決の観点

第 25 周 面 D 第 3 周の是正（2026-10-23）で解決。学習者の入場判定を services.resolve_course_entry の 1 関数にし、get_course_data / get_accessible_course_data を委譲させた。（以下は起票時の見立て）入場判定を 1 関数にまとめ、非公開化後の既存受講者の扱いを方針として決める（オーナー判断の候補）。

## 一般化

入場の規則を経路ごとに選ぶと、閉じる操作のあとで画面が部分的に開いたままになる。確かめ方は、受講中のコースを非公開にして学習者の全ルートを叩き、
すべてが同じ答え（入れる／入れない）を返すことを見ること。
