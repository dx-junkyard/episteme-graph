# 知識の転用層（Knowledge Transfer — 束の往復・RAG への構造 1 hop・参照の健全性・版の語彙・D/C層の表現語彙）

> **状態: 実装済み（正本・凍結）**（2026-09-13 起票・同日実装。migration は **083** `doubt_citation_vocab`（P4-5 のみ）。
> 実装記録は §14。以後は §14 の追記のみ）。
> 親文書は [知識構造の見直し提案 2026-09-12](../architecture/knowledge_structure_review_2026-09-12.md)
> の **Phase 4**（§4 P4-1〜P4-5）。前提は Phase 1 = [知識オブジェクト層](knowledge_objects_design.md)（KO1〜KO10・stable_key）、
> Phase 2 = [学ぶ単位の一級化](learning_units_design.md)、Phase 3 = [概念レジストリ](concept_registry_design.md)。判断材料は付属調査
> [B 格納構造](../architecture/knowledge_structure_review_2026-09-12/B_storage.md)（S-16）/
> [C 下流消費](../architecture/knowledge_structure_review_2026-09-12/C_consumers.md)（C-5・C-14）/
> [D 外部標準](../architecture/knowledge_structure_review_2026-09-12/D_standards.md)（X-5〜X-7・X-12・X-13・X-16）。

**本書で固定した判断（オーナー判断を要する 3 種＝不変条項の解釈変更・人の権利や制度・後戻りしにくい構造＝には該当しないと判断し、
推奨で固定した。撤回可）**

| # | 判断 | 採用 | 根拠 |
|---|---|---|---|
| T-1 | **取り込んだ知識行の確定状態** | 束の `review_status` は**引き継がない**。取り込み行は常に `teacher_review_required` / `candidate` 始まりで、束の値は `agent_payload.import.source_review_status` に事実として残す | 承認は制度（誰が・どの権限で・どの手続で）に属し、他インスタンスの承認をこのインスタンスの承認として扱うことは原則1（確定は人間・再構成可能な手続にのみ）の外。値は落とさない（原則3） |
| T-2 | **live 行のある document への取り込み** | 既定は **409**（事実文）。`replace=true` を教員が明示したときだけ Phase 1 と同じ supersede 同期で置き換える（人間が触った行は `sync_live_rows` の保護列のまま） | 再解析と同じ意味論に載せる。無言で上書きしない（原則8） |
| T-3 | **参照の健全性の見せ方** | 教材行には**状態の事実文 1 行**（問題なし / 切れがある / 未確認）だけ。切れている参照の**列挙**は詳細モーダル（教員向け・運用情報）。件数バッジは作らない | 原則4（数値の用途と粒度）。ID の破断は運用上の事実であり、列挙は再構成に必要 |

**正本**: 本ドキュメント。**関連**: [知識オブジェクト層](knowledge_objects_design.md) / [画面文脈アダプター（SA層）](assistant_screen_adapter_design.md) §11 /
[共有物のバージョン管理（V層）](shared_versioning_design.md) / [D層 issue 分割](doubt_layer_issues.md) / [賭け金の台帳（SL層）](stakes_ledger_design.md) /
[承認・共有レイヤー（C層）](../architecture/layer_registry.md) / [確定文脈の記帳](decision_context_design.md) / [段階ラベル・共有語彙表](label_vocab_design.md) /
[レイヤー索引表](../architecture/layer_registry.md) §4（版の語彙）。

---

## 1. 目的 — 構造化の投資が「外」と「対話」に還っていない

親文書 §4 Phase 4 の 5 件はどれも**新しい知識を作らず、既にある知識を別の場所へ運ぶ**:

- **S-16 / C-14**: export bundle は artifact-first で ID 正規化（`_normalize_export_references`）と検証（`check_refs`）を持つが、
  **import が無い**（片道）。`check_refs` は ID 破断を検出できる唯一の仕組みだが束を作ったときにしか走らない。
- **X-13 / X-16**: 束は独自 JSON で、外部ツールが読める語彙（JSON-LD `@context`）が無い。
- **C-5**: RAG は `chunks` しか読まない。claim / 理論操作グラフ / 導出鎖 / 記号は**学習者の対話に一度も現れない**。
- **X-12**: 「版」を担う構造が V層 / atlas 骨格 / L層凍結版 / 知識行 supersede / 同一性リンクに散り、revision（改訂）と
  alternate（同じものの別表現）が語彙として区別されていない。
- **X-5 / X-6 / X-7**: D層・C層に「根拠の線（SEPIO）」「疑義の向き（undercut）」「引用の意図（CiTO）」を書く器が無い。

本層は 5 件を **A層非改変・LLM 0 回・確定は人間・数値非表示**で積む。新しい格納庫は作らず、既存の束・既存の解決器登録・既存の
run 記録・既存 3 表への列追加で済ませる。

## 2. 不変条項（KT1〜KT8）

