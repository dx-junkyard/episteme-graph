# 再構成ループ（Reconstruction Loop）+ つまづきサマリー 設計書

学習者の「能動的・生成的理解」を支える第五の学習機構（仮称: R層）。
学習者に理論の再構成（予測・言い直し・並べ替え）をさせ、A層が生成した精密な構造
（`theory_claims` / `derivation_chain` / `verification_scopes` / counterfactual）を
**答えキー（ground truth）** として構造照合し、ズレを事実として返す閉ループを構築する。
併せて、学習者のつまづき信号を claim 単位で集約し、原稿スタジオの「根拠リンク」ペインに
表示切り替えで提示する（教員の説明改善・個別資料作成の起点にする）。

- ステータス: 実装済み（learning-ux。Phase 1–3。migration 036 / `backend/core/reconstruction/` /
  `backend/api/routes/reconstruction.py` / `frontend/public/js/reconstruction.js` / `admin.js` トグル /
  `backend/tests/test_reconstruction_{guardrails,loop,api}.py`）
- 前提ブランチ: `learning-ux`
- マイグレーション: `backend/db/036_reconstruction_loop.sql`（新規）
- 関連: A層（`src/episteme_graph/agents/`・非改変）/ B層（tension・structure_anchor）/
  C層（承認・共有）/ D層（epistemic ledger）/ 原稿スタジオ（`admin.js` lecture-studio タブ）

---

## 1. 背景 — なぜこの機構が必要か

### 1.1 現状診断: システムは「2つの動詞」で作られている

episteme-graph の既存層は次の 2 つに最適化されている。

1. **知識を忠実に表現する**（A・C・D層）— evidence-based、source-backing、承認、
   検証スコープ、反実仮想。fidelity と正直さの技術。
2. **学習者の認識状態を正直に記録する**（B層）— tension、structure_anchor、
   interest_traces。煽らず・監視せず・断定せず記録する技術。

しかし理解という現象は第 3 の動詞で起きる:

3. **学習者が自分で再構成し、自分のモデルと正解のズレを突きつけられる。**

現状、知識は「構造 → 学習者」の一方向にしか流れていない（RAG チャット・レクチャー音声・
casual 対話・Field Atlas はすべて消費側）。学習者の産出物は engagement メタデータ
（`interest_traces.payload`）としてしか捕捉されず、**ground truth と照合されて
「あなたの理解はここが違う」と返る経路が存在しない**。これが空白の中核である。

### 1.2 理解の要素と現状の充足度

| 理解の要素 | 現状 |
|---|---|
| 再構成・生成（generation effect） | ✗ ほぼ無い。すべて消費側 |
| 予測してから答え合わせ | ✗ 無い |
| 説明する（Feynman） | △ 説明を書くのは教員/AI。学習者は書かない |
| 転移 | △ `content_grounding` が off-corpus を下位 tier 扱いし抑制側 |
| 境界を知る（scope） | ✓✓ D層で世界的に強い。ただし見せているだけ |
| 問いを感じる | △ ThesisReconstruction が復元するが体験させない |
| 分かったつもりの較正 | △ no-score 原則ゆえ鏡が無い |

### 1.3 鍵となる洞察: A層の構造は「使われていない解答キー」

パイプラインが生成する `atomic_claims`（1 命題）、`derivation_chain`（導出順序）、
`TheoryOperationGraph`、`verification_scopes`（成立条件）は、学習教材として前代未聞に
精密な解答キーである。現状は表示専用。**「学習者 → 構造」の逆向きの矢印を一本通すだけで
この資産の価値が反転する**:

- 導出並べ替え → `derivation_chain` で決定論的に採点可能
- 成立条件の列挙 → `verification_scopes` と集合照合可能
- 前提を偽に倒した予測 → counterfactual の決定論的伝播がそのまま答え合わせ
- 言い直し → atomic claim との構造照合（曖昧な部分は candidate 止まり）

### 1.4 罠の回避: 「生成的エージェント」を作らない

生成効果は**学習者が**産出する摩擦から生まれる。エージェントを饒舌にすると
「エージェントの明晰な説明を自分の理解と錯覚する」方向に悪化する
（illusion of explanatory depth）。よって:

> エージェントに持たせるのは「生成する能力」ではなく
> **「学習者に生成させ、そのズレを突きつける能力」**。
> エージェントは司会者兼インタビュアーであって、採点者ではない。

