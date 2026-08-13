# 賭け金の台帳（Stakes Ledger, SL層）設計書 — 理解サイクル Phase 3

**作成日:** 2026-08-13
**状態:** 設計書 + **SL-1〜SL-5 実装済み（2026-08-13, §15 実装記録参照）**。本書が SL層の**正本**。
**位置づけ:** `understanding_cycle_design.md` §7 が要求する Phase 3 の専用設計書。
D層（`doubt_layer_issues.md`）の上に積む第4の拡張であり、**D層の既存5テーブル・既存公理の
意味論は変えない**。出典は `vision_expansion_proposals_2026-08.md` 提案3（哲学者の反証条件
レジストリ × 宇宙物理学者の観測反実仮想・晴れ間望遠鏡 × ネットワーク科学者の独立支持経路
の結合案「賭け金の台帳」「観測の一点吊り検査」「独立支持経路の台帳」）。

---

## §0 要旨 — D層を記帳装置から研究前線の航法装置へ

「この主張は、何が崩れたら危うくなるのか」を読めるようにする。4つの部品が揃って初めて、
Mayo の厳格テスト理論が要求する完全形 — **失敗し得る条件が明文で、失敗の帰結が追跡可能で、
まだ実行されていない厳格なテスト** — が一つの台帳として立ち上がる:

| 部品 | 答える問い | 基盤 |
|---|---|---|
| SL-1 反証条件レジストリ | 何が起これば覆るか | epistemic_ledger の双対列（verification_scopes の鏡像） |
| SL-2 観測の反実仮想 | 覆れば何処まで届くか | 既存 counterfactual 伝播（観測系 claim を seed に） |
| SL-3 独立支持経路 | どこが一点吊りか | DependencyGraph 上の決定論 max-flow / 最小カット |
| SL-4 晴れ間 | どこで確かめられていないか | open_assumptions 投影の拡張 + 昇格ゲート |

出口は **verification_proposals に一本化**する（D3-2 の既存出口を太くする。新しい出口を
作らない）。学習者には discuss 開幕「最も脆い一手」と台帳事実行への静かな併記のみ。

---

## §1 不変条項（SL1〜SL10）

- **SL1 閉世界語彙の固定**: 検証記録の不在について言えるのは
  **「このコーパスの中では検証記録がありません」だけ**である。分野レベルの不在を主張する
  語彙（「この分野では未検証」「誰も検証していない」「世界初」「未踏」等）をコード・UI 文言・
  プロンプトから構造的に禁止する（ガードレール denylist）。台帳はコーパスの射影であって
  分野の射影ではない。**晴れ間は発見の候補地であって発見ではない** — 空の穴だと確かめるのは
  望遠鏡ではなく人間の仕事。
- **SL2 AIに疑わせない（D層 §0 継承）**: 反証条件の LLM 出力は常に candidate。確定は教員。
  「この主張は反証不可能である」という判定を AI に断定させない（反証不可能の記帳も人間のみ）。
- **SL3 記帳は人間・帰属必須**: 到達可能性区分（reachability）は**人間専用語彙**。
  builder / worker が書き込むことを構造的に禁止する（`HUMAN_ONLY_VERIFICATION_STATUSES` と
  同型の分離）。匿名記帳なし・全書き込みを監査。
- **SL4 数値を見せない**: 支持経路の本数・最小カットのサイズ・confidence の生値を API / UI に
  出さない。段階事実文のみ（「単一の支持線に立っています」「複数の独立した支持線に支えられて
  います」）。discuss opening の `_strip_numeric_keys` が落とすキー名
  （`confidence` / `load_score` / `score`）を DTO に使わない。
- **SL5 情報を落とさない（P4 継承）**: 候補の dismiss・条件の撤回は状態遷移で保持。
  `DELETE FROM` 禁止（新モジュールを `core/doubt/` 配下に置くことで既存ガードレールが
  自動適用される — 意図的な配置指定）。
- **SL6 egocentric のみ（KN-1 継承）**: 分野全体のダッシュボード・一点吊りランキング・
  晴れ間の一覧地図を作らない。提示は常に「検討中の主張・前提を起点とした旅」の形。
- **SL7 研究価値の判断は師弟の対話に残す**: テーマの推奨・スコアリング・順位づけをしない。
  D層の煽り語彙禁止（「疑え」「ノーベル賞」「危険地帯」「要注意ゾーン」「崩壊させよ」）を
  継承し、新規フロント資産をガードレールの検査対象に追加する。
