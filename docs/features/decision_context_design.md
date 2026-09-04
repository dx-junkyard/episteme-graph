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
  "evidence_shown": true,
  "client_reported": {"presented_placement_ids": ["..."], "evidence_shown": true}
}
```

- **`basis`** — どの画面のどの一括確定か。語彙は `BASIS_RELEASE_REVIEW_PLACEMENTS` /
  `BASIS_EXPLANATION_REVIEW_BULK`（新経路を足すときはここに定数を1本足す）。
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
  確認していないものを `True` にしない。
- **`client_reported`** — 検証していない自己申告。未指定・空なら `None`。

`attach_decision_context(metadata, ctx)` は**新しい dict** を返す（引数を破壊しない）。
1リクエストで複数行を記帳する一括確定で、同じ ctx を安全に使い回すため。

本モジュールは純データ + 純関数（FastAPI / sqlalchemy / LLM 非 import）。

---

## 4. 適用先（2026-09-04 時点の2経路）

### 4.1 リリース前の確認 ステップ2「この配置で次へ」

`POST /api/admin/landscape/courses/{course_id}/placements/accept`

| 項目 | 値 |
|---|---|
| `basis` | `release_review.placements` |
| `presented_ids` | **サーバ導出**。更新前に `landscape_store.list_for_documents(..., statuses=[inferred])` で edit 権限のある document の live な `inferred` を取り直す（クライアント申告に依存しない — DC2） |
| `applied_ids` | `accept_inferred_for_documents` が実際に遷移させた行 |
| `alternatives` | `reject` / `reconsider` / `skip_step`（各行の [却下] [再検討] とステップの「あとで」— RR4 / RR1） |
| `reopen` | `PATCH /api/admin/landscape/placements/{placement_id}` / `rejected`・`review_required` |
| `evidence_shown` | body の申告値（未指定は `None`） |
| `client_reported` | body に `presented_placement_ids` / `evidence_shown` があるときだけ |

body（`AcceptPlacementsRequest`）に optional の `presented_placement_ids` /
`evidence_shown` を追加した。**どちらもサーバの判断には使わない**（提示集合の正本は
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
| `evidence_shown` | `True`。キューの各行は本文に加えて `evidence_quote` / `reason` を描いている（`deliberation.js` の `_explanationReviewCardHtml`、`evidence.evidence_quote ? ...` の行） |
| `client_reported` | `sort_order` が指定されたときだけ（TT3 の作法をそのまま踏襲） |

既存の `sort_metadata`（TT3）・`"bulk": True`・部分成功セマンティクスは不変。レスポンスに
`decision_context` を追加した（既存キー `updated` / `skipped` は不変）。

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
  `evidence_shown: true` を載せる。`true` が正直であるのは、**引用の有無に関わらず全行に
  折りたたみを出す**ようにしたためで、「根拠の提示が行われた」という事実に対応する。

---

## 6. 段階適用の残り

vision §9 は本層を「段階適用中」と位置づけている。v1 で入れたのは**一括確定の2経路**だけで、
以下は未適用（着手時は本書 §4 に節を足し、`basis` 定数を1本足す）。

- 単発の承認（component / claim / 説明の個別 approve・dismiss、W層注釈の commit）
- 骨格の凍結（atlas freeze）・ライブラリの凍結
- コースの公開（visibility → public）
- 学習者側の確定（tension / anchor の confirm）— 本人の痕跡は監視しない原則（PN-1 / P3）と
  の兼ね合いを先に決める。**「本人が自分の確定を後から再構成できる」ための記帳**であって、
  教員・運営が読むための記帳にしてはならない。

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
- 一括確定2経路のソースに `build_decision_context(` / `attach_decision_context(` /
  それぞれの `basis` 定数が現れること（DC1）
- リリース前の確認 JS が `presented_placement_ids` / `evidence_shown` を送り、
  `release-review.evidence` アンカー付きの折りたたみと再審の事実文を描くこと

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
