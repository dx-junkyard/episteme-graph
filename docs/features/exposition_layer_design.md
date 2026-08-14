# E層（Exposition Layer / 段階的翻訳レイヤー）設計

> **状態: 未実装（2026-07-12 時点、実装コードなし）**。本書は設計のみで、
> `backend/core/exposition/` / `backend/api/routes/exposition.py` / migration 034
> （`exposition_views` 等）はいずれもリポジトリに存在しない。「第五の層」を名乗る
> R層（migration 036）・V層（migration 037）は実装済みだが、E層は issue化・着手ともに
> 未了。実装に着手する際は本書 §10 の issue 分割を正本として使うこと。

> **着手前提の更新（2026-07-17、vision×UX ギャップ調査 N41）**: 本書起草後にリポジトリの
> 前提が4点変わった。§10 の issue 分割は有効なまま、着手時に以下を織り込むこと。
>
> 1. **migration は 068 以降で採番する**（2026-08-14 時点の空き番号。本書の「migration 034」は
>    Admin Copilot（`034_assistant_actions.sql`）と衝突済みで、044〜067 も他機能
>    （object_group_permissions 〜 賭け金の台帳）で使用済み）。一次情報は `backend/db/0NN_*.sql`
>    の実ファイル名（`docs/architecture/layer_registry.md` §3 参照）。
> 2. **生成 worker は独立モジュールの新設ではなく `backend/core/llm_worker/` への
>    アダプタ接続で実装する**（現行の家風。tension / structure_anchor / reconstruction /
>    doubt×2 / deliberation.standardization に続く7系統目として、`BaseJSONLLMClient` +
>    `run_with_repair` + `CostGate` に15〜20行のアダプタで乗る。コピペ禁止 — CLAUDE.md
>    「横断基盤」参照）。
> 3. **UI の差し込み先 DOM は `admin-lecture-studio.js` に移動済み**。本書が想定した
>    admin.js 内の原稿スタジオ画面（`ls` 接頭辞の関数群）は Tier 3-17b で
>    `admin-lecture-studio.js`（`window.LectureStudio`、DI 注入方式）へ分離された。
>    E層の教員向け UI はこちらに書く。
> 4. **「学習者はグラフ表示に無垢」という前提（§1）は崩れている**。Field Atlas
>    （分野の地図オーバーレイ + ミニマップ）と「わたしの地図」（個人知識ネットワーク、
>    `personal-map*.js`）の実装により、学習者は既に2種類の node/edge 視覚語彙に接触して
>    いる。E層ビューが同じ視覚語彙を使うと「また別の地図？」という混乱を生むため、
>    E層ビューは Atlas・わたしの地図と**視覚語彙を意図的に差別化**する設計検討
>    （レイアウト・ノード形状・配色・呼称）を issue 分割に追加すること。

> **目的**: A層パイプラインが論文から再構成した component / claim / equation / TheoryOperationGraph は
> 「その分野の専門家だからこそ読める」構造物である。これを、入門的立場の学生や非専門家が
> 興味を持って手に取れる形へ**段階的に翻訳する層**を、既存実装に違和感なく積む。
> **位置づけ**: A層（構造化）・B層（学習）・C層（承認）・D層（疑義）に続く第五の層。
> 本書は設計のみ（実装なし）。issue 登録時は本書を正本として分割する（§10）。

---

## 1. 背景と問題設定

現状の到達点と、そこにあるギャップ:

- A層は論文を `theory_claims` / `theory_components` / equations / derivations に分解し、
  `theory_component_graphs.graph_json` に **TheoryOperationGraph** として保存する
  （`backend/db/013_theory_components.sql`、`backend/core/document_pipeline/persistence.py`）。
- グラフは既に **main（理論ステージ 5〜8 ノードのバックボーン）/ equation_detail / debug** の
  層構造を持ち、`parent_component_id` / `member_component_ids` で相互参照する
  （`src/episteme_graph/agents/component_graph/schema.py:342-410`、issue #306/#308）。