- **SL8 コーパス外文献確認の必須ステップ**: challenge → verification_proposal の昇格時、
  「コーパス外の文献を確認した」旨の人間の記帳（自由記述・非空）を**必須**とする（422）。
  晴れ間はまずコーパスの穴であり、proposal はそれが空の穴でもあることを人間が確かめた
  あとにだけ立つ。
- **SL9 同期パスに LLM を入れない（P6 継承）**: 反証条件候補の生成は非同期 worker。
  伝播・最小カット・晴れ間投影・観測系 claim 同定はすべて非LLM・決定論・読み時。
- **SL10 既存意味論の非改変**: `verification_scopes` の意味（どこで確かめられたか）は不変。
  反証条件はその**双対の別列**であり、既存列に混ぜない。A層非改変・counterfactual の
  伝播アルゴリズム非改変。

---

## §2 実装済み基盤の事実確認（偵察 2026-08-13 済み）

設計はすべて以下の**実測済みの事実**の上に置く（実装時に再確認不要のもの）:

1. `epistemic_ledger`（migration 029）は `verification_scopes JSONB '[]'` /
   `scope_candidates JSONB '[]'` / `scope_candidates_analyzed_at` を持ち、
   029 以降 D層5テーブルへの列追加 migration は存在しない。migration **067 は未使用**。
2. scope 候補の確定 API（`POST .../scope-candidates/{cid}/confirm`, doubt.py:543）が
   確立パターン: 候補行は昇格せず `status='confirmed'` で保持・新 scope_id 発行・
   `recorded_by` に**教員** user_id（帰属が人間に移る）・`from_candidate_id`・上書き可・
   1軸+根拠の再検証。**SL-1 の確定 API はこれの写し**。
3. `compute_propagation(graph, seeds)` は純関数。seed 解決の fallback
   （counterfactual.py:73-77）が **claim id を既に受けられる**（`graph.claim_refs` 経由）
   — SL-2 は伝播計算に手を入れない。
4. `graph_json.nodes[]` に **`stage` キーは存在しない**。stage は
   `element_vocab.theory_stage_key(node.label or node.visual_label)` で復元する
   （context_lens.py:1246 が実例）。
5. observation 系の同定素材: `graph_json.dsl.edges[].predicate == "MEASURES"` +
   `evidence_refs.claim_ids`（DSL 層の node id は component 層と**別体系** — claim 経由でのみ
   渡れる）/ main node stage / `theory_claims.claim_type ∈ {observable_definition,
   diagnostic_claim}`。`dsl` ブロックが空の旧 run が存在しうる。
6. backend に **networkx / numpy / scipy は無い**。max-flow / 最小カットは純 Python 実装
   （単位容量 Edmonds–Karp・BFS・閉路対応）。既存コードに該当実装は 0 件。
7. `dependency.build_dependency_graph` は `graph_layer='debug'` と
   `source_backing_status='inferred'` のみ除外（`review_required` は含まれる）。
   フォールバック生成の簡易 edge（`source_backing_status` 空）は除外規則をすり抜けて残る。
8. `verification_proposals` は**作成 API のみ**（status 遷移 API なし・course_id 列なし）。
   challenge→proposal 昇格は `withdrawn` からでも通る（前提チェックなし）。
9. `VerificationScope` の Pydantic モデルと実 JSONB に既知の乖離がある
   （`evidence_quote` / `from_candidate_id` がモデルに無い）。**SL-1 の新モデルは実 JSONB と
   一致させる**（乖離を踏襲しない）。
10. 日本語段階ラベル表はサーバ（doubt.py / core/doubt/schema.py）とフロント
    （doubt-atlas.js）に二重存在する。SL の新語彙は**必ず両方に**追加する。

---

## §3 SL-1 反証条件レジストリ

### 3.1 DB（migration 067、scope_candidates と完全同型）

```sql
-- backend/db/067_stakes_ledger.sql
ALTER TABLE epistemic_ledger
    ADD COLUMN IF NOT EXISTS falsification_conditions JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE epistemic_ledger
    ADD COLUMN IF NOT EXISTS falsification_candidates JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE epistemic_ledger
    ADD COLUMN IF NOT EXISTS falsification_analyzed_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_epistemic_ledger_falsification_pending
    ON epistemic_ledger(course_id) WHERE falsification_analyzed_at IS NULL;
```

