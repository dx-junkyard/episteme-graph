---
id: IK-0102
title: 完全性の判定を件数比較という代理指標で行い「完全」と誤報する
status: resolved
recorded_at: 2026-09-12
resolved_at: 2026-09-12
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 0 P0-5
  - docs/architecture/knowledge_structure_review_2026-09-12/A_fidelity.md F-17
feature_context:
  realizing: 取り込みと式抽出がひととおり済んだかを教員に示す
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [completion]
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
    処理: 判定の実装は書かれたとおり動き、比較そのものに誤りは無い（判定条件の誤りと読む余地は残る）。構造:
    判定に必要な原本のラベル一覧は前段に存在しており、表現・責務・分割・集約のいずれも変えずに判定条件だけで直せた。接続: 原本のラベル一覧という情報が判定の段まで運ばれず、期待件数という数だけが渡っていた。統制:
    「完全」の定義が件数の一致であり、何が欠けたかを突き合わせないまま完了を宣言していた。実測でラベル 29 件中 17 件が欠けたまま完全と返っていた。
generalization:
  level: general
  general_form: >-
    完了を、実体の突き合わせではなく数が揃ったという代理指標で判定するため、欠落があるまま
    完了と報告される
pattern: completion-defined-by-proxy
discovery:
  perspective: [data_inspection, invariant_audit]
  note: >-
    原本のラベル一覧と抽出済みレコードの差集合を取って 17 件の欠落（主結果の式を含む）を
    数えたうえで、判定フラグが true であることを確認した。「出所の正直さ」の原則と現況の照合。
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: >-
    判定条件に「原本ラベルとの差集合」と「頁被覆の下限」を足し、欠落と被覆不足をそれぞれ別の
    コードで報告するようにした（同じ警告文に混ぜない）。
  landed_in:
    - backend/core/document_pipeline/completeness.py
    - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 0 実装記録
related: [IK-0101]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.completion, connection.information]
    to: axes=processing=[none]; structure=[none]; connection=[information]; governance=[completion]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 主結果の関係式に対応するレコードが無く、原本の 2 割強が未取込のまま、完全性ゲートが
「complete」「sufficient」を返す。教員には「解析はひととおり済んだ」と見える。

**原因**: 判定が「抽出できた件数 ≧ 期待件数」という数の比較だけで、**何が欠けたか**を突き合わせて
いなかった。欠落は数では表れない（別の式が代わりに数を埋める）。

## 発見の観点

原本の式ラベル一覧と抽出レコードの差集合を実データで取り（`data_inspection`）、「出所の正直さ」
の原則が要求する報告と現況を照合した（`invariant_audit`）。

## 解決の観点

完了の定義を代理指標から実体の差集合へ置き換え、被覆の下限を別条件として明示した
（`explicit_contract`）。「欠落」と「被覆不足」を同じ警告に潰さないのは、読み手が次に取る行動が
違うため。

## 一般化

同じ型は「同期が済んだか」「移行が終わったか」「レビューが一巡したか」に現れる。数が揃うことは
中身が揃うことの代理でしかなく、代理で完了を宣言した瞬間に欠落は見えなくなる。
