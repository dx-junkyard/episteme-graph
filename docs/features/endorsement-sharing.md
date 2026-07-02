# 承認・共有レイヤー（C層）

[← ドキュメント目次](../README.md)

受講者の質問から生まれた AI 回答を、教員の査読を経て承認し、承認済みの理論・概念を
「コンポーネント」として教員間で共有・再利用できるようにする層。
「AI が構造化した知識を人間の価値判断で検証し、承認されたものが知識ネットワークを更新する循環」の
**価値判断の関門**をシステム化したもの。

実装（マイグレーション 021）:
`backend/api/routes/theory_components.py`（API）/ `backend/core/component_candidates.py`（候補生成）/
`backend/api/routes/learning.py`（学習者向け）/ `frontend/public/js/admin.js`・`app.js`（UI）。

---

## 1. 位置づけ — A層を書き換えない

- **A層** … `src/episteme_graph/agents/` の生成パイプライン、export_validation_gate 等。**変更しない。**
- **B層** … 学習者体験レイヤー（interest_traces, マイグレーション 020）。
- **C層** … 本ドキュメント。A層が出力した `theory_components` / `theory_claims` を**読む側**として実装し、
  承認・共有情報を新規テーブルに積む（B層と同じ立場）。

---

## 2. 解決する3つの課題

| 課題 | 従来 | C層での解決 |
|---|---|---|
| 承認の重み | `review_status` は単一状態（承認したか否かのみ） | 複数教員の承認を個別記録し、人数・レベル・専門性の広がりで**厚み**を合成 |
| 独自解釈の並存 | 1コンポーネントに説明は1つ | 「標準の説明」「A先生の説明」…を**代替バージョンとして並存**、作者に帰属 |
| 質問→候補の経路 | `unanswered_query_logs` はログ止まり | 質問+AI回答から**候補生成**、教員が claim 紐づけを確定 |

---

## 3. データモデル

詳細は [データモデル](../architecture/data-model.md)。

- **`component_explanations`** — 説明バージョン。`kind='standard'`（A層 `summary` から遅延生成）/
  `kind='personal'`（教員の独自解釈）。標準説明は1コンポーネント1つ（部分ユニークインデックス）。
- **`component_endorsements`** — 承認を1行ずつ（**explanation 単位**、`UNIQUE(explanation_id, endorser_id)`、
  取り消しは `revoked=TRUE`）。
- **`component_citations`** — 引用の帰属記録。
- **`component_explanation_endorsement_summary`（VIEW）** — 承認の厚みを都度集計。

### なぜ explanation 単位か
独自解釈が並存する以上、「B先生の説明を承認したのか、標準説明を承認したのか」を区別する必要がある。
そのため承認はコンポーネントではなく**説明バージョン単位**で付ける。

### 標準説明の遅延生成
A層は `component_explanations` 行を作らない。承認対象を成立させるため、C層が
`_ensure_standard_explanation()` で `theory_components.summary` から `kind='standard'` 行を遅延生成する。

---

## 4. 承認の重みと表示

`component_explanation_endorsement_summary` の集計（endorser_count / strong_count /
provisional_count / expertise_breadth）から、アプリ層で**段階ラベル**を組み立てる。

- 例: 「未承認」「暫定的に1名が承認」「強い支持: 3名の教員が承認(専門2分野)」

> **原則: 承認の重みを学習者への評価点にしない。** 表示は段階ラベルのみで、数値スコアは学習者に出さない
> （B層と一貫し、報酬化・点数化を避ける）。

---

## 5. 質問 → コンポーネント候補 → 教員確定

```
受講者が質問（RAGで回答できないと unanswered_query_logs に記録）
   │
   ▼
POST /theory-components/candidates/from-query
   - core/component_candidates.py が LLM で「回答が説明する理論・概念」を抽出
   - 依拠しうる既存 claim を候補提示（提示リストにある claim_id のみ）
   │  すべて candidate / teacher_review_required で保存
   ▼
teacher が Lecture Studio で査読
   - claim 紐づけを確認・修正して確定（← 人間が価値判断の関門）
   - 承認 → component_endorsements に1行 + review_status 遷移
   - 独自解釈を書くなら explanation を別バージョン追加
```

> **claim 紐づけの最終確定は必ず教員。** AI は候補提示に限定し、`backing_claims` は
> `confirmed=false` の候補として保持する。教員が確定するまで確定しない。

---

## 6. API

`/api/admin/*`（`routes/theory_components.py`）。一覧は [API](../backend/api.md)。

| メソッド/パス | 役割 | 権限 |
|---|---|---|
| `POST /theory-components/candidates/from-query` | 質問+回答から候補生成 | teacher |
| `GET/POST /theory-components/{id}/explanations` | 説明バージョン一覧 / 追加 | teacher |
| `PATCH /explanations/{id}` | 編集・shared 切替・review_status 遷移 | 作者 or admin |
| `POST/DELETE /explanations/{id}/endorse` | 承認 / 取り消し | teacher |
| `GET /explanations/{id}/endorsements` | 承認一覧＋集計＋段階ラベル | teacher |
| `POST /explanations/{id}/cite` | 他コースで引用 | teacher |
| `GET /courses/{id}/sharing-dashboard` | 引用数・承認の厚みの集団集計 | teacher |
| `GET /api/learning/courses/{id}/components/{cid}/explanations` | 学習者向け（承認済みのみ） | 受講者 |

承認・引用・共有切替・review_status 遷移はすべて `_record_review_event` で `theory_review_events` に監査記録
（`entity_type` を `endorsement` / `explanation` / `citation` に拡張）。

---

## 7. UI

- **教員（`admin.js`, Lecture Studio）** — 理論コンポーネントのカードに「説明・共有の承認」ボタン。
  モーダルで説明バージョン一覧、承認（level/専門タグ）・取消・査読承認・共有 ON/OFF・引用、
  独自解釈の追加、質問からの候補生成を行う。
- **学習者（`app.js`）** — 教材の graph 要素が理論コンポーネントの場合、
  「標準の説明」「各教員の説明」を承認の厚みラベル付きでポップアップ表示（承認済みのみ）。

---

## 8. 設計原則との対応

- **構造化の履歴を残す** — endorsements の note、`theory_review_events`、citations、`origin_query_id` で
  「誰がなぜ承認し、どの質問から生まれ、誰が引用したか」の全履歴が残る。
- **検証可能性による分類** — 承認の厚み（人数・レベル・専門性の広がり）が検証状態の連続的な指標になる。
- **新しい抽象化の共存** — 独自解釈の並存は、同じ知識に複数の抽象化を許す。承認は知識を1つの正解に
  収束させず、複数の説明を責任と専門性で重みづけたまま並存させる。
- **人間が価値判断の関門** — claim 紐づけと承認の確定は必ず教員。AI は候補生成に限定。
