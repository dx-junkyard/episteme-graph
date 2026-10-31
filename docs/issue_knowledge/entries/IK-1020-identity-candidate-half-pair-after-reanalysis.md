---
id: IK-1020
title: "再解析で部品が superseded になると、同一性の候補のうちその部品側のリンクは「いま参照できない要素」として隠され、ハブのエントリには相手の版のリンク 1 本だけが残る — 対の相手の分からない半分の候補が「別の論文の記述と本文の意味が近い」と言い、旧規則の注記（stale_rule_note）も見えるリンクだけで判定するので出ない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "同じ論文の 2 版の部品の同一性の候補を、再解析の後も対として読んで確認・見送りする"
  layers: [concept_registry, knowledge_objects, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [version]
    governance: [resume]
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
    接続軸 version: 候補の対は「ハブのエントリ ⇄ 部品」のリンク 2 本で表され、片側（TeX 版）のリンクが指す部品が再解析で superseded になると routes/library.py list_identity_candidates の _link_resolves_to_live_component がそのリンクを落とす（hidden_unresolved）。残る PDF 版のリンクは旧規則（chunk-proxy・vector_similarity）で立った時の reason「別の論文の記述と本文の意味が近いと測定されました」を保存値のまま出し、_is_stale_same_paper_proxy は visible なリンクの document が 2 つ以上のときだけ真になるので、片側が隠れると旧規則の注記が付かない（library.py:1552-1573・実測 te-03 seq2 の 1〜9・11 番）。構造軸 representation: 対の同一性を表す受け皿がリンクの 2 本組で、片側の版が消えたことを対として表せない。統制軸 resume: 再解析（再実行）が前回の候補の対を引き継がず、片側だけを残す。処理軸 none: 各処理は自分の規則どおりに動く。
generalization:
  level: repo_pattern
  general_form: "2 つの対象の対をハブ経由の 2 本の結び付きで表し、片側の対象が再生成で消えると残った 1 本が相手の分からない半端な記録になり、対を前提にした注記も消える"
pattern: reexecution-overwrites-human-decision
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    第 25 周 第 6 周 te-03 (a) の同一性の候補 11 件のうち 10 件がリンク 1 本だけで「別の論文」と言った（uxsim/runs/c-astro-r25-share-r6/20261027T122225Z-3503 seq2）。candidate の一覧の組み立てを辿り、隠されるリンクと stale_rule_note の判定の材料が同じ visible 集合であることを確かめた。
resolution:
  perspective: [explicit_contract, single_point_fix]
  note: >-
    片側の部品が superseded でリンクが一覧から隠れた同一性の候補は、残ったリンクに保存値の「別の論文の記述と…」を言わせず PARTNER_REPLACED_REASON（組になっていた版の部品は解析のやり直しで置き換わりました）に差し替え、元の文は recorded_reason に残す。エントリのカードには partner_replaced_note を出す。旧規則の注記（stale_rule_note）の判定は隠したリンクも含めたリンク全体（visible + unresolved）で行う。ライブラリの一覧（routes/library.py）と W層の共通部品の一覧（routes/deliberation.py）の両方が同じ定数を使う。付け替えが決まった組は IK-1012 の remap でリンクが新部品へ移るので、そもそも半端にならない。
  landed_in:
    - "backend/core/deliberation/identity_links.py"
    - "backend/api/routes/library.py"
    - "backend/api/routes/deliberation.py"
    - "frontend/public/js/admin.js"
    - "docs/features/concept_registry_design.md"
    - "backend/tests/test_share_round25_r6_fixes.py"
  principles:
    - principle: sync-by-stable-key-and-protect-human-decisions
      use: consulted
      note: "判断前の候補を半端なまま「別の論文」と言わせず、置き換わった事実を言う形に揃えた。"
  verification:
    methods: [guardrail]
    unverified:
      - "段の再実行で古い対の候補を作り直す（superseded にする）処理は入れていない"
      - "実画面での片側が置き換わった候補の表示（第 7 周 te-05）"
related: [IK-1012, IK-0980, IK-0981, IK-0919]
view_of: []
history: []
---

## 課題

症状: 再解析後の同一性の候補一覧で、11 件のうち 10 件が PDF 版の部品へのリンク 1 本だけを持ち、相手（TeX 版の部品）が画面に無い。PDF 版のリンクには「（意味の近さ）」「別の論文の記述と本文の意味が近いと測定されました」— 実際は同じ論文の 2 版で、第 5 波で入れた旧規則の注記（同じ論文の別の版どうしを文の近さで結んだ候補です）も出ない。一覧の先頭には「解析がやり直されたなどの理由で、いま参照できない要素への候補は表示していません。」が 1 行あるだけで、どのカードの何が隠れたかは読めない。

原因: 対の同一性はハブのエントリへのリンク 2 本で表される。再解析で TeX 版の部品の名前が変わり superseded になると（IK-1012）、そのリンクは一覧から落とされ、残るのは PDF 版の 1 本。旧規則の注記の判定（_is_stale_same_paper_proxy）は見えるリンクの document が 2 つ以上のときだけ働くので、片側が隠れた瞬間に注記が消える。

## 発見の観点

再現（reproduction）: 再解析した snapshot r25e で te-03 が候補一覧を開いた。経路の追跡（trace_walk）: routes/library.py の visible / hidden_unresolved と stale 判定の入力を突き合わせた。

## 解決の観点

是正済み（第 25 周・面 A 第 6 波・2026-10-27）。

旧案: 片側のリンクが解決できない対は「同じ論文の別の版の部品と組になっていた候補です。その版の部品は解析のやり直しで置き換わりました」と事実文で出し、旧規則の注記は隠したリンクも含めて判定する。再解析のときに旧候補を作り直す（段の再実行で古い対を superseded にする）かは IK-1012 と同じ判断。

## 一般化

対（ペア）の判断を、ハブ経由の 2 本の結び付きに分解して保存する設計では、片側の対象が版の更新で消えたときに残る 1 本の意味が変わる。対を前提にした注記・判定（同じ論文か、旧規則か）は、見えている片側だけで計算すると消える。型 reexecution-overwrites-human-decision（候補の記録が版の更新で半端に残る面）。

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演: 候補の行は GONE。片側が置き換わった 10 件のカードに partner_replaced_note と旧規則の注記（stale_rule_note）が出て、残ったリンクは「組になっていた相手の部品は、解析のやり直しで置き換わりました…」と言う（uxsim/runs/c-astro-r25-share-r7/20261027T171357Z-51091 seq2）。判断した後の行は、保存値の「別の論文の記述と本文の意味が近いと測定されました」に戻り札も英語「[rejected]」になる → NEW IK-1050（差し替えが candidate の行だけに掛かる）。
