---
id: IK-1075
title: "凍結の ⚓ の部品（comp_001）は学習者の部品の文脈・説明の API では「再解析で置き換わり、後継が決まっていません」（IK-1051 の見え方）なのに、学習チャットの画面文脈（kind element）は同じ ⚓ を凍結時の解決を通さず焦点の論文で agent ID のまま引き、別の live 部品「EOS network mapping log-density to log-pressure」の役割・式・支える主張・検証スコープを「学習者が選んでいる論理要素」として AI に渡す"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/features/assistant_screen_adapter_design.md"
  - "docs/features/component_evidence_redesign.md"
feature_context:
  realizing: "学生が開いた ⚓ の部品について、パネルと AI が同じ対象・同じ事実で答える"
  layers: [screen_adapter_sa, learner_experience_b, knowledge_objects]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [version]
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
    確認（コード + 製品頭脳の要求の材料）: routes/learning.py::get_course_component_context と component_explanations は
    _resolve_frozen_component_ref（凍結時の記録から後継を辿る・決まらなければ _superseded_component_payload）を先に通す。
    画面文脈の element の材料（routes/learning.py の _screen_element_sources 付近・kind element）は _component_context_with_explanation を
    element_id（agent ID comp_001）と焦点の論文で直接呼び、凍結の解決を通らない。st-05 の seq7・10 は 200 で available:false +
    「後継が決まっていません」、seq8 の要求（mailbox req-04286）の画面文脈は「学習者が選んでいるのは論理要素「EOS network mapping log-density to log-pressure」」
    + 役割・式・支える主張・「この内容は「状態方程式が単調に増える範囲で」の範囲で検証されたと記録されています。」。
    接続軸 version = 凍結時の参照を現行版へ読む規則が経路ごとに違う。構造軸 responsibility = 凍結の ⚓ の解決が 1 つの route の責務で正本になっていない。
generalization:
  level: general
  general_form: "旧い版の参照を現行の対象へ読む規則を画面向けの経路にだけ掛けると、同じ参照を別の経路（AI への材料）が素の ID で現行版に解決し、画面が「決まらない」と言う対象について別の対象の事実を語る"
pattern: rule-enforced-per-surface
discovery:
  perspective: [trace_walk, symptom_report]
  note: "st-05 の頭脳が「パネルは後継なしなのに AI は部品の検証スコープを答えた」と指摘し、製品頭脳の要求の画面文脈と 2 つの route の呼び出しを並べた。run: uxsim/runs/c-r25-doubt-r9/20261031T015310Z-38509（seq7・8・10）。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    学習者に届く部品の文脈の解決点を routes/learning.py::resolve_learner_component_context 1 つにした（凍結の ⚓ →
    _resolve_frozen_component_ref → 後継が決まらなければ _superseded_component_payload・後継ありは付け替えて version_note・
    それ以外は焦点論文で _component_context_with_explanation）。部品の文脈 API（get_course_component_context）と画面文脈の
    _screen_element_sources（SA層 kind element）の両方がここを通り、画面文脈は topic_info の id と course_data を渡す。
    解決器 resolve_element は available:false の部品に凍結時の名前と frozen_label_line（パネルと同じ事実文）の 2 行だけを渡し、
    題名の縮退にも落とさない。後継の決まらない ⚓ は対象 ID を持たないので台帳（検証スコープ）も引かない。経路の一覧を
    SA層設計書 §11.21 に置き、部品の文脈を組んでよい関数を AST の呼び出し元検査で固定した。
  landed_in:
    - "backend/api/routes/learning.py"
    - "backend/core/assistant_context/resolvers/learning.py"
    - "backend/tests/test_assistant_context_learning_guardrails.py"
    - "backend/tests/test_doubt_round25_wave9.py"
    - "docs/features/assistant_screen_adapter_design.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "規則（凍結の ⚓ の後継解決）を API の出口から参照を解く 1 点へ移し、新しい経路が迂回できないことを呼び出し元の AST 検査で固定。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場で ⚓ を開いたまま尋ねる往復（st-05 × t11）を再演していない（第 10 周）"
      - "後継の決まる ⚓（付け替え）の画面文脈を実データで読んでいない"
related: [IK-1051, IK-1021, IK-1059, IK-0553]
view_of: []
history: []
---

## 課題

症状: 第 9 周 st-05（固定コース 1bad7d9f・t11）で ⚓「EOS network map（原文）」を開くと「教材を作ったときの部品『EOS network map』は、再解析で置き換わり、後継が決まっていません。」
（部品の説明も同文）。同じ画面で「この部品の主張は、どの条件のもとで確かめられているのですか？」と尋ねると、AI は名前の違う部品の検証スコープ
（状態方程式が単調に増える範囲）を答えた。学生はパネルと AI のどちらが正しいか判断できない。

## 発見の観点

同じ ⚓ について、パネルの API・説明の API・AI の材料の 3 経路を並べた。

## 解決の観点

参照を解く 1 点（resolve_learner_component_context）に規則を置き、パネルと AI の材料が同じ解決結果を読む（上の resolution.note）。

## 一般化

型 rule-enforced-per-surface（IK-1059 と同じ根）。参照の読み替えは「どの画面に掛けたか」ではなく、参照を解く 1 点に置く。

## 第 10 周の再演（第 25 周・面 B 第 10 周、2026-10-31・judge_doubt_r10 — 第 9 周と同じコース 1bad7d9f・同じ t11・同じ ⚓・同じ問い）

**GONE**。st-05 が ⚓「EOS network map」を開いたまま「この部品の主張は、どの条件のもとで確かめられているのですか？」と尋ねると、製品頭脳の要求（mailbox req-04302）の画面文脈は「学習者が選んでいるのは論理要素「EOS network map」です」+「教材を作ったときの部品『EOS network map』は、再解析で置き換わり、後継が決まっていません」の 2 行だけ（第 9 周は別の live 部品の役割・検証スコープまで）。AI は「後継の部品がまだ決まっていません。そのため…「検証の記録」は、いま手元の材料からは示せません」とパネルと同じ事実で答え、論文に書かれた条件を出典付きで分けて述べた。残り（本件の外）: 第 9 周に AI が答えた「状態方程式が単調に増える範囲で」は、わたしの地図の「理論の土台」（live の主グラフノード）の記録された条件として出続け、学生は ⚓ の部品と同じものか別物か画面から判断できない（IK-1051 の後継の決まらない側・IK-1113）。run: uxsim/runs/c-r25-doubt-r10/20261031T053655Z-69809（seq7・8・16・17）。