| # | 条項 | 具体 |
|---|---|---|
| KT1 | **A層非改変** | `src/episteme_graph/agents/` を触らない。import は agent 結果を再構成せず、Phase 1 の `sync_live_rows` に**行として**流す |
| KT2 | **取り込みは候補・確定は人間**（T-1） | 取り込み行は `review_status='teacher_review_required'`・component `status='candidate'`。束の承認状態は payload に事実として残すだけ。取り込みで `theory_review_events` に承認系の行を作らない |
| KT3 | **決定論・LLM 0 回・embedding 0 回** | import / 構造 1 hop / 健全性 / 版の語彙はすべて DB 読みと純関数。`core/knowledge_import/` `core/reference_health.py` `resolvers/learning.py` は `core.llm` を import しない |
| KT4 | **stable_key は取り込み先で再計算する** | stable_key は `document_id` を材料に含むので、束の値をそのまま使うと別 document では一致しない。取り込み側は Phase 1 と同じ `core/knowledge_objects/stable_key.py` の関数で**再計算**し、束の値は `agent_payload.import.source_stable_key` に残す（同じ論文を別の UUID で持つインスタンス間の往復で、再取り込みが supersede 同期として働く） |
| KT5 | **情報を落とさない** | import は DELETE を書かない（`theory_component_links` の派生再作成という Phase 1 の明示例外だけ継承）。健全性の結果は「解決済みフラグ」ではなく検査時点の事実 |
| KT6 | **権限 fail-closed** | export は閲覧、import は **編集**（`resolve_document_access.can_edit`）。健全性は閲覧。RAG の 1 hop は当該ターンの `allowed_document_ids` を `ANY(:doc_ids)` で強制し、discuss の `all_visible` でも構造側で広げない（範囲は検索と同一） |
| KT7 | **数値・内部 ID を学習者に見せない** | 1 hop の事実文に cosine / confidence / 件数 / `eq_op_*` 等を載せない（`learner_context_common.contains_internal_id` で遮断）。教員向け健全性も件数バッジを作らない（T-3） |
| KT8 | **監査必須・帰属は台帳** | import は `AUDIT_ENTITY_IMPORT`（entity_id = document_id・metadata に export_id / 出所 document / 件数 / 束の sha256）+ `record_knowledge_audit`（sync 統計）。束の中に取り込んだ人は書かない（export と同じ） |

## 3. 全体像

```
[P4-1]  export bundle ─┬─ ro-crate-metadata.json（JSON-LD・RO-Crate 1.1・PROV 語彙）      ← X-13 / X-16
                       ├─ 各項目に stable_key / knowledge_object_id（live 行との対応）      ← KO 前提
                       └─ import ──(検証 check_refs → dry-run 事実 → 教員確定)──▶ sync_live_rows  ← S-16 / C-14
[P4-2]  学習チャット: chunk 近傍 → theory_claims_live(chunk_id) → graph main node(linked_claim_ids)
        = SA層 kind "retrieved_structure"（決定論・LLM 回数不変）                         ← C-5
[P4-3]  core/reference_health.py: live 行の参照（graph→claim / component→claim・equation / claim→chunk / unit→*）
        を検査 → 解析完了時に run へ事実を残す + 教材行の事実文 + 詳細 API                  ← C-14
[P4-4]  layer_registry §4「版の語彙」: revision（wasRevisionOf）と alternate（alternateOf）を
        全「版」構造に割り当てる宣言（コード変更 0・export の @context が同じ語を使う）    ← X-12
[P4-5]  migration 083: challenges.challenge_mode / target_element_ref、
        epistemic_ledger.evidence_lines、component_citations.citation_intent                ← X-5 / X-6 / X-7
```

## 4. P4-1 束の往復（export JSON-LD + import）

### 4.1 export への追加（既存ファイル・キーは不変・additive）

- **`ro-crate-metadata.json`** を束のルートに追加（RO-Crate 1.1 の形）。`@context` は
  `https://w3id.org/ro/crate/1.1/context` + ローカル語 `{"episteme": "https://episteme-graph.local/vocab#", "prov": "http://www.w3.org/ns/prov#"}`。
  `@graph` は ①メタデータ記述子 ②ルート `Dataset`（`datePublished` / `hasPart` / `conformsTo` = manifest の `export_schema_version`）
  ③束の各ファイルを `File` として（`encodingFormat` / `episteme:schemaVersion` / `episteme:artifact`）④解析 run を `prov:Activity`
  （`prov:used` = document、`prov:generated` = 束）。**人（生成者・取り込んだ人）は書かない**（export と同じ: 帰属は監査台帳）。
  数値スコアは載せない。
- **`stable_key` と `knowledge_object_id`** を claims / components / equations / evidence / derivation chain step の各項目に付ける
  （live 行を `agent_claim_id` / `agent_component_id` / `agent_equation_id` / `agent_evidence_id` / `agent_step_id` と `legacy_ids`
  で join。行が無ければキーを**付けない**＝事実。DB を読めない環境（テスト・fallback）でも export は落ちない）。
- **manifest** `export_schema_version` は `0.2.0` → **`0.3.0`**（additive だが import の前提になるキーが増えたので minor を上げる。
  既存テストの固定値は本書に従って更新）。`manifest.jsonld = {"metadata_file": "ro-crate-metadata.json", "context": [...]}`、
  `manifest.import_support = {"min_schema_version": "0.3.0", "stable_key_recomputed_on_import": true}` を足す。
- README（束内）に「取り込み」の節を 1 段落足す（`POST /api/documents/{id}/import-bundle`・T-1・T-2 の事実）。

### 4.2 import（`POST /api/documents/{document_id}/import-bundle`、`routes/export.py` に同居）