（worker の claim SQL が `analyzed_at IS NULL` を全走査しないよう部分 index を最初から付ける
— 既存 `scope_candidates_analyzed_at` の index 欠落と同じ穴を掘らない。既存側の是正は
本 migration のスコープ外・§15）

加えて verification_proposals の拡張（SL-4/SL-8 用）:

```sql
ALTER TABLE verification_proposals
    ADD COLUMN IF NOT EXISTS course_id TEXT NOT NULL DEFAULT '';
ALTER TABLE verification_proposals
    ADD COLUMN IF NOT EXISTS reachability TEXT NOT NULL DEFAULT 'unassessed'
        CHECK (reachability IN ('reachable','next_generation','unreachable','unassessed'));
ALTER TABLE verification_proposals
    ADD COLUMN IF NOT EXISTS external_check TEXT NOT NULL DEFAULT '';
ALTER TABLE verification_proposals
    ADD COLUMN IF NOT EXISTS external_checked_by UUID REFERENCES users(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_verification_proposals_course ON verification_proposals(course_id);
```

counterfactual_sessions の拡張（SL-2 用）:

```sql
ALTER TABLE counterfactual_sessions
    ADD COLUMN IF NOT EXISTS toggled_observations JSONB NOT NULL DEFAULT '[]'::jsonb;
CREATE INDEX IF NOT EXISTS idx_counterfactual_sessions_course ON counterfactual_sessions(course_id);
```

（course index は既存 GET の絞り込み列の既知欠落を同時に是正 — 最終状態の是正であり
意味変更ではない）

### 3.2 反証条件要素の構造（語彙の正本は `core/doubt/schema.py` に追加）

```
condition_id      : str（発行はサーバ）
statement         : str（何が観測・測定されたら覆るか。事実文）
kind              : "observation_value" | "auxiliary_hypothesis" | "not_formulable"
                    （Duhem 区別: 観測値そのもの / 較正・装置等の補助仮説。
                     not_formulable = 「反証条件を定式化できない」という人間の明示記帳 —
                     これにより 反証不可能（記帳あり）と未検討（空配列）が初めて区別できる）
reachability      : "reachable" | "next_generation" | "unreachable" | "unassessed"
                    （人間専用語彙・SL3。既定 unassessed。LLM 候補には含めさせない）
evidence_ids      : list[str]
evidence_quote    : str（出典の逐語。候補由来は validator が verbatim 検査済み）
recorded_by       : str（確定/記帳した人間の user_id。空にしない — SL3）
reason            : str（非空）
recorded_at       : str（UTC ISO）
from_candidate_id : str | ""（候補確定由来のとき）
```

Pydantic モデル `FalsificationCondition` は**この実 JSONB と完全一致**させる（§2-9）。
候補要素 `FalsificationCandidate` は `ScopeCandidate` と同型
（candidate_id / statement / kind / evidence_quote / reason / confidence / status /
detector_version / created_at。**reachability を持たない** — LLM 出力に含まれていたら
validator が剥いで warning）。status 語彙は最初から3値で宣言する
（`candidate` / `confirmed` / `dismissed` — scope 側 docstring の2値記載という既知の
ズレを繰り返さない）。

### 3.3 候補生成 worker（`backend/core/doubt/falsification_conditions/`・9ファイル構成）

`scope_candidates/` の完全な写し（agent / input_builder / prompt / llm_client / schema /
validator / repair / worker / examples）。差分のみ記す:

- **入力**: `input_builder` は scope 側と同じ4分岐（claim / equation / component /
  assumption）の出典収集に加え、**DependencyGraph の下流到達集合の要約**（この対象に
  依存するノードのラベル最大5件・非LLM）を文脈に足す — 「覆えたときの帰結」を条件文の
  具体性に使わせる。
- **プロンプト絶対ルール**: 判定しない・候補である・確定は人間（SL2）/ 反証条件は
  「観測・測定の言明」の形で書く（宇宙物理の実感: 反証条件はほとんど常に観測精度の言明）/
  kind の2値（observation_value / auxiliary_hypothesis）を必ず付ける（`not_formulable` は
  **LLM に出力させない** — 人間専用）/ evidence_quote は出典の逐語 / **到達可能性を
  評価しない** / SL1 の閉世界語彙（分野レベルの不在言明の禁止）。
- **validator（hard）**: JSON スキーマ / statement 非空 / kind 2値 / evidence_quote 逐語
  （`_normalize_ws` 方式）/ reason 非空 / 候補数 ≤ 3。reachability・not_formulable の
  混入は剥いで warning。