- しかし main 層ですら `Theory basis` → `Equation system` → `Consistency relation` という
  **理論操作の語彙**で書かれており、読者に「導出とは何か」「なぜ消去するのか」という
  専門的リテラシーを要求する。ノードの description は atomic claim 文＝原文の圧縮であり、
  入門者向けの再叙述ではない。
- 学習者UI（`app.js`）は現状グラフを一切表示しない（vis.js は admin.html のみ）。学習者の
  接点は教材チャットと「この要素の説明」ポップアップ（C層 explanations）に限られ、
  **「この論文は全体として何をどう主張しているのか」の見取り図が学習者に存在しない**。

やりたいことは「わかりやすい要約の生成」ではない。**対象者の理解水準ごとに、
要素（component / claim / equation）をまとめ直した粗い粒度のノードを再定義し、
それらの関係で全体の主張構造を組み直したビュー**を、原構造への追跡可能性を保ったまま
何層か重ねることである。main 層 ⇄ equation_detail 層の集約パターンは、この
「対象者別の段階ビュー」の**社内前例**であり、E層はその一般化にあたる。

---

## 2. 既存レイヤー・既存機能との関係

| レイヤー/機能 | 関係 |
|---|---|
| **A層**（`src/episteme_graph/agents/`） | **非改変**。E層は `theory_claims` / `theory_components` / `theory_component_graphs` を読むだけ（C層・D層と同じ立場） |
| **B層**（学習体験） | E層 published ビューが学習者の「全体像」導線になる。既存のドリルダウンチャット・structure_anchor に接続する（§9） |
| **C層**（`component_explanations`） | 「1コンポーネントに複数の説明バージョン + 承認」の先行モデル。E層は**複数要素の集約と関係の再構成**を扱う点が異なる。教員承認済み・shared な explanation は E層ノードの叙述素材として引用でき、引用時は既存 `component_citations` に帰属記録する |
| **D層**（epistemic ledger） | E層ノードの member に台帳記帳対象が含まれる場合、学習者向け表示に検証状態を一行事実で併記する（D3-6 と同じ流儀）。**入門向けであっても未検証を検証済みのように語らない** |
| **Field Atlas（分野の地図）** | 別機能。Atlas は「分野全体の中での現在地」、E層は「一論文/一コースの主張構造の対象者別翻訳」。doubt 層と同じ規律で、コード・API・UI 文言とも `exposition-` プレフィックスを使い衝突を避ける |
| **`theory_components.blackbox_policy`** | 既存の「未習得なら summary 水準で隠す」ポリシーはコンポーネント単体の開示制御。E層はグラフ全体の再構成であり置き換えない。将来的に E層ノードの展開既定値として参照してよい（§12） |
| **graph_layer（main/equation_detail/debug）** | E層は `graph_json` に新しい layer を**追加しない**。A層成果物のスキーマ（`ComponentGraphResult`）には触れず、独立テーブルに積む（§5） |

---

## 3. 不変条項（全 issue 共通）

B〜D層で確立した積層規律を踏襲する。

| 原則 | 内容 |
|---|---|
| **A層非改変** | E層は A層成果物を読む側。`src/episteme_graph/agents/` のコードは変更しない |
| **LLM 出力は常に candidate** | 翻訳ビューの生成・集約・再叙述は LLM 候補に留め、教員（TEACHER 以上）の承認まで学習者に出さない。publish は承認済みノードのみで構成される（fail-closed） |
| **情報を落とさない（P4）** | 集約は「畳む」であって「消す」ではない。全 E層ノードは下位層への `member_refs` を必ず持ち、ドリルダウン可能。ある水準のビューに載せなかった要素は `unplaced_refs` に理由つきで残す。却下ノードは `rejected` で保持 |
| **出所の正直さ（fidelity 明示）** | 各ノード・各エッジに `fidelity`（忠実要約/簡略化/比喩/再構成）を必須付与し、学習者にもラベルとして表示する。比喩を事実の顔で見せない |
| **煽らない・数値を見せない** | 「理解度○%」「かんたん度」等の数値・スコアを作らない。confidence の生数値は API に出さない（既存規律の継承） |
| **同期パスに LLM を入れない（P6）** | 生成は教員の明示操作をトリガーにした非同期バッチ（tension / structure_anchor / doubt と同型の worker）。学習チャットの同期パスには一切入れない |
| **学習者を監視しない（P3）** | 学習者の水準は自己申告（表示切替）のみ。行動ログから水準を自動推定・自動降格しない |
| **帰属と監査** | 生成・承認・編集・publish・引用は `theory_review_events` に記録（`entity_type` に `'exposition_view'` / `'exposition_node'` を追加） |
| **domain-independent** | 特定分野・特定論文の用語をコードにハードコードしない。語彙の拡張はカートリッジ（`audience_profiles.json`、optional）から読む |