| 項目 | 内容 |
|---|---|
| 入力 | multipart `bundle`（zip・上限 50MB）+ query `dry_run`（既定 true）/ `replace`（既定 false） |
| 権限 | `_require_teacher` + `resolve_document_access(user, document_id).can_edit`（不在・権限なしは 404 に畳む） |
| 検証（422） | manifest 欠落・`export_schema_version < 0.3.0`・必須ファイル欠落・JSON 不正・`_validate_export_references` の **errors 非空**（束の内部参照が切れている）。detail は日本語の事実文 + `report`（errors / warnings をそのまま） |
| dry-run（200） | `{dry_run: true, source: {export_id, object_type, object_id, document_ids, exported_at, app}, counts: {claims, components, equations, evidence, derivation_steps, graph_nodes}, target: {document_id, has_live_rows}, would_supersede: bool, facts: [...]}`。**書き込み 0** |
| 実行（200） | `dry_run=false`。`has_live_rows` かつ `replace=false` なら 409 事実文（T-2）。成功時は `{imported: true, run_id, stats: {...sync 統計...}, facts}` |
| 書き込み | ① `document_analysis_runs` に 1 行（`status='completed'` / `current_stage='import'` / `options.import = {export_id, source_document_id, bundle_sha256, imported_by_role, replace}` / `stage_outputs.import = 統計`）。**採用 run にはしない**（採用は教員の既存操作）② `core/knowledge_import/rows.py` が束の項目 → `sync_live_rows` の incoming 行に写す（KT4 の再計算 stable_key・`agent_payload.import` に出所）③ graph は `theory_component_graphs` を import 用 UUID 写像で書き換え upsert（`persist_component_graph` の course_id 規約を読んで同じにする）④ `record_knowledge_audit` + `AUDIT_ENTITY_IMPORT` |
| 束 → 行の写し | claims: `text / normalized_text / claim_type(語彙外は unknown + claim_type_text) / concepts / equation / support_status / evidence_text / source_scope / origin`。components: `name / component_type(丸め) / summary / inputs / outputs / preconditions / constraints / cautions / dependencies / evidence_claims(→ 新 UUID) / linked_* / operation / source_scope`。equations / evidence / derivation steps: 対応する `knowledge_*` 表へ。`review_status` は T-1 |
| 非対象（v1） | chunks 本文と embedding（束に無い）/ 図画像 / learning_units / コース（course_info.json は読まない）/ 同一性リンク・レジストリ行。取り込み後に `identity_candidates` を回す経路は教員の既存「再解析」に委ねる |

core は `backend/core/knowledge_import/`（`bundle.py` = zip 読み・manifest 検証・sha256、`rows.py` = 項目 → 行の純関数、`apply.py` = sync 呼び出し）。
FastAPI / LLM 非 import。`_validate_export_references` は routes 側の既存関数を route から呼ぶ（core へ移さない）。

### 4.3 UI（第2波）

教材行 `⋯` メニュー「束を取り込む…」→ モーダル（ファイル選択 → dry-run の事実表示 → 「取り込む」）。live 行があれば
「この教材には解析結果があります。取り込むと、束に無い既存の項目はこの教材の表示対象から外れます（教員が確定した項目は外しません）」の確認 + `replace=true`
（文言は 2026-09-13 のレビュー是正 P4-R2。旧文言「教員が確定した状態は保たれます」は、束に無い行が supersede される事実を隠していた）。
アンカー `materials.row-import` / `materials.import-modal` / `materials.import-submit` + マニュアル節（`11-admin-materials.md`）。

## 5. P4-2 RAG の構造 1 hop（SA層 kind `retrieved_structure`）

- **入力**: `_learning_chat_core` で採用した `cited_sources`（score ≥ 0.30・既に `allowed_document_ids` で絞られた chunk）。
- **射影（route 層・DB 読み 2 本）**: ① `theory_claims_live WHERE chunk_id = ANY(:chunk_ids) AND document_id = ANY(:doc_ids)`
  （`superseded` 除外は live ビューが担う。`origin` は span / claim_object / atomic_rewrite を対象、`equation_synthesis` は本文が式なので v1 対象外）。
  ② 各 document の最新 `theory_component_graphs.graph_json` の **main 層ノード**で `linked_claim_ids ∩ (claim UUID ∪ agent_claim_id ∪ legacy_ids)` が非空のもの
  （`graph_dialogue.load_latest_graph` を再利用。detail / debug 層は使わない）。
- **解決器（`core/assistant_context/resolvers/learning.py` に `resolve_retrieved_structure` を登録。kind 名 `retrieved_structure`）**:
  出典番号ごとに「[出典N] の箇所には次の主張が構造化されています: 「…（120 字）」（主張の種類: 定義 / 診断 …）」を最大 2 主張、
  主張が main ノードに掛かるとき「この主張は理論の骨格では『{THEORY_STAGE_LABELS}』の段階（{node.label}）に置かれています」を 1 行。
  全体上限 8 行・`MAX_BLOCK_CHARS_LEARNING` を共有。`contains_internal_id` に当たる文は捨てる。数値なし。
- **合流点**: 既存の画面文脈ブロックの**直後**（`_screen_block` → `_retrieved_block` → `_selection_block` → 発話）。`screen_context` が無いターンでも働く
  （画面の申告ではなく検索結果由来）。**モード別**: casual = なし / `cycle_mode="elicit"` = なし（答えの手渡し）/ それ以外 = あり。
  `UNTRUSTED_SOURCE_NOTICE` は既存の条件分岐に相乗り（claim 本文は PDF 由来の untrusted）。