- **worker**: `falsification_analyzed_at` を冪等マーカーに `FOR UPDATE SKIP LOCKED` で
  claim → 上限超過時は NULL 戻しで翌日回し → `falsification_candidates` 列への append
  のみ（**本体列・verification_status・reachability に触れない** — ガードレールで固定）。
  `bind_usage_context("doubt:falsification_conditions", ...)`。
- **env**: `DOUBT_FALSIFICATION_MAX_CALLS_PER_DAY`（既定 10・独立カウンタ）/
  `DOUBT_FALSIFICATION_LLM_MODEL`（空 = fast tier）。config.py の D層セクションに5系統目
  として追加。**U層/M層の3点セット**: `KNOWN_FEATURES` + `llm_policy.scene_for_feature` +
  `_FEATURE_ENV_SETTINGS` に `doubt:falsification_conditions` を同時登録
  （どれか欠けると KNOWN_FEATURES 全解決テストが即死する — 理解サイクル Phase 2 §15 と
  同じ罠）。
- **トリガー**: `POST /api/admin/doubt/courses/{course_id}/falsification-candidates/refresh`
  （scope 側 refresh と同型・非同期）。パイプラインフックへの相乗りは v1 では行わない
  （D層フック3本の直後に足すのは実測後の判断 — §15）。

### 3.4 API（`routes/doubt.py` に追加・全て `_require_teacher`）

| メソッド・パス（/api/admin/doubt 配下） | 役割 |
|---|---|
| `POST /ledger/{t}/{id}/falsification-conditions` | 手動記帳。statement/kind/reason 非空 + evidence（ids or quote）必須 → 422。reachability 指定可 |
| `PATCH /ledger/{t}/{id}/falsification-conditions/{condition_id}` | 訂正（reachability の後付け含む）。訂正後も必須項目を再検証 |
| `POST /ledger/{t}/{id}/falsification-candidates/{cid}/confirm` | scope confirm の写し: 上書き可・新 condition_id 発行・recorded_by=教員・候補行は status='confirmed' で保持。**reason 非空検証を confirm 経路にも置く**（scope 側の既知の抜けを踏襲しない） |
| `POST /ledger/{t}/{id}/falsification-candidates/{cid}/dismiss` | status='dismissed' + dismissed_by/at（行保持） |

- `GET /ledger/{t}/{id}`（既存）のレスポンスに `falsification_conditions`（全件）と
  `falsification_candidates`（candidate のみ）を追加。生値 confidence は落とす
  （`_candidate_out` と同型の射影）。
- 監査: 新 entity_type は作らず **`AUDIT_ENTITY_LEDGER` を流用**（entity_id =
  `f"{target_type}:{target_id}"` の既存慣行）。`metadata.action` に
  `falsification_add` / `falsification_patch` / `falsification_candidate_confirm` /
  `falsification_candidate_dismiss` を追加し、KPI（metrics.py）の action 語彙表にも追記。

---

## §4 SL-2 観測の反実仮想

### 4.1 観測系 claim の多段同定（`core/doubt/observation_targets.py` 新規・非LLM・読み時）

単一手段に賭けず3段の縮退で同定し、**どの経路で同定したかを `identified_via` として保持**
する（P4）:

1. **A: DSL 述語**（第一）: `graph_json.dsl.edges[]` の `predicate == "MEASURES"` →
   `evidence_refs.claim_ids`。DSL node id は component 層と別体系のため claim 経由でのみ渡る。
2. **B: theory stage**（第二）: main 層 node の stage を
   `theory_stage_key(label or visual_label)` で復元し
   `{diagnostic_application, observation_model, observable_construction}` に属する node の
   `linked_claim_ids`。
3. **C: claim 型**（第三の縮退）: `theory_claims.claim_type ∈
   {observable_definition, diagnostic_claim}`。

`dsl` が空の旧 run では A が空になるだけで B/C が生きる（fail-soft）。戻り値は
`[{claim_id, label, identified_via}]`。

### 4.2 API 拡張（伝播計算は非改変）

- `CounterfactualComputeRequest` に optional
  `toggled_observations: list[{claim_id: str, aspect: "value"|"systematics"}]` を追加。
  `aspect` は Duhem 区別の**記帳のみ**（伝播は同一 — 観測値を疑うのも較正モデルを疑うのも、
  依存範囲の計算上は同じ seed）。seed 解決は既存 fallback（claim id →
  `graph.claim_refs`）をそのまま通す — **counterfactual.py の伝播ロジックに変更なし**。