空白を埋める最小十分な形は三点セット:
**反転したエージェント + 学習者成果物の器 + 構造による判定**。

### 1.5 readiness（解く資格）の扱い: 資格審査を作らない

「要素の大枠を理解していないと主張の再構成はできない」という問題に対し、
前段の readiness ゲートは作らない（A3 の前提チェックと同じ脆さを再発させるため）。
代わりに **生成ループそのものを診断に使う**:

- 主張レベルの DIFF でズレは特定の要素（記号・式）に局在する
- 局在した要素に対して**同じループを一段下で回す**（自己相似・再帰的）
- 要素階層は A層が既に構造化済み:
  記号（SymbolRegistry #355）→ 式（EquationSemantics）→ 主張（atomic_claims）
  → 操作列（derivation_chain / TheoryOperationGraph）→ 中心命題（ThesisReconstruction）

支援の本質は「教えてあげる」ではなく「**正しい段に降ろす**」こと。
前提がそもそも無い（この論文内で埋まらない）場合のみ、REQUIRES エッジ / コース前提 /
Field Atlas で外部へ誘導する。

### 1.6 葉の粒度: 主張（claim）を既定の葉、記号は点検口

| | 記号葉 | 主張葉 |
|---|---|---|
| 粒度 | 最細 | 意味を持つ最小 |
| 原因特定 | 最強 | 主張どまり・必要なら降りる |
| 体験 | 用語当てに断片化 | 「分かる」を問える |

**決定: 主張（theory_claims / atomic_claim）を既定の葉（通常の最下段）とし、
記号（SymbolRegistry）は主張レベルで原因が絞れないときだけ降りる下層（点検口）とする。**

---

## 2. 要件

### 2.1 機能要件

- R1: 学習画面で、承認済み claim から生成された再構成課題（予測 / 言い直し）を
  学習者に提示できる（ELICIT）。
- R2: 学習者の産出を、チャットメタデータではなく**一級の成果物**として
  改訂履歴付きで保存できる（CAPTURE）。
- R3: 産出と ground truth のズレを**構造照合**（非LLM・同期）で検出し、
  出典（claim 本文・式・evidence）の開示（REVEAL）とともに事実として提示する（DIFF → REFLECT）。
- R4: リビール直後に学習者の 1 タップ自己確認（合っていた / 違っていた / 判定がおかしい）を
  必須ステップとして挟む（SELF-CHECK）。
- R5: 学習者は再挑戦（REVISE）または一段下（記号葉）への降下ができる。
- R6: 課題（item）は LLM が自動オーサリングし、**教員確定なしで配信**する
  （`status='auto'`）。教員確定は事後の是正（confirmed 昇格 / retire）とする。
- R7: item ごとの健全性シグナル（回答数・誤り率・機械判定×自己確認の乖離率・異議数）を
  集計し、疑わしい item をランク付けした review キューとして教員に提示できる。
- R8: **つまづきサマリー**: claim 単位のつまづき信号（誤り率・記号降下頻度・判定乖離・
  よくある質問/誤解）を、原稿スタジオ右ペイン「根拠リンク」との**表示切り替え**で
  教員が確認できる。
- R9: すべての状態変更（オーサリング・flag・confirm/retire・自己確認・降下）を
  `theory_review_events` に監査記録する。

### 2.2 非機能要件・制約（既存原則との整合）

- P1（AIに断定させない）の**運用形**: 機械判定（DIFF）は「仮説」であり、
  **権威は出典のリビール**にある。判定を authoritative に見せない。
  緩めるのは「配信前の人間確定」のみで、①出典が権威 ②点数化しない
  ③`auto`=candidate 相当 ④全件監査・retire で回収可能 ⑤自己確認と異議 flag で
  人間の目が常時入る、の 5 点で P1 の精神を守る。
- P3（監視にしない）: 教員向け集計は k-匿名（k=3、n<3 セル非表示）。個別学習者の
  再構成履歴を教員に見せない。評価利用禁止。
- P4（情報を落とさない）: item は削除せず `auto → flagged → retired` の状態遷移。
  学習者の成果物・自己確認・異議も行削除しない。
- P6（同期パスを重くしない）: 実行時の DIFF は非LLM。LLM を使うのは
  item オーサリング（非同期 worker）のみ。
- P7（点数化・演技化させない）: スコア・正答率数値を学習者に見せない。
  REFLECT は「あなたの版と出典はここが違う」という事実文のみ。
  教員向けも段階ラベル + レンジ表示（3-5 / 6-10 / 11+）。
