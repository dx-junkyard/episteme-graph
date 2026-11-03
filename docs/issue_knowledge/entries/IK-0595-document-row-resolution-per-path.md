---
id: IK-0595
title: "「どの documents 行か」（documents.id と source_path の両形）の解決が経路ごとに別実装で、id 優先の規則・コース sources の UUID 形の扱いが食い違う"
status: resolved
recorded_at: 2026-10-02
resolved_at: 2026-10-02
sources:
  - docs/architecture/root_cause_consolidation_2026-09-30.md §5a
feature_context:
  realizing: "教材 ref（documents.id = UUID / source_path = material_id）を受ける全経路で、同じ ref を同じ教材行に着地させる"
  layers: [shared_infra, auth_visibility, paper_discovery, guidance_g, status_notification]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: IK-0550 の是正後も `id::text = :ref OR source_path = :ref` 型の両形照合が正本 2 ファイルの外に 6 ファイル
    （`routes/admin.py::get_material` / `core/status/projector.py` / `core/paper_discovery/{radar,corpus}.py` /
    `core/admin_assistant/next_steps.py` / `core/doubt/seminar_brief.py`）残り、legacy allowlist で黙認されていた。
    単数版は `LIMIT 1` だけで「id 一致を優先」の規則を持たず、get_material は逆に `ORDER BY (d.source_path = :ref) DESC`
    で source_path を優先していた。加えて source_path 形だけで引く経路（教材の公開範囲更新・削除・原稿スタジオの
    パイプライン実行・構造再解析・descent・分野コーパス・コース経由の可視性・教材削除時の巻き添えコース）は、
    コースの `sources[].material_id` に documents.id（UUID）形が入る場合に教材を見落とした。構造軸 aggregation =
    同じ解決が経路ごとに別実装で分散し正本が一本化されていない / 接続軸 target = 経路によって同じ ref が別の行または
    無しに着地する。判定式単体の誤りではなく規則の置き場所の問題なので処理軸は none。順序・予算・判断の割り当てに
    関わらないので統制軸は none。
generalization:
  level: repo_pattern
  general_form: "2 つの ID 体系の突き合わせを正本に寄せたあとも、旧来の経路が独自の SQL で突き合わせを続け、規則（優先順位・両形の受理）が経路ごとにずれる"
pattern: id-namespace-conflated
discovery:
  perspective: [inventory]
  note: "`grep source_path` で api/ と core/ の SQL を全件列挙し、比較している形（UUID のみ・material_id のみ・両形）と権限ゲートの有無を表にした。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    core/document_refs に 1 SQL の解決関数を足し（列を選べる `resolve_document_rows` / 単数 `resolve_document_row` /
    一覧向けに同じ source_path の行を落とさない `list_documents_for_refs` / `canonical_document_ids`。列は許可表のみ・
    同じ source_path の行は新しい方を採る）、両形照合の 6 ファイルと material_id 形だけの経路をそこへ寄せた。
    権限ゲートは各経路のまま（解決した documents.id 1 行に対して所有者・可視性条件を掛ける）。コース経由の可視性
    （services.list_visible_document_ids と教材一覧・詳細の共通句）と教材削除の巻き添えコースは両形を当てる。
    既存ガードレールの legacy allowlist を空にし、material_id 形だけの照合を (ファイル, 関数) の allowlist（理由付き）
    以外に書けない検査を足した。learning.py の `_ordered_source_document_ids`（コースビルダーの候補表）も同日に
    `resolve_document_refs` へ寄せ、allowlist の「未是正」行を消した。
  landed_in:
    - backend/core/document_refs.py
    - backend/api/routes/admin.py
    - backend/api/routes/lecture_studio/pipeline.py
    - backend/api/services.py
    - backend/core/status/projector.py
    - backend/core/paper_discovery/radar.py
    - backend/core/paper_discovery/corpus.py
    - backend/core/admin_assistant/next_steps.py
    - backend/core/doubt/seminar_brief.py
    - backend/core/descent/resolve.py
    - backend/core/versioning/deletion.py
    - backend/api/routes/learning.py
    - backend/tests/test_document_id_resolution_guardrails.py
    - backend/tests/test_document_refs.py
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB での確認（UUID 形の sources を持つコースの教材一覧・削除・構造再解析）"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "対象の選択の派生形（解決関数 1 つ + 衝突は id 優先）。権限ゲートは各経路に残し、解決だけを寄せた。"
related: [IK-0550, IK-0580]
view_of: [IK-0550]
history: []
---

## 課題

教材には `documents.id`（UUID）と `documents.source_path`（material_id）の 2 つの ID がある。IK-0550 で両形の判別を
`services._resolve_document` と `core/document_refs` に寄せたが、それ以前からの 6 ファイルが独自の SQL で両形を
突き合わせ続け（id 一致の優先規則を持たない・get_material は逆に source_path を優先）、material_id 形だけで引く経路は
コースの `sources[].material_id` が UUID 形のとき教材を見落としていた。

## 発見の観点

`source_path` を含む SQL を api/ と core/ で全件列挙し、比較している形・権限ゲートの有無・置き換え先を表にした。

## 解決の観点

「どの documents 行か」の解決を core/document_refs の 1 SQL に寄せ、権限は解決した行に対して各経路が掛ける。
両形の照合と material_id 形だけの照合の両方を、正本と理由付きの allowlist 以外に書けないことを構造検査で固定した。

## 一般化

2 つの ID 体系の突き合わせを正本に寄せたあとも、旧来の経路が独自の SQL で突き合わせを続け、規則が経路ごとにずれる。
