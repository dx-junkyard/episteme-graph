---
id: IK-0114
title: 接続できなかった箇所の出典を位置で代入し、根拠の強さまで底上げしていた
status: resolved
recorded_at: 2026-09-12
resolved_at: 2026-09-12
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 0 P0-4
  - docs/architecture/knowledge_structure_review_2026-09-12/C_consumers.md C-4 / C-10
  - docs/architecture/knowledge_structure_review_2026-09-12/B_storage.md S-11
feature_context:
  realizing: コースの各見出しに、その内容の出典となる本文区画と式を添えて学習者に示す
  layers: [course_builder]
classification:
  primary: connection
  facets: [connection.meaning, local.logic]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続に失敗したときの代替として「見出しの順番と同じ位置の区画」を出典に据えたため、根拠として
    無関係な本文が添えられ、さらに後段が「実根拠あり」と読んで信頼度の段階を最上位まで上げていた。
    各段は単体では筋が通っており、前段の「これは代替である」という意味が後段に伝わらないことだけが
    原因なので接続の定義に当たる。実データで、見出しと無関係な章の抜粋が出典として出ていることを確認。
generalization:
  level: general
  general_form: >-
    対応が取れなかったときに位置や順番で代替物を埋め、代替であることが後段に伝わらないため、
    無根拠が根拠として扱われる
pattern: fallback-fabricates-missing-link
discovery:
  perspective: [data_inspection, invariant_audit]
  note: >-
    凍結済みコースの出典を実際に読み、内容と無関係な抜粋が付いていることを確かめた。「出所の
    正直さ」の原則に照らすと、欠落を埋める代替それ自体が違反であることが明確になった。
resolution:
  perspective: [fail_closed, explicit_contract]
  note: >-
    位置代入を廃止し、接続できない見出しは出典を空にして事実文で書くようにした。式も本文が
    参照するものだけに絞り、全件複製をやめた。
  landed_in:
    - backend/core/course_content_builder.py
    - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 0 実装記録
related: [IK-0113, IK-0102]
view_of: []
history: []
---

## 課題

**症状**: 学習者に見える出典が、その見出しの内容とまったく関係ない章の抜粋になる。しかも
「根拠のある教材」として信頼度の段階が最上位に表示される。凍結された教材の 85% が、
関係のない式の複製で占められる。

**原因**: 見出しと成果の接続に失敗したとき、代替として「同じ順番の位置にある区画」を出典に
据えていた。代替であることを示す印は残らないため、後段は本文があることをもって「実根拠あり」と
判定し、表示上の信頼度を底上げした。

## 発見の観点

凍結済み教材の出典を実際に読み（`data_inspection`）、「出所の正直さ」の原則と突き合わせた
（`invariant_audit`）。代替の値が下流でどう解釈されるかを辿って、信頼度の底上げに行き着いた
（`trace_walk`）。

## 解決の観点

欠落を埋めない方向に倒した（`fail_closed`）。接続できなかったことを状態として持ち、事実文で
示す形にして、後段が「本文があるかどうか」で根拠の強さを推し量れないようにした
（`explicit_contract`）。

## 一般化

対応が取れないときに「近いもの」「同じ位置のもの」「既定のもの」を代入する処理は、欠落を
不可視にするだけでなく、下流で**根拠として扱われる**点で有害になる。埋めるより、欠落を
表現できる場所を作るほうが安い。