- `toggled_assumption_ids` と `toggled_observations` の**少なくとも一方が非空**なら計算する
  （両方空 → 422。既存の「toggled_assumption_ids 空 → 422」を緩和）。
- 保存: `counterfactual_sessions.toggled_observations JSONB`（migration 067）。
  既存列 `toggled_assumption_ids` の意味は不変。
- `GET /api/admin/doubt/courses/{course_id}/observation-targets` を新設
  （観測系 claim の一覧 = 「倒せる観測」の選択肢。identified_via 併記・数値なし）。
- 補助: `compute_counterfactual` の `node_labels` に surviving のラベルも含める拡張は
  UI 要求が確定するまで保留（§15）。

### 4.3 UI（doubt-atlas.js の反実仮想セクション拡張）

- 既存の「前提を仮に倒す」の隣に「観測を仮に倒す」入り口。observation-targets から選択 →
  aspect の2択（「観測値そのもの」「較正・装置などの補助仮説」）→ 既存の試算・保存フロー。
- 文言は既存規約を継承: 「崩壊」を使わず「この観測に依存する範囲」。可逆・非破壊の明示。
- 学習者向けの反実仮想操作は**作らない**（D層 §8-3 の地位勾配判断を継承）。

---

## §5 SL-3 独立支持経路

### 5.1 計算（`core/doubt/support_paths.py` 新規・非LLM・純 Python）

- **入力**: `dependency.build_dependency_graph` の `DependencyGraph`（既存・非改変）+
  観測系 claim 集合（§4.1）+ 対象（target_type/target_id → `seed_nodes_for_target` で
  ノード集合に解決）。
- **アルゴリズム**: 仮想 super-source（全観測系 claim の `claim_refs` ノード）→ 対象ノードの
  単位容量 max-flow（Edmonds–Karp・BFS）。**エッジ非交差経路数 = 独立な支持線の数**、
  最小カット = 「同時に崩れると支持が途切れるノード/エッジの最小集合」。networkx を
  使わない（requirements に無い・追加もしない）。閉路は BFS で自然に扱う。
- **支持線の資格（二次フィルタ）**: エッジは `source_backing_status ∈
  {source_backed, partially_source_backed}` のもののみ容量1で数える。
  `review_required` / `inferred` / **空文字（フォールバック簡易 edge）は容量0**
  （§2-7 のすり抜けをここで塞ぐ。dependency.py 自体は非改変 — 除外規則の非対称は
  build 側でなく資格側で扱う）。
- **出力（数値なし・SL4）**:
  ```
  support_line_level : "none" | "single" | "several"   （0 / 1 / 2以上）
  fact_line          : 「単一の支持線に立っています」等の事実文
  cut_members        : [{node_id, label}]  （最小カット構成。数は言わない・列挙のみ）
  observation_roots  : [{claim_id, label, identified_via}]
  ```
  内部の経路数・容量は関数外に出さない。DTO キーに `count` / `score` 系の名前を使わない。

### 5.2 提示（egocentric・読み時・fail-soft）

- `GET /api/admin/doubt/ledger/{t}/{id}`（既存）に optional キー `support_lines` を追加
  （導出失敗は キーなし で返す — 台帳本体を壊さない）。
- 事実文の例（3値・段階のみ）:
  - none: 「この対象への、観測記録からの支持線はこのコーパスの中では見つかりません。」
  - single: 「この対象は単一の支持線に立っています。『{カット構成のラベル列挙}』が
    同時に崩れると、観測からの支持が途切れます。」
  - several: 「この対象は複数の独立した支持線に支えられています。」
- Assumption Atlas への表示・コース横断の一点吊り一覧は**作らない**（SL6）。

---

## §6 SL-4 晴れ間 — 未検証×高負荷×到達可能の候補地図

### 6.1 投影の拡張（新テーブルなし・読み時導出）

`compile_open_assumptions` の item に3キーを追加する（既存キーは不変）:

```
has_falsification_condition : bool（not_formulable を除く条件が1件以上）
reachability_summary        : "reachable" | "next_generation" | "unreachable" | "unassessed" | ""
                              （条件群の最良値。reachable > next_generation > unreachable。
                               条件なしは ""）
support_line_level          : "none" | "single" | "several" | ""（§5。導出失敗は ""）
```

