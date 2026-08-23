# オブジェクトスコープ権限是正と要素文脈 Phase 3 — 完了報告

- 実施日: 2026-08-11
- ブランチ: `ura-dev`
- 正本: [`security_and_context_phase3_implementation_directive.md`](security_and_context_phase3_implementation_directive.md)
- 体制: Fable 5（指揮・レビュー・検証）+ Opus 5 サブエージェント3体（P0 / P2選抜 / P2結線）
- 状態: **P0・P2 とも実装完了、全テスト green。P1 は指示どおり非接触。**
  git commit はセッションの `.git/objects` 権限問題により未実施（下記 §6）。

---

## 1. 実施サマリ

| 優先度 | 内容 | 状態 |
|---|---|---|
| P0 | 5経路のオブジェクトスコープ権限 fail-closed 化 | ✅ 実装・テスト・docs 完了 |
| P1 | help_kb Phase 3 | ⏸ 保留（コード・設定・migration・docs 一切非接触） |
| P2 前半 | required equation の選抜是正（§5.2） | ✅ 実装・テスト完了 |
| P2 後半 | approved contextual 説明のラベル結線（§5.3〜5.5） | ✅ 実装・テスト・設計書更新完了 |

## 2. 変更ファイル（19ファイル + 新規1、計 +2,045 / -85 行）

### Commit 1 相当 — P0 セキュリティ境界

| ファイル | 要旨 |
|---|---|
| `backend/api/routes/admin.py` | 共通ゲート `_require_editable_document_or_404` / `_require_editable_course_or_404` 新設 + 4経路適用（reanalyze=:598 / PDF差し替え=:1160 / unanswered-queries=:3085 / bridge-insights=:3593） |
| `backend/api/routes/learning.py` | `get_source_chunk_route` を全域可視集合 → コース sources スコープへ（:3062） |
| `backend/tests/test_object_scope_authorization.py` | **新規** 59 tests（正例/負例/副作用前認可/detail 同一性） |
| `backend/tests/test_source_chunk_visibility.py` | 旧スコープ固定の静的ガードレール+ルートテストを新仕様へ追随 |
| `backend/tests/test_document_pipeline.py` / `test_llm_model_policy_api.py` | reanalyze ハーネスに権限ゲート stub 追加（ゲートなし前提だった） |
| `docs/features/auth-visibility.md` | §4.5「オブジェクトスコープの権限」新設 |
| `docs/backend/api.md` / `CLAUDE.md` / `.claude/skills/episteme-graph-dev/SKILL.md` | 対象エンドポイントの権限記述を実装後の姿へ更新 |

### Commit 2 相当 — P2 選抜是正

| ファイル | 要旨 |
|---|---|
| `backend/core/document_pipeline/contextual_explanation_inputs.py` | 3区分選抜（required equations 別枠 → 既存優先要素 → optional equations）。`collect_required_equation_ids()` / `build_course_snapshot_equation_ids()` 新設 |
| `backend/core/document_pipeline/orchestrator.py` | 配線13行（`material_id` / `derivations` 透過 + payload に required 3キー） |
| `backend/tests/test_contextual_explanation_stage.py` | 32 → 49 tests（既存32件は無改変で通過） |

### Commit 3 相当 — P2 approved 結線

| ファイル | 要旨 |
|---|---|
| `backend/core/element_explanations.py` | 一括読み出し helper `approved_contextual_bodies()` 新設（1クエリ・approved/contextual/role IS NULL/本文非空のみ・空入力 SQL 非発行） |
| `backend/core/deliberation/context_lens.py` | `_approved_equation_explanations()`（投影1回=1クエリ）→ 4レンズの `_equation_label(..., explanations=)` → `labels.equation_label(explanation=)` へ橋渡し。全経路 `_safe()` 包み |
| `backend/core/course_content_builder.py` | ビルド時に document 集合まとめて1回取得 → `(document_id, equation_id)` 一致本文を添付 → **ラダーが採用したときだけ** snapshot に可読 `headline` 保存。読み取り時も TeX/内部 ID を防衛棄却 |
| `backend/tests/test_deliberation_context_lens.py` ほか2ファイル | +59 tests（27/11/21）。既存テスト修正ゼロ |
| `docs/features/element_context_presentation_redesign.md` | §10.5 実装済み化 + §10.6 実装記録追記 |

