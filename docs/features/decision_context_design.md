# 確定文脈の記帳（`decision_context`）

> **状態:** 実装済み（正本・凍結）— 2026-09-04

**正本**: 本ドキュメント（`backend/core/decision_context.py` の正本。以後の変更は §8 実装記録への追記で行う）。
**親文書**: [`docs/vision.md`](../vision.md) §4 改訂原則1（2026-09-04 の再審）/
[候補フロー設計書](candidate_flow_design.md)（候補→確定の制御フロー）/
[リリース前の確認](release_review_flow_design.md)（RR1〜RR7）。
**関連 migration**: なし（既存 `theory_review_events.metadata` JSONB に1ブロック足すだけ）。

---

## 1. 背景

2026-09-04 の[研究調査による再審](../architecture/vision-research-evaluation-and-reframed-direction-2026-09-04.md)
は、全層で最も強く反復されてきた原則「AI は候補まで・確定は人間」に対して2つの実証的な
指摘を突きつけた。

- **automation bias** — 自動助言は、人間が独力なら下せた正しい判断を覆すことがある。
  「人間が最後に見た」という事実は、判断の質を保証しない。
- **moral crumple zone** — 時間・情報・拒否権のいずれかを欠いた確定者は、判断の主体では
  なく**事故時に責任を吸収する置き場**になる。ゴム印の押印者を作ることは、答責性の実装
  ではなくその外注である。

原則そのものは維持しつつ、根拠を「AI の能力が劣るから」ではなく「訂正・撤回・制裁の**宛先**
が人間にしかないから」へ置き直し、確定の条件を次のように精密化したのが改訂原則1である。

> AI は生成と検査を担いうる。確定は、十分な能力・情報・時間・拒否権を持つ人間の判断を
> 含み、後から再構成・異議申立できる手続にのみ与える。

「後から再構成できる」を実装に落とすと、監査に必要なのは「誰が押したか」だけではない。

1. AI・人間・外部ツールがそれぞれ行った操作
2. 確定者に**提示された**根拠・代替案・既知の不確実性
3. 承認しない選択（却下・再検討・後回し）が可能だったか
4. 誰が再審を開始でき、どの経路・どの証拠で覆せるか

本層はこのうち 2〜4 を1つの JSON ブロックに畳み、**一括確定の経路がそれ無しには記帳
できない**構造を作る（1 は既存の `changed_by` / `action` 語彙が担っている）。

---

## 2. 不変条項（DC1〜DC4）

| ID | 条項 | 意味 |
|---|---|---|
| **DC1** | **一括確定は `decision_context` 無しに記帳しない** | 「次へ＝承認」型・「選択したN件を承認」型の一括確定は、監査 metadata に本ブロックを必ず含める。ガードレールが各経路のソースを構造的に検査する。 |
| **DC2** | **提示と適用を分けて記帳し、一致を偽らない** | `presented` と `applied` は別キーで持ち、その一致は集合比較で**導出**する（呼び出し側が「一致した」と申告できない）。表示上限で列挙を切り詰めた事実は `truncated` で出す。使えない再審経路を `reopen` に書かない。 |
| **DC3** | **代替の無い確定は記帳できない** | `alternatives_available` が空なら `ValueError`。却下・再検討・後回し・選択解除のいずれも無い「確定」はゴム印であって判断ではない。`decline_possible` は引数ではなく導出値（常に `True`）で、「断れなかった確定」を本プリミティブでは表現しない。 |
| **DC4** | **来歴申告はサーバ導出値と混ぜない** | 「画面に何を出していたか」はクライアントの自己申告であり、サーバは検証できない。`client_reported` に隔離し、未指定なら載せない（`core/teacher_triage.py::sort_metadata` が `sort_order` 未指定を `default` と偽装しないのと同じ流儀）。 |

