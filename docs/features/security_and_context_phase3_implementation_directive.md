# オブジェクトスコープ権限是正と要素文脈 Phase 3 — 実装指示書

- 作成日: 2026-08-11
- 対象ブランチ: `ura-dev`
- 状態: **実装指示確定**
- 優先順位: **P0 を実装 → P1 は保留 → P2 の Phase 3 を実装**

本書は、残存作業を GitHub issue ではなく `docs/` 配下の設計文書を正本として実装するための
作業指示である。issue 番号・issue 本文・コメントを実装根拠にしてはならない。

---

## 1. 正本と優先順位

| 優先度 | 判断 | 実施内容 | 主な正本 |
|---|---|---|---|
| **P0** | **実装する** | 教員・学習者 API のオブジェクトスコープ権限を fail-closed 化する | [`auth-visibility.md`](auth-visibility.md) §2〜§5 / [`discussion_mode_design.md`](discussion_mode_design.md) DM1・DM2・§6.1 / [`learner_element_context_design.md`](learner_element_context_design.md) §3「ゲート」 / [`docs/backend/api.md`](../backend/api.md) |
| **P1** | **保留** | help_kb Phase 3 の維持・既定値・撤去を裁定しない。コード・設定・migration を変更しない | [`manual_help_kb_design.md`](manual_help_kb_design.md) §2.3・§5 Phase 3・§8 |
| **P2** | **Phase 3 を実装する** | 承認済み contextual 説明を数式のラベルラダーへ結線し、教材に出る数式を説明生成対象から落とさない | [`element_context_presentation_redesign.md`](element_context_presentation_redesign.md) §2.3・§3・§5.1・§8 Phase 3 / [`hierarchical_context_explanation_design.md`](hierarchical_context_explanation_design.md) §5 / [`equation_hover_content_design.md`](equation_hover_content_design.md) EH1〜EH5 |

### 1.1 「P2 Phase 3」の意味

本書で実装する Phase 3 は、
[`element_context_presentation_redesign.md`](element_context_presentation_redesign.md) §8 の
**「日本語一行の本命（二層説明の結線）」**を指す。

[`hierarchical_context_explanation_design.md`](hierarchical_context_explanation_design.md) §6 の
「汎用×固有の結線（generic ブロック、identity link UI、journey）」は別フェーズであり、
今回のスコープに含めない。

---

## 2. 全作業に共通する不変条件

1. **404 fail-closed**: オブジェクトが存在しない場合と権限がない場合を外部から区別させない。
   ロール不足は既存の `_require_teacher` 等が 403 を返してよいが、ID 解決後の権限不足は 404 とする。
2. **サーバ側で確定する**: UI のボタン非表示・disabled・画面遷移を認可根拠にしない。
3. **副作用より先に認可する**: DB 集計、学生名取得、MinIO 読み書き、バックグラウンド実行、
   LLM 呼び出しより先に権限ゲートを通す。
4. **既存の権限正本を再利用する**: document は `services.resolve_document_access()`、
   course は `services.get_editable_course_data()` / `services.get_accessible_course_data()`、
   course source は `services.list_course_source_document_ids()` を使う。ルートごとの独自 SQL で
   visibility / group / editor 判定を再実装しない。
5. **SYSTEM_ADMIN の既存全権限を維持する**: bypass はロールを明示確認したうえで行い、
   TEACHER 全体へ拡張しない。
6. **N+1 を増やさない**: document 集合を扱う経路では集合ヘルパーと SQL の `ANY(...)` を使う。
7. **A 層非改変**: P2 では `src/episteme_graph/agents/` のプロンプト・スキーマ・validator を
   変更しない。選抜と表示結線は backend の投影・入力構築層で行う。
8. **候補を学習者に出さない**: P2 のラベルに利用できるのは
   `kind='contextual' AND status='approved'` の本文だけである。
9. **生 TeX・内部 ID をラベルへ戻さない**: EH1/EH2 と CP1〜CP8 を維持する。

---

## 3. P0 — オブジェクトスコープ権限の fail-closed 化

### 3.1 実装単位

P0 は1つのセキュリティ変更として実装する。最低限、次の5経路をすべて是正する。