- **観測**: `structured_grounding_present` に kind `retrieved_structure` を additive（payload 空・種別だけ）。
- **ガードレール**: 登録 kind が 5 つ（`element / verification / placement / view / retrieved_structure`）に固定 — 既存テストの「4 つ」を本書に従って更新し、
  `resolve_topic` / `resolve_visible` 不在は維持。LLM 回数不変（`generate_text` 呼び出し箇所が増えない）・数値非漏洩・スコープ強制 SQL の字面。
- **docs**: SA層設計書 §11.16、`docs/backend/rag-chat.md` ④⑤、`docs/features/learning.md` 構造 grounding 節、親文書 C-5 に解消注記。

## 6. P4-3 参照の健全性（`core/reference_health.py`）

- **検査対象（live 行・DB のみ・純 SQL）**:
  ① graph main / detail ノードの `linked_claim_ids` → `theory_claims_live`（UUID / `agent_claim_id` / `source_scope.legacy_ids`）
  ② `theory_components_live.evidence_claims` / `linked_claim_ids` → claims、`linked_equation_ids` → `knowledge_equations`（agent ID）
  ③ `theory_claims_live.chunk_id IS NULL`（出典 chunk に着地していない主張）
  ④ `learning_units_live.linked_claim_ids / linked_component_ids` → 各表。
  結果は `{status: "ok"|"broken"|"unchecked", checked_at, facts: [...], details: {kind: [{ref, from_label}]}}`。**件数は details の配列長として
  読み手が数えられるだけで、facts に数字を書かない**（T-3）。
- **実行点**: ① 解析パイプライン `_stage_completed` 直前（best-effort・失敗は握る）に `stage_outputs.reference_health` へ検査時点の事実を保存
  （run の生成ログの一部。「解決済み」フラグではない = KT5）② `GET /api/admin/documents/{id}/reference-health`（`_ensure_document_viewable`・
  読み取り専用・その場で再検査、保存しない）。
- **教材行**: `MaterialOut.reference_health`（latest run の `stage_outputs.reference_health` から `{status, checked_at}` だけ）→ 行に事実文チップ
  「参照: 問題なし / 参照: 切れがあります / 参照: 未確認」。`⋯` メニュー「参照の整合を確認…」→ 詳細モーダル（facts + details 列挙・再検査ボタン）。
- **export との関係**: 束の `check_refs` は束内部（artifact 由来）の整合、本層は DB live 行の整合。両方を残す。export_validation.json への
  同梱は v1 では行わない（束は artifact 由来で DB の状態を写す場ではない。必要なら別途）。

## 7. P4-4 版の語彙（PROV-O 2 系統・コード変更 0）

`docs/architecture/layer_registry.md` に **§4「版の語彙」**を新設し、「版」を担う全構造に **revision**（`prov:wasRevisionOf` —
同じものの新しい状態）か **alternate**（`prov:alternateOf` — 同じものの別表現・別 ID）かを宣言する。

| 構造 | 語彙 | 意味 |
|---|---|---|
| `shared_versions.version_no`（V層） | revision | 発行版の連鎖。`component_citations.source_release_id` はその 1 点への pin（`prov:hadPrimarySource`） |
| `atlas_skeletons`（domain_key, version）+ `id_migrations` | revision | 骨格の凍結版。`id_migrations` は revision 間の node 対応（K-6） |
| `library_entry_versions` | revision | L層エントリの凍結版 |
| 知識行 `superseded_by_run_id` / `produced_by_run_id` | revision | run による改訂（stable_key が同一性、run が版） |
| `element_explanations` / `landscape_placements` / `landscape_gap_signals` の supersede | revision | 候補の改訂 |
| `element_identity_links` / `library_entry_relations(exact_match, close_match)` / `library_atlas_node_links` | **alternate** | 同じ概念の別表現・別 ID。統合しない（KN-2） |
| `element_id_remap` | revision 間の対応表 | 旧 ID → 新 ID（同一 stable_key） |
| `document_analysis_runs`（同一 document の run 列） | revision | 解析の改訂。採用 run は「現在の版」 |

export の `ro-crate-metadata.json` は同じ語（`prov:wasRevisionOf` を run に、`prov:alternateOf` は v1 では書かない — 同一性リンクは束に含めないため）を使う。
ガードレール `test_version_semantics_docs.py`: §4 の表が上記の構造名を全て含むこと（宣言の欠落を機械検査）。

## 8. P4-5 D層・C層の表現語彙（migration 083・列追加のみ）

| 表 | 追加 | 語彙（正本） |
|---|---|---|
| `challenges` | `challenge_mode TEXT NOT NULL DEFAULT 'direct'`（CHECK）+ `target_element_ref JSONB NOT NULL DEFAULT '{}'` | `core/doubt/schema.py::CHALLENGE_MODES = ("direct", "undercut")`（direct = 主張そのものへ / undercut = 主張と根拠のつながりへ）。`target_element_ref` は `{element_type, element_id, document_id}`（グラフ上の位置・任意） |
| `epistemic_ledger` | `evidence_lines JSONB NOT NULL DEFAULT '[]'` | `EvidenceLine`（`line_id / line_kind / evidence_ids[] / claim_ids[] / equation_ids[] / recorded_by / reason / recorded_at`）。`EVIDENCE_LINE_KINDS = ("observation", "derivation", "external_reference", "consistency")`。**人間の記帳専用**（worker・ledger_builder は書かない = SL3 同型）。`support_paths` の計算結果は記帳しない（PN-2） |
| `component_citations` | `citation_intent TEXT`（NULL 可・CHECK） | `core/schema.py::CITATION_INTENTS = ("uses_as_evidence", "extends", "qualifies", "contrasts_with", "cites_for_background")`。既存行 NULL = 記録なし |