「晴れ間候補」= `load_level ∈ {high, highest}` × 低検証（既存条件）×
`reachability_summary == "reachable"` — **並び順の変更・順位演出はしない**（SL7）。
事実キーを返すだけで、絞り込みはフロントのフィルタ操作（教員の明示操作）に委ねる。

### 6.2 昇格ゲート（SL8 の実装）

- `POST /challenges/{challenge_id}/proposals`（既存）に **`external_check: str` を必須化**
  （空 → 422。detail は事実文「コーパス外の文献確認の記録が必要です」）。`reachability` は
  optional（既定 unassessed）。`external_checked_by` = 実行者。
- **破壊的変更の扱い**: 呼び出し元は doubt-atlas.js のみ（偵察確認済み）。UI を同時改修する。
- `PATCH /proposals/{id}` を新設（status 遷移 `proposed → in_progress → completed` /
  `withdrawn`、reachability の更新。owner または TEACHER）。**§2-8 の既存欠落
  （遷移 API なし）をここで埋める**。`challenges.status='withdrawn'` からの昇格は 422 に
  是正する（§2-8 後段の前提チェック欠落。挙動変更だが「取り下げた疑義から提案が立つ」のは
  記帳の整合性バグであり最終状態の是正と判断）。
- proposal 作成時に `course_id` を challenge から複製（migration 067 の新列）。

### 6.3 閉世界語彙の全面適用（SL1）

- 晴れ間・支持経路・台帳のすべての不在言明は
  **「このコーパスの中では〜が見つかりません/記録がありません」** の形に固定。
- ガードレール: 禁止語彙（「この分野では未検証」「誰も検証していない」「世界初」「未踏」）を
  doubt.py / open_assumptions.py / support_paths.py / falsification_conditions/（プロンプト
  含む）/ doubt-atlas.js で検査 + 肯定形の固定文言の原文存在検査。

---

## §7 discuss「最も脆い一手」への結線

- `open_assumptions` item の新キー（§6.1）を受けて、`opening.py::_assumption_fact_line` に
  分岐を追加する:
  - 条件あり: 「何が起これば覆るかが記帳されている前提です。」
  - not_formulable のみ: 「反証条件を定式化できないと記帳されている前提です。」
  - 条件なし（既存文の後段に）: 「覆る条件はまだ定式化されていません。」
- **第3の kind / 第3の主語は作らない**（2-way quota `_allocate_fragile_quota` は非改変。
  主語固定規律「論文 / システム」を維持）。
- DTO キー名に `confidence` / `load_score` / `score` を使わない
  （`_strip_numeric_keys` が黙って落とす — §2 の実測仕様）。

## §8 学習者導線（v1 最小・読み取り専用）

- `GET /api/learning/courses/{course_id}/ledger/{t}/{id}`（既存）に、反証条件の**事実文のみ**を
  追加投影する（`recorded_by` を落とす既存の射影規約と同型。kind・reachability は日本語
  段階ラベルで、「教員の記帳」出所ラベル付き）。support_line_level の一行事実も同様。
- 台帳行が無ければ既存どおり 404（セクションごと非表示 = fail-closed）。
- 学習者からの反実仮想・晴れ間閲覧・条件への異議は**非スコープ**（D層 §8-3 継承）。

## §9 UI（教員・doubt-atlas.js）

1. 台帳詳細ペイン（`renderNodeLedgerSection` 系）に「覆る条件」区画:
   条件一覧（kind / reachability の日本語ラベル・出所）+ 候補カード（confirm / dismiss /
   上書き編集）+ 手動記帳フォーム。空欄は `doubt-muted`（警告色にしない — 空欄は発見）。
2. 反実仮想セクションに「観測を仮に倒す」（§4.3）。
3. 未検証合意リストに3列追加（条件の有無 / 到達可能性 / 支持線の段階）+
   「到達可能な反証条件がある項目だけ表示」フィルタ（教員の明示操作）。
4. proposal 昇格フォームに external_check 入力（必須）+ reachability 選択。
5. **段階ラベルの二重表**（サーバ / フロント）の両方に新語彙を追加（§2-10）。
   `data-ui-anchor`（`doubt-atlas.falsification-*` 等）+ teacher マニュアル節 +
   ADMIN_UI_ANCHORS の**3点セット**を揃える（網羅テストが落ちる）。

## §10 コスト・計測

- env: `DOUBT_FALSIFICATION_MAX_CALLS_PER_DAY`(10) / `DOUBT_FALSIFICATION_LLM_MODEL`
  （fast 既定）。既存 D層カウンタとは独立。