- A層非改変: `src/episteme_graph/agents/` のコードは読むだけ。変更しない。
- 出題対象は `support_status='source_backed'` かつ承認済み review_status の claim のみ
  （未検証の構造で学習者を試さない）。

### 2.3 スコープ外（今回やらない）

- 導出並べ替え・反実仮想予測などの上位段タスク（階段の上段。主張葉の1往復が通ってから）
- 言い直し（自由記述）の LLM 採点（candidate 提示に留める拡張として将来）
- 間隔反復スケジューラ（tension と同型の worker として将来別 issue）
- 学習者の再構成を C層で共有する機能
- 個別資料の自動生成（つまづきサマリーは*きっかけ*まで。資料作成は既存スタジオ機能の下流）

---

## 3. 仕様

### 3.1 融合ループ全体像

```
① 正解キー（共有データ）
   theory_claims（主張葉・既定） / SymbolRegistry（記号葉・点検口）
        │ 答えキーを供給
        ▼
② 学習画面 — 再構成ループ（B: 自動オーサリング + A: 自己確認）
   出題 ELICIT → 提出 CAPTURE → 照合 DIFF(=仮説) → 開示 REVEAL(=権威)
     → 自己確認 SELF-CHECK →（再挑戦 ↺ / 記号葉へ降下 ↓）
        │ claim_id で全操作を記録
        ▼
③ 観測レイヤー（claim_id 単位・k匿名）
   誤り率 / 記号降下頻度 / 判定×自己確認の乖離 / 質問・誤解(FAQ)
   すべて theory_review_events に監査
        │ 怪しい箇所を claim に集約
        ▼
④ 原稿スタジオ（教員）
   右ペイン: 根拠リンク ⇄ つまづきサマリー（表示切り替え・同じ claim anchor に相乗り）
   教員: 説明を改善 / 難所を個別資料化 / item を confirmed・retire
        │
        └──→ 改善が①へ戻る（ループが閉じる）
```

- ②は「A で1往復→B」の順ではなく **A と B の融合**: `auto` item を即配信し、
  リビール後の自己確認を必須にする。自己確認は教育ステップ（鏡）であると同時に
  **最も精度の高い故障検出器**（機械判定 mismatch × 自己確認「判定がおかしい」
  → ほぼ確実に bad item）。
- ④は③の読み取りビュー。教員は門番ではなく**異常の監査役**
  （全 item を事前に見ず、怪しいものだけ事後に見る。工数は件数でなく問題数にスケール）。

### 3.2 ELICIT — claim → 課題への変換

出題モードは 2 種（初期実装）:

| モード | 条件 | 問い | 照合 |
|---|---|---|---|
| `predict`（予測） | `concepts[].role` に subject/driver が取れ、関係の型が構造化できる | 「δO は g に対してどう振る舞う？」+ 選択肢（∝g / ∝1/g / ∝g² / 依存しない 等） | 選択肢 ID の機械照合（決定論的） |
| `restate`（言い直し） | 上記が取れない claim | 「この結果を一文で述べて」 | 構造照合は concept 被覆のみ。ズレの最終判断は自己確認（鏡） |

- **見せるフィールド**: `concepts`（subject / driver）、`source_scope`（文脈）
- **伏せるフィールド**（=答え）: `text` / `normalized_text` / `equation.latex` / `evidence_text`
- 選択肢と想定解（`expected`）は item オーサリング worker（LLM・非同期）が生成し、
  `reconstruction_items` に保存。実行時に LLM は呼ばない。

### 3.3 item のライフサイクル

```
auto ──(教員が事後追認)──→ confirmed
  │
  ├──(異議・乖離シグナル)──→ flagged ──(教員判断)──→ retired
  │                              └──(修正)──→ auto/confirmed
```

- `auto`: 配信可・確定ではない（candidate 相当）。学習者 UI には
  「この問いは AI が自動生成したものです」を一行明示。
- `retired`: 配信停止。既に受けた学習者の履歴は保持（P4）。

### 3.4 DIFF / REFLECT / SELF-CHECK

- DIFF（同期・非LLM）:
  - concept 被覆: 学習者の応答が subject / driver 概念に言及しているか
  - 関係の型: 選択肢 ID と `expected` の照合（predict モードのみ）
  - 結果は `machine_verdict ∈ {match, mismatch, na}`（restate は na が既定）
