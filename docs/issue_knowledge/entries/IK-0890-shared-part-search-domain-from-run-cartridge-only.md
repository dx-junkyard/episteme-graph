---
id: IK-0890
title: "共通部品の検索（深く検討の同一性リンク）が分野を解析 run の cartridge_id だけで決めるため、分野を指定せずに取り込んだ教材（2026-09-10 是正 F9 以降の既定）では検索自体が「分野（カートリッジ）が特定できない」で止まり、コースの分野・配置ドメイン・教員の選択への後退も出口も無く、同一性リンクが1本も作れない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が教材の部品を深く検討で開き、ナレッジライブラリの共通部品と結び付ける（同一性リンクの作成）"
  layers: [deliberation_w, concept_registry, cartridges, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition, information]
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
    接続軸 condition: backend/api/routes/deliberation.py:753 の list_shared_part_candidates_for_element は domain_key を dialogue.document_domain_key（backend/core/deliberation/dialogue.py:262 → persistence.document_run_cartridge_id）だけで決め、空なら 0 件 + note で返す。取り込み時の分野は F9（EPISTEME_DEFAULT_CARTRIDGE_ID 既定空）で空が正常な状態になったのに、検索の条件は「run に cartridge がある」ことを前提にしたまま。接続軸 information: 同じ document の分野は別の正本に在る — 教材を sources に含むコースの cartridge_id（paper_discovery.corpus.document_domain_keys。固定コース 1bad7d9f は atlas_cartridge_id=astrophysics）、landscape_placements の domain_key、ライブラリ側の作成済みエントリの domain_key（astrophysics）。どれにも後退しない。W層の対話の同一性候補（dialogue.py:320）も同じ関数で同じく空になる。構造軸は none（判定を置く場所は 1 つで、材料の供給が欠けている）。統制軸は none。
generalization:
  level: repo_pattern
  general_form: "入口で任意になった属性を、後段の機能が唯一の判定材料として要求し続け、同じ値を持つ別の正本へ後退しない"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員 te-04 が TeX 版教材の部品を深く検討で開き、「共通部品と結びつける」で検索すると「この教材の分野（カートリッジ）が特定できないため、共通部品を検索できません。」で止まった（gave_up）。同じ周の一覧には同名エントリ（分野 astrophysics）があった。note の分岐を route から run の cartridge_id の解決まで辿った。run: uxsim/runs/c-astro-r25-share-r2/20261022T215750Z-73307 seq5。設計の pre-mortem（design_share.md の疑い #5）の実測確認。
resolution:
  perspective: [canonical_source, carry_through]
  note: >-
    分野の解決を core/deliberation/dialogue.py::resolve_document_domains の 1 関数（採用解析 run の cartridge_id → 教材を sources に含むコースの cartridge_id（paper_discovery.corpus.document_domain_keys）→ live な landscape_placements の domain_key、最初に値が出た段）にまとめ、共通部品の検索 route と W層の対話の同一性候補（collect_identity_candidates）の両方が通る。検索 route は教員の選択（クエリ domain_key）を最優先し、1 つに決まらない（複数・どこにも無い）ときはサーバが黙って選ばず、表示名付きの domains（当たった分野 → ライブラリにエントリがある分野）と事実文（「カートリッジ」の語なし）を返す。deliberation.js は分野の選択欄を描き、選んだ分野で検索し直す。
  landed_in:
    - "backend/core/deliberation/dialogue.py"
    - "backend/api/routes/deliberation.py"
    - "frontend/public/js/deliberation.js"
    - "backend/core/library/schema.py"
    - "backend/tests/test_share_round25_r2_fixes.py"
    - "backend/tests/test_deliberation_api.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "検索 route と対話の同一性候補を同じ段順の 1 関数に寄せた。"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB で分野未指定の TeX 版教材から候補が出ること・分野を選ぶ出口の操作（第 3 周の te-04 (a') で確かめる）"
related: [IK-0861, IK-0866]
view_of: []
history: []
---

## 課題

症状: 分野を指定せずに取り込んだ教材の部品では、共通部品の検索が「分野（カートリッジ）が特定できない」で止まり、
同一性リンクの作成に進めない。画面には分野を決める出口が無く、内部語「カートリッジ」が出る。原因: 検索の分野を解析 run の
cartridge_id だけで決め、コースの分野・配置ドメイン・教員の選択に後退しない。

## 発見の観点

教員ペルソナの通し受講（reproduction）。応答の note が出る分岐を run の解決まで辿った（trace_walk）。

## 解決の観点

第 2 波（2026-10-23）で是正。分野の解決を段順の 1 関数にまとめ、最後は教員に選ばせる（AI・サーバが黙って決めない）。

## 一般化

入口の既定を「指定しない」に変えたとき、その値を判定材料にしていた後段を棚卸ししないと、後段が静かに止まる。確かめ方は、
分野を指定せずに取り込んだ教材・コースに分野がある教材・どこにも分野が無い教材の 3 つで検索し、前 2 つで候補が出ること、
最後で分野を選ぶ出口が出ることを見ること。
