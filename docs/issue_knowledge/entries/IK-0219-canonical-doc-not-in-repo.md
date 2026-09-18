---
id: IK-0219
title: 正本と名指しされた文書がリポジトリに存在しないまま、番号付きで参照され続ける
status: resolved
recorded_at: 2026-08-13
resolved_at: 2026-08-14
sources:
  - docs/architecture/doc_review_findings_2026-08-13.md §1
  - docs/architecture/doc_review_findings_2026-08-13.md §6
  - docs/development_checklist.md §5-5
feature_context:
  realizing: 設計判断の根拠を、後から誰でも辿れる形で残す
  layers: [docs]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [none]
    governance: [review]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    参照している各文書の記述は一貫しており、参照の書き方にも誤りは無い。原因は、正本と
    呼ぶための条件（版として残っていること）が取り決められておらず、参照先が実在するかを
    確かめる手続も無いこと。処理も表現も正しいのに、何をもって正本とするかの決めごとが
    欠けているので統制とする。
generalization:
  level: general
  general_form: >-
    正本と名指しされた対象が実在しないまま参照され続け、根拠を辿れない参照だけが残る
discovery:
  perspective: [doc_code_diff, inventory]
  note: >-
    参照されている文書名を全て集め、実ファイルと突き合わせた。八つ以上の文書が節番号付きで
    参照している「仕様の正本」が、全履歴を探しても一度も版に入っていないことが分かった。
    同型の参照が他にもあり、番号のずれによるリンク切れも同じ検査で見つかった。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    実装と既存の文書から逆に組み立てた再構成版を作り、参照側を差し替えた（旧い節番号との
    対応は保証しない、という但し書き付きで正直に残した）。取り決めとして「正本と呼ぶ文書は
    版に入っていること」を置き、参照先の実在を機械検査に入れた。原本が見つからないものは
    見つからないまま記録している。
  landed_in:
    - docs/features/field_atlas_overlay_spec.md
    - docs/development_checklist.md §5-5
    - backend/tests/test_docs_registry_guardrails.py
related: [IK-0221]
view_of: []
pattern: referenced-source-does-not-exist
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.review, structure.aggregation]
    to: axes=processing=[none]; structure=[aggregation]; connection=[none]; governance=[review]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 地図機能の「仕様の正本」として、八つ以上の文書が節番号付きで参照している文書が、
リポジトリに一度も存在したことがない。設計判断の根拠（層の構造・上限値・導線の抑制規則）を
辿ろうとすると、参照が全て宙に浮く。同型の参照が他にもあり、一次資料が失われているものも
あった。

**原因**: 検討の場が版の外（一時的な場所）にあり、そこで書かれた仕様が版に入らないまま
「正本」として参照され続けた。正本と呼ぶ条件も、参照先が実在するかを確かめる手続も無かった。

## 発見の観点

`doc_code_diff` と `inventory`。参照されている名前を集めて実ファイルと突き合わせるという
機械的な照合でしか見つからない。参照している側の文書はどれも自然に読めるため、読むだけでは
気づけない。

## 解決の観点

`canonical_source`（実装と周辺文書から逆に組み立てた再構成版を版に入れ、参照を差し替える）+
`guardrail_fix`（参照先の実在を検査に入れる）。参照側の差し替えは同じ変更で行った。再構成版には「旧い節番号との
対応は保証しない」と明記し、失われたものを取り戻したかのようには書いていない。原本が
見つからないものは見つからないまま記録している。

## 一般化

設計の検討を版の外で行う限り、どのプロジェクトでも起きる。参照している側が増えるほど
発覚が遅れ、発覚時には根拠そのものが失われている。辞書に同型が無いため新しい型として提案する
（`referenced-source-does-not-exist`）。