- **API**: `POST /challenges` body に optional `challenge_mode`（既定 direct）/ `target_element_ref`。`POST|PATCH /ledger/{t}/{id}/evidence-lines[/{line_id}]`
  （`_require_teacher`・`reason` 非空・`evidence_ids ∪ claim_ids ∪ equation_ids` 非空・帰属は認証ユーザー・削除なし＝`status`? 持たず訂正は PATCH）。
  台帳 GET に `evidence_lines` を additive。`POST /explanations/{id}/cite` に optional `citation_intent`（語彙外 422）。学習者向け台帳 GET は
  `evidence_lines` を**事実文 1 行**（「根拠の線が記帳されています（種類: …）」）だけに射影し `recorded_by` を出さない。
- **ラベル**: `label_vocab.CHALLENGE_MODE_LABELS` / `EVIDENCE_LINE_KIND_LABELS` / `CITATION_INTENT_LABELS`（JS 側は逐語ミラー + mirror テスト）。
- **UI（第2波）**: `doubt-atlas.js` 疑義フォームに「疑義の向き」select、台帳パネルに「根拠の線」区画（一覧 + 追加）、原稿スタジオの引用に「引用の意図」select（任意）。
  アンカー 3 点セット。
- **監査**: 既存 `AUDIT_ENTITY_CHALLENGE` / `AUDIT_ENTITY_LEDGER` / `AUDIT_ENTITY_CITATION`（新 entity_type なし）。

## 9. 権限・監査・数値（横断）

- export = 閲覧 / import = 編集 / 健全性 = 閲覧 / 台帳・疑義・引用 = TEACHER（既存）。学習者向けの新 API は無い（P4-2 はプロンプト内部の変化のみ）。
- 監査: import は `AUDIT_ENTITY_IMPORT`（新設・カタログ登録）。他は既存定数。
- 数値: 事実文に数値・スコアを書かない。健全性の details は運用列挙（教員向け）で、バッジ・比率にしない。

## 10. 非スコープ（v1）

- コース（course_info.json）・chunks 本文・embedding・図画像・learning_units・同一性リンクの取り込み / 他インスタンスの承認の継承（T-1）
- import の差分プレビュー（行単位の before/after）/ 取り込みの取り消し（supersede 行が残るので情報は失われない）
- P4-2 の detail 層・derivation・symbol の事実化 / 取り込んだ構造の学習者向け表示
- 健全性の自動修復・G層 To-Do（運用実測後）
- `prov:alternateOf` の束への書き出し（同一性リンクを束に含めない v1）
- evidence_lines の LLM 候補生成 / undercut の自動判定 / citation_intent の推定

## 11. ガードレール

`test_knowledge_import_{core,api,guardrails}.py` / `test_export_jsonld.py` / `test_retrieved_structure_{core,route,guardrails}.py` /
`test_reference_health_{core,api}.py` / `test_version_semantics_docs.py` / `test_doubt_citation_vocab_{migration,api,guardrails}.py`。
検査内容: core の FastAPI / LLM 非 import・DELETE 文不在・T-1（取り込み行の review_status 固定）・KT4（再計算）・422/409 の事実文・
学習者射影に `recorded_by` / 数値 / 内部 ID が無い・登録 kind 5 つ・LLM 呼び出し箇所不変・語彙表 ⇄ migration CHECK 一致・worker が
`evidence_lines` を書かない・JS ミラー一致。

## 12. vision §6（14 原則）照合

| 原則 | 扱い |
|---|---|
| 1 確定は人間 | T-1・KT2。import は候補で着地・承認は継承しない |
| 2 evidence-based | 束の evidence_text / verbatim は不変。1 hop は claim 本文を出典番号に結ぶ |
| 3 情報を落とさない | supersede 同期・DELETE なし・束の値は payload に保持 |
| 4 数値の用途と粒度 | KT7・T-3 |
| 8 出所の正直さ | dry-run の事実・409 の事実文・健全性「未確認」 |
| 9 同期パスに LLM を入れない | KT3 |
| 10 完了フラグを持たない | 健全性は検査時点の事実（KT5） |
| 11 fail-closed | KT6 |
| 13 積層 | 列追加のみ（083）・A層非改変 |
| 14 監査必須 | KT8 |

## 13. 実装体制

Fable 5.1 指揮。第1波（並列・UI なし）: A = P4-1 / B = P4-2（docs 込み）/ C = P4-3 / D = P4-5。第2波: E = A・C・D の UI + アンカー + マニュアル。
P4-4 と索引系文書（README / layer_registry / data-model / api.md / CLAUDE.md / 親文書の解消注記）は指揮者が担当。

## 14. 実装記録

### 14.0 2026-09-13 — Phase 4 v1（Fable 5.1 指揮・Opus 5 の 5 担当）