| 経路 | 必要な境界 | 現在利用すべき正本 |
|---|---|---|
| `GET /api/admin/courses/{course_id}/unanswered-queries` | course owner / editor（SYSTEM_ADMIN は可） | `services.get_editable_course_data()` |
| `POST /api/admin/documents/{document_id}/reanalyze` | document owner / editor（SYSTEM_ADMIN は可） | `services.resolve_document_access()` の `can_edit` |
| `GET /api/admin/courses/{course_id}/bridge-insights` | course owner / editor（SYSTEM_ADMIN は可） | `services.get_editable_course_data()` |
| `PUT /api/admin/materials/{material_id}/pdf` | document owner / editor（SYSTEM_ADMIN は可） | `services.resolve_document_access()` の `can_edit` |
| `GET /api/learning/courses/{course_id}/source-chunk/{chunk_id}` | course にアクセス可能、かつ chunk の document がその course の source | `services.get_accessible_course_data()` + `services.list_course_source_document_ids()` |

学生名・学習痕跡・教材本文は、単なる「TEACHER であること」や document の `public` / viewer 権限だけで
開示・変更してはならない。

### 3.2 共通ゲート

`backend/api/routes/admin.py` 内に、少なくとも次の意味を持つ薄い内部ヘルパーを置く。
既存ルート群に同等の共通ヘルパーがある場合は新設せず委譲する。

```python
def _require_editable_document_or_404(document_ref: str, current_user: dict) -> DocumentAccess:
    """不在・権限なしを同じ 404 に畳む。SYSTEM_ADMIN のみ明示 bypass。"""

def _require_editable_course_or_404(course_id: str, current_user: dict) -> dict:
    """course owner/editor の HEAD データを返す。不在・権限なしは 404。"""
```

要件:

- detail 文言も存在・権限なしで同一にする。
- helper は認可と対象解決だけを担当し、業務処理を内包しない。
- document は UUID と `source_path` の両参照を `resolve_document_access()` に委譲する。
- `get_material()` は viewer/public/course 経由も許す閲覧 API なので、PDF 差し替えの認可には使わない。

### 3.3 経路別の変更指示

#### A. 未回答クエリ

対象: `backend/api/routes/admin.py::list_unanswered_queries`

1. `_require_teacher` の直後、SQL 実行前に course owner/editor ゲートを置く。
2. 権限がない場合は空配列ではなく 404。
3. `JOIN users` と `student_name` の取得はゲート通過後にだけ行う。
4. course ID を別教員が直指定しても、件数・学生名・質問本文・日時を一切返さない。

#### B. document 再解析

対象: `backend/api/routes/admin.py::reanalyze_document`

1. 現在の `SELECT documents ... WHERE id=:id` より先、または同じ解決を置き換える形で
   editable document ゲートを通す。
2. `can_view` / `public` は再解析権限として扱わない。
3. MinIO 取得、前回 options 読み出し、background task 起動より先に 404 を返す。
4. 権限ゲートが返した canonical `document_id` / `source_path` を後続処理で再利用し、二重解決しない。

#### C. bridge-insights

対象: `backend/api/routes/admin.py::get_bridge_insights`

1. `aggregate_bridge_candidates(course_id)` より先に course owner/editor ゲートを置く。
2. k 匿名であっても、権限のない教員へ集約の存在・空非空・対象 course ID を開示しない。
3. 認可失敗時に集約処理を呼ばないことをテストで固定する。

#### D. 教材 PDF 差し替え

対象: `backend/api/routes/admin.py::reupload_material_pdf`

1. `get_material(material_id, current_user)` による閲覧ゲートを editable document ゲートへ置換する。
2. ファイル読取、PDF パース、類似度計算、MinIO upload より先に認可する。
3. viewer、course 経由閲覧者、public 文書を閲覧できるだけの教員は 404。
4. owner、document editor、SYSTEM_ADMIN のみ成功できる。

#### E. source-chunk

対象: `backend/api/routes/learning.py::get_source_chunk_route`

1. `get_accessible_course_data(user_id, course_id)` が `None` なら 404。
2. `list_course_source_document_ids(course_data)` で course source の canonical document ID 集合を得る。
3. 必要なら `list_visible_document_ids(user_id)` と積集合を取る。ただし course への正規アクセスが
   source 文書の開示根拠になる現行設計を壊してはならない。最終集合は必ず course source の部分集合にする。