- U層 feature `doubt:falsification_conditions`（KNOWN_FEATURES / scene_for_feature /
  _FEATURE_ENV_SETTINGS の3点同時登録）。
- KPI: `GET /api/admin/doubt/metrics`（既存・SYSTEM_ADMIN）の action 集計語彙に
  falsification 系4種 + proposal 遷移を追加。専用カウンタテーブルは作らない（DX-2 継承）。

## §11 実装フェーズ分割

| フェーズ | 内容 | migration |
|---|---|---|
| SL-1 | 反証条件レジストリ（067 + 語彙 + worker + 確定/手動 API + 台帳 GET 拡張 + UI 区画） | 067 |
| SL-2 | 観測の反実仮想（observation_targets + compute/保存拡張 + UI） | （067 に同梱） |
| SL-3 | 独立支持経路（support_paths + 台帳 GET 拡張 + 事実文） | 不要 |
| SL-4 | 晴れ間投影 + 昇格ゲート + proposal PATCH + UI 列/フィルタ | （067 に同梱） |
| SL-5 | discuss 結線 + 学習者事実行 + マニュアル/アンカー3点セット | 不要 |

SL-1 と SL-2/SL-3 は独立に実装・検証可能。SL-4 は SL-1（reachability）と SL-3
（support_line_level）に依存。067 は SL-1 着手時に一括で切る（同一機能群の migration を
分割しない）。

## §12 ガードレール（`backend/tests/test_stakes_ledger_guardrails.py`）

1. **SL1 閉世界語彙**: 禁止語（「この分野では未検証」「誰も検証していない」「世界初」
   「未踏」）が対象ソース群（doubt.py / open_assumptions.py / support_paths.py /
   falsification_conditions/ 全体 / doubt-atlas.js）に無い + 固定文言
   「このコーパスの中では」の原文存在。
2. **worker の書き込み分離**（D層契約2〜4と同型）: falsification worker の
   `SET ... WHERE` に `falsification_conditions` / `verification_status` /
   `verification_scopes` / `reachability` が現れない。
3. **SL3 人間専用語彙**: `FalsificationCandidate` に reachability フィールドが無い /
   validator が剥ぐことのテスト / builder（ledger_builder）ソースに `falsification` が
   現れない。
4. **SL4 数値非表示**: support_paths の公開 DTO 構築関数に `count` / 経路数の数値キーが
   無い / doubt.py に `"path_count"` 等が無い / 段階3値語彙の固定。
5. **SL5**: `core/doubt/` 配下 `DELETE FROM` 禁止（既存テストが自動カバー — 配置で担保）。
6. **SL8**: proposal 昇格 API に external_check 空の 422 が存在（関数ソース検査）。
7. **SL9**: support_paths / observation_targets が fastapi・LLM クライアントを import
   しない / networkx を import しない（純 Python の固定）。
8. bind-cast アンチパターン（`:name::type`）検査対象に新モジュールを追加。
9. 監査 action 語彙・AUDIT_ENTITY_TYPES 非拡張（LEDGER 流用）の固定。
10. `_strip_numeric_keys` 禁止キー名を SL の DTO が使わない（opening 結線分）。

**着手条件（understanding_cycle_design.md §7）の充足**: 本設計書が「専用設計書」。
上記ガードレール一覧が「先に書けること」の証明であり、実装はガードレールテストの
作成から始める。

## §13 非スコープ（v1）

- LLM 事前知識への三角測量照会（「この前提×領域の検証を知っているか」）— 標準化判定
  （Phase S）と同型の拡張として実測後に判断
- コーパス横断の認識的ストレステスト（identity link を渡る伝播）— Phase 4 の領域（KN-3 の
  弁・Buldyrev 型の相互依存網リスクの検討が前提）
- Assumption Atlas 散布図への支持線・反証条件の表示（点の情報過多を避ける）
- 学習者の反実仮想操作・晴れ間閲覧・条件への異議
- 反証条件の自動再生成トリガー（パイプラインフック相乗り）
- atlas 骨格領域語彙との「前提×領域」二次元晴れ間マップ

## §14 未決事項（実装時に確認）

1. `scope_candidates_analyzed_at` の index 欠落（既存）を 067 で同時是正するか
2. `compute_counterfactual` の `node_labels` に surviving を含める拡張（UI 要求次第）
3. `dependency.py` が `review_required` を除外しない非対称（§2-7）— 支持線の資格
   フィルタで足りるか、実データで検証