---

## 4. 対象者水準（audience_level）の語彙

固定の順序つき語彙とする（カートリッジで表示名・比喩許容度を上書き可、水準自体は増やさない）。

| level | 対象者像 | 構成方針 |
|---|---|---|
| `expert`（L0） | 分野の専門家 | **既存の TheoryOperationGraph そのもの**。E層テーブルに行を持たない仮想水準（ビュー切替の起点） |
| `graduate_intro`（L1） | 分野に入りたての大学院生 | main 層の理論ステージ構造をほぼ保ち、各ノードを「何をしている段階か」の平易な一段落 + 用語注で再叙述。数式は「この式が言っていること」1行を併記 |
| `undergraduate`（L2） | 学部生・隣接分野の読者 | ステージを 3〜5 ノードに集約（例:「前提とする物理」「観測とどうつなぐか」「何と何を比べるか」「何がわかるか」）。数式は原則畳み、関係語を平易化 |
| `general`（L3） | 非専門家 | 問い → アイデア → 確かめ方 → わかったこと・限界、の物語構成（3〜4 ノード）。比喩は `fidelity='analogy'` を明示して使用可 |

- 各水準のノードは**一つ下の水準のノード（L1 は A層オブジェクト）を member として参照**する。
  これにより L3 → L2 → L1 → main graph → equation_detail → 原文 evidence という
  **一本のドリルダウン鎖**が成立する（§5 `member_refs`）。
- すべての水準を必ず作る必要はない。コースの対象者に応じて教員が必要な水準だけ生成する。
  水準が欠けている場合のドリルダウンは、存在する次の下位水準へ縮退する。

---

## 5. データモデル（migration 034）

> この番号は使用済み・着手時は 068 以降で再採番（`docs/architecture/layer_registry.md` 参照）。

`backend/db/034_exposition_views.sql`。A層の `graph_json` に混ぜず独立テーブルにする理由:
(1) A層非改変の維持、(2) ノード単位の review_status 遷移と監査、(3) 将来 C層の
endorsement をノード単位に付けられる FK 実体を持つため。

