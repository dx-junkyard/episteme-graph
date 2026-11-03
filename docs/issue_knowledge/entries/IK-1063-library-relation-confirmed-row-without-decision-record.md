---
id: IK-1063
title: "ナレッジライブラリの関係（上位・関連・同じ・近い）は、追加の画面に理由の欄が無く、確定した行に確定者・日付・確定の理由が出ず（API の行は decided_by を持つ）、「上位: X」がどちら向きかを言わない — 部品の結び付きの行は「確定: 名前・日付」を出すのに関係の行だけ判断の記録が読めない"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "教員が概念どうしの関係を理由付きで記録し、後から別の教員が誰の判断か・どちら向きかを読む"
  layers: [concept_registry, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [review]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    統制軸 review: 関係の確定は判断だが、判断の印（人・日付・理由）が画面に出ない — 同じライブラリの同一性リンクには decision_line がある（判断の記録の描き方が面ごと）。
    接続軸 information: core/library/registry.py の関係の行は decided_by / decided_at を返し、create_relation は reason を受けるが、
    admin.js の関係の追加には理由の欄が無く（runner の注記「理由の欄が無い（reason は送っていない）」）、一覧の描画は種類・相手・出所だけ。
    処理軸 wording: 「上位: X」が「X がこの概念の上位」か逆かを言う文が無い。構造軸 none: 列はある。
generalization:
  level: repo_pattern
  general_form: "同じ画面の判断のうち一部の種類だけ、判断の印（人・日付・理由）を描く経路が作られず、記録はあるのに読めない"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    第 25 周 面 A 第 8 周 te-03 が関係「上位: TOV 方程式（静水圧平衡）」を追加して確定した（uxsim/runs/c-astro-r25-share-r8/20261027T223603Z-94618 seq14・15）。確定した行に
    確定者・日付・理由が無い。第 7 周の審判は同じ点を軽い観察として記帳しなかった（2 周目の再現）。API の行と admin.js の描画を突き合わせた。
resolution:
  perspective: [carry_through, vocabulary_table]
  note: >-
    関係の一覧・判断の応答に、見ている概念から見た種類の札（broader を object 側から見ると「下位」）・向きの一文（direction_line）・追加の行（added_line「追加: 名前・日付（理由: …）」）・判断の印（decision_line「〔確定〕 確定: 名前・日付」）・判断の理由（decision_reason_line）を添えた（routes/library._describe_relations。文言の正本は core/library/schema.py の relation_* 関数）。追加の画面に任意の理由の欄、確定に任意の理由の prompt。判断の応答は facts に decision_line を返し画面が 1 行で知らせる。
  landed_in:
    - "backend/core/library/schema.py"
    - "backend/api/routes/library.py"
    - "frontend/public/js/admin.js"
    - "docs/manual/teacher/19-admin-knowledge-library.md"
    - "backend/tests/test_share_round25_r8_fixes.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "同一性リンク・ラベルと同じ判断の記録の形に関係もそろえた。固有の差分: broader の向きは見ている側で札が変わる。"
  verification:
    methods: [guardrail]
    unverified:
      - "関係を確定 → 別の教員で開いて読む通し（第 9 周 te-03 × (c)）"
related: [IK-1050, IK-0953, IK-1024]
view_of: []
history: []
---

## 課題

症状: 関係を確定しても、行は「上位: TOV 方程式（静水圧平衡） （教員の判断） ［見送り］」だけで、誰がいつ・なぜ確定したかが読めない。向きも書かれていない。

原因: 関係の行の描画が判断の記録（decided_by・decided_at・理由）を使っていない。追加の画面が理由を取らない。

## 発見の観点

再現: 追加 → 確定 → 行を読む。経路の追跡: registry の行の列 → admin.js の関係一覧の描画。

## 解決の観点

関係の行に判断の記録と向きの一文を添え、追加に理由の欄を足した。 元の見立て: 同一性リンク・ラベルと同じ判断の記録の形にそろえる。

## 一般化

同じ画面に並ぶ判断は同じ記録の形で描く。型 rule-enforced-per-surface。確かめ方は、関係を確定 → 別の教員で開き、誰の判断かを読むこと。

## 第 9 周の再演（第 25 周・面 A 第 9 周、2026-10-31・judge_share_r9）

GONE。関係の行は「上位: TOV 方程式（静水圧平衡）」→「…がこの概念の上位です（この概念は…の下位）。」→「追加: 岡村 千尋・2026-10-27」→「〔確定〕 確定: …」→「確定の理由: …」の順。関連は「向きはありません」。残り（軽い）: ①理由の欄が入る前の旧い確定の行は理由の行が無いまま黙る（リンクの「記録なし（理由を取る前に…）」と非対称）②既にある関係を足し直すと「この関係は既に記録されています。変更はありません。」で、書いた理由は捨てられ、後から理由を足す手が無い。run: uxsim/runs/c-astro-r25-share-r9/20261031T015651Z-40470（seq3・16）。

## 第 9 波の是正（第 25 周・面 A、2026-10-31）

残り 2 点を是正。①理由の無い確定の行は「確定の理由: 記録なし（理由は書かれていません）」と言う（core/library/schema.relation_decision_reason_line・
RELATION_CONFIRMED_REASON_UNRECORDED）。②既にある関係を足し直したときの理由: 関係を足した本人が理由の空の行に書いたときだけ足す
（registry.add_relation_reason — `COALESCE(reason,'')=''` かつ `created_by` 一致の UPDATE・監査 relation_reason_add・行は 1 本のまま）。
それ以外は「書いた理由は保存していません: …」と書いた理由を返して黙って捨てない（routes/library._relation_readd_reason_fact）。
テスト backend/tests/test_share_round25_r9_fixes.py::TestRecordsNotSilent。確かめていない: te-03 × (c) の再演。

## 第 10 周の再演（第 25 周・面 A 第 10 周、2026-10-31・judge_share_r10）

残り GONE。理由の無い確定の関係に「確定の理由: 記録なし（理由は書かれていません）」、別の人が足した関係を理由付きで足し直すと「この関係は既に記録されています。関係を足した人の記録に別の人の理由は足さないため、書いた理由は保存していません: …」（理由を黙って捨てない）。run: uxsim/runs/c-astro-r25-share-r10/20261031T052922Z-65582（seq3・16）。
