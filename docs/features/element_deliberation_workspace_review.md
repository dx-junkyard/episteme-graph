# W層（Element Deliberation Workspace）実装レビュー

対象仕様: `docs/features/element_deliberation_workspace_design.md`  
レビュー対象: 作業ツリー上の W層 Phase 0 / W-β 実装（2026-07-15）

## 指摘事項

### [P1] 数式の同一性リンクが document_id で区別されず、別論文のリンクを衝突・漏えいさせる

仕様 §2 は `equation` の ElementRef を **`document_id + equations.json 内 equation_id`** で一意化すると定めている。しかし migration の一意制約は `instance_document_id` を含まず、`instance_element_type, instance_element_id, shared_part_id` のみである。そのため、複数の論文で一般的な `equation_id`（例: `eq_1`）を使うと、同じ共通部品へのリンクを別インスタンスとして保存できない。

- [048_element_identity_links.sql](/Users/urakawashinichi/IdeaProjects/dx-junkyard/episteme-graph/backend/db/048_element_identity_links.sql:56) の `UNIQUE` に `instance_document_id` がない。
- [identity_links.py](/Users/urakawashinichi/IdeaProjects/dx-junkyard/episteme-graph/backend/core/deliberation/identity_links.py:138) はこの制約の衝突時に既存行を返すため、論文 B からの候補作成が論文 A の候補を返す。
- [identity_links.py](/Users/urakawashinichi/IdeaProjects/dx-junkyard/episteme-graph/backend/core/deliberation/identity_links.py:239) の一覧取得も `element_type + element_id` のみで検索する。したがって、論文 B の overview から `eq_1` のリンク一覧を開くと論文 A のリンクも返り、呼出側の document 閲覧ゲートをすり抜ける。

`instance_document_id` を一意制約と instance 一覧の検索条件の両方に含め、API から解決済み `ref.document_id` を渡す必要がある。既存 DB 向けには、新しい migration で旧制約を置換する必要がある。

### [P1] domain-scoped の取得 API が由来 document の権限を確認せず、保護対象の情報を返す

W5 は、`library_entry` の本文は教員全体に開示してよい一方で、例示画像は由来 document の権限を継承し、非所有者には 403 とするよう求めている。しかし shared_part の overview は `_require_teacher` だけで通し、凍結版スナップショット全体を返す。このスナップショットには `exemplar_images`（`source_document_id`、`figure_id`、`minio_key` を含む）がそのまま入る。

- [deliberation.py](/Users/urakawashinichi/IdeaProjects/dx-junkyard/episteme-graph/backend/api/routes/deliberation.py:73) は shared_part に document 単位のゲートを適用しない。
- [decomposition.py](/Users/urakawashinichi/IdeaProjects/dx-junkyard/episteme-graph/backend/core/deliberation/decomposition.py:273) は `library_entry_versions.content` を `frozen_content` として応答にそのまま入れる。
- L層では `exemplar_images` に由来 document とオブジェクトキーを保存することが明示されている（[library.py](/Users/urakawashinichi/IdeaProjects/dx-junkyard/episteme-graph/backend/api/routes/library.py:110)）。

加えて、`GET /shared-parts/{id}/identity-links` は全リンクの `instance_document_id`、ローカル表記、evidence を返すが、各インスタンス document の可視性を検査していない（[deliberation.py](/Users/urakawashinichi/IdeaProjects/dx-junkyard/episteme-graph/backend/api/routes/deliberation.py:262)）。共通部品の本文開示を根拠に、由来論文の情報まで無条件に開示している。

共有可能な本文フィールドと document 保護対象フィールドを分離し、例示画像・由来 document を含む同一性リンクは、各 document の閲覧権限でフィルタするか、対象を要求した利用者に 403 を返すべきである。

### [P2] C層レンズが citation を集約しておらず、§4.4 の要求を満たさない

仕様 §4.4 は C層について「誰が承認・**引用**したか」を `component_endorsements` と `component_citations` から集約して表示するよう求めている。実装は `component_explanations` と endorsement summary のみを読み、citation テーブルを照会していない。

- [positioning.py](/Users/urakawashinichi/IdeaProjects/dx-junkyard/episteme-graph/backend/core/deliberation/positioning.py:565)
- C層の既存一覧は citation 数を集約している（[theory_components.py](/Users/urakawashinichi/IdeaProjects/dx-junkyard/episteme-graph/backend/api/routes/theory_components.py:2813)）。

引用の有無を生件数ではなく段階ラベルで返す形で、既存 C層と同じ citation 集約を位置づけレンズに加える必要がある。

### [P2] 4要素型すべてへの入口というフロント要件が未達で、仕様本文と状態説明も矛盾している

§1 / §9 は `figure`、`theory_component`、`theory_claim`、`equation` の4要素型を一つのワークスペースで扱い、各詳細画面から「深く検討」へ入ることを求めている。現状のフロント導線は図と revisions 画面の数式のみで、component / claim には導線がない。

- 図の導線: [admin.js](/Users/urakawashinichi/IdeaProjects/dx-junkyard/episteme-graph/frontend/public/js/admin.js:1018)
- 数式の導線: [admin.js](/Users/urakawashinichi/IdeaProjects/dx-junkyard/episteme-graph/frontend/public/js/admin.js:6801)
- 同ファイルは component / claim の revisions 上の `entity_id` が DB UUID ではないため、意図的に見送ったとしている（[admin.js](/Users/urakawashinichi/IdeaProjects/dx-junkyard/episteme-graph/frontend/public/js/admin.js:6801)）。

この制限は設計書冒頭の状態説明にも書かれているが、§9 の「各要素」要件は更新されていない。component / claim の DB ID を得られる既存詳細画面に導線を追加するか、Phase 0 の受け入れ範囲を図・数式に限定するよう仕様を明確化すべきである。

## 確認結果

- W-β の candidate → confirmed / rejected の状態遷移、`decided_by` 必須、監査カタログ追加、削除 API を作らない方針は、確認した実装と整合している。
- `confidence` の API 応答は段階ラベルへ変換されており、W8 に整合している。
- 関連テストを実行し、`116 passed` を確認した。ただし上記の数式スコープ衝突、domain-scoped 取得時の document 権限、citation 集約、および4要素型導線を検証するテストは含まれていない。

