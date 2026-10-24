---
id: IK-0939
title: "関係（辺）を次版の下書きへ反映する確認と凍結の影響に、その採用を誰がいつどんな根拠でしたかが写らず、別の教員の採用（論文の根拠なしの推定・採用のメモなし）が凍結を止めても、凍結しようとする教員は取り込むか戻すかを決める材料を持たない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_relation_edges_design.md"
  - "docs/features/decision_context_design.md"
feature_context:
  realizing: "複数の教員が採用した候補・関係を、凍結する教員が次版に入れるか戻すかを決めて分野の地図を公開する"
  layers: [relation_edges_re, field_atlas_s, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [review, assignment]
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
    接続軸 information: 判断の帰属（provenance_line）と根拠の一文（basis_line）は候補一覧の DTO にあるが、
    incorporate-preview の確認（admin.js 8229 付近 = つなぐ項目・種類・「学習者には表示されません」の 3 行）と
    freeze-impact の止め理由（atlas_lifecycle.freeze_readiness の pending_edges = 「A — B」のラベルだけ・
    routes/atlas.py _edge_pair_label）には運ばれない。統制軸 review / assignment: 採用は理由を求めず（見送りだけ
    review_note 必須）、凍結は「採用済み・未反映」を一律に止めるので、他人の採用を下書きへ入れる・戻す判断が凍結する
    教員に回るのに、その人の判断材料（誰が・いつ・何を根拠に・どんなメモで）が凍結の手前に無い。確認画面の
    「学習者には表示されません」は下書きの間の事実で、凍結後は学生の地図に関係として出ることを言わない。
    構造軸 representation: 採用に理由の欄が無い。処理軸 none。
    確認: run 20261023T070217Z-13812 seq 10 / 13 / 15（te-05 が別の run で採用した「惑星形成 — 星形成」が凍結を止め、
    凍結の影響にも反映の確認にも帰属が無い）、run 20261023T032742Z-99530 seq 11 / 13。
generalization:
  level: repo_pattern
  general_form: "ある人の判断が別の人の確定操作を止める仕組みで、止める側の判断の帰属と根拠が確定操作の画面へ運ばれず、確定する人が他人の判断を材料なしで引き受けるか戻すかを迫られる"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, trace_walk]
  note: "委任型の教員が関係を採用だけして終わり、別の run で精読型の教員が凍結しようとして、その採用に止められ根拠も帰属も確認できずに保留した。freeze-impact と incorporate-preview の応答と、候補一覧の decision の持つキーを突き合わせた。 run: uxsim/runs/c-astro-r25-atlas-r3/20261023T032742Z-99530・20261023T070217Z-13812。"
resolution:
  perspective: [carry_through, explicit_contract]
  note: >-
    止め理由と反映の確認に帰属と根拠を構造で運ぶ。atlas_review_display.pending_freeze_items が採用 1 件ずつ {kind, label, decision_line,
    basis_line, note_line} を作り（判断者は表示名・根拠の論文は閲覧できる題名だけ・関係は記録された出所の一文）、routes/atlas.py::_freeze_readiness が
    current_user を受けて atlas_lifecycle.freeze_readiness の pending_items（追加キー・判定は不変）に載せる。freeze 409 の detail にも同じ
    pending_items。incorporate-preview（辺・ギャップ）に decision_line / basis_line / note_line /
    timing_line（「下書きの間は学習者に表示されません。公開（凍結）すると、学習者の分野の地図に関係／項目として出ます。」）。採用に任意のメモ欄（既存
    review_note。見送りの理由必須は不変）。他の教員の採用を候補へ戻すときは理由必須（atlas_gaps route の _overrides_other_teacher → 422）で、監査 metadata
    に overrides_other_teacher と理由を残す。辺の見送り（元から理由必須）も同じ metadata を残す。凍結を止める規則は緩めていない。
  landed_in:
    - "backend/core/atlas_review_display.py"
    - "backend/core/atlas_lifecycle.py"
    - "backend/core/atlas_gaps/store.py"
    - "backend/core/atlas_edges/store.py"
    - "backend/api/routes/atlas.py"
    - "backend/api/routes/atlas_gaps.py"
    - "backend/api/routes/atlas_edges.py"
    - "frontend/public/js/admin.js"
    - "docs/manual/teacher/17-admin-atlas.md"
    - "backend/tests/test_atlas_review_round25_r3.py"
  principles:
    - principle: sync-by-stable-key-and-protect-human-decisions
      use: consulted
      note: "他人の採用（人の判断）を黙って捨てない — 戻すには理由を求め監査に残す。ゲートは緩めない。"
  verification:
    methods: [guardrail]
    unverified:
      - "複数の教員レーンで、他人の採用が止める凍結の影響に帰属・根拠・メモが読めることは再演で未確認"
      - "採用のメモ欄は window.prompt で、専用のフォームではない"
related: [IK-0885, IK-0848, IK-0884]
view_of: []
history: []
---

## 課題

症状: 凍結の影響が「採用済みでまだ次版の下書きに入っていない関係があるため、このままでは公開できません: 惑星形成 — 星形成」
と名前だけを言う。その関係は別の教員が論文の根拠なしの推定を採用したもので、採用のメモは無い。反映の確認も
「つなぐ項目・種類・学習者には表示されません」だけで、誰の判断か・いつ学生に見えるようになるかが無い。凍結する教員は
他人の判断を材料なしで引き受けるか戻すかを迫られ、保留した。

原因: 帰属と根拠は一覧にだけあり、確定の手前の 2 画面に運ばれない。採用に理由の欄が無い。

## 発見の観点

複数の教員レーンを続けて通した再演（reproduction）で、ある教員の採用が別の教員の凍結を止めた。応答のキーを辿った（trace_walk）。

## 解決の観点

第 3 波（2026-10-23）で是正。止め理由と反映の確認に帰属と根拠を構造で運ぶ。

## 一般化

承認・採用の判断を別の人の確定（公開・凍結・リリース）が引き受ける流れ全般（リリース前の確認・説明の一括承認）で出る。
確かめ方: 教員 A が採用だけした関係を教員 B が凍結の影響で見て、A の帰属と根拠の一文が読めることを確かめる。