4. observation_targets の3段同定の実効カバレッジ（dsl 空 run の割合）
5. falsification worker のパイプラインフック相乗りの是非（コスト実測後）
6. 二重ラベル表（サーバ/フロント）の一本化リファクタ（別 issue）

---

## §15 実装記録（2026-08-13）

Fable 5 指揮・Sonnet 3体（core / UI / routes の2波構成・ファイル所有権分離）。
設計書の着手条件どおり**ガードレールテストを先行作成**してから実装。
バックエンドフルスイート **9,235 pass**（着手前から +199・リグレッションなし）。
migration **067** 適用（設計 §3.1 のまま）。

- **core**（`backend/core/doubt/`）: `falsification_conditions/`（scope_candidates 完全同型の
  9ファイル + examples）/ `observation_targets.py`（3段同定・A>B>C 優先・claim_id 昇順）/
  `support_paths.py`（純 Python 単位容量 Edmonds–Karp。**仮想 super-sink は大容量** —
  単一 sink ノードでも独立2経路が flow=2 になることをテストで証明。カットエッジが仮想
  source に接する単一ルート事例は下流実ノードへ縮退）。語彙・ラベル・Pydantic モデルは
  `core/doubt/schema.py` に追加（実 JSONB と完全一致 — §2-9 の乖離を踏襲していない）。
- **routes**（`routes/doubt.py`）: §3.4/§4.2/§6.2 の全エンドポイント + 台帳 GET 拡張
  （candidates は confidence 落とし射影・support_lines は fail-soft の optional キー）+
  学習者投影（statement / kind_label / reachability_label / 「教員の記帳」・
  support_fact_line のみ — cut_members / recorded_by 非漏洩）。counterfactual は
  **assumption ids と観測 claim ids を結合して既存 fallback に渡すだけ**（伝播非改変）。
  proposal 昇格は external_check 必須 422 + withdrawn チャレンジからの昇格 422 に是正 +
  `PATCH /proposals/{id}`（前進遷移 + withdrawn）新設。
- **投影**: `open_assumptions` item に `has_falsification_condition` /
  `falsification_not_formulable` / `reachability_summary` / `support_line_level` を追加。
  **support_paths はコンテキスト再利用にリファクタ**（`build_support_context` +
  `compute_support_lines_from_context`。コース1回のグラフ構築を全 item で共有 —
  公開シグネチャは薄いラッパとして不変）。opening の `_assumption_fact_line` は §7 の
  3文言を逐語で分岐（新キー不在の旧入力には従来文を返す後方互換ガード付き）。
- **UI**（doubt-atlas.js）: 覆る条件区画（一覧 / 候補カード confirm・dismiss +
  reachability 選択 / 手動記帳 / AI 再生成）・観測を仮に倒す（aspect 2択・既存前提トグルと
  併用可）・支持線事実行・未検証合意リスト3列 + 到達可能フィルタ（クライアント側・既定 off）・
  proposal external_check 必須フォーム + status 遷移カード。アンカー5件の**3点セット**
  （KNOWN_ADMIN_UI_ANCHOR_IDS + `docs/manual/teacher/18-admin-doubt-atlas.md` 節 +
  網羅テスト件数 255→260）。
- **テスト**: `test_stakes_ledger_{guardrails,core,api,ui_static}.py`（§12 の10項を充足）。
- **実装裁定（設計書からの確定差分）**:
  1. validator の混入処理: `reachability` は剥いで warning（候補は生存）、
     `kind='not_formulable'` はその候補1件だけ drop（warning・repair は発火させない）。
  2. proposal の status 遷移カードは POST 応答からのインプレース描画
     （proposal 一覧 GET が存在しないため。台帳リフレッシュ後は再表示されない既知の制約 —
     一覧 GET の新設は §14 に積む）。
  3. 既記帳条件の PATCH 編集 UI は v1 未提供（API はあり。手動記帳・候補確定・
     reachability 後付けで運用可能）。
- **残作業**: docker 実機 E2E（反証条件の記帳→伝播→支持線の実データ確認）。
  §13 非スコープ・§14 未決事項は不変。

---

*出典: vision_expansion_proposals_2026-08.md 提案3（結合案「賭け金の台帳」「観測の一点吊り
検査」「独立支持経路の台帳」と、討論で確定した閉世界誤謬対策・Duhem 区別・到達可能性区分）。
D層内部の実測事実（§2）は 2026-08-13 の偵察による。*