- REVEAL: DIFF 直後に `text` / `equation.latex` / `evidence_text` を開示。
- REFLECT の文面規約: 点数を出さない。
  「出典はこう述べています: … / あなたは…と予測しました / 食い違いは○○の一点です」
  という事実文のみ。verdict が na のときは対比提示のみ。
- SELF-CHECK（必須・1 タップ）:
  `合っていた(agreed)` / `違っていた(disagreed)` / `判定がおかしい(verdict_wrong)`
- 分岐: 再挑戦（`revision_of` を立てて新規行） / 記号葉へ降下
  （claim の `concepts` / equation に含まれる記号を SymbolRegistry から引き、
  「この記号は何を指す？」の一行プローブ。結果は同じ器に `elicit_mode='symbol'` で保存）。

### 3.5 つまづきサマリー（原稿スタジオ）

- 置き場所: lecture-studio 右ペイン（`admin.js` の「根拠リンク」タイトル領域）に
  トグル `[根拠リンク | つまづき]` を追加。**別タブは作らない**。
  根拠リンクが `evidence_links → claim_id` で解決される構造にそのまま相乗りする。
- 表示単位: 選択中トピックに紐づく claim ごとに 4 軸を段階表示:
  1. 誤り率（mismatch 率）— 段階ラベル（低 / 中 / 高）
  2. 記号降下頻度 — レンジ（3-5 / 6-10 / 11+）
  3. 判定×自己確認の乖離 — 段階ラベル（item 健全性の代理指標として注記）
  4. よくある質問・誤解 — 既存 B層資産の再利用:
     `interest_traces`（kind='question'、structure_anchor.anchor_id が当該 claim）と
     `personal_layer.misconceptions_by_topic` を claim/topic 単位で k-匿名集約
- k-匿名: k=3。母数は**人数（DISTINCT user）**で数える（応答行数ではない。D層 naive_signal
  と同じ扱い — 同一学習者の複数回答でセルが開示されない）。n<3 のセルは「まだデータなし」。
  数値の生値・個人特定情報は出さない。
- テスト初期はほぼ全て「まだデータなし」になる（fail-closed を許容。k は緩めない）。
- サマリーからのアクションは「該当 item の review キューを開く」「説明改善の編集へ」の
  導線までとし、資料生成そのものは既存スタジオ機能に委ねる。

---

## 4. 詳細設計

### 4.1 データベース（migration 036）

```sql
-- backend/db/036_reconstruction_loop.sql

CREATE TABLE IF NOT EXISTS reconstruction_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    claim_id UUID NOT NULL REFERENCES theory_claims(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL DEFAULT '',
    elicit_mode TEXT NOT NULL DEFAULT 'predict',      -- predict | restate | symbol
    prompt TEXT NOT NULL,
    response_space JSONB NOT NULL DEFAULT '[]',       -- 選択肢（predictのみ）
    expected JSONB NOT NULL DEFAULT '{}',             -- 想定解（選択肢ID・型）
    claim_fields_used JSONB NOT NULL DEFAULT '[]',    -- provenance
    author TEXT NOT NULL DEFAULT 'llm',               -- llm | teacher
    author_confidence REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'auto',              -- auto | flagged | retired | confirmed
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_recon_items_claim ON reconstruction_items(claim_id);
CREATE INDEX idx_recon_items_status ON reconstruction_items(status);

CREATE TABLE IF NOT EXISTS learner_reconstructions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id TEXT NOT NULL DEFAULT '',
    item_id UUID NOT NULL REFERENCES reconstruction_items(id) ON DELETE CASCADE,
    claim_id UUID NOT NULL,                           -- 非正規化（集計用）
    response JSONB NOT NULL DEFAULT '{}',
    machine_verdict TEXT NOT NULL DEFAULT 'na',       -- match | mismatch | na
    self_check TEXT,                                  -- agreed | disagreed | verdict_wrong | NULL
    descended_to_symbol BOOLEAN NOT NULL DEFAULT FALSE,
    revision_of UUID REFERENCES learner_reconstructions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_learner_recon_user ON learner_reconstructions(user_id);
CREATE INDEX idx_learner_recon_item ON learner_reconstructions(item_id);
CREATE INDEX idx_learner_recon_claim ON learner_reconstructions(claim_id);
```

