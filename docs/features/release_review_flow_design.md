# リリース前の確認フロー（Release Review Flow）

**正本**: 本ドキュメント（2026-08-05 起票・同日実装）。
**親文書**: `knowledge_landscape_design.md`（配置層 LS1〜LS10）/
`atlas_binding_lifecycle_design.md`（コース⇄地図バインドの AB系条項、AB1〜AB8）/
`guidance_layer_design.md`（G層 G1〜G8）。
**関連 migration**: なし（既存 `landscape_placements` / `learning_courses.data` のみを使う）。

---

## 0. 不変条項（RR1〜RR7）

| ID | 条項 | 意味 |
|---|---|---|
| **RR1** | **既定は「提示されたものが出る」** | 教員が何もしなければ AI の提案がそのまま学習者に届く。確認画面をスキップしても、閉じても、学習者側の表示は変わらない（未確認 `inferred` も「AIによる推定（未確認）」ラベル付きで表示される既存挙動をそのまま使う）。 |
| **RR2** | **承認は人間の1操作** | 「次へ」は教員が提示を見て進めた明示操作であり、これを承認とみなす。AI が自分で `confirmed` を書く経路は作らない（LS3 不変）。 |
| **RR3** | **一括承認の出所を偽らない** | 「次へ」経由の確認は個別レビューと区別できる形で記録する（`review_note` に事実文、監査は `action="accept_on_release"`）。「1件ずつ読んで確認した」ことにしない。 |
| **RR4** | **修正はいつでもできる** | 確認画面の各行に [却下] [再検討] を出したままにする。却下・再検討済みの行は「次へ」で `confirmed` に**しない**（教員の判断を一括操作が上書きしない）。 |
| **RR5** | **情報を落とさない** | 行削除しない（P4 / LS3 継承）。却下は `rejected`、再解析での置換は `superseded`。 |
| **RR6** | **数値を見せない** | weight・confidence の生値を出さない（LS5 継承）。件数（「未確認 6件」）は評価ではない事実なので出してよい。 |
| **RR7** | **リリースを止めない** | 確認画面はゲートではない。骨格なし・配置ゼロ・API 失敗のいずれでも、事実文に縮退して「公開」まで進める（fail-open。公開の前提条件を新設しない）。 |

RR1 と RR2 は一見矛盾するが、役割が違う。RR1 は**配信の既定値**（黙っていれば出る）、
RR2 は**ラベルの意味**（`confirmed`＝教員が見た）を守る条項である。両立させるために、
「次へ」を押さずに閉じた場合も配信は起こり、ただし status は `inferred` のまま残る。

### 承認ラベルについての明示的なトレードオフ

「次へ＝一括承認」は、教員が1件ずつ読まずに `confirmed`（学習者向けラベル「教員確認済み」）
を付けられることを意味する。ラベルの厳密さは下がる。この判断はオーナーの明示的な要求
（2026-08-05「通常はマップを提示し、『次へ』などを押すことで承認とみなすでよい」）に基づく。
緩和策は RR3 のみ（監査と `review_note` で「リリース前の確認画面で一括確認」と正直に残し、
1件ずつのレビューと区別できるようにする）。学習者向けラベルは区別しない（学習者に
「一括確認済み」という内部事情を見せない方が誠実だと判断した）。

---

## 1. 背景（2026-08-05 の調査結果）

宇宙物理（`astrophysics`）の基準地図を追加した直後に、「論文投入からコースのリリースまで、
教員は何を確認させられるのか」を実装ベースで調査した。結果:

**満たされていたこと**
- `landscape_placement` ステージは解析パイプラインに常時登録済みで、凍結骨格を持つ active
  ドメイン全部（`astrophysics` / `particle_physics`）に対して自動で配置候補を作る。
- 配置候補は全件 `status='inferred'` で、**却下しない限り学習者に表示される**
  （`LEARNER_VISIBLE_STATUSES` に `inferred` が含まれる）。つまり「修正しなければ AI の提案が
  リリースされる」は既に実装されていた。
