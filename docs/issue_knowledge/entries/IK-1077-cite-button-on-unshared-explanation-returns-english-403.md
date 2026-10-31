---
id: IK-1077
title: "原稿スタジオの説明カードは、共有 OFF で著者でない教員の説明（標準の説明を含む）にも［別コースで引用］を出し、押すと英語の 403「This explanation is not shared」だけが出る — 引用できない理由（共有されていない）も、誰が共有を ON にできるかも日本語で出ない"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
feature_context:
  realizing: "教員が他の教員・AI の説明を、自分にできる操作（支持・引用）だけで扱い、できないときは理由を読める"
  layers: [endorsement_c, lecture_studio, frontend_admin_ui, auth_visibility]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [assignment]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 condition: routes/theory_components.py cite_explanation は shared が偽で著者でも SYSTEM_ADMIN でもないとき 403「This explanation is not shared」
    （英語の detail）を返す。admin-lecture-studio.js の説明カードは［別コースで引用］を全カードに無条件で描く（IK-0925 の是正は can_edit で［公開の査読］
    ［共有ON/OFF］だけを出し分け、「支持・引用は誰でも」とした — 引用の条件 shared を画面に運んでいない）。統制軸 assignment: 引用の可否の判定（サーバ）と
    ボタンの見え方（画面）が別々。
generalization:
  level: repo_pattern
  general_form: "操作の可否の条件の一部（ここでは共有の有無）を画面に運ばず、押せるボタンが断られ、断りの文が利用者の言語でない"
pattern: permission-and-affordance-asymmetric
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    第 25 周 面 A 第 9 周 te-04 (b2) が共有 OFF の標準の説明の［別コースで引用］を押し、POST /api/admin/explanations/e9575abf…/cite が 403
    （uxsim/runs/c-astro-r25-share-r9/20261031T015134Z-37470 seq6）。カードの描画と cite_explanation の分岐を突き合わせた。
resolution:
  perspective: [canonical_source, carry_through]
  note: >-
    引用の可否の正本を routes/theory_components._explanation_can_cite（共有 ON / 著者 / SYSTEM_ADMIN）にし、cite_explanation の 403 と
    説明一覧の can_cite / cite_note が同じ判定を使う。断りの文は core/explanation_governance.citation_blocked_note（教員の説明 =
    「共有を ON にできるのは著者だけ」/ 標準の説明 =「この部品の論文を編集できる教員」）で、403 の detail も同じ日本語の事実文。
    admin-lecture-studio.js の説明カードは can_cite が偽なら［別コースで引用］を出さず事実文を描く。
  landed_in:
    - "backend/api/routes/theory_components.py"
    - "backend/core/explanation_governance.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "backend/tests/test_share_round25_r9_fixes.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "IK-0925 の出し分けを引用にも広げた。固有の差分: 標準の説明は共有を ON にできる人が著者でない。"
  verification:
    methods: [guardrail]
    unverified:
      - "著者でない教員で共有 OFF / ON の説明を開き、ボタンと文を読む通し（第 10 周 te-02 × (b2)）"
related: [IK-0925, IK-1061]
view_of: []
history: []
---

## 課題

症状: 共有 OFF の標準の説明に［別コースで引用］が出て、押すと「This explanation is not shared」とだけ出る。教員は共有されていないから引用できないと推測したが、
どうすれば引用できるか（共有を ON にするのは誰か）は画面に無い。

原因: 引用の条件（共有されているか・著者か）が説明一覧の応答に無く、画面は全カードにボタンを描く。サーバの断りの detail が英語のまま。

## 発見の観点

再現: te-04 の (b2) で標準の説明を引用しようとした。経路の追跡: admin-lecture-studio.js の説明カードの描画と cite_explanation の 403 の分岐。

## 解決の観点

未着手。見立ては IK-0925 と同じ形（行ごとの可否と事実文をサーバが返し、画面はできない操作を出さない）を引用にも広げること。

## 一般化

操作の可否を出し分ける是正は、その画面の操作すべての条件を数えてから行う。型 permission-and-affordance-asymmetric。確かめ方は、著者でない教員で共有 OFF / ON の説明を開き、
引用のボタンと文を読むこと。

## 第 10 周の再演（第 25 周・面 A 第 10 周、2026-10-31・judge_share_r10）

GONE（同じ標準の説明 e9575abf・著者でない教員 te-02 で比較可）。共有 OFF の標準の説明に［別コースで引用］が無く「この説明は共有されていないため、別のコースで引用できません。共有を ON にできるのは、この部品の論文を編集できる教員です。」。共有 ON の説明には引用ボタンが出て引用は 200。run: uxsim/runs/c-astro-r25-share-r10/20261031T053828Z-70459（seq3・6）。