## 3. 権限マトリクスのテスト結果（P0）

全5経路 × 正例（owner / editor / SYSTEM_ADMIN）・負例（無関係 TEACHER / viewer のみ /
public 直指定 / course 経由閲覧のみ / 不明 ID）を `test_object_scope_authorization.py` で固定。

- 全負例が **404** で、detail は存在時と完全同一（存在判別不能）
- 認可失敗時に background task / MinIO / `aggregate_bridge_candidates` / SQL 集計が**呼ばれない**ことをカウンタで検証
- source-chunk: sources 空集合は SQL 非発行で 404 / 可視な別コース chunk・course source 外の public chunk → 404
- 結果: **59/59 passed**（+ 追随修正後の既存 `test_source_chunk_visibility.py` 25/25）

## 4. required equation の選抜（P2 前半）

- 導出ソース: ①course snapshot の `![[equation:id]]`（`learning_courses` 逆引き、LIMIT 20・read-only・fail-soft）②component `linked_equation_ids` + `evidence_refs.equation_ids` ③claim `equation_ids` ④thesis（central + support_structure）/ derivation は**チェーン終端 output のみ**（全 step 展開は required ≒ 全式となり別枠の意味が消えるため）
- stage artifact の `meta` / payload に `required_equations_considered` / `required_equations_selected` / `required_equations_unresolved` を記録（既存キー不変）。実運用での選抜件数は次回 Docker 実機解析の stage artifact で確認する
- 未解決 ID は `skipped_reason='equation_not_resolved'` で明示記録（捏造なし）/ CostGate は硬い上限のまま
- `max_elements <= 0`（運用 kill switch）は required も含め停止 — 判断として明記

## 5. approved / fallback の両表示確認（P2 後半）

テストで固定（実 UI は Docker 実機確認が残、§7）:

- **approved あり**: 第1文が headline・`label_source='explanation'`・S2/S3展開/S4 の lens と snapshot title の end-to-end 反映・別 document の同名 equation ID 非混線
- **fallback**: candidate / dismissed / superseded / approved **generic** / `role='discussion_seed'` は非採用 → 既存ラダー（式番号 → 記号+役割 → semantic summary → 一般ラベル）維持。DB 取得失敗でも lens は正常 DTO・course build は停止しない
- 学習者 DTO に `label_source` / reviewer / 確度 / 説明ステータス非漏洩、headline に TeX・UUID・`eq_tex_*` 非出現、CP6 関係集合不変

## 6. テスト結果（最終状態）

| スイート | 結果 |
|---|---|
| 指示書 §7 指定6ファイル | **471 passed** |
| backend 全体 | **8,837 passed / 25 skipped**（ベースライン 8,708 → 新規 +129、回帰ゼロ） |
| src 全体 | **1,803 passed**（テスト数不変 = A層非改変） |

## 7. git commit について（要対応）

このセッション（unix user `dev`）からの commit は `.git/objects/1c` `b7` `fe` の3ディレクトリ
（所有者 `urakawashinichi`・グループ書込不可 `drwxr-xr-x`）への object 書込で失敗する
（`test_source_chunk_visibility.py` の blob が prefix `1c` に決定論的に衝突）。修正は所有者権限が必要:

```bash
chmod -R g+w .git/objects
```

そのうえで、コミット境界（指示書 §6）どおりの3コミットを下記ファイルセットで作成する
（3セットは互いに素であることを検証済み。メッセージ案は本報告と同じ内容で用意済み）。

## 8. 残作業

- Docker 実機確認（指示書 §7 の5項目: 他教員 ID 直指定 404 / 別コース chunk 404 / S2・S3展開・S4 の見出し一致 / candidate のみの縮退 / トピック再生成後の S1・S3外殻追随）
- P0 の意図された UX 縮退の運用観察: コース sources 外の引用（`other_material` grounding・discuss `all_visible`）の出典ポップアップは 404 に縮退（フロントは事実文表示で degrade。指示書 §3.3 E-5 の明示要求）
- `_ensure_required_equations_in_material`（教材本文「この節で使う数式」行）のラベルは従来のまま（S1〜S4 の外・§10.6 残作業に記載）
- P1（help_kb Phase 3）の裁定は別途（保留継続）