- 公開（`PUT /api/admin/courses/{id}/visibility`）に前提条件ゲートは無い。

**満たされていなかったこと（本設計の動機）**
1. **リリース前に提示されない。** 配置は教材管理の「⋯」メニュー →「位置づけ（分野マップ）…」
   の奥にあり、教員が自発的に探さない限り目に入らない。
2. **状態が一覧に出ない。** 教材行・コース行に配置状態のインジケータが無い。
3. **G層 To-Do も無い。** `material.landscape_unreviewed` は設計上 Phase 2 送り。図分類・
   議論のきっかけには通知があるのに配置には無い、という非対称。
4. **一括操作が無い。** 配置1件ずつ [確認][却下][再検討]。論文2本×2ドメインで十数クリック。
5. **リリース手順が分散している。** バインド（コースビルダー直後のパネル）→ 公開
   （コース管理タブの別ボタン）が別画面で、間に「何が学習者に出るのか」を見る場所が無い。

---

## 2. 決定した UX

コース登録後、**1つのウィザード**で「AI が作った地図を見る → 次へ（＝承認）→ 公開」まで
到達させる。ステップは3つで、各ステップの主ボタンは「次へ」（承認とみなす）。

```
コースを登録（既存）
      ↓  ← 自動で開く
┌─ リリース前の確認 ───────────────────────────────┐
│ ステップ 1/3  学習マップの割り当て                          │
│   ・propose の提案が既定で選択済み（既存 atlas-binding UI 再利用）  │
│   ・[この対応で次へ] = PUT atlas-binding して次へ                │
│   ・[あとで] = 保存せず次へ（RR7）                              │
├──────────────────────────────────────────────┤
│ ステップ 2/3  論文の位置づけ（分野マップ上の配置）                 │
│   ・ドメイン別・観点別に配置候補を提示（未確認/確認済みのチップ付き） │
│   ・各行に [却下] [再検討]（RR4）                               │
│   ・[この配置で次へ] = 未確認(inferred)を一括 confirmed（RR2/RR3） │
│   ・[あとで] = 何も書かずに次へ（inferred のまま = RR1）           │
├──────────────────────────────────────────────┤
│ ステップ 3/3  公開                                          │
│   ・「学習者に出るもの」の事実サマリ（地図・配置・未確認件数）        │
│   ・[公開する] = PUT visibility public                        │
└──────────────────────────────────────────────┘
```

### 入口（2つ）

1. **コースビルダーで登録した直後**（自動）。従来はインラインの atlas-binding パネルが
   開いていたが、その位置でウィザードを開く。パネル自体は fallback として残す
   （ウィザードが読み込まれていない環境で従来動作に縮退する）。
2. **コース管理タブの所有行「確認して公開」ボタン**（いつでも）。既存コースの状態確認・
   再リリースにも使える。ここが「今どういう状態かを常に確認できる」導線になる。

### 提示するが承認を求めないもの

図の分類（`suggested_mode`）・二層説明・議論のきっかけは本ウィザードの対象外。前者は
未レビューでも AI 候補で動くため提示不要、後者2つは `approved` のみ配信という別の
（意図的な）オプトイン設計なので、リリースの必須ステップに混ぜない。

---

## 3. 実装

### 3.1 バックエンド

**`core/landscape/store.py`** — 関数1本を追加。

```python
accept_inferred_for_documents(session, document_ids, *, reviewer_id, note="") -> list[dict]
```

- `status='inferred'` の行だけを `confirmed` に遷移させる（`WHERE status = 'inferred'`）。
  `rejected` / `review_required` / `confirmed` / `superseded` には触らない（RR4 / LS3）。
- `document_ids` が空なら **SQL を発行せず** `[]`（fail-closed。「空集合＝全件」に転ばせない）。
- `reviewed_by` / `reviewed_at` / `review_note` を書く。`note` は空なら既存を保持（P4）。
- DELETE 文は追加しない（RR5）。

**`api/routes/landscape.py`** — 教員 API を2本追加（どちらも `_require_teacher`）。

