---
id: IK-0371
title: "学ぶ単位の handle（U3 等）は候補表の位置番号で、草案を出したターンの教材集合と登録時の sources が違うと、登録は別の候補表に対して handle を解決し、トピックに違う論文の単位が黙って付く"
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コースビルダーで学ぶ単位を選んだ草案を登録する
  layers: [course_builder, learning_units]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [condition, contract]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: handle は「material_ids 順で並べた候補表の位置」で、単位の同一性（stable_key）を持たない
    （representation。core/course_units.py は候補表に無い handle だけを捨て、有る handle は位置で解決する）。
    接続軸: 草案を出したターンの教材集合（selected_material_ids）が登録時の sources に運ばれず（condition）、
    登録は sources から候補表を作り直して handle を解決する（contract。同じ "U3" が別の単位を指す）。
    確認: 砂場 run（第 4 周）で 2 教材のターンが出した草案（U1–U70）を、sources 1 教材で登録したところ、
    Cepheus B の章の全トピックに中性子星論文の単位（音速の軟化・TOV 損失・R1.4）が付き、sources は
    1 document になった。エラーも警告も出ない。画面（admin.js の approveCourse）も state.selectedMaterialIds を
    登録時に読むため、チャット後に選択を変えると同じことが起きる。
generalization:
  level: repo_pattern
  general_form: 位置番号の参照を、それを作った文脈（集合・順序）と一緒に運ばず、別の文脈で解決する
pattern: id-unique-only-within-inner-scope
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    実ペルソナの教員段でターンごとに教材の選択が変わったまま登録し、登録後の topic.evidence と sources を
    砂場 DB と学生段の観測で突き合わせた。
resolution:
  perspective: [representation_change, carry_through]
  note: >-
    草案に handle → stable_key の対応表（unit_candidate_keys）をターンごとに同梱し、units を {handle, stable_key} に正規化。登録は stable_key で解決し、無ければ捨てて監査に unit_refs_outside_sources を残す。画面（admin.js）とハーネスも同じ形を送る。
  landed_in:
    - backend/api/routes/admin.py
    - backend/core/course_units.py
    - backend/api/routes/learning.py
    - frontend/public/js/admin.js
    - backend/tests/test_ik0371_unit_identity.py
  verification:
    methods: [reproduction_rerun, docker_e2e, guardrail]
    unverified:
      - 対応表の無い旧草案で sources が違うときの 422（採らなかった）
      - 登録応答で「外した単位」を教員に見せること（監査のみ）
related: []
view_of: []
history: []
---

## 課題

草案の handle が位置番号なので、登録時の教材集合が違うと別の単位に解決される。誤りは静かに起きる。

## 発見の観点

実ペルソナの教員段（選択の揺れ）と、登録後の DB・学生画面の突き合わせ。

## 解決の観点

未解決。handle に stable_key を添え、登録は key で解決する。

## 一般化

内側の文脈でしか一意でない参照を外へ持ち出す型（id-unique-only-within-inner-scope）。