4. `get_chunk_passage(chunk_id, allowed_document_ids=final_ids)` の SQL 内 `document_id = ANY(...)`
   でスコープを強制する。取得後の Python 判定だけにしない。
5. 同じユーザーが閲覧可能な別 course / public document の chunk ID を指定しても 404。

### 3.4 P0 テスト指示

新規テストは `backend/tests/test_object_scope_authorization.py` を正本とするか、既存の
`test_source_chunk_visibility.py` 等へ責務ごとに追加する。少なくとも以下を網羅する。

| 対象 | 正例 | 必須の負例 |
|---|---|---|
| unanswered-queries | owner / course editor / SYSTEM_ADMIN | 無関係な TEACHER、viewer のみ、存在しない course |
| reanalyze | document owner / document editor / SYSTEM_ADMIN | 他教員、viewer のみ、public 文書 ID 直指定、不明 ID |
| bridge-insights | owner / course editor / SYSTEM_ADMIN | 無関係な TEACHER、viewer のみ、不明 ID |
| PDF 差し替え | document owner / document editor / SYSTEM_ADMIN | 他教員、viewer のみ、public 文書、course 経由閲覧のみ、不明 material |
| source-chunk | 受講可能 course の source chunk | 同一ユーザーが見える別 course の chunk、public 文書だが course source でない chunk、他人の private chunk、不明 course/chunk |

追加ガードレール:

- 全負例が 404 で、レスポンス本文から対象の存在を判別できない。
- 認可失敗時に background task、MinIO upload、集約関数が呼ばれない。
- UI の状態に依存せず route 関数単体/API テストで成立する。
- `source-chunk` は空集合なら SQL を発行せず 404 へ縮退する。

### 3.5 P0 完了条件

- 5経路すべてが上記のサーバ側境界を持つ。
- 正例・負例テストが通る。
- [`auth-visibility.md`](auth-visibility.md) と [`docs/backend/api.md`](../backend/api.md) の
  対象エンドポイント記述を実装後の権限へ更新する。
- 全 backend テストが回帰なしで通る。

---

## 4. P1 — help_kb Phase 3 は保留

### 4.1 今回変更してはならないもの

- `HELP_KB_VECTOR_ENABLED` の既定値
- `backend/core/help_kb/vector.py`
- `backend/core/help_kb/store.py`
- `backend/core/help_kb/audit.py`
- migration 058 / 059 の追加・撤去・書き換え
- DB draft/freeze API と管理 UI の追加・撤去
- serving source の既定値・自動切替

### 4.2 保留中に許されること

- 読み取り専用の実測収集
- 稼働設定、ベクトル呼び出し回数、失敗時縮退、運用利用者の有無の記録
- 後続裁定のための Markdown メモ作成

保留解除には、[`manual_help_kb_design.md`](manual_help_kb_design.md) §5 Phase 3 の着手条件に対応する
実測根拠と、維持・既定 OFF・撤去の明示判断が必要である。P0/P2 の実装に便乗して判断しない。

---

## 5. P2 — 要素文脈提示 Phase 3

### 5.1 目的

数式ラベルの最上位候補として、教員が承認した contextual 説明の第1文を使う。
これにより A 層を再生成・改変せず、S2「文脈を見る」、S3 管理画面の展開、S4「深く検討」で
論文文脈に即した一行見出しを表示する。

既存の `labels.equation_label(explanation=...)` は入口まで実装済みである。
今回の本体は次の2点である。

1. contextual 生成の要素選抜で、教材に提示される数式を equation が末尾にあるため落とす問題を直す。
2. document + equation に対応する **approved contextual** 本文だけを取得し、
   `equation_label(..., explanation=body)` へ渡す。

### 5.2 説明生成対象の選抜是正

対象:

- `backend/core/document_pipeline/contextual_explanation_inputs.py`
- `backend/core/document_pipeline/orchestrator.py`（必要な配線と stage metadata のみ）
- `backend/tests/test_contextual_explanation_stage.py`

現状の `component → figure → claim → equation` を単純連結してから `max_elements` で切る方式では、
文書が大きいと equation が全件落ちる。次の区分に変更する。