| メソッド・パス | 役割 |
|---|---|
| `GET /api/admin/landscape/courses/{course_id}/placements` | コースのソース論文の live 配置をまとめて返す（document 別・DTO は既存 `admin_placement_dto`）。`unplaced_domains`・骨格版・`pending_count`（未確認件数）・`hidden_count`（閲覧不可 document 由来の件数）を同梱。 |
| `POST /api/admin/landscape/courses/{course_id}/placements/accept` | 「次へ」の実体。**edit 権限のある** document の `inferred` を一括 `confirmed` にし、1件ごとに監査記帳（`action="accept_on_release"`）。戻り値は `{course_id, confirmed, skipped_documents, decision_context}`（`decision_context` は 2026-09-04 追補 — §6）。 |

- コースのゲートは `services.get_accessible_course_data`、document のゲートは
  `services.resolve_document_access`（read=view / accept=edit）。権限の無い document は
  **静かに除外し件数だけ返す**（403 にしない — 共同編集コースで自分の権限外の論文が
  混ざっていてもリリースを止めない、RR7）。
- 対象 document は `services.list_course_source_document_ids`（コース sources のみ）。
- DELETE ルートは追加しない（LS3 / P4）。

### 3.2 フロントエンド

**`frontend/public/js/admin-release-review.js`**（新規・ES5・`window.AdminReleaseReview`）

- `init(deps)` で DI（`apiFetch` / `escHtml` / `atlasBindingPropose` / `refreshNextSteps` /
  `onPublished`）。admin-llm-usage.js と同型。
- `open(courseId, courseTitle, options)` でウィザードを開く。`options.autoOpened` は
  コースビルダー直後の自動表示を示す（文言だけを変える）。
- ステップ1は **既存 `atlasBindingRenderEditor` をそのまま埋め込む**（分割ロジック・保存
  ペイロードを再実装しない）。主ボタンのラベルだけ `options.saveLabel` で「この対応で次へ」に
  差し替え、`onSaved` で次のステップへ進む。
- ステップ2は `GET .../courses/{id}/placements` を描画し、[この配置で次へ] で
  `POST .../accept`。各行の [却下] [再検討] は既存 `PATCH /admin/landscape/placements/{id}`。
- ポーリングしない。数値は件数のみ（weight / confidence のキーを読まない、RR6）。
- どのステップも API 失敗時は事実文を出したうえで次へ進める（RR7）。

**`frontend/public/js/admin.js`**

- `atlasBindingRenderEditor` に `options.saveLabel`（既定「この対応で反映」）を追加。
- コース管理の所有行に「確認して公開」ボタン（`data-ui-anchor="course-management.release-review-btn"`）。
- コースビルダー登録直後の分岐で `AdminReleaseReview.open(...)` を呼ぶ（未ロード時は従来の
  インラインパネルへ縮退）。
- 起動時に `AdminReleaseReview.init({...})`。

### 3.3 管理UI 3点セット（CLAUDE.md の規約）

| anchor_id | 担体 | マニュアル節 |
|---|---|---|
| `course-management.release-review-btn` | コース管理の所有行ボタン | `teacher/13-admin-course-management.md#release-review-btn` |
| `release-review.modal` | ウィザードのオーバーレイ | `#release-review-modal` |
| `release-review.next` | 各ステップの主ボタン（＝承認） | `#release-review-next` |
| `release-review.publish` | ステップ3の「公開する」 | `#release-review-publish` |

`KNOWN_ADMIN_UI_ANCHOR_IDS` / `ADMIN_UI_ANCHORS` は 244 → 248 件。
`_ADMIN_FRONTEND_SOURCES`（`test_admin_help_inspect_ui_static.py`）に新 JS を登録する。

---

## 4. ガードレール

`backend/tests/test_release_review.py`（store + API）と
`backend/tests/test_release_review_ui_static.py`（UI 契約）で以下を構造的に守る。

- accept が `inferred` 以外を触らない（`rejected` / `review_required` / `confirmed` /
  `superseded` が不変であること）。