```sql
CREATE TABLE IF NOT EXISTS exposition_views (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     TEXT NOT NULL,                 -- 1論文=1グラフ（theory_component_graphs と同じ単位）
    course_id       TEXT REFERENCES learning_courses(id) ON DELETE CASCADE,  -- NULL 可（document 単位でも成立）
    audience_level  TEXT NOT NULL
                        CHECK (audience_level IN ('graduate_intro', 'undergraduate', 'general')),
    title           TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT '',      -- ビュー全体の1〜2文（学習者向けリード文）
    status          TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'published', 'archived')),
    revision        INTEGER NOT NULL DEFAULT 1,    -- 楽観ロック（atlas_skeletons と同じ、衝突は 409）
    source_graph_fingerprint TEXT NOT NULL DEFAULT '',  -- 生成元 graph_json のハッシュ（陳腐化検知, §7）
    coverage        JSONB NOT NULL DEFAULT '{}'::jsonb, -- {placed: n, unplaced_refs: [{target_type, target_id, reason}]}
    generated_by    TEXT NOT NULL DEFAULT 'llm_proposed'
                        CHECK (generated_by IN ('llm_proposed', 'deterministic_skeleton', 'teacher_authored')),
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(document_id, course_id, audience_level)
);

CREATE TABLE IF NOT EXISTS exposition_nodes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    view_id         UUID NOT NULL REFERENCES exposition_views(id) ON DELETE CASCADE,
    label           TEXT NOT NULL,                 -- 短い見出し（≤30字。visual_label と同じ規律, #337）
    narrative       TEXT NOT NULL DEFAULT '',      -- 対象者向けの再叙述（数段落まで）
    term_notes      JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{term, note}] 用語注（L1 で主に使用）
    fidelity        TEXT NOT NULL DEFAULT 'faithful_summary'
                        CHECK (fidelity IN ('faithful_summary', 'simplified', 'analogy', 'reframed')),
    member_refs     JSONB NOT NULL DEFAULT '[]'::jsonb,
                    -- [{target_type: 'exposition_node'|'graph_node'|'component'|'claim'|'equation'|'derivation',
                    --   target_id, role: 'primary'|'supporting'}]
                    -- graph_node は theory_component_graphs.graph_json 内 component_id を指す
    source_backing_status TEXT NOT NULL DEFAULT 'review_required',
                    -- member の source_backing_status から保守的に導出（最弱値）。語彙は A層と同一
    review_status   TEXT NOT NULL DEFAULT 'teacher_review_required'
                        CHECK (review_status IN ('teacher_review_required', 'teacher_approved',
                                                 'needs_revision', 'rejected')),
    display_order   INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exposition_edges (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    view_id         UUID NOT NULL REFERENCES exposition_views(id) ON DELETE CASCADE,
    source_node_id  UUID NOT NULL REFERENCES exposition_nodes(id) ON DELETE CASCADE,
    target_node_id  UUID NOT NULL REFERENCES exposition_nodes(id) ON DELETE CASCADE,
    relation_label  TEXT NOT NULL DEFAULT '',      -- 対象者向けの平易な関係語（例:「これを使って」「だから」）
    edge_kind       TEXT NOT NULL DEFAULT 'leads_to'
                        CHECK (edge_kind IN ('leads_to', 'supports', 'contrasts', 'applies_to', 'defines')),
    underlying_edge_refs JSONB NOT NULL DEFAULT '[]'::jsonb,  -- 元グラフのエッジ（source/target component_id の組）
    fidelity        TEXT NOT NULL DEFAULT 'faithful_summary'
                        CHECK (fidelity IN ('faithful_summary', 'simplified', 'analogy', 'reframed')),
    review_status   TEXT NOT NULL DEFAULT 'teacher_review_required'
                        CHECK (review_status IN ('teacher_review_required', 'teacher_approved',
                                                 'needs_revision', 'rejected')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**設計上の決め:**

- **カバレッジ保証（P4 の器）**: 生成時、元グラフの main 層全ノードは
  「いずれかの exposition ノードの `member_refs` に入る」か「`coverage.unplaced_refs` に
  理由つきで載る」のどちらかでなければならない（validator でハード検証）。
  理由語彙: `out_of_scope_for_level` / `technical_detail_collapsed` / `deferred`。
  空の `unplaced_refs` を装って要素を黙って落とすことを構造的に禁止する。
- **fidelity は学習者に見せる**: 表示ラベルは「原文に忠実な要約」「簡略化しています」
  「比喩です」「構成を組み替えています」。とくに `analogy` は視覚的にも区別する
  （D層の「空欄スコープは点線」と同じ、事実と装飾を混ぜない流儀）。
- **source_backing_status の保守的継承**: member に `inferred` / `review_required` が
  含まれるノードはそれ以上強くしない。admin UI の表示分岐（実線/細線/点線/薄色）を
  そのまま流用できる。
- **thesis anchor の継承**: 元グラフの `is_thesis_anchor` ノードを member に含む
  exposition ノードは、どの水準でも「この論文の主張の到達点」として強調表示を継承する。

---

## 6. 生成パイプライン（`backend/core/exposition/`）

tension / structure_anchor / doubt と同型の独立モジュール群:

```
backend/core/exposition/
  __init__.py
  schema.py          → Pydantic モデル + 語彙 Enum（audience_level, fidelity, edge_kind, unplaced 理由）
  skeleton_builder.py → 決定論的骨格生成（非LLM）
  input_builder.py   → LLM 入力の構築
  prompt.py          → 水準別プロンプト定義
  llm_client.py      → structured output 呼び出し
  validator.py       → カバレッジ・fidelity・語彙・ドリルダウン鎖の検証
  repair.py          → validation 失敗時の再試行（2回失敗で骨格版に縮退、行は捨てない）
  worker.py          → threading.Thread 方式の非同期実行（既存 worker と同型）
  examples/          → サンプル入出力 JSON