第1波（並列・UI なし）: A = P4-1 / B = P4-2 / C = P4-3 / D = P4-5。第2波: E = UI・アンカー・マニュアル・JS ミラー。P4-4 と索引系文書は指揮者。
migration は **083** `doubt_citation_vocab`（P4-5 のみ・列追加のみ）。オーナー判断を要する項目は無し（T-1〜T-3 を推奨で固定・撤回可）。

| # | 実装先 | 設計からの意図的差分 |
|---|---|---|
| P4-1 export | `routes/export.py`（`ro-crate-metadata.json` / 各項目の `stable_key`・`knowledge_object_id` の live ビュー join / manifest `EXPORT_SCHEMA_VERSION="0.3.0"` + `jsonld` / `import_support` / `knowledge_object_keys` / README 追補） | 人名ゼロ・数値ゼロを再帰走査テストで固定。DB 不達は `{}` に握り export を落とさない |
| P4-1 import | `POST /api/documents/{id}/import-bundle`（`routes/export.py` 同居）+ `core/knowledge_import/{bundle,rows,apply}.py` | ①**採用 run の固定**: `resolve_artifact_runs(policy="adopted")` は `documents.active_analysis_run_id` が NULL のとき最新 completed run へ fallback するため、取り込み run が採用 run を横取りしないよう `apply.preserve_adopted_run()` が run 作成の**前**に「いまの採用先」を明示固定する（採用先未設定 × 既存 completed run ありのときだけ。暗黙の採用を明示にするだけで新しい採用はしない）②claim は `agent_payload` 列を持たないので出所ブロックは `source_scope["import"]`（他は `agent_payload["import"]`）③束に `parent_claim_id` が無いので `origin` は `claim_object` / `equation_synthesis` のみ（親子を捏造しない）・`maturity_source="imported"` ④claim の `chunk_id` は NULL（束に本文が無い事実）・component の `course_id` NULL・graph は `component_graph.json` のみ（operation_graph は取り込まない）・写せない参照 ID はそのまま残す |
| P4-2 | `core/assistant_context/resolvers/learning.py::resolve_retrieved_structure`（kind `retrieved_structure`・定数は `schema.py` の `BLOCK_HEADER_RETRIEVED` / 8 行 / 出典ごと 2 主張 / 120 字）+ `routes/learning.py` の 4 ヘルパと合流点 1 箇所（新ルートなし・本体の字面不変） | ①ノード事実文の括弧は `node.label`（= 英語の stage 表示名で同語反復になる）ではなく `display_label` の理論対象。stage を引けないノードは事実文ごと出さない ②観測は既存 `structured_grounding_present` の条件を `_screen_block or _retrieved_block` に広げるだけ（DO1 で payload は空なので由来は区別しない・新 event なし）③グラフは document ごと 1 回・最大 4 document（コード定数）。超えた分は主張本文だけが載る |
| P4-3 | `core/reference_health.py` + `routes/reference_health.py`（`GET /api/admin/documents/{id}/reference-health`・main.py 直接登録）+ orchestrator `_stage_completed` の `stage_outputs.reference_health`（`upsert_analysis_run` に同梱・失敗は `unchecked`）+ `MaterialOut.reference_health {status, checked_at}` | ①export_validation への同梱は v1 非対象（§6 に反映済み）②グラフの検査対象は main / equation_detail のみ（debug 層は fallback / inferred の置き場）③`details` に上限を設けない（列挙が再構成の材料）④`from_label` に内部 ID 形を出さない（ラベルが無ければ空）⑤IG4: 新 GET は集約計器ではないので `test_indicator_catalog_guardrails` の `_NON_INDICATOR_ROUTES` に理由付きで登録 |
| P4-4 | `docs/architecture/layer_registry.md` §4 + `backend/tests/test_version_semantics_docs.py`（12 構造の宣言網羅・語彙 2 語・同一性リンク系が alternate であること） | コード変更 0 |
| P4-5 | `backend/db/083_doubt_citation_vocab.sql` + `core/doubt/schema.py`（`CHALLENGE_MODES` / `EVIDENCE_LINE_KINDS` / `EvidenceLine` / `HUMAN_ONLY_LEDGER_FIELDS`）+ `core/label_vocab.py` の 3 表 + `routes/doubt.py`（challenge_mode / target_element_ref・`POST|PATCH .../evidence-lines`・台帳 GET の additive・学習者射影 `evidence_lines_fact`）+ `routes/theory_components.py::cite_explanation` の optional `citation_intent` | ①`evidence_lines` は JSONB 配列なので CHECK を張らず API 層で語彙を強制（migration に語彙をコメント・ガードレールが一致を固定）②根拠 ID がすべて空の記帳は 422（「経路なし」語彙を作らない）③`EpistemicLedgerRecord` に列を足さない（SL層の前例に合わせ Pydantic は投影用）④`component_citations` の読み取り側（sharing-dashboard 等）への `citation_intent` 投影は v1 非対象 |

