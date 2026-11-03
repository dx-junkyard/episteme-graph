---
id: IK-0865
title: "Admin Copilot の能力登録簿にナレッジライブラリの概念レジストリ（同一性の候補・別名・関係・地図との対応）の項目が無く、教員の「候補が空なのはなぜか・学生にどう見えるか」に装置の参照・凍結の手順で答え、操作 KB の内部語（expected_revision・409・retired・draft・TEACHER）までそのまま言う"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/admin_assistant_design.md"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "教員がナレッジライブラリの画面から Copilot に同一性の候補の出かたと学生への見え方を尋ねる"
  layers: [admin_copilot, concept_registry, help_kb]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: backend/core/admin_assistant/capabilities.py:805-820 の knowledge-library の capability は
    library.view_and_freeze 1 件だけで、概念レジストリ（Phase 3）の 6 アンカーに対応する項目が無い。マニュアルの
    docs/manual/teacher/19-admin-knowledge-library.md#identity-candidates は回答の citations に出ている（届いている）が、
    手順の正本は capability KB が勝つ設計なので、回答は docs/admin_operations/library.md#view-and-freeze の手順になった
    （製品頭脳の所見 seq 3984 も同じ結論）。処理軸 wording: その KB 節（library.md:22-26）が `expected_revision`・409・
    `retired`・draft・「必要ロール: TEACHER」を教員向けの文に含み、LLM がそのまま言い換えた。同型の前例 IK-0530（図の
    深く検討）は capability を 1 件ずつ足して閉じたので、新しい面が増えるたびに再発している。
generalization:
  level: repo_pattern
  general_form: "画面に機能と利用者マニュアルの節が揃っても、案内役の登録簿に項目が足されず、案内役が古い隣の項目で答える"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, inventory]
  note: >-
    教員 te-01 が Copilot の回答で諦めた（gave_up）。登録簿の knowledge-library の項目と画面の UI アンカー表を突き合わせた。
    run: uxsim/runs/c-astro-r25-share-r1/20261022T123625Z-33222 seq4（製品頭脳 uxsim/runs/round25/BRAIN_NOTES_product_P0.md）。
resolution:
  perspective: [vocabulary_table, doc_correction]
  note: >-
    capabilities に案内専用の 4 項目（library.identity_candidates / labels / relations / atlas_links）を足し、locate の論理アンカーを admin.js に登録した。docs/admin_operations/library.md の KB 節から楽観ロック・HTTP 番号・状態キー・ロール英字を外し、4 項目の節を足した。ADMIN_UI_ANCHORS の各区画に capability かマニュアル節の宣言を求める全面の検査は作っていない（本面の 4 項目と KB 節の内部語の不在だけを固定）。
  landed_in:
    - "backend/core/admin_assistant/capabilities.py"
    - "frontend/public/js/admin.js"
    - "docs/admin_operations/library.md"
    - "backend/tests/test_share_round25_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "ADMIN_UI_ANCHORS 全区画の capability 網羅の検査（IK-0530 型の再発防止）は未実装"
      - "Copilot に実際に尋ねた応答の確認"
related: [IK-0530]
view_of: []
history: []
---

## 課題

症状: 候補が空の理由と学生からの見え方を尋ねたのに、装置の参照・凍結の手順が返り、内部語が混ざる。原因: 登録簿に
概念レジストリの項目が無く、手順の正本（capability KB）が隣の古い節で答える。KB 節に内部語がある。

## 発見の観点

教員ペルソナの通し受講（reproduction）。登録簿と画面アンカーの棚卸し（inventory）。

## 解決の観点

未解決。項目を足すだけでなく、画面の区画と案内の項目の対応を検査で固定する。

## 一般化

新しい区画を画面とマニュアルに足しても案内役の登録簿が追随しない型。確かめ方は、各区画について Copilot に「これは何で、
空なのはなぜか」を問い、回答の出所がその区画の節であること・内部語が無いことを見ること。

## 第 3 周の再演（第 25 周・面 A 第 3 周、2026-10-23）

残存（理由が変わった）。概念レジストリの 4 項目は登録簿（capabilities.py）に入ったが、意図の解決の語表（intent.py の _KEYWORDS）に
library.* の行が無く、KB 検索も開いているタブを見ないため、ライブラリのタブからの「これを確定したら学生に何が出る？」に
admin_operations/atlas.md#freeze（地図の凍結）が答えた。回答に「必要ロール: 教員（TEACHER）」の英字も残る（atlas.md の節）。
語表の二重化を根として IK-0921 に起票した。run: uxsim/runs/c-astro-r25-share-r3/20261023T032742Z-99528 seq14（面の審判 A10・A11）。

第 3 波で是正（2026-10-23）: 語表を登録簿（`Capability.keywords`）に移して正本を 1 つにし、library.* の項目に
同一性・確定・学生等の語を持たせた。KB 検索は開いているタブの項目を優先し、`docs/admin_operations/library.md` に
「確定すると学生に何が見えるか」の節を足した（IK-0921）。「必要ロール: 教員（TEACHER）」の英字は atlas.md の節の
書式で、今回は触っていない。