```

**2段構え: 決定論的骨格 → LLM 再叙述**

1. **`skeleton_builder.py`（非LLM・決定論的）**: `theory_component_graphs.graph_json` の
   main 層を読み、水準ごとの骨格を機械的に作る。
   - L1: main ノード 1:1 写像（label は `THEORY_STAGE_LABELS` の平易版、narrative は空）
   - L2/L3: `stage_for_edge_type()` のステージ族を固定ルールでグルーピング
     （例: L3 は `theory_basis`+`observation_model` →「前提」、`observable_construction`+
     `equation_system`+`elimination` →「確かめ方」、`consistency_relation` →「わかること」、
     `diagnostic_application` →「使い道と限界」）。domain-independent（ステージ語彙のみ使用）
   - エッジは元グラフエッジの端点付け替えで導出、`underlying_edge_refs` に記録
   - この時点でカバレッジは構造的に 100%。教員が LLM を使わず手で narrative を書く運用も成立する
     （`generated_by='deterministic_skeleton'`）
2. **LLM 再叙述（非同期 worker）**: 骨格に対し、水準別プロンプトで
   label / narrative / term_notes / relation_label / fidelity を生成し、必要ならノードの
   統合・分割を**候補として**提案する。入力素材は: main ノードの description・atomic claim
   text・equation の意味役割（EquationSemantics）・thesis 文・**C層の承認済み shared
   explanations**（引用したら `component_citations` に記録）。structured output +
   validator + repair。2回修復失敗は骨格版のまま保持（P4）。
3. **validator のハード検証**: カバレッジ規則（§5）/ fidelity 必須 / label ≤30字 /
   member_refs 非空 / ドリルダウン鎖の破断なし（member の target が実在すること）/
   禁止表現（member の D層台帳が `untested`/`unknown` の claim を「証明された」「実証済み」等の
   断定で語らない — 禁止語彙リストは doubt ガードレールの流儀）。

**トリガーとコスト**: 自動実行しない。管理画面の「入門ビューを生成」明示操作のみ
（`background_tasks` 既存基盤でステータス提示）。コスト上限は独立の env:
`EXPOSITION_LLM_MODEL`（既定 fast tier）/ `EXPOSITION_MAX_CALLS_PER_DAY`（既定 10）。
1 水準 = 1 LLM コール（ノード数が少ないため一括生成でよい）。

**陳腐化検知**: 生成時に `source_graph_fingerprint`（graph_json の正規化ハッシュ）を記録。
ドキュメント再解析で元グラフが変わったら admin UI に「元のグラフが更新されています」を
一行表示（自動再生成はしない。published ビューを黙って書き換えない）。

**再生成の規則**: 再生成は `teacher_approved` のノード・エッジを保持し、
`teacher_review_required` の candidate のみ置換する。全置換したい場合は明示の
「破棄して再生成」操作（監査記録つき）。楽観ロックは `revision` 照合・衝突 409
（atlas_skeletons と同じ）。

---

## 7. API 設計（`backend/api/routes/exposition.py` 新設）

実パスは既存慣例に合わせ `/api/admin/...` / `/api/learning/...`。認可は既存 RBAC
（生成・編集・承認・publish = TEACHER 以上。学習者 = published の読み取りのみ。
visibility はコース/document の既存判定を継承）。

**教員向け:**

| メソッド | パス | 内容 |
|---|---|---|
| POST | `/api/admin/documents/{document_id}/exposition/generate` | body: `{audience_level, course_id?}`。骨格生成 + 非同期 LLM 再叙述を起動。既存 view があれば §6 の再生成規則 |
| GET | `/api/admin/documents/{document_id}/exposition` | 全水準の view 一覧（status / coverage / 陳腐化フラグ込み） |
| GET | `/api/admin/exposition/views/{view_id}` | ノード・エッジ込み全体 |
| PATCH | `/api/admin/exposition/views/{view_id}` | title / summary / status（publish・archive）。revision 楽観ロック。publish は可視ノード全 `teacher_approved` が条件（fail-closed） |
| PATCH | `/api/admin/exposition/nodes/{node_id}` | label / narrative / fidelity / member_refs / review_status の編集・承認。全遷移を `theory_review_events` へ |
| PATCH | `/api/admin/exposition/edges/{edge_id}` | 同上（relation_label / edge_kind / review_status） |

**学習者向け:**

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/api/learning/courses/{course_id}/exposition?level=` | published ビューのみ。ノードは `teacher_approved` のみ返す（`rejected` / 未承認は出さない）。fidelity ラベル・D層検証状態の一行・thesis 強調フラグ込み。confidence 数値は返さない |
| GET | `/api/learning/exposition/nodes/{node_id}/drilldown` | そのノードの member を一段だけ解決して返す（下位 exposition ノード、または component summary / claim text / equation の意味役割 1 行）。published 鎖の範囲のみ |