1. **required equations**: 教材本文・根拠リンクに提示される数式。
   - course snapshot に既に `![[equation:id]]` があればその ID
   - 同じ教材生成規則が参照する component の `linked_equation_ids`
   - claim の `equation_ids`
   - thesis / derivation から教材提示対象として明示参照される equation ID
2. **existing priority elements**: component、linked figure、thesis 直下 claim、その他 claim。
3. **optional equations**: required に含まれない equation。

選抜順は `required equations → existing priority elements → optional equations` とする。

不変条件:

- required equation は通常の `max_elements` による優先順位競合で落としてはならない。
  `max_elements` は optional 枠の上限として扱い、required 分は別枠で確保する。
- 日次 CostGate は従来どおり最終的な硬い上限とする。上限到達時は生成せず、
  `skipped_by_limit` と対象数を stage artifact に記録する。
- 不明な equation ID を捏造して入力化しない。artifact に実在しない ID は
  `skipped_reason='equation_not_resolved'` として記録する。
- 同じ equation は1回だけ入力する。ID 正規化規則を course builder と二重実装しない。
- `meta` に少なくとも `required_equations_considered` / `required_equations_selected` /
  `required_equations_unresolved` を追加する。既存キーの意味は変えない。

### 5.3 approved contextual 説明の読み出し

対象:

- `backend/core/deliberation/context_lens.py`
- `backend/core/course_content_builder.py`
- 必要なら `backend/core/element_explanations.py` に**読み取り専用の一括 helper**
- `backend/tests/test_deliberation_context_lens.py`
- `backend/tests/test_context_lens_readability.py`
- `backend/tests/test_topic_material_evidence_items.py`

実装:

1. equation lens の構築時、`document_id` と `equation_id` をキーに説明を取得する。
2. 条件は `element_type='equation'`、`kind='contextual'`、`status='approved'`、本文非空。
3. 取得した本文を `_equation_label(record, symbols=..., explanation=...)` 相当の経路から
   `labels_mod.equation_label()` へ渡す。
4. 説明取得に失敗した場合は例外を伝播せず、既存ラダーへ fail-soft に縮退する。
5. candidate / dismissed / superseded / generic は見出し候補に使わない。

`core/deliberation/decomposition.py::_approved_contextual_explanation_for_instance()` に同じ読み出しの
先例がある。SQL や status 語彙をコピーせず、`core.element_explanations` の定数と既存 store API を使う。

同一 document で複数 equation を投影する経路では1件ずつ DB を引かない。
`approved_for_elements()` または同等の一括読み出しで `(element_type, element_id) -> body` を作り、
リクエスト中に再利用する。

### 5.4 snapshot への結線

S1 ホバーと S3 外殻は `learning_courses.data` の snapshot を読むため、live lens だけを直しても
Phase 3 は完了しない。`backend/core/course_content_builder.py` に次を実装する。

1. `build_course_content()` が解決済み `document_ids` を得た後、対象 document 全体の
   approved contextual equation 説明を1回で取得する。
2. 索引キーは `(document_id, equation_id)` とする。agent ID は document ごとに再利用され得るため、
   equation ID 単独の索引を禁止する。
3. `_collect_structured_content()` / `_enrich_topics()` の equation 投影へ、表示用 headline または
   approved 本文を内部材料として渡す。DB 取得を `build_topic_evidence_items()` のような純粋な
   読み取り helper 内へ持ち込まない。
4. `_equation_display_title()` は最終的に
   `labels.equation_label(record, explanation=approved_body, symbols=...)` と同じラダーを使う。
5. snapshot へ保存するのは可読 title/headline を基本とし、reviewer、status、confidence を保存しない。
   approved 本文を保存する場合も学習者へ必要な説明本文だけに限定する。
6. approved が無い旧文書・DB 読み出し失敗時は既存の決定論ラベルへ縮退し、course build を止めない。

これにより S2 / S3展開 / S4 はデプロイ直後、S1 / S3外殻はトピック再生成後に同じ見出しへ揃う。

### 5.5 ラベルと DTO の契約