item 健全性は専用カウンタテーブルを持たず、集計ビューで出す（DX-2 と同じ方針）:

```sql
CREATE OR REPLACE VIEW reconstruction_item_health AS
SELECT
    i.id AS item_id,
    i.claim_id,
    i.status,
    i.author_confidence,
    COUNT(r.id)                                        AS n_responses,
    COUNT(*) FILTER (WHERE r.machine_verdict='mismatch')            AS n_mismatch,
    COUNT(*) FILTER (WHERE r.self_check='verdict_wrong')            AS n_verdict_dissent,
    COUNT(*) FILTER (WHERE r.machine_verdict='mismatch'
                       AND r.self_check='agreed')                   AS n_verdict_self_disagree,
    COUNT(*) FILTER (WHERE r.descended_to_symbol)                   AS n_descend,
    COUNT(DISTINCT r.user_id)                                       AS n_users  -- k-匿名の母数（人数）
FROM reconstruction_items i
LEFT JOIN learner_reconstructions r ON r.item_id = i.id
GROUP BY i.id;
```

疑わしさランク = `author_confidence 低 × (mismatch率高 OR 乖離率高 OR dissent あり)`。
ランク計算はアプリ側（`health.py`）で行い、SQL に埋め込まない。

### 4.2 バックエンド構成（tension / structure_anchor と同型）

```
backend/core/reconstruction/
  __init__.py
  schema.py          → dataclass（ReconstructionItem, LearnerReconstruction, DiffResult）
  item_builder.py    → claim → ELICIT 変換（predict 可否判定・restate 縮退）※非LLM部分
  prompt.py          → item オーサリング用プロンプト
  llm_client.py      → structured output（オーサリングのみ）
  input_builder.py   → LLM 入力整形（claim の concepts/equation/scope）
  validator.py       → item スキーマ検証（expected が response_space に含まれる等）
  repair.py          → 検証失敗時の再試行（2回失敗で item を生成しない=配信しない）
  diff.py            → 実行時構造照合（非LLM・同期）
  health.py          → item_health ビューの読み取り + 疑わしさランク
  stumble.py         → claim 単位つまづき集約（k-匿名・段階ラベル化）
  worker.py          → item オーサリング worker（threading.Thread、
                        トリガー: claim の承認時 / 手動バッチ。冪等性: claim_id+mode の既存 item 有無）
  examples/          → サンプル入出力 JSON
```

- コスト上限: `RECON_MAX_ITEMS_PER_DOCUMENT`（既定 30）/
  `RECON_MAX_CALLS_PER_DAY`（既定 10、tension・anchor とは独立）。
  モデルは fast tier 既定（`RECON_LLM_MODEL` で上書き）。
- `core/` に FastAPI を import しない（既存ルール）。
- 出題対象クエリ: `theory_claims` から `support_status='source_backed'` かつ
  `review_status IN ('endorsed', ...)`（承認語彙は C層の実装に合わせて確定）。

### 4.3 API

学習者向け（`backend/api/routes/learning.py`。本人のみ・受講ゲートは
`course.sources[].material_id → document_id` を `_ensure_document_viewable` 相当で検証）:

| メソッド/パス | 役割 |
|---|---|
| `GET  /api/learning/courses/{course_id}/topics/{topic_id}/reconstruction/next` | 次の item を 1 件返す（status='auto'/'confirmed' のみ。伏せフィールドは返さない） |
| `POST /api/learning/reconstruction/{item_id}/submit` | 応答を保存 → diff 実行 → verdict + リビール内容（claim 本文・式・evidence）を返す |
| `POST /api/learning/reconstruction/{recon_id}/self-check` | body: `{result: agreed\|disagreed\|verdict_wrong}` |
| `POST /api/learning/reconstruction/{recon_id}/descend` | 記号葉プローブを返す（SymbolRegistry 由来）。`descended_to_symbol=TRUE` を記録 |
| `POST /api/learning/reconstruction/{item_id}/revise` | 再挑戦（`revision_of` 付きの新規行を作り submit と同フロー） |

教員向け（`backend/api/routes/admin.py` 配下、`_require_teacher`）:

| メソッド/パス | 役割 |
|---|---|
| `GET  /api/admin/reconstruction/items/review-queue` | 疑わしさランク順の item 一覧（health 集計付き） |
| `PATCH /api/admin/reconstruction/items/{item_id}` | status 遷移（confirmed / retired / auto へ戻す）+ prompt/expected 修正 |
| `GET  /api/admin/documents/{document_id}/claims/stumble-summary` | claim 単位つまづきサマリー（4軸・k-匿名・段階ラベル）。原稿スタジオのトグルが呼ぶ |