published ビューが存在しないコースでは学習者側セクション自体を出さない
（D3-6 / Field Atlas と同じ fail-closed）。

---

## 8. UI 統合

**管理画面（admin.js / admin.html — ES5 互換で記述）:**

- 既存の理論グラフ画面に**新画面を作らず統合**する。`ls-graph-layer-toolbar`
  （主グラフ/式の詳細/すべて）の隣に「読者水準」セレクタを追加:
  `専門(既存グラフ) / 大学院入門 / 学部 / 一般`。専門以外を選ぶと同じ vis.js Network に
  exposition ビューを描画する（ノード 3〜8 個なので既存の固定位置レイアウトで足りる）。
- ノード選択 → 既存の詳細ペイン（`ls-component-graph-detail`）に narrative / fidelity /
  member 一覧 / 「下の層を見る」/ 承認・修正・却下ボタン。表示分岐
  （source_backing_status → 実線/細線/点線/薄色、thesis ★）は既存関数を流用。
- 未生成水準には「この水準のビューを生成」ボタン。coverage の `unplaced_refs` は
  詳細ペインに「この水準では畳んだ要素」として一覧表示（隠さない）。

**学習UI（app.js — ES6+）:**

- 学習者が初めてグラフ的表示に触れる箇所になるが、B層原則（割り込まない）を守る:
  トピック教材ビューに「この論文の全体像」ボタンを置くだけで、自動表示・通知はしない。
- レンダリングは vis.js を学習側に導入**しない**。ノード 3〜8 個・直列に近い構造なので、
  縦フロー（カード + 矢印）の軽量 HTML/CSS で描く（Field Atlas がフロント専用描画を
  持つのと同じ判断）。カードに fidelity ラベルと D層検証状態の一行を併記。
- 水準切替はビュー右上のトグル（published 水準のみ表示）。既定はコース設定の推奨水準。
  学習者はいつでも上下に切り替えられ、選択は保存するが**評価・推定に使わない**（P3）。
- ドリルダウン: カードの「もっと詳しく」で一段下を展開（`/drilldown`）。最下層まで来たら
  既存導線に接続 — 「ここについて質問」で学習チャットへ（§9）。

---

## 9. B層・C層・D層との接続