- accept の対象 document が空なら SQL を発行しない（fail-closed）。
- accept が edit 権限の無い document を除外し、件数として正直に返す。
- 監査が `action="accept_on_release"` で記帳され、個別レビュー（`action="review"`）と区別できる。
- 新ルータに DELETE が無い。
- ウィザードが weight / confidence を読まない（RR6）。
- ウィザードがポーリングしない・公開を前提条件でブロックしない（RR7）。
- ステップ1が分割・保存ロジックを再実装せず既存 `atlasBindingRenderEditor` に委譲している。

---

## 5. 非スコープ（次の候補）

- **教材一覧・コース一覧の行インジケータ**（「位置づけ: 6件（未確認 6件）」）。ウィザードで
  「リリース前に必ず見る」は解決したが、「一覧で常に見える」は別途。
- **G層 `material.landscape_unreviewed`**（Phase 2 のまま）。ウィザードが主導線になったので
  優先度は下がる。追加する場合は全却下 document を再点灯させない設計を踏襲すること。
- **`GET /api/admin/landscape/overview` の UI 配線**（API は実装済み・UI ゼロ）。分野の地図
  タブに「この分野に何本の論文が置かれているか」を出す。
- 学習者への「教員が一括確認したか個別確認したか」の開示（意図的にしない）。

---

## 6. 追補（2026-09-04）— 確定文脈の記帳

[`docs/vision.md`](../vision.md) §4 の改訂原則1（研究調査による再審）を受け、「次へ」による
一括確認に**確定文脈**（`decision_context`）の記帳を追加した。プリミティブとスキーマの
正本は [確定文脈の記帳](decision_context_design.md)（DC1〜DC4）。

### 6.1 RR2 の解釈補足

RR2（承認は人間の1操作）は変更しない。ただしその読み方を次のように限定する。

> **「次へ」が承認と読めるのは、①そのとき何が提示されていたか ②承認しない選択肢
> （却下・再検討・あとで）があったこと ③後から誰がどの経路で覆せるか が記帳される
> 限りにおいてである。**

記帳を伴わない一括確認は、教員を「確定者」ではなく事故時の責任の置き場（moral crumple
zone）に置くだけであり、RR3（出所を偽らない）の趣旨にも反する。§0 の「承認ラベルに
ついての明示的なトレードオフ」で緩和策は RR3 のみと書いたが、2026-09-04 以降は
**RR3 + 確定文脈**の2本になる。

### 6.2 実装差分

- **evidence の表示**: ステップ2の各行に「根拠を見る」（`<details>`、アンカー
  `release-review.evidence`）を追加し、`admin_placement_dto.evidence[].quote` を逐語で
  描く。引用の無い行でも折りたたみは出し、その事実を書く（提示の有無を行ごとに
  静かに変えない）。
- **accept の body**: `AcceptPlacementsRequest` に optional の `presented_placement_ids` /
  `evidence_shown` を追加。**どちらも来歴申告**で、サーバの判断には使わない
  （`client_reported` に隔離 — DC4）。提示集合の正本は、更新前にサーバが
  `list_for_documents(..., statuses=[inferred])` で取り直した live の状態である。
- **accept のレスポンス**: `decision_context` を追加（既存キーは不変）。画面は
  `presented_matches_applied` から一致／不一致の事実文を1行出す。不一致でも公開は
  止めない（RR7）。
- **監査**: per-row の payload に `decision_context` を添付（既存キー `action` /
  `course_id` / `document_id` と `action="accept_on_release"` は不変）。ゼロ件確認で
  監査を出さない挙動も不変。
- **画面の事実文**: 「次へ」の意味に続けて「確認後も、教材管理の『位置づけ（分野マップ）』
  から個別に再検討・却下へ戻せます。」を常時表示する。

### 6.3 ドキュメントの是正

§3.1 の API 表にあった戻り値 `pending_after` は実装に存在しなかった（実際は
`{course_id, confirmed, skipped_documents}`）。2026-09-04 の追加分と併せて
`{course_id, confirmed, skipped_documents, decision_context}` に是正した。