- approved 説明の**第1文のみ**を見出しへ使う。切り詰めは `core/text_excerpt.py` の正本に委譲する。
- `label_source` は `LABEL_SOURCE_EXPLANATION` / 文字列 `"explanation"` になる。
- 説明全文は `focus.intrinsic_summary` へ複製しない。headline と summary の同一化を禁止する CP1 を守る。
- 学習者 DTO では従来どおり `label_source` を落とすが、生成された可読ラベルは表示してよい。
- candidate の存在、reviewer、confidence、生スコアを学習者へ出さない。
- approved 説明が TeX・内部 ID だけで構成される場合は `_usable()` により棄却し、既存ラダーへ落とす。
- S1 ホバー / S3 外殻は snapshot 再生成まで旧表示でもよい。live lens を読む S2 / S3展開 / S4 は
  デプロイ直後に改善される。

### 5.6 P2 テスト指示

最低限、次を追加する。

#### 選抜

- component / figure / claim が上限を埋めても required equation が選ばれる。
- required equation は重複しない。
- artifact にない required ID は生成入力に入らず、明示的 skip として記録される。
- optional equation は既存上限に従う。
- CostGate 到達時は LLM を呼ばず、required 対象数を artifact に残す。

#### ラベル結線

- approved contextual の第1文が equation headline になり、`label_source='explanation'`。
- candidate / dismissed / superseded / approved generic は採用されない。
- approved が無い場合は式番号・記号+役割・semantic summary・一般ラベルの既存順を維持する。
- DB 取得失敗時も lens 全体は 200 相当の DTO を返し、既存ラベルへ縮退する。
- course build が approved 説明を document + equation の組で解決し、再生成した snapshot の title に反映する。
- 別 document に同じ equation ID があっても説明が混線しない。
- course build の説明一括取得が失敗しても既存 snapshot 生成は成功する。
- 学習者 DTO に `label_source`、confidence、reviewer、説明ステータスが漏れない。
- headline に TeX、UUID、`eq_tex_*` 等の内部 ID が現れない。
- CP6 の関係集合 `(type,id,relation,status)` が変更前後で不変。

### 5.7 P2 完了条件

- required equation が contextual 生成対象から優先順位だけを理由に脱落しない。
- 承認済み contextual 説明が equation lens の最上位ラベルとして使われる。
- トピック再生成後の S1 / S3外殻にも同じ説明由来の可読見出しが保存される。
- 未承認・取得失敗時の既存縮退が維持される。
- A 層、DB schema、migration を変更していない。
- `element_context_presentation_redesign.md` §10.5 の Phase 3 を「実装済み」に更新し、
  変更ファイル・テスト結果・既知の縮退を実装記録へ追記する。
- backend 全体と関連する src テストが回帰なしで通る。

---

## 6. 実装順序とコミット境界

### Commit 1 — P0 セキュリティ境界

- 共通認可 helper
- 5 API のゲート
- 正例・負例・副作用前認可テスト
- `auth-visibility.md` / `docs/backend/api.md` 更新

P0 のテストが通るまで P2 と混ぜない。

### Commit 2 — P2 選抜是正

- required equation の導出・優先選抜
- stage metadata
- contextual stage テスト

### Commit 3 — P2 approved 結線

- approved contextual の一括読み出し
- equation label への配線
- lens / learner projection / readability ガードレール
- `element_context_presentation_redesign.md` 実装記録更新

P1 のコード変更は、いずれのコミットにも含めない。

---

## 7. 最終検証

最低限、以下を実行する。

```bash
backend/.venv/bin/pytest -q \
  backend/tests/test_object_scope_authorization.py \
  backend/tests/test_source_chunk_visibility.py \
  backend/tests/test_contextual_explanation_stage.py \
  backend/tests/test_deliberation_context_lens.py \
  backend/tests/test_context_lens_readability.py \
  backend/tests/test_element_context_core.py
```

新規テストを既存ファイルへ統合した場合はパスを読み替える。その後 backend 全体を実行する。

Docker 実機では次を確認する。

1. 他教員の course/document ID 直指定が一律 404。
2. 別 course で見える chunk を source-chunk に渡しても 404。
3. approved contextual 説明のある数式が S2 / S3展開 / S4 で同じ可読見出しになる。
4. candidate しかない数式は従来ラベルへ縮退し、学習者に候補状態が漏れない。
5. トピック再生成後、S1 / S3外殻も可読見出しへ追随する。

完了報告には、変更ファイル、権限マトリクスのテスト結果、required equation の選抜件数、
approved / fallback の両表示確認、全テスト結果を Markdown で記録する。