- **チャット接続（B層）**: exposition ノードから「ここについて質問」を押すと、member の
  代表 component / chunk を既存の `chunk_id` / `element_id` / `element_type` 経路で
  チャットに渡す（**structure_anchor の A 経路＝同期・非LLM がそのまま成立**。
  `anchor_type` 語彙の拡張は不要。将来 `exposition_node` を anchor 語彙に足すかは §12）。
- **C層引用**: LLM 再叙述が教員の shared explanation を素材にした場合、
  `component_citations` に記録し、ノード詳細に「◯◯先生の説明を参照」と帰属表示する。
- **D層併記**: 学習者向けカードで、member claim の台帳が `untested` / `unknown` を含む場合
  「この部分はまだ直接検証されていません」を一行事実として併記（D3-6 の文体を踏襲）。
  E層 validator は untested member に対する断定検証表現を候補段階で弾く（§6）。

---

## 10. issue 分割（1 issue ≒ 1 PR）

```
E1-1 → E1-2 → E1-3 → E2-1 → E3-1 → E3-2
                └────→ E2-2（E2-1 と並行可）
全体 → EX-1（各マイルストーン末に実施）
```

| issue | 内容 | 依存 |
|---|---|---|
| **E1-1** | migration 034 + `core/exposition/schema.py`（語彙 Enum・Pydantic）+ `theory_review_events.entity_type` 拡張。pytest: 直列化・語彙・カバレッジ構造の検証 | なし |
| **E1-2** | `skeleton_builder.py`（非LLM 骨格生成、L1/L2/L3 のステージ族グルーピング）+ カバレッジ 100% のハード検証。サンプル graph_json での golden test | E1-1 |
| **E1-3** | LLM 再叙述: input_builder / prompt / llm_client / validator / repair / worker + generate API + コスト上限 env。2回修復失敗の骨格縮退テスト | E1-2 |
| **E2-1** | admin.js 統合: 読者水準セレクタ・exposition 描画・詳細ペイン編集・承認フロー・unplaced_refs 表示。PATCH 系 API 込み | E1-3 |
| **E2-2** | C層接続: shared explanation の素材化 + citation 記録 + 帰属表示。再生成規則（approved 保持）と陳腐化検知 | E1-3 |
| **E3-1** | publish ゲート + 学習者向け read API（published のみ・approved ノードのみ・D層一行併記・fail-closed） | E2-1 |
| **E3-2** | app.js 学習者UI: 「全体像」ボタン・縦フローカード・水準トグル・ドリルダウン・チャット接続 | E3-1 |
| **EX-1** | ガードレールテスト（`backend/tests/test_exposition_guardrails.py`）: LLM 出力 candidate 固定 / publish の fail-closed / fidelity 必須 / カバレッジ規則 / ドリルダウン鎖 / 断定検証表現の禁止 / 数値非表示 | 各M末 |

---

## 11. やらないこと（非ゴール）

- **A層スキーマ・パイプラインの変更**（`graph_json` への新 layer 追加を含む）
- **リアルタイム LLM 生成**（学習者の水準切替で動的生成しない。published の静的ビューのみ）
- **理解度推定・適応的水準変更**（学習者の行動から「あなたは L2」と判定しない）
- **わかりやすさの数値化**（読みやすさスコア・難易度スコアを作らない・見せない）
- **論文横断の統合ビュー**（本設計は 1 document = 1 view 群。コース横断は将来検討）
- **学習者による E層ノードの編集・投稿**（読み取りとドリルダウンのみ）

## 12. 未決事項（運用観察後に別 issue で判断）

1. `blackbox_policy` と E層ノード展開既定値の統合
2. `structure_anchor` の anchor_type への `exposition_node` 追加（問いの帰属を翻訳層の粒度でも取るか）
3. C層 endorsement を exposition ノード単位に付けるか（現設計では view の publish 承認のみ）
4. コース横断（複数論文）の統合 exposition ビュー
5. 音声レクチャー（lecture.py）への narrative 供給