- `submit` / `self-check` は同期・非LLM のみ（P6）。
- 監査: item 生成 / status 遷移 / flag / self-check(verdict_wrong) / descend を
  `theory_review_events` に記録。`entity_type` は
  `'reconstruction_item'` / `'reconstruction_response'` を追加。

### 4.4 フロントエンド

学習画面（`app.js`、ES6+）:
- トピック学習ビューに「再構成に挑戦」導線（自動割り込みはしない。P7: 演技化させない）。
- 出題カード: 問い + 選択肢（predict）or 自由入力（restate）。
  「AI が自動生成した問いです」の一行注記。
- 提出 → リビールカード（出典本文・式・evidence を明示、機械判定は
  「食い違いの可能性: ○○」という仮説文体で表示）→ 自己確認 3 ボタン（必須）
  → 再挑戦 / 「記号を確認する」導線。

原稿スタジオ（`admin.js`、ES5）:
- 右ペインタイトル行（`lsRenderCoursePane` 系、`rightTitle.textContent = "根拠リンク"` 付近）に
  トグルボタンを追加: `根拠リンク | つまづき`。
- 「つまづき」選択時は `GET .../claims/stumble-summary` を呼び、claim ごとに
  4 軸の段階ラベル + FAQ 要約を描画。n<3 は「まだデータなし」。
- 各 claim 行に「この claim の問いを確認」→ review キュー（該当 item）へ、
  「ドラフトの該当セグメントへ」→ 既存の根拠リンク⇄ドラフト対応付けを再利用。

### 4.5 ガードレールテスト

`backend/tests/test_reconstruction_guardrails.py` で構造的に守る:

- 全 API レスポンスに正答率・スコアの生数値が含まれない（学習者向け）
- `next` のレスポンスに伏せフィールド（text / equation / evidence_text）が漏れない
- 出題対象が source_backed + 承認済みに限定される
- item の削除 API が存在しない（retire のみ）
- stumble-summary が n<3 セルを返さない（k-匿名）
- `core/reconstruction/` が FastAPI を import しない
- REFLECT 文面に禁止語彙（点数・順位・煽り文句）が入らない

### 4.6 段階導入

1. **Phase 1（最小閉ループ）**: migration 036 / `item_builder`+`diff`+`worker` /
   学習者 API 5 本 / 学習画面カード UI。restate モードは concept 被覆のみで開始可。
2. **Phase 2（観測と是正）**: health ビュー / review キュー API+UI /
   theory_review_events 拡張 / ガードレールテスト。
3. **Phase 3（つまづきサマリー）**: `stumble.py` / stumble-summary API /
   原稿スタジオ右ペイントグル。
4. **Phase 4（将来・別 issue）**: 上位段タスク（導出並べ替え・counterfactual 予測）、
   言い直しの LLM candidate 採点、間隔反復 worker、canary 配信・confirmed 昇格の自動推薦。

---

## 5. 決定事項と未決事項

### 決定済み

- 葉は主張（claim）、記号は点検口（§1.6）
- A（自己確認）と B（自動オーサリング）は融合させ、教員確定ゲートは置かない。
  教員は事後の監査役（§3.1, §3.3）
- 判定は構造（非LLM）、権威は出典リビール、点数は出さない（§3.4）
- つまづきサマリーは根拠リンクペインの表示切り替え（別タブにしない）（§3.5）
- 4 軸: 誤り率 / 記号降下頻度 / 判定×自己確認の乖離 / FAQ・誤解（§3.5）
- テスト初期の「まだデータなし」多発は許容し、k=3 は緩めない（§3.5）
- サマリーからの資料生成は既存スタジオ機能の下流（スコープ外）（§2.3）

### 未決（実装時に確定）

- 出題対象とする `review_status` の承認語彙の正確な集合（C層実装との突き合わせ）
- worker のトリガー詳細（claim 承認イベントへのフック位置 / 手動バッチ API の要否）
- 学習画面での出題導線の文言・頻度（「再構成に挑戦」ボタンの配置）
- FAQ 集約における structure_anchor の anchor_id → claim_id 突き合わせの精度確認
  （anchor_type='claim' 以外の縮退粒度をどこまで含めるか）