第2波（E・UI）: `admin-knowledge-import.js`（新規・`window.KnowledgeImport`・dry-run の事実 → 確定の 2 段・`replace` は明示チェック）/ `admin.js`
（教材行の事実文チップ「参照: 問題なし / 切れがあります / 未確認」・⋯ メニュー「束を取り込む…」「参照の整合を確認…」・詳細モーダル + 再確認）/
`doubt-atlas.js`（疑義の向き select・根拠の線の一覧 + 追加フォーム）/ `admin-lecture-studio.js`（引用の意図 select・任意）。アンカー 10 件
（`materials.row-import` / `import-modal` / `import-submit` / `row-reference-health` / `reference-health-modal` / `reference-health-recheck`、
`doubt-atlas.challenge-mode` / `evidence-lines` / `evidence-line-add`、`lecture-studio.cite-intent`。件数の正本は `test_admin_help_ui_anchors.py`）+
マニュアル節（`11-admin-materials.md` / `18-admin-doubt-atlas.md` / `14-admin-lecture-studio.md`）+ JS 逐語ミラー `test_knowledge_transfer_vocab_mirror.py`。
**nginx**: `/api/documents/` location に `client_max_body_size 60m`（multipart の束が既定 1m で 413 になるため。拒否の判断はサーバの 422 に任せる）。
dry-run の `warnings`（束内部参照の警告）は UI で「束の中で解決できなかった参照」として表示し、取り込みは阻止しない。
根拠の線の**訂正（PATCH）UI は未実装**（API はある。追加のみ）。Copilot の locate 対象（capability）は増やしていない。

検証: backend フルスイート 15,863 pass / 27 skipped（E 報告時。ベースライン 15,515 → +348）。src 1,924 pass（A層非改変）。
実 DB での 083 適用・取り込みの往復 E2E は docker 復帰後。

### 14.1 2026-09-13 — 敵対的レビュー + 実データ検証の是正（班 F4）

第1波の実装に対する敵対的セキュリティレビュー（P4-R1〜R12）と scratch DB での実データ検証（V-2 / V-8 / V-9 / V-11）の是正。
migration なし・新エンドポイントなし・LLM 回数不変。

