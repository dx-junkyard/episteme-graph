---
id: IK-0962
title: "関係（辺）の候補を採用しても候補の出所（説明文の近さ・論文の共起）が判断に記帳されず、反映の確認と凍結の影響が「採用の根拠の記録はありません」と言う — 同じ組を一覧は「プロトタイプ近傍」と出所付きで出す（API は出所を受け取れるが、唯一の呼び出し元の画面が送らない）"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_relation_edges_design.md"
feature_context:
  realizing: "教員が他の教員の採用した関係を、何を根拠にした採用かを読んだうえで次版の下書きへ反映し凍結する"
  layers: [relation_edges_re, field_atlas_s, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract, information]
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
    接続軸 contract: routes/atlas_edges.py の decide は optional な origin を受けて mapping_justification に写し
    （_JUSTIFICATION_BY_ORIGIN）、反映の確認と凍結の影響は edge_store.origins_for_decisions でその列だけを読む
    （IK-0939 の是正）。しかし admin.js edgeDecide は body に edge_key / action / review_note / kind しか入れず origin を
    送らない（ハーネスの edges.accept も同じ形）。受け手だけ足して送り手を足していない。接続軸 information: 候補の
    origins は導出時に一覧へ出ているのに、判断の時点で記帳されず確定の手前の 2 画面に運ばれない。処理軸 none（計算は
    正しい）。構造軸 none（列はある）。統制軸 none（ゲートは正しく止める）。
generalization:
  level: repo_pattern
  general_form: "後段の画面のために受け手の API に任意の欄を足したが、唯一の送り手が欄を送らず、後段の画面が『記録なし』と言い続ける"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, trace_walk]
  note: "新任の教員が自分で採用した組の反映の確認を開き、一覧では『プロトタイプ近傍・論文の配置の根拠はありません』と出ていたのに確認では『採用の根拠の記録はありません』と言われた。admin.js の decide の body と routes の origin を突き合わせた。run: uxsim/runs/c-astro-r25-atlas-r4/20261024T233034Z-8618 seq 6、20261024T233234Z-25818 seq 10 / 13。"
resolution:
  perspective: [carry_through]
  note: >-
    送り手と受け手を揃えた。admin.js の関係の採用（edgeDecide）が候補の出所を origin として送る（論文の共起を優先）。記録の無い過去の採用は、反映の確認・凍結の止め理由で routes/atlas_edges.derived_origins_for_unrecorded → core/atlas_edges/derive.derived_origins_for_keys が**いま**導出される組だけ出所を引き、PENDING_EDGE_BASIS_DERIVED_TEMPLATE「採用の時点の根拠は記録されていません。いまの導出では: …」と時点を明示して添える（導出されない組は従来の「記録はありません」のまま・推測で埋めない・embedding を呼ばない）。
  landed_in:
    - "frontend/public/js/admin.js"
    - "backend/api/routes/atlas_edges.py"
    - "backend/api/routes/atlas.py"
    - "backend/core/atlas_edges/derive.py"
    - "backend/core/atlas_review_display.py"
    - "backend/tests/test_atlas_review_round25_r4.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実機で採用 → 判断行の mapping_justification を読む 1 操作は確かめていない（砂場・実 DB 不使用）"
related: [IK-0939, IK-0887]
view_of: []
history: []
---

## 課題

症状: 教員が関係を採用すると、一覧には「プロトタイプ近傍」「説明文どうしが近いという推定です。論文の配置の根拠はありません」と
出所が出る。ところが同じ組の反映の確認と凍結の影響は「採用の根拠（論文の配置か説明文の近さか）の記録はありません」と言う。
新任は「一覧と確認で言うことが違う」と読み、凍結を引き受ける教員は他人の採用の根拠を確定の手前で読めない。

原因: 採用 API は出所（origin）を受け取って判断行に記帳できるが、管理画面の採用操作がそれを送らない。

## 発見の観点

通し受講（reproduction）で教員 2 名が同じ食い違いを記した。送り手（admin.js）と受け手（routes）の欄を辿った（trace_walk）。

## 解決の観点

第 4 波（2026-10-25）で解決。送り手と受け手を揃えた。

## 一般化

受け手に欄を足す是正は、送り手が同じ変更で欄を送るまで効かない。確かめ方: 欄を足した API の呼び出し元を全部挙げ、
実際のリクエストの本文にその欄があることを見る（画面からの 1 操作の後に判断行を読む）。

第 5 周の再演（2026-10-27・campaign c-astro-r25-atlas-r5）: 解消を確認。第 1 周に記帳の無いまま採用された「ブラックホール — 重力波」の反映の確認が「採用の時点の根拠は記録されていません。いまの導出では: 両方の概念の説明文…どうしが近いという推定です」と記録の無さと現在の導出を言い分け、一覧の「プロトタイプ近傍」と食い違わない。te-05 はこの一文を読んで他教員の採用を反映し、凍結 2026.2 を成立させた。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T070623Z-23509 seq 10 / 11。