継承する既存原則: 数値（`confidence` / `weight` / `score`）を載せない（[原則4](../vision.md#6-横断設計原則カタログ)）/
記帳先は既存 `theory_review_events`（新テーブル・新 `entity_type` を作らない・原則13）/
行削除しない（原則3）。

---

## 3. スキーマ

`core/decision_context.py::build_decision_context(...) -> dict`。キーは以下で固定
（過不足があればテストが落ちる）。

```json
{
  "basis": "release_review.placements",
  "presented": {"count": 6, "ids": ["...", "..."], "truncated": false},
  "applied":   {"count": 6, "ids": ["...", "..."], "truncated": false},
  "presented_matches_applied": true,
  "alternatives_available": ["reconsider", "reject", "skip_step"],
  "decline_possible": true,
  "reopen": {
    "path": "PATCH /api/admin/landscape/placements/{placement_id}",
    "statuses": ["rejected", "review_required"],
    "actor": "teacher"
  },
  "evidence_shown": null,
  "client_reported": {
    "presented_placement_ids": ["..."],
    "evidence_expanded_placement_ids": ["..."]
  }
}
```

- **`basis`** — どの画面のどの確定か。語彙の正本は `core/decision_context.py` の
  `BASIS_*` 定数（カタログ `BASIS_VALUES` が全値を列挙する。**新経路を足すときは定数を
  1本足し、§4 に節を足す** — 本書に一覧を書き写さない）。値は `画面.操作` の規約
  （小文字）で、カタログは検査用であって検証ゲートではない（任意の `basis` を弾かない）。
- **`presented` / `applied`** — id は正規化（空除去・重複除去）・ソートのうえ
  `PRESENTED_IDS_MAX`（200）件まで列挙する。`count` は切り詰め前の件数で、`truncated` が
  切り詰めの事実を残す。**一致判定は切り詰め前の集合**で行う（表示上限の副作用で判定が
  変わらない）。
- **`alternatives_available`** — 語彙は `reject` / `reconsider` / `dismiss` / `edit` /
  `skip_step` / `deselect`。未知の値は `ValueError`。**空も `ValueError`**（DC3）。
  「画面に出ていた代替」を書くのであって、「API 的に可能な操作」を書くのではない。
- **`decline_possible`** — 常に `True` の導出値。引数として受け取らない（キーワード専用
  引数の集合にこの名前が現れないことをガードレールが固定する）。
- **`reopen`** — 覆せる経路（HTTP メソッド + パス）と、戻せる status 語彙。戻せる語彙が
  無ければ `statuses` は空のまま（「戻せる」と偽らない）。`actor` は v1 では `teacher` 固定
  （学習者からの異議申立は vision §9 の未実装項目）。
- **`evidence_shown`** — 根拠（逐語引用）が画面に出ていたか。`None` は**不明**で、
  確認していないものを `True` にしない。**2026-09-10（是正 F7）以降、適用中の全経路で
  常に `None`** — 「根拠が出ていたか」はサーバが検証できないので、サーバが断言する位置
  （このトップレベルのキー）には載せない。画面側の事実は `client_reported` へ隔離する。
- **`client_reported`** — 検証していない自己申告。未指定・空なら `None`。
  是正 F7 以降、根拠の提示に関する申告は**DOM の実測**だけを送る
  （リリース前の確認 = `evidence_expanded_placement_ids`（折りたたみを実際に開いた行）/
  説明レビューキュー = `evidence_rendered_ids`（逐語引用が実際に描かれていた行））。
  開いた/描かれたの2値のみで、滞在時間・回数・視線は測らない（原則5: 監視しない）。
  1件も開かずに確定した事実は**空配列としてそのまま残る**（確定は止めない）。

`attach_decision_context(metadata, ctx)` は**新しい dict** を返す（引数を破壊しない）。
1リクエストで複数行を記帳する一括確定で、同じ ctx を安全に使い回すため。

本モジュールは純データ + 純関数（FastAPI / sqlalchemy / LLM 非 import）。

---

## 4. 適用先

> **適用先の正本は `backend/core/decision_context.py` の `BASIS_*` 定数**（本節に件数を
> 書き写さない — §5-6）。v1（2026-09-04）は §4.1 / §4.2 の一括2経路、2026-09-10 に
> §4.3（学習マップの対応付け保存）と §4.4〜§4.7（一括取り込み・骨格の凍結・コースの公開・
> 単発の承認）を追加した。カタログ `BASIS_VALUES` と定数集合の一致・命名規約（`画面.操作`・
> 小文字）は `test_decision_context_guardrails.py` が固定する。

**単発の確定における `presented` / `applied`（§4.6〜§4.7 の設計判断）**: 確定の対象が1
オブジェクトの経路では、`presented_ids` と `applied_ids` は**どちらもそのオブジェクト**に
する。「画面に出ていた根拠（backing claim・退避した解析時の警告）」を `presented` に
入れると、2つの集合が別の種類のものになるため `presented_matches_applied` が構造的に常に
`False` になり、DC2 が用意した「提示と適用の差の検出」を意味の無い定数に変えてしまう
（監査行に、起きていない不一致が毎回書かれることになる）。根拠は**同じ監査行の隣接キー
`grounds`** に id / フィールド名だけで残す（本文・`confidence` は載せない）。

### 4.1 リリース前の確認 ステップ2「この配置で次へ」

`POST /api/admin/landscape/courses/{course_id}/placements/accept`

| 項目 | 値 |
|---|---|
| `basis` | `release_review.placements` |
| `presented_ids` | **サーバ導出**。更新前に `landscape_store.list_for_documents(..., statuses=[inferred])` で edit 権限のある document の live な `inferred` を取り直す（クライアント申告に依存しない — DC2） |
| `applied_ids` | `accept_inferred_for_documents` が実際に遷移させた行 |
| `alternatives` | `reject` / `reconsider` / `skip_step`（各行の [却下] [再検討] とステップの「あとで」— RR4 / RR1） |
| `reopen` | `PATCH /api/admin/landscape/placements/{placement_id}` / `rejected`・`review_required` |
| `evidence_shown` | **常に `None`**（是正 F7 / 2026-09-10。サーバが検証できない値をサーバ導出値の位置に載せない） |
| `client_reported` | body の申告があるときだけ。`presented_placement_ids` / `evidence_expanded_placement_ids`（実際に開いた行）/ 旧 `evidence_shown`（後方互換で受けるが `client_reported` 内に留まる） |

body（`AcceptPlacementsRequest`）に optional の `presented_placement_ids` /
`evidence_expanded_placement_ids`（+ 後方互換の `evidence_shown`）を持つ。
**いずれもサーバの判断には使わない**（提示集合の正本は
サーバ側の取り直し）。レスポンスに `decision_context` を追加し、画面が一致の事実文を
出せるようにした（既存キー `course_id` / `confirmed` / `skipped_documents` は不変。
GET のエンベロープは非改変）。

ゼロ件確認では従来どおり監査を1件も出さない（記帳する確定が無い）。

### 4.2 説明レビューキューの一括承認・一括却下

`POST /api/admin/documents/{document_id}/element-explanations/bulk-review`

| 項目 | 値 |
|---|---|
| `basis` | `explanation_review.bulk` |
| `presented_ids` | body の `explanation_ids`（正規化後）。ここでは**教員がキューで選んだ集合そのもの**が提示集合である |
| `applied_ids` | `bulk_transition` が遷移させた行（競合・不正 id は `skipped`） |
| `alternatives` | 常に `deselect`（チェックボックス）、承認時は `dismiss`（行ごとの [却下]）。`edit` は**適用行がすべて開幕素材（document スコープ）のときだけ** — 要素スコープの行には「本文を編集」が出ない（`deliberation.js::_explanationReviewCardHtml`）ので、出ていない代替を「あった」と書かない |
| `reopen`（承認） | `PATCH /api/admin/element-explanations/{explanation_id}` / statuses は**空**。本文編集は旧行を `superseded` にして新 revision を作る（履歴保持）が、**status を `candidate` へ戻す経路は無い** |
| `reopen`（却下） | `POST /api/admin/documents/{document_id}/reanalyze` / `candidate`。却下行は `_EDITABLE_STATUSES`（candidate / approved）に入らないため PATCH では覆せない。実際の復帰経路は再解析による新しい candidate の再生成である |
| `evidence_shown` | **`None`**（是正 F7 / 2026-09-10。以前はキューの各行が `evidence_quote` / `reason` を描いているというコード上の観察を根拠にサーバが `True` を断言していたが、それは「描画するコードがある」ことしか意味しない） |
| `client_reported` | `sort_order`（TT3 の作法）と `evidence_rendered_ids`（是正 F7。逐語引用が実際に描かれていた行を `deliberation.js` が DOM から実測して申告する）。いずれも指定されたときだけ |

既存の `sort_metadata`（TT3）・`"bulk": True`・部分成功セマンティクスは不変。レスポンスに
`decision_context` を追加した（既存キー `updated` / `skipped` は不変）。

### 4.3 学習マップの対応付け保存（2026-09-10 追記・v1 の後に追加）

`PUT /api/admin/courses/{course_id}/atlas-binding`（リリース前の確認 ステップ1
「この対応で次へ」も同じ経路。実装は `routes/atlas.py::save_course_atlas_binding`）

| 項目 | 値 |
|---|---|
| `basis` | `atlas_binding.save` |
| `presented_ids` | 画面に並んでいた topic id（サーバがコース構造から取り直す） |
| `applied_ids` | 実際に `atlas_node_id` を確定させた topic id |
| `alternatives` | `deselect`（各行の「（対応なし）」）/ `skip_step`（ウィザードの「あとで」— RR1） |
| `reopen` | `PUT /api/admin/courses/{course_id}/atlas-binding` / statuses は**空**（status 語彙を持たない層なので「戻せる status がある」と偽らない） |
| `evidence_shown` | `None`（提案の根拠が画面に出ていたかはサーバから検証できないため、無条件 `True` にしない） |

レスポンスに `decision_context` を追加（既存キー `course_id` / `cartridge_id` /
`bindings_applied` / `bindings_skipped` は不変）。

### 4.4 arXiv 候補の一括取り込み（2026-09-10 追記）

`POST /api/admin/discovery/ingest-batch`（`routes/paper_discovery.py::ingest_batch`）

| 項目 | 値 |
|---|---|
| `basis` | `discovery.ingest_batch` |
| `presented_ids` | body の `items[].arxiv_id`。**教員が画面で選んだ集合そのものが提示集合**（§4.2 と同じ理由） |
| `applied_ids` | `enqueue_items` が実際にキューへ積んだ行（不正 ID 等は `skipped` に事実文つきで残る） |
| `alternatives` | `deselect`（候補行のチェックボックス）/ `dismiss`（行ごとの「見送る」= `POST /dismiss`。行削除ではなく status 遷移で保持） |
| `reopen` | `DELETE /api/admin/materials/{material_id}` / statuses は**空**。キュー行の `retry` は「失敗の再試行」であって取り込みの取り消しではないので、実際に覆せる経路（教材そのものの削除。§4 の是正 F11 で記帳されるようになった）を書く |
| `evidence_shown` | `None` |

取り込みは知識の確定ではなく資料の受け入れだが、**取り込みの弁は教員だけが持つ**（PD1）
＝「選択した N 件を承認した」型の一括確定なので記帳の対象に含めた
（[六つのレンズ 02_teacher.md](../architecture/six_lenses_2026-09-10/02_teacher.md) 付記の
未決を「対象とする」で決着させた）。監査 metadata には既存キーに加えて `"bulk": True`。
**レスポンスは非改変**（`queued` / `skipped` / `notice` のみ — この波は記帳だけを足す）。

### 4.5 分野の地図 骨格の凍結（2026-09-10 追記）

`POST /api/admin/cartridges/{cartridge_id}/atlas/skeleton/freeze`（`routes/atlas.py::freeze_atlas_skeleton`）

| 項目 | 値 |
|---|---|
| `basis` | `atlas_skeleton.freeze` |
| `presented_ids` | 影響プレビュー（`freeze-impact`）が計算対象にした **draft の node id**（region + concept） |
| `applied_ids` | 実際に版へ入った **frozen の node id**。presented とは別オブジェクトから導出するので、一致判定が本物の検査になる |
| `alternatives` | `edit`（凍結せず draft を直す）/ `skip_step`（凍結を見送り draft のまま置く）— 影響プレビューは事実文つき confirm で提示される |
| `reopen` | `POST /api/admin/cartridges/{cartridge_id}/atlas/skeleton/draft/from-frozen` / statuses は**空**。凍結版は不変（AB3）で「戻せる status」は無く、実際に覆す道は次版 draft を起こして直すことだけ |
| `evidence_shown` | `None`（confirm を読んだかはサーバから検証できない） |

プレビューで**見せた影響そのもの**は監査 metadata の隣接キーに事実として残す
（`impact_removed_node_ids` / `impact_added_node_ids` / `impact_affected_course_ids`）。
既存キー（`version` / `note` / `report_credits` / `category_gaps_applied` /
`relation_edges_applied` 等）とレスポンス（`cartridge_id` / `frozen` / `impact` /
`notified`）は不変。

### 4.6 コースの公開（2026-09-10 追記）

`PUT /api/admin/courses/{course_id}/visibility` の **`visibility="public"` のときだけ**
（`routes/admin.py::update_course_visibility`。リリース前の確認 ステップ3「公開する」も
同じ経路を通るので、記帳はこの1箇所で足りる）

| 項目 | 値 |
|---|---|
| `basis` | `course_visibility.publish` |
| `presented_ids` / `applied_ids` | どちらも `[course_id]`（確定の対象はコース1件 — 上記「単発の確定における presented / applied」） |
| `alternatives` | `skip_step`（ウィザードの各ステップの「あとで」。飛ばしても学習者側の表示は変わらない — RR1） |
| `reopen` | `PUT /api/admin/courses/{course_id}/visibility` / `group`・`private`（同じ経路で非公開へ戻せる。ただし**一度見られた事実**は戻らない） |
| `evidence_shown` | `None`。ウィザード経由かどうかもサーバから判別できないので `client_reported` も使わない（既存の「申告しない」方針を維持） |

`group` / `private` への遷移は従来の記帳のまま（確定文脈を付けない）。公開だけが
「一度出たら戻らない」開示だからである。レスポンス（`course_id` / `visibility` /
`group_id`）は不変。

### 4.7 単発の承認 — component / claim（2026-09-10 追記）

`POST /api/admin/theory-components/{component_id}/approve` と
`POST /api/admin/claims/{claim_id}/review`（グラフ対話レビュー画面の承認ボタン・
根拠 claim 行の承認／却下。実装は `routes/theory_components.py`）

| 項目 | component | claim |
|---|---|---|
| `basis` | `component_review.single` | `claim_review.single` |
| `presented_ids` / `applied_ids` | `[component_id]` | `[claim_id]` |
| `grounds`（隣接キー） | `backing_claim_ids`（コンポーネント全体 + 各項目の `evidence_claims`。承認可能性の判定が読むのと同じ集合）/ `retained_validation_warning_fields`（是正 F6 で**消さずに退避**した解析時の警告のフィールド名） | `document_id` / `support_status` |
| `alternatives` | `reject`（却下ボタン）/ `skip_step`（「次の未レビューへ」= 判断を保留して飛ばす） | `reconsider` + `skip_step`。却下でないときは `reject` も。**却下へ遷移するときは画面に出ていない `reject` を書かない** |
| `reopen` | `POST /api/admin/theory-components/{component_id}/reject` / `rejected` | `POST /api/admin/claims/{claim_id}/review` / 適用した語彙以外の `_CLAIM_REVIEW_STATUSES` |
| `evidence_shown` | `None` | `None` |

**承認可能性の判定（`_component_approval_problems`）と遷移実体
（`_transition_component_review` / `_apply_claim_review_side_effects`）は非改変**。両者に
optional の `audit_metadata` を足しただけで、遷移する列・却下伝播・R層の item オーサリング
起動は一切変わらない（ガードレールが「判定と 422 の区画に警告が現れない」ことと
「SQL が status 系の列だけ」を固定する）。component の**却下**（`/reject`）には v1 では
確定文脈を付けない（却下は「学習者に出す」確定ではなく差し戻しであり、`reject` 経路自体が
`approve` の再審経路として記帳される側だから）。レスポンス（`TheoryComponentOut` /
`ClaimOut`）は不変。

---

## 5. UI（リリース前の確認）

- **根拠を見る**（`release-review.evidence`）— ステップ2の各行に、その配置の根拠として AI が
  引用した論文の逐語引用を畳んで置く（既定は閉じたまま）。引用の無い行でも折りたたみ自体は
  出し、「この配置には論文からの引用が残っていません。」と事実で書く（無い行だけ静かに
  欠けると「見た／見ていない」が後から再構成できない）。マニュアル節は
  `docs/manual/teacher/13-admin-course-management.md#release-review-evidence`。
- **再審の事実文** — 「次へ」の意味（`NOTICE_NEXT_MEANING`）に続けて
  「確認後も、教材管理の「位置づけ（分野マップ）」から個別に再検討・却下へ戻せます。」を
  常時表示する（確定の前に代替が、確定の後に再審経路が見えている状態を画面で作る）。
- **一致の事実文** — accept の応答 `decision_context.presented_matches_applied` から
  「表示されていた配置と確認した配置は一致しています」／
  「表示と確認した配置に差がありました（画面を再読み込みしてください）」を1行出す
  （差があっても公開は止めない — RR7）。数値は既存の「未確認 N件」以外に増やさない。
- accept の body には `presented_placement_ids`（画面に `inferred` として描かれていた行）と
  `evidence_expanded_placement_ids`（**根拠の折りたたみを実際に開いた行**）を載せる
  （是正 F7 / 2026-09-10）。固定の `evidence_shown: true` はやめた — 「引用の有無に
  関わらず全行に折りたたみを出している」のは事実だが、それは「根拠が提示された」であって
  「根拠が見られた」ではなく、後から見分けられなくなる。開いた行の id は `<details>` の
  `toggle` イベントだけから集め（開いた/開かなかったの2値。滞在時間は測らない）、1件も
  開いていなければ空配列がそのまま監査に残る（確定は止めない — RR7）。

---

## 6. 段階適用の残り

vision §9 は本層を「段階適用中」と位置づけている。v1 で入れたのは**一括確定の2経路**だけで、
2026-09-10 に §4.3〜§4.7（学習マップの対応付け保存・一括取り込み・骨格の凍結・コースの
公開・単発の承認）を追加した。以下がまだ未適用（着手時は本書 §4 に節を足し、`basis` 定数を
1本足す）。

- 説明の**個別**の approve / dismiss、W層注釈の commit（`element_annotations` の
  candidate → committed）— 一括経路（§4.2）と単発経路の記帳が非対称なまま
- ライブラリの凍結（L層 `library_entries` の freeze）
- 学習者側の確定（tension / anchor の confirm）— 本人の痕跡は監視しない原則（PN-1 / P3）と
  の兼ね合いを先に決める。**「本人が自分の確定を後から再構成できる」ための記帳**であって、
  教員・運営が読むための記帳にしてはならない。
- component の**却下**（`/reject`）— 却下は差し戻しであって「学習者に出す」確定ではなく、
  それ自体が §4.7 の再審経路として記帳される側なので、v1 では意図的に対象外にした

`reopen.actor` に `learner` を足すのは、学習者からの異議申立経路（vision §9「切断・撤回の
一級化」「帰属記帳と開示の分離」）が実装されてからにする。

---

## 7. ガードレール

`backend/tests/test_decision_context.py`（プリミティブ）と
`backend/tests/test_decision_context_guardrails.py`（構造）で以下を固定する。

- core が fastapi / sqlalchemy / pydantic / openai を import しない・SQL を書かない
- `decline_possible` / `presented_matches_applied` が**引数に無い**（申告できない — DC2/DC3）
- 代替が空・未知語彙・`basis` 空・`reopen_path` 空は `ValueError`（DC3）
- 上限 200 の切り詰めと `truncated`、一致判定が切り詰め前の集合であること
- `client_reported` の隔離・未指定時 `None`・入力 dict の別名共有をしないこと（DC4）
- 適用済みの各経路のソースに `build_decision_context(` / `attach_decision_context(` /
  それぞれの `basis` 定数が現れること（DC1。経路が増えたら本テストに1ケース足す）
- カタログ `BASIS_VALUES` が `BASIS_*` 定数の集合と一致し、ソート済みで、全ての値が
  `画面.操作`（小文字・ドット1つ以上）の規約に従うこと。カタログは**検証ゲートではない**
  （任意の `basis` を弾かない — 既存呼び出しを壊さない）
- 全 `basis` で DC1〜DC4 が同じ形で成り立つこと（basis ごとの例外を作らない。
  `alternatives=()` は basis を問わず `ValueError`）
- 単発の確定が `presented_ids == applied_ids == [対象]` であること（§4 の設計判断。
  `presented_matches_applied` を構造的な定数にしない）
- 記帳の無い状態変更を塞いだ3経路（原則14 / 是正 F11）の回帰検出 —
  教材の物理削除が commit **後**に記帳されること・タイトルを metadata に載せないこと /
  トピック保存が変更フィールド名だけを載せ本文を載せないこと / 版の adopt が
  `core/versioning/audit.py` 経由で記帳され、route 側で二重記帳しないこと
- リリース前の確認 JS が `presented_placement_ids` / `evidence_expanded_placement_ids` を
  送り（固定の `evidence_shown` は送らない）、`release-review.evidence` アンカー付きの
  折りたたみと再審の事実文を描くこと。開いた事実は `toggle` イベントだけから集め、
  滞在時間・回数（`Date.now()` / `setInterval` / `dwell`）を測らないこと
- 適用済みの全経路が `evidence_shown=None` を渡し、`evidence_shown=True` の断言が
  ソースに現れないこと（是正 F7 / DC4）

加えて `test_release_review.py`（提示と適用の一致・不一致・来歴申告の隔離）と
`test_teacher_triage_api.py`（説明の一括承認・一括却下の `basis` / 代替 / 再審経路）が
経路ごとの値を固定する。

---

## 8. 実装記録（2026-09-04）

- `backend/core/decision_context.py` 新設（純データ + 純関数。`core/privacy.py` /
  `core/label_vocab.py` / `core/candidate_flow.py` に続く横断プリミティブ）
- `backend/api/routes/landscape.py` — `AcceptPlacementsRequest` に optional 2フィールド、
  更新前の提示集合の取り直し、per-row 監査への添付、レスポンスへの `decision_context`
- `backend/api/routes/element_explanations.py` — bulk-review の per-row 監査への添付と
  レスポンス追加。代替・再審経路は action と適用行のスコープから導出（出ていない代替を
  書かない）
- `frontend/public/js/admin-release-review.js` — 「根拠を見る」折りたたみ・再審の事実文・
  一致の事実文・accept body の来歴申告
- アンカー3点セット: `release-review.evidence` を `KNOWN_ADMIN_UI_ANCHOR_IDS` /
  `ADMIN_UI_ANCHORS` / マニュアル節に追加（総数は `test_admin_help_ui_anchors.py` が正）
- テスト: `test_decision_context.py`（24）/ `test_decision_context_guardrails.py`（15）+
  既存2ファイルへの追記
- ブリーフからの逸脱（判断の記録）:
  1. 説明の一括却下の `reopen_path` を承認と同じ PATCH にしなかった。却下行は編集できず、
     PATCH は 422 になる — 使えない経路を再審経路として記帳するのは DC2（一致・可能性を
     偽らない）に反する。実際に効く経路（再解析による新しい candidate）を書いた。
  2. `edit` を説明の代替に無条件では入れなかった。「本文を編集」は開幕素材行にしか
     出ないため、適用行がすべて document スコープのときだけ記帳する。

### 8.1 追記（2026-09-10）— 学習マップの対応付け保存への適用

`PUT /api/admin/courses/{course_id}/atlas-binding`（`routes/atlas.py::save_course_atlas_binding`）を
3本目の適用先として追加した（`BASIS_ATLAS_BINDING_SAVE` / `_BINDING_REOPEN_PATH`。値の詳細は
§4.3）。文書側の追随はこのとき漏れており、
[六つのレンズ調査](../architecture/vision_ux_gap_six_lenses_2026-09-10.md) §4 第1波 #11 の
文書ズレとして 2026-09-10 に解消した（`docs/vision.md` §4/§9 と本書 §4/§6/§7）。

### 8.2 追記（2026-09-10）— `evidence_shown` の隔離（是正 F7b）

[六つのレンズ調査](../architecture/vision_ux_gap_six_lenses_2026-09-10.md) §4 第1波 #5
（`six_lenses_2026-09-10/02_teacher.md` §1-8 と提案7）の是正。**「根拠が画面に出ていたか」を
サーバが断言するのをやめた**。

- `routes/element_explanations.py` — bulk-review の `evidence_shown=True`（コード上の観察を
  根拠にした断言）を `None` にし、body に optional `evidence_rendered_ids` を追加。
- `routes/landscape.py` — accept が body の申告値をトップレベル `evidence_shown` へ載せ替えて
  いたのをやめ（常に `None`）、body に optional `evidence_expanded_placement_ids` を追加。
  旧 `evidence_shown` は後方互換で受けるが `client_reported` に留まる。
- `frontend/public/js/admin-release-review.js` — `<details class="release-review-evidence">` に
  `data-placement-id` を付け、`toggle` イベントで開いた行だけを `state.evidenceExpanded` に
  記録して送る（固定の `evidence_shown: true` を削除）。モーダルを開き直すとリセットする。
- `frontend/public/js/deliberation.js` — 一括承認・一括却下が、選択行のうち逐語引用が実際に
  描かれていた行（`.deliberation-annotation-reason` の実在）を DOM から実測して申告する。
- 判断（迷った点）: 提案7 の (c)「1件も開かずに確定しようとしたら確認1行を出す」は本波では
  入れなかった。リリース前の確認に確認ダイアログが無いこと自体が別の指摘（02_teacher.md
  §1-7）で、そこと合わせて設計するのが筋だと判断した。空配列がそのまま記帳されるので、
  「開かずに確定した」事実は今の実装でも失われない。

### 8.3 追記（2026-09-10）— 段階適用の第2波 + 記帳の無い状態変更の是正

[六つのレンズ調査](../architecture/vision_ux_gap_six_lenses_2026-09-10.md) §4 第1波 #6
（是正 F11 / 既知 A-04・F-18 / 監査 C6）の実装。**A. 記帳の無い状態変更**と
**B. `decision_context` の適用拡張（記帳のみ・挙動不変）**の2つを同時に入れた。

**A. 記帳の無い状態変更（原則14 の穴）**

| 経路 | entity_type | 記帳 |
|---|---|---|
| `DELETE /api/admin/materials/{material_id}`（`routes/admin.py::delete_material`） | `material`（新設） | `active → deleted` + `action="deleted"` / `document_id` / `deleted_course_ids` / `confirm_name_matched`。**DB 削除の commit 後**に記帳する（ロールバックした削除を「消した」と書かない）。タイトル・本文は載せない。既存の V層 `teardown_versioning` 後始末は非改変 |
| `PUT .../lecture-studio/course-topics/{topic_id}`（`routes/lecture_studio/topics.py`） | `course_topic`（新設） | `"" → edited` + `action="topic_draft_saved"` / `topic_id` / `changed_fields` / `topic_audio_cache_invalidated`。**本文は載せず変わったフィールド名だけ**。未設定 → 空を「変更」と書かないため `_TOPIC_DRAFT_FIELD_EMPTY` の正規形で前後比較する |
| 版の adopt（`versioning.py::adopt_release`） | — | **是正不要（調査の偽陽性）**。V層は CLAUDE.md が認めた core 直記帳（`core/versioning/audit.py`）で既に `shared_subscription` / `action="adopt"` を記帳している。調査はルートファイルの `record_review_event` を grep した結果で「無い」と読んだ。route に足すと1操作2行になるので**足さず**、`test_shared_versioning_guardrails.py` に「core 側で記帳・route 側で二重記帳しない」の回帰検出を置いた |

新 entity_type 2本は `core/schema.py` の `AUDIT_ENTITY_*` カタログと `AUDIT_ENTITY_TYPES` に
登録し、`test_audit_entity_catalog_guardrails.py` の `_AUDIT_CALLER_FILES` に
`lecture_studio/topics.py`（+ `paper_discovery.py`）を追加した（生文字列の混入検出の対象に
含める）。

**B. 適用拡張** — §4.4〜§4.7（`BASIS_DISCOVERY_INGEST_BATCH` /
`BASIS_ATLAS_SKELETON_FREEZE` / `BASIS_COURSE_VISIBILITY_PUBLISH` /
`BASIS_COMPONENT_REVIEW_SINGLE` / `BASIS_CLAIM_REVIEW_SINGLE`）。あわせてカタログ
`BASIS_VALUES` を追加した（適用先の正本は定数のままで、カタログは命名規約と網羅の**検査用**。
`build_decision_context` は任意の `basis` を受け続ける）。**この波はレスポンスを1つも
変えていない**（記帳のみ）。

**判断（迷った点）**

1. **単発の確定の `presented`** — ブリーフは「presented = 承認画面に出ていた backing claim /
   validation_warnings の ID 集合」としていたが、そうすると提示集合と適用集合が別の種類の
   ものになり `presented_matches_applied` が構造的に常に `False` になる。DC2 は「一致を
   偽らない」ための条項であって、起きていない不一致を毎回書くのは同じ条項に反する。
   よって **presented = applied = 対象オブジェクト**とし、根拠は隣接キー `grounds` に
   移した（記帳される情報量は同じで、再構成に必要な面は落ちていない）。§4 に明記した。
2. **`_transition_component_review` / `_apply_claim_review_side_effects` の非改変** —
   監査の記帳がこの2関数の中にあるため、確定文脈を「加算的に」渡すには口が必要だった。
   optional `audit_metadata` を1つ足し、遷移 SQL・承認可能性の判定・却下伝播・R層フックは
   一切触っていない（ガードレールで固定）。
3. **骨格の凍結の `presented`** — 「プレビューで見せた影響コース／差分」を `presented` に
   入れると 1 と同じ問題が起きる。`presented` = プレビューの計算対象になった draft の node、
   `applied` = 版に入った node とし（別オブジェクトからの導出なので一致判定が本物の検査に
   なる）、見せた影響そのものは `impact_*` の隣接キーに残した。
4. **取り込み（ingest-batch）を対象に含めた** — 02_teacher.md 付記は「取り込みは知識の確定
   ではないので対象外という整理はありうる」と未決にしていたが、**取り込みの弁は教員だけが
   持つ**（PD1）＝「選択した N 件を承認した」型の一括確定であり、しかも取り消しには教材の
   削除（不可逆）が必要なので、記帳の対象に含めるほうが答責性の実装として一貫すると判断した。
5. **公開だけに付けた** — `group` / `private` への遷移は可逆で「一度出たら戻らない」性質を
   持たないので、従来の記帳のままにした（確定文脈の意味を薄めない）。