| # | 何が問題だったか | 是正 |
|---|---|---|
| P4-R1 | 束の検査が**圧縮サイズ**（50MB）しか見ず、`zf.read()` が無制限に伸長した（PoC: 654KB の zip で RSS 1.1GB） | `bundle.py` に `MAX_UNCOMPRESSED_BYTES`(200MB) / `MAX_ENTRY_UNCOMPRESSED_BYTES`(64MB)。読む前に**宣言サイズの合計**を検査し、読むときも `zf.open()` + `read(limit+1)` で**実測**打ち切り（宣言が嘘でも伸ばさない）。`json.loads` の `RecursionError`（深い入れ子）も `BundleError` に畳む = 500 を出さない（P4-R12）。門は `parse_bundle` の中なので `dry_run=true` でも同じく効く。是正後の同 PoC で RSS 29.5MB |
| P4-R2 | `replace=true` が、束に無い live 行を**教員が承認・却下した行ごと** supersede した（回復は手作業） | `apply.py` が sync に渡す**前**に、人間が確定した行（`review_status ∈ teacher_approved / teacher_reviewed / endorsed / rejected / needs_revision`、component は `_component_human_touched` も）を incoming へ「そのまま」合流させる（`merge_protected_rows`。`values` は空なので内容列は 1 つも UPDATE されない）。`sync_live_rows` は非改変。dry-run は `would_supersede_counts`（種別ごとの `superseded` / `kept_human_decided` / ラベル列挙）を返し、事実文を「束に無い既存の項目は表示対象から外れます」+「教員が確定した項目は外しません」の 2 文に改訂（旧「教員が確定した状態は保たれます」は削除）。**stable_key を持たない live 行は合流できない**ので `unmatchable` として正直に数える |
| P4-R3 | 1 つの束で作れる行数に天井が無かった | `MAX_IMPORT_ITEMS_PER_KIND`(5000)。超過は 422 の事実文（数値は書かない） |
| P4-R4 | 確認（dry-run）と確定の間に別の束へ差し替えられた（TOCTOU）。一括確定なのに `decision_context` が無かった | dry-run が `bundle_sha256` を返し、確定は**必須**引数として受けてサーバの再計算値と照合（欠落 422 / 不一致 409・どちらも書き込み 0）。`decision_context.BASIS_KNOWLEDGE_IMPORT_BUNDLE` を新設し、`presented` = dry-run が見せた「種別:件数」/ `applied` = 実際に着地した「種別:件数」/ `alternatives` = `dismiss`(取り込まない) + `skip_step`(確認だけで止める) / `reopen_path` = `POST /api/admin/claims/{claim_id}/review` / `evidence_shown=False` / `client_reported` に `replace_requested`・`dry_run_confirmed` を隔離（DC4） |
| P4-R5 | manifest の `app` が入れ子 dict のまま run options / 監査 / 画面へ素通しした（画面は `[object Object]`） | `app` は `{name, version, git_commit}` の**スカラー 3 つ**に絞り各 200 字で切る。`export_id` / `exported_at` / `source_object_id` も 200 字、`source_document_ids` は 50 件。画面用に `app_label`（`name（version）`）を dry-run が返す |
| P4-R6 | `support_status` の既定が `source_backed`（= この教材で出典に当たったという主張） | 既定を廃止。束が明示した値のみ採り、無ければ `review_required`（`IMPORT_SUPPORT_STATUS_FALLBACK`） |
| P4-R7 | 取り込み直後の参照の整合がどこにも残らなかった | `apply_import` 末尾で `check_document_references` を best-effort 実行し、取り込み run の `stage_outputs.reference_health` に残す（パイプラインと同じ位置・同じ形。失敗は `unchecked`） |
| P4-R8 | `POST|PATCH .../ledger/{type}/{id}/evidence-lines` が `_require_teacher` だけで、**他人の教材の台帳**へ記帳できた | `_require_editable_ledger_target()` が target → document（claim / component / equation は live 行、assumption は `document_ids`、加えて台帳行の `document_id`）を解決し `_require_editable_document_or_404` を通す。解決できない・編集できないはどちらも同一の 404。本文の形の検証は DB を開く前のまま（不正な本文は対象の有無に関わらず 422） |
| P4-R9 | 画面の「書き出し元: [object Object]」 | `source.app_label` を描く（サーバが組んだ 1 行。JS 側で辞書を組み立てない） |
| P4-R10 | 参照の整合 API が**開くたびに**グラフ・主張・部品・単位を全走査した。nginx の `client_max_body_size 60m` が API 上限より過大 | 既定は run に保存済みの事実（`load_recorded_reference_health`。採用 run 優先 → 最新 run）を返し、`?recheck=true` のときだけ再計算。**保存が無い教材（本層より前に解析した教材）は空の画面を返さずその場で検査する**（設計からの意図的差分）。返す `source` は `recorded` / `rechecked`。nginx は 55m（50MB + multipart の包み） |
| P4-R11 | 「スキーマ版が対応していません」で次の一手が無かった | 422 の事実文に「書き出し元のインスタンスで書き出し直してください」を明記。0.2.x を stable_key 再計算で受けることは技術的には可能だが v1 では**やらない**（束の形が違えば取り込みの前提も違う。受けるなら版ごとの読み分けを設計してから） |
| V-2 | derivation step の `step_id`（`step_001`）はチェーン内でしか一意でなく、別チェーンの同名 step と stable_key が衝突して取り込みが `uq_knowledge_derivation_steps_stable_key_live` 違反で落ちた | `rows.py` が `ko_keys.derivation_step_agent_id(derivation_id, step_id)` と `derivation_step_stable_key(..., derivation_id=, step_index=)`（`core/knowledge_objects/stable_key.py` = 書き手と共有の正本）を使う。**書き手（`persistence._derivation_items`）と同じ結果を出すこと**をテストで固定 |
| V-8 | `resolve_retrieved_structure` の学習者向け事実文に生 TeX がそのまま出た（`Equation (3.55) defines \delta P_{\mathrm{shot}}.`） | 解決器の `_safe()` を `core/learner_context_common.py::safe_text`（内部 ID + 生 TeX の正本述語）に委譲。**この層で TeX 判定を再実装しない**（テストが `looks_like_tex_math` の不在を固定） |
| V-9 | `reference_health` が式由来の合成 claim（`origin='equation_synthesis'`・`chunk_id` を持たないのが正常）を `claim_without_chunk` に数え、計器が常時赤になった | `_CHUNKLESS_CLAIM_ORIGINS` で対象外に。由来不明の `chunk_id` 無し claim は従来どおり事実として挙げる（黙って消さない） |
| V-1（余波） | 取り込みが `knowledge_evidence` に `review_status` を積んでいた（078 にその列は無い = 実 DB では INSERT が落ちる） | `evidence_rows` は値を積まず、evidence の `preserved_columns` も空にした（書き手 `persistence._evidence_items` と同じ扱い）。保護スキャンも evidence では `review_status` を読まない |
| V-11 | export→import の往復で claim の `origin` / `parent_claim_id` が失われた（`atomic_rewrite` 74 → 0 / 親 74 → 73） | export が claims に `origin` / `parent_claim_id`（**束の中の claim_id 空間**）を載せる（値が無ければキーごと出さない）。import は語彙内の `origin` のみ復元し、**分からないときは `values` にキーを入れない**（= 既存行の内容列を上書きしない）。親子は sync 後に `link_claim_parents` が id 写像で張り直し、解決できない親は触らない（NULL で潰さない） |

不変条項との関係: KT2（承認非継承）・KT4（stable_key 再計算）・KT5（行を消さない）・KT6（権限 fail-closed）は崩していない。
P4-R2 は KT5 の**強化**（supersede も「人間の確定を倒す」なら情報の喪失とみなす）。

ガードレール: `test_knowledge_import_guardrails.py::TestAdversarialBundles`（zip bomb / 実測打ち切り / 深い入れ子 / 項目数 / `../` エントリ / manifest の縮約 / 版の案内）、
`test_knowledge_import_api.py::TestBundleHashHandshake`・`TestApprovedRowsSurviveReplace`、`test_knowledge_import_core.py::TestWriterAndImporterAgreeOnKeys`、
`test_stakes_ledger_api.py::TestEvidenceLineRequiresDocumentEdit`、`test_decision_context_guardrails.py`（新 basis の call site）、
`test_reference_health_{core,api}.py`（V-9 / スナップショット既定）、`test_knowledge_transfer_retrieved_structure.py`（V-8）。
`zf.open` は**メモリ上のストリーム**なので、「ディスクへ展開しない」ガードレールは `extractall` / `.extract(` / `tempfile` と**素の** `open(`（`zf.` が前に付かない）を禁止する形に精密化した。

検証: backend フルスイート 16,014 pass / 27 skipped。実 DB での往復 E2E は docker 復帰後。
