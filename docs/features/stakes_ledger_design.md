# 賭け金の台帳（Stakes Ledger, SL層）設計書 — 理解サイクル Phase 3

**作成日:** 2026-08-13
**状態:** 設計書 + **SL-1〜SL-5 実装済み（2026-08-13, §15 実装記録参照）**。2026-10-23 追補 §16（台帳の行キーに論文を含める・IK-0899）実装済み。本書が SL層の**正本**。
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
   → **解消（2026-08-14）**: [提案 §2-2](../architecture/feature_consolidation_proposals_2026-08-13.md) の
   実装で決着。正本は [`label_vocab_design.md`](label_vocab_design.md)。実測の結論は
   「生値→段階ラベルの変換はサーバに100%あり、フロントには無い（二重なのは語彙 enum の
   日本語訳）」であり、**フロントの表は消さずミラーとして固定**した。SL層に関係する具体は
   ①`doubt-atlas.js` の `FALSIFICATION_KIND_LABELS` / `REACHABILITY_LABELS` /
   `FALSIFICATION_ASPECT_LABELS` / `SUPPORT_LEVEL_BADGE_LABELS` / `COVERAGE_LABELS` /
   `CHALLENGE_STATUS_LABELS` / `PROPOSAL_STATUS_LABELS` / `STATUS_LABELS` / `LOAD_LABELS`
   のサーバ正本を `core/doubt/schema.py`（+ `core/label_vocab.py`）に揃え、
   `backend/tests/test_doubt_vocab_mirror.py` が逐語一致を固定（片側だけの変更は落ちる）
   ②`FACT_LINE_*`（`core/doubt/support_paths.py`）は SL1 の閉世界語彙なので**統合対象外**
   （原文固定のまま）③参照ゼロだった `DOUBT_TYPE_LABELS` を削除。文言・API 応答は不変。

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

## §16 追補（2026-10-23）: 台帳の行キーに論文を含める（IK-0899）

**状態:** 実装済み（第 25 周 面 B 第 2 周の是正波・migration **092**）。判断は審判の推奨どおり
（「uxsim/runs/round25/judge_doubt_r2.md」 §5 — 3 種のうち「後戻りしにくい構造」）。

### 16.1 何が起きていたか

migration 029 以来、`epistemic_ledger` の行キーは `UNIQUE(target_id, target_type)` で、対象の ID が
全体で一意である前提に立っていた。理論操作グラフのノード ID（`theory_op_0001` …）と式の agent ID
（TeX ラベル由来）は**論文の中でしか一意でない**ため、論文をまたいで 1 行を共有し、ある論文で記帳した
スコープ・検証状態が別の論文の同じ番号のノードにも出ていた（学生の地図の札・支持線・依存の件数も同じ行）。

### 16.2 決めたこと

1. **行キーは `(document_id, target_type, target_id)`**（`UNIQUE NULLS NOT DISTINCT`。前提の行は複数の論文に
   またがるため document が NULL のことがある）。claim / assumption と UUID 形の id は全体で一意なので、
   document_id は権限ゲートと出所にだけ使い、行の突き合わせは従来どおり対象の id だけ（意味は不変）。
2. **既存の衝突行は自動で振り分けない**。記帳時にどの論文を開いていたかは行にも監査にも残っていない。
   migration 092 は旧キーが残っている DB で 1 回だけ、論文の中でしか一意でない id の行のうち人の記帳
   （スコープ・覆る条件・根拠の線・人間専用の検証状態）が入ったものに `attribution_check_needed = TRUE` を
   立て、台帳 GET が `attribution_note`（`ledger_targets.LEDGER_ATTRIBUTION_CHECK_NOTE`）で教員に
   「どの論文の記帳か確かめてください」と事実で示す。記帳の入っていない衝突行は再解析の ledger_builder が
   新キーで論文ごとに作り直す。行は消さない・複製しない。
3. **行キーの正本は `core/doubt/ledger_targets.ledger_row_filter`**（ここ以外で WHERE 句を組まない）。読み手は
   `document_id` を**必須のキーワード引数**で受け（`_fetch_ledger_row` / `learner_ledger_line` /
   `resolve_component_ledger_target` / `open_assumptions.target_label` / `personal_graph.queries.
   fetch_component_ledger_statuses`）、論文の中でしか一意でない対象で document が分からなければ閉じる
   （API は 422 の事実文、学習者の一行は出さない）。worker（スコープ候補・覆る条件候補）は claim した行の
   document を `LedgerTargetRef.document_id` で運ぶ。
4. **監査の metadata に `document_id`** を残す（対象単位の書き込みの全経路）。今後の振り分けの手掛かり。
5. 代案（target_id に論文の名前空間を前置）は、既存の参照（反実仮想のトグル・ID の再係留表・疑義の対象）の
   書き換えが要り、付け忘れが再発の入口になるので採らなかった。

### 16.3 同じ波で入れたこと（document を必須にしたので乗った是正）

- **IK-0900**: 対象単位の書き込みルートはすべて `_require_editable_ledger_target`（対象の教材の編集権限。
  ノード・式は指定の論文にその対象が実在することも確かめる）。前提の確定・却下は `_require_editable_assumption`、
  疑義と検証提案は**閲覧できる教員なら**残せる（帰属必須の公開の学術行為）。台帳 GET は閲覧ゲート + `can_edit`
  （偽なら `can_edit_note` を出し、画面は記帳ボタンを無効にする）。AST ガードレールで固定。
- **IK-0901**: 台帳対象の表示名の正本は `core/doubt/target_labels.py`（`LedgerTargetLabels` / `ledger_target_label`。
  式は印字番号、ノードはゼミ前ブリーフと同じ段の日本語名 + 根拠の主張、読めなければ「名前を引けない対象」）。
  学習者の未検証の前提一覧は同じ文面を畳み、名前を引けない行を後ろへ。
- **IK-0902**: 支持の根は対象へ届いた経路の根だけ。支持線ゼロ（none）では出さない。
- **IK-0903**: 観測の選択肢は `observation-targets?document_id=` で開いているグラフの論文に絞る。範囲外の観測は
  伝播に渡さず warnings（`counterfactual.OBSERVATION_OUTSIDE_SCOPE_WARNING_PREFIX`）。
- **IK-0904**: 式・部品の台帳は `challenge_allowed: false` + `challenge_note` + `supporting_claims`（ノードの主張参照の
  うち live の主張）を返し、画面はそこから主張の台帳を開ける。前提の一覧は `mining_note` で前提マイニングの
  候補が範囲にあるかを毎回事実で添える。
- **IK-0905**: 入力検査の事実文は画面の語（`label_vocab.LEDGER_SCOPE_AXIS_LABELS`）、記帳者は `recorded_by_label`、
  同じ内容のスコープは受けたうえで `duplicate` + `note`、検証状態の PUT は `changed` + `note`。
- **IK-0907**: 学習画面の画面文脈 `view.personal_map_seen`（この学習で「わたしの地図」を開いたか）を受けて、
  SA層 `view` の解決器が「地図は状態の札だけ・条件は出典タブの一行」の事実を渡す。

### 16.4 設計時の検査（pre-mortem）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 論文の中でしか一意でない ID を全体キーにしていた | `id-unique-only-within-inner-scope` | 行キーに親（論文）を含める。読み手は document を必須の引数で受け、無ければ閉じる |
| 権限・範囲の条件を経路ごとに書いていた | `condition-not-propagated` | `enforce-condition-in-query-with-required-argument` を採る（document_id 必須・`ledger_row_filter` の 1 箇所） |
| 表示名の解決が 2 つに分かれていた | `duplicate-canonical-sources` | `target_labels.py` に寄せ、旧 `target_label` は委譲（戻り値の契約は不変） |
| 既存の記帳が誰のものか分からない | `reexecution-overwrites-human-decision` | 自動で振り分けず印を立てて人に確かめさせる（行は消さない） |

### 16.5 残した課題

- 衝突行の印は「確かめてください」までで、教員が別の論文へ付け替える操作（移動 API）は無い（v1）。
- スコープ候補・覆る条件候補の文脈組み立て（`input_builder.build_target_context`）は式の id を論文で絞らずに
  主張を引く（候補の材料に別の論文の主張が混ざりうる）。行の更新は論文ごとになった。
- 前提の地図に主張の点を出す層の選択（IK-0904 の第 2 案）は入れていない（式・部品の台帳から主張へ辿る道で代える）。
- 地図の札から台帳の一行を直接開く操作（IK-0907 の第 3 案）は入れていない（往復の材料に画面の事実を渡す方で代える）。

### 16.6 第 3 波の追補（2026-10-23）: 主張参照の解決・行の無い台帳・母集合・学生に見せる条件

第 3 周（uxsim c-r25-doubt-r3）で、疑義の通しが 3 周続けて成立しない根と、学生に条件が届かない根が分かった（IK-0930〜0936）。

- **主張参照の解決の正本（IK-0930）**: 理論操作グラフ・DSL の主張参照は生成時の局所 ID（`synth_claim_*` / `claim_span_*`）か UUID。
  `backend/core/knowledge_objects/claim_refs.py`（`ClaimRefResolver`）が UUID はそのまま・局所 ID は `theory_claims_live` の
  (document_id, agent_claim_id) と `source_scope.legacy_ids` で UUID へ・どちらにも無ければ `claim_object_builder` の本文で名前だけを返す
  （綴りの正規化は `references.py` の正本）。支える主張・観測の選択肢（A/B 段と C 段を UUID に揃えて重ねる）・支持線の根の名前・
  表示名・依存グラフの種（`claim_refs` に別名を足す）がこれを通る。**D層の読み手で主張参照を UUID 形だけで篩わない。**
- **行の無い台帳（IK-0931）**: 台帳 GET は行が無くても、閲覧ゲートと対象の実在の確認を通れば 200 の `exists: false`
  （名前・論文の題名・疑義の入口・`can_edit`）。404 は実在しない・閲覧できないときだけ。
- **母集合（IK-0933）**: 前提の地図・未検証合意リスト（教員・学習者）・ゼミ前ブリーフ・discuss 開幕の前提の母集合から、
  claim チェーンの導出ステップ（理論モジュール層の `claim_sequence_node_ids` と同じ判定 — `core/doubt/population.py`）を外し、
  外したときは `population_note` の事実文を添える。依存の伝播は全ノードのまま。
- **見出し（IK-0934 / IK-0901）**: 台帳の見出しは「論文の題名 › 対象の名前」。主グラフのノードの名前は段の日本語名 + 根拠の主張。
- **学生に見せる条件の範囲（IK-0932・オーナー判断の候補 → 推奨どおり実装）**: 学習者の地図の札は、状態が直接検証・間接的な支持・
  反証のときだけ「記帳された条件: 成立条件「…」・領域「…」」の 1 行を添える（`core/doubt/schema.py::learner_scope_line`）。
  **根拠の逐語・記帳者（地位勾配 §8-3）・精度・系・数値は出さない**（P7 / SL4）。条件が 3 つ以上なら件数を言わずに「ほかにも条件の
  記帳があります」。未検証・検証情報なしの札には何も添えない（空欄は発見）。AI に渡す画面の事実文
  （`LEARNING_PERSONAL_MAP_SCREEN_FACT`）は地図に合わせて「札の下に条件が 1 行で出る」と言う。D3-6（読み取り専用・一行の事実の併記）
  の範囲内の拡張として扱い、「検証を単一ブールにしない」を学習者側にも効かせる判断。代案（札から学習者向け台帳 GET を開く展開）は
  入れていない。
- 開幕の「確かめていないこと」は前提の document_id で各論文の区画へ（IK-0935）。英語の問いが分類で使い方に回り該当節が無いときは
  RAG へ戻す（IK-0936・学習チャット側）。

pre-mortem: `id-namespace-conflated`（主張参照の 2 つの ID 空間 → 解決器 1 つ）/ `permission-and-affordance-asymmetric`（行の有無と
権限の事実を切り離す）/ `extent-decided-by-proxy-signal`（母集合を理論モジュール層の判定に寄せる）/
`information-dropped-as-unrepresentable`（条件を札まで運ぶ）。残した課題: 地図に主張の点を足す層・札から台帳を開く展開・
`_compute_with_observation_scope` に局所 ID の旧クライアントが観測を渡したときの範囲判定（選択肢は UUID に揃えたので画面からは来ない）。

### 16.7 第 4 波の追補（2026-10-25）: 提案の常設・範囲の明示・閾値の母集団・記帳なしの言い方

- **検証提案は台帳の一部（IK-0968）**: 台帳 GET の `challenges[]` に `status_label` と `proposals[]`（`proposal` /
  `external_check` / `reachability_label` / `status_label` / `proposer_name` / `created_at`。提案者の UUID は出さない・取り下げた
  提案も状態つきで残す）を載せる。画面は疑義の下に提案カードを常設し、昇格・状態遷移の後は**台帳を読み直す**（作成応答の
  `proposal_id` でその場に描く旧実装は撤去）。コース単位の提案一覧（前提の地図の区画）は次段。
- **閲覧だけの台帳（IK-0969）**: `disableLedgerWrites` が覆る条件の AI 候補の予約と提案の状態遷移も無効にする。台帳 GET は
  `falsification_refresh_note`（予約の範囲はコースの教材全体）を返し、ボタンの下に出す。
- **段の閾値の母集団（IK-0970）**: `load_percentiles(..., exclusion=ClaimChainExclusion)` が一覧と同じ除外（導出の手順）を
  閾値の母集団にも掛ける（行を読んでアプリ側で `percentiles_from_scores`。閾値の計算では `excluded` の印を立てない）。
  前提の地図・未検証合意リスト（ゼミ前ブリーフ経由を含む）・台帳詳細の負荷段が通る。教員のトリアージ
  （`core/teacher_triage.py` の `sort=load`）はまだ除外前の母集団（残した課題）。
- **記帳なしは 200（IK-0971）**: 学習者の台帳 GET も、受講ゲートと対象がコースの教材に実在することを通れば行が無くても
  200（`exists: false`・「この内容の検証スコープはまだ記帳されていません。」）。出典タブは同じ文を 1 行に畳み、主語を並べる。
  SA層 `verification` 解決器（`learner_ledger_line`）は従来どおり行なしで何も言わない。
- **開幕の空（IK-0972）**: 「確かめていないこと」の空は区画専用の `OPENING_UNVERIFIED_EMPTY_NOTE`（記帳の不在を言う）。
  ゼミ前ブリーフの汎用 `SECTION_EMPTY_NOTE` は教員向けのまま。
- **マイニングの状態（IK-0853）**: 前提の一覧の `mining_note` が「候補あり / 実行中 / 実行し終えて候補なし / 失敗 / 範囲に
  候補なし」を言い分ける（状態はプロセス内・永続化しない。再起動後は範囲の事実だけ）。画面は一覧の頭に 1 行。

pre-mortem: `threshold-reused-across-regimes`（閾値と表示の母集団を揃える）/ `available-but-unwired`（保存済みの提案を読む経路）/
`permission-and-affordance-asymmetric`（後から足したボタンの無効化漏れ）/ `information-dropped-as-unrepresentable`（記帳なしを
404 で表さない）/ `wording-mismatch`（否定の見出しの下の汎用の空文）。

### 16.8 第 5 波の追補（2026-10-27）: 拒否の順序・論文単位の依存の広がり・終端状態の居場所・欄の出所

第 25 周 面 B 第 5 周（`uxsim/runs/round25/judge_doubt_r5.md`）の IK-0998〜IK-1005 の是正。

- **権限の検査は入力の検査より先（IK-0999）**: 台帳の書き込みルート（スコープ・検証状態・覆る条件・根拠の線・疑義・
  検証提案（作成と状態遷移）・前提の手動登録と確定）は、対象の解決と権限（`_require_editable_ledger_target` /
  `_ledger_target_access` / `_require_viewable_challenge_target` / `_require_viewable_proposal_target`（新設） /
  `_require_editable_assumption` / コース・論文の編集ゲート）を本文の 422 より前に呼ぶ。404（不在と権限なしは同一）・422 の意味は
  不変で、権限の無い教員は入力の欠けた要求でも 404 を受ける（「直せば通る」と読める拒否を返さない）。例外はパスの形
  （`target_type` の語彙外）の 422 で、本文に依らないので先に残す。根拠の線の「形の検証は DB を開く前」（P4-R8 当時の規律）は
  この順序に置き換えた。検証提案の状態遷移（`PATCH /proposals/{id}`）は権限の検査を持っていなかったので、疑義の対象を閲覧できる
  教員に限った（不在と同一の 404）。
- **依存の広がりは論文単位で保存（IK-1001・オーナー判断・推奨どおり）**: `recompute_load_scores` は範囲に何本の論文があっても
  台帳行ごとにその行の論文（行キー — IK-0899）1 本の依存グラフで計算する。グラフのノード ID（`theory_op_*` / `eq_*`）は論文の
  中でしか一意でなく、論文の集合を 1 枚に混ぜると別の論文の同じ ID が繋がって下流到達が膨らむ — 解析完了時（論文 1 本）と
  前提の地図の［再計算］（コースの論文）で同じ行の値が食い違い、段の並びが入れ替わっていた。仮説は読み取り 1 本
  `compare_load_scopes`（同じ行を論文 1 本とコース全体で計算して並べる・書き込みなし）で確かめ、テストで固定した。コースの地図は
  読み時に論文ごとの値を並べ、段の閾値だけをコースの母集団（導出の手順を外した後 — IK-0970）で取る。台帳詳細の段は従来どおり
  対象の論文の母集団（コースの文脈を持たない画面）。論文をまたぐ依存を見せたくなったら別の列か読み時の集計で足す。
- **既定のコースは本人所有（IK-1000）**: 前提の地図の既定は `role=owner` の先頭、無ければ従来どおり一覧の先頭。選択肢に役割
  （`role_label` — コース一覧の正本）を併記。前回の選択を端末に覚える案は入れていない（所有の優先で第 5 周の症状は止まる）。
- **取り下げた疑義の居場所（IK-0998）**: 台帳 GET は従来どおり取り下げ済みの行を返し、画面は疑義区画の末尾に折りたたみ
  「取り下げ済みの疑義（読み取りのみ）」（`data-ui-anchor="doubt-atlas.withdrawn-challenges"`・操作ボタンなし・件数なし）で描く。
  取り下げ API は事実文 `CHALLENGE_WITHDRAWN_NOTE` を返し、画面は台帳を読み直してその文を添える。
- **支える主張の印（IK-1003）**: `supporting_claims[]` の各行に `challenge_state`（`challenged` / `proposed` / `in_progress` — 取り下げは
  数えない・優先は着手 > 提案 > 疑義）と `challenge_state_label`、ノードへは事実文 `supporting_challenge_note` 1 行（件数なし）。
- **欄の出所（IK-1002）**: 欄名の正本は `label_vocab.LEDGER_FIELD_LABELS`（検証状態（教員の記帳）/ 観測からの支持線 /
  依存の広がり（理論操作グラフの下流・段階）/ 行動上の合意（解析時に数えた参照））で、台帳 GET と未検証合意リストが
  `field_labels` + `field_sources_note` として渡す（JS にミラーしない）。状態が未記帳でスコープだけあるときは
  `verification_status_note`（「検証状態はまだ記帳されていません（…スコープを記帳しても検証状態は変わりません）」）。
  行動上の合意を再計算で更新するかは決めていない（欄名で別の量と言い分けるにとどめた）。
- **学習チャット（IK-1004 / IK-1005・面 A と同じ `routes/learning.py`）**: 逆質問の直後に前提の説明へ流すのは、前提名に触れる
  発話か、別の語を主語に立てない否定・説明要求（「いいえ」「それってどういう意味」）だけ（`_names_other_subject`）。
  「『未訪問』ってどういう意味」は通常の経路で答え、逆質問は保留のまま。使い方の回答の直後に同じ発話を送り直したとき
  （`_is_resend_after_usage_help`）と、地図で開いた場所の名前・札・地図に触れる問い（`view.atlas_node_label` がある — SA層の
  事実文が届く。`_is_screen_fact_question`）は、pre-route でも分類の USAGE_HELP でも HELP に回さず RAG へ
  （`_usage_help_goes_to_rag` 1 箇所・`manual_help_kb_design.md` §9 の根 4 と同じ規律。案内の文言は不変で、案内どおりに動く）。

#### 16.8.1 設計時の検査（pre-mortem）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 拒否の理由が最初に失敗した検査で決まる | `gate-position-wrong` | 全書き込みルートで権限を先に。ソースの順序をガードレール（AST）で固定 |
| 同じ列を違う範囲で計算して上書き | `threshold-reused-across-regimes` | 保存は論文単位 1 つに。2 範囲で計算して比べる読み取りで仮説を確かめた |
| 一覧の先頭を既定にする | `condition-not-propagated` | 所有の役割を既定の選び方に渡す |
| 終端状態（取り下げ）を画面が描かない | `information-dropped-as-unrepresentable` | 折りたたみで読み取りのみ描く（消さない） |
| 下位の記録が上位の入口に見えない | `last-mile-missing` | 支える主張の行に印・ノードに事実文 |
| 別の量を同じ語で並べる | `duplicate-canonical-sources` | 欄名の正本を label_vocab に 1 つ |
| 会話の状態で次の発話の意味を決める | `completion-defined-by-proxy` | 話題の一致（前提名・別の主語）を条件に足す |
| 案内した出口が同じ経路に戻る | `available-but-unwired` | 送り直しを判定して RAG へ |

当たらない族: 再実行と削除（行の削除・再生成なし）/ 外部 API（呼ばない）。

#### 16.8.2 残した課題

- IK-1006（無効な出典の目印の除去で名詞の位置が空になる）は面を跨ぐ別枠で触っていない。
- 台帳詳細の段（論文の母集団）と前提の地図の段（コースの母集団）は母集団が違う。両者を同じ画面に並べたときの言い分けは未対応。
- 行動上の合意（解析時の参照数）を依存の広がりと同じ再計算で更新するかは未決（欄名で言い分けた）。
- `core/teacher_triage.py` の `sort=load` は §16.7 の残課題のまま。

### 16.9 第 6 波の追補（2026-10-27）: 出現順の辺を依存に数えない・拒否の文・前提から台帳へ・地図の場所の事実

第 25 周 面 B 第 6 周（`uxsim/runs/round25/judge_doubt_r6.md`）の IK-1030 / IK-1001 の残り / IK-1027 / IK-1028 / IK-1031 /
IK-0853 の残り③の是正。

- **依存の広がりと反実仮想は主張の出現順の辺を数えない（IK-1030・オーナー判断・推奨どおり）**: D層 D2-1 の「下流到達」は
  「偽なら崩れる」関係の到達で、論文の主張を出現順に並べた claim チェーンの辺（読む順）はそれではない。負荷（`recompute_load_scores`
  と読み取り `compare_load_scopes`）と反実仮想（`compute_counterfactual`）は `build_dependency_graph(..., exclude_claim_sequence_edges=True)`
  で作ったグラフを使い、claim チェーン由来ノードに接する辺を依存に数えない。**判定の正本は一覧の母集合（IK-0933）と同じ**
  `core/theory_modules/builder.py::claim_sequence_node_ids`（`derivation_chain` の `chain_type`）で、D層は
  `population.claim_sequence_ids_for_payload`（成果物の読みはセーブポイントの中・判定できなければ外さない）を通すだけ。
  ノードと主張・式の参照は残す（台帳行の種は消えない — 下流が出現順の辺を通らないだけ）。**反実仮想の伝播規則
  （collapsed / indeterminate / surviving の区分）は変えていない**（辺の母集合だけ）。支持線・ゼミ前ブリーフ・反証条件の入力は
  既定（外さない）のままで、同じ判定を使うかは別に決める。仮説は読み取り 1 本 `compare_scores_with_and_without`（純関数・同じ台帳行を
  辺あり / なしで並べる）+ `compare_claim_sequence_edges`（1 論文・書き込みなし）で確かめる形にし、テストで「出現順の先頭の主張だけが
  辺を外すと縮み、式の主張は変わらない」を固定した。**実データでの確かめ（Cepheus TeX 1 論文で上の読み取りを 1 回）と全論文の
  再計算は運転側の作業**（`POST /api/admin/doubt/courses/{id}/load/recompute`。是正前の値が台帳に残っているので、これをしないと
  一覧は序論のまま — IK-1001 の残り）。
- **台帳への書き込みを断る文（IK-1027）**: `_require_editable_ledger_target` は、対象の論文を閲覧できるが編集できない相手には
  **403 + 画面と同じ理由文**（`label_vocab.LEDGER_READ_ONLY_NOTE` — 台帳 GET の `can_edit_note` と同じオブジェクト。正本を
  `core/doubt/scope.py` から `label_vocab` に移し、scope.py は再エクスポート）、閲覧もできない・対象が解決できないときは
  **日本語の同一の 404**（`LEDGER_WRITE_TARGET_NOT_FOUND_NOTE`「この台帳の対象は見つからないか、記帳できません。」— 存在の有無を
  言わない = fail-closed は維持）。閲覧できる相手には台帳 GET が既に存在を見せているので 403 で漏れる情報は無い。判定の順序
  （IK-0999: 権限 → 入力）は不変。
- **確定済みの前提から台帳へ（IK-1028）**: `GET /courses/{id}/assumptions` の各項目に `origin_label` / `status_label`
  （`label_vocab.ASSUMPTION_ORIGIN_LABELS` / `ASSUMPTION_STATUS_LABELS` — migration 030 の CHECK と同じ語彙・語彙外は事実語）を足し、
  確定済み（confirmed / operationalized）には `ledger_document_id`（結ばれた論文のうちコースの範囲のものを優先した先頭）か、
  論文に結ばれていなければ `ledger_note`（`ASSUMPTION_NOT_LINKED_TO_DOCUMENT_NOTE`）を返す。画面はカードに［台帳を開く］
  （`data-ui-anchor="doubt-atlas.assumption-open-ledger"`）を置き、散布図・未検証合意リストと同じ `renderNodeLedgerSection` で
  台帳を開く（別の詳細を作らない）。台帳行は論文単位（IK-0899）なので、行が無い論文でも開いた画面から記帳すれば行ができる
  （記帳のときに作る — 既存の規則）。論文に結ばれていない前提に行キーを作る規則は決めていない（事実文で止める）。
- **確定済みの表示で候補の文を出さない（IK-0853 の残り③）**: `mining_note` は候補・すべての表示だけに返し、確定済み・却下の表示では
  `null`（画面は描かない）。
- **地図の場所の事実（IK-1031・SA層 §11 の範囲内）**: 学習画面は分野の地図で開いた場所の参照 `view.atlas_node_id` を足す
  （表示しない・パネルの文や論文の題名は送らない = SA1）。ルート（`_screen_atlas_node_sources`）が受講ゲート済みのコースから
  分野を導き、凍結骨格の状態キャッシュ（`GET /api/atlas/node` と同じ行）の検証・承認の行と、`learner_landscape_for_documents`
  （遮断はそちらの正本）で引いたその場所に置かれたコースの論文の題名（最大 3・出所ラベル付き）を `sources["atlas_node"]` にし、
  `view` 解決器が事実文にする（「その場所の詳細パネルに出ている行: …」「このコースの論文のうち、…置かれているもの: 『…』（教員確認済み）
  …出典タブの「分野の中の位置づけ」で読めます」/ 引けたうえで無ければ「置かれているものはありません」）。論文の母集合は
  `grounding_document_ids` だけ（DM1）・LLM 回数は不変・`cycle_mode=elicit`（`kinds=("view",)`）では引かない（DB 0 本の規律）。

#### 16.9.1 設計時の検査（pre-mortem）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 表示の並びのための辺を依存の計算に使う | `extent-decided-by-proxy-signal` | 出現順の辺を依存グラフから外す。判定は一覧の母集合と同じ正本 1 つ |
| 判定を 2 か所で持つ | `duplicate-canonical-sources` | `claim_sequence_node_ids` を通すだけ（D層で再判定しない） |
| 是正前の値が保存列に残る | `stale-derivative-served` | 全論文の再計算を運転側の作業として明記（自動の一括再計算は入れていない） |
| 隠すための畳み込みを既に見せた相手にも使う | `wording-mismatch` | 閲覧できる相手は 403 + 画面と同じ文、他は日本語の同一 404 |
| 確定の次の操作への入口が無い | `last-mile-missing` | カードに［台帳を開く］・行の無い場合は事実文 |
| 画面にある事実が AI に届かない | `available-but-unwired` | 参照だけを渡しサーバが解決（SA1 / SA2） |
| 画面の参照で範囲が広がる | `scope-widened-silently` | 論文はコースの範囲（grounding）からだけ |

当たらない族: 外部 API（呼ばない）/ 行の削除（しない）。

#### 16.9.2 残した課題

- 実データでの仮説の確かめ（`compare_claim_sequence_edges` を Cepheus TeX で 1 回）と全論文の load の再計算は運転側（砂場 DB に
  触れない是正者は走らせていない）。
- 支持線・ゼミ前ブリーフ・反証条件の入力が出現順の辺を使い続けるか（同じ判定に揃えるか）は未決。
- 論文に結ばれていない前提に台帳行を作る行キーの規則（前提を論文に結ぶ UI も無い）。
- 地図の場所の事実は検証・承認の行と論文の題名まで。どの論文のどの主張がその場所の根拠かまでは渡していない。

### 16.10 第 7 波の追補（2026-10-28）: 支持線も出現順の辺を数えない・前提に結ばれた論文の題名

第 25 周 面 B 第 7 周（`uxsim/runs/round25/judge_doubt_r7.md`）の IK-1048 / IK-1028 の残りの是正（同じ波の学習者側は
`assistant_screen_adapter_design.md` §11.20）。

- **観測からの支持線も主張の出現順の辺を数えない（IK-1048・第 6 波のオーナー判断の適用範囲を広げるだけ — 新しい判断ではない）**:
  §16.9 で「別に決める」とした支持線・ゼミ前ブリーフ・反証条件の入力を同じ判定に揃えた。`build_support_context` は既定で
  `build_dependency_graph(..., exclude_claim_sequence_edges=True)` を作り、`_qualified_adjacency` は**同じグラフが外した
  claim チェーン由来ノードの集合**（`DependencyGraph.claim_sequence_node_ids`）に接する資格付きエッジを数えない。辺の判定は
  `dependency.is_claim_sequence_edge` の 1 つで、依存グラフと隣接リストが別々に判定しない。ゼミ前ブリーフの読める名前の
  依存グラフと反証条件の候補の入力（下流の名前）も同じ除外を渡す。最大流・最小カット・事実文（SL1）・段階語は変えていない
  （辺の母集合だけ）。**仮説は読み取り 1 本で確かめる形**: `compare_support_levels`（純関数）+
  `compare_support_lines_with_and_without`（1 論文・書き込みなし・段階語だけを返す = SL4）。`exclude_claim_sequence_edges=False`
  を渡してよいのはこの比較だけ。テストは run の形（序論の背景文 2 つが出現順の辺で式どうしの依存の主張に届き「複数」になり、
  外すと観測の手順 1 本の「単一」になって背景文が支持根から消える）を固定した。
- **確定済みの前提のカードに結ばれた論文の題名（IK-1028 の残り）**: 同じ文面の確定済みの前提を見分けられるよう、`ledger_document_id`
  がコースの範囲の論文のときだけ `ledger_document_line`（「結ばれた論文: 『…』（［台帳を開く］はこの論文の台帳の行を開きます）」・
  題名は `_document_title_entry` の正本）を返し、画面はその文を素通しで描く。範囲外の論文の名前は出さない。

#### 16.10.1 設計時の検査（pre-mortem）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 1 つの計算から外した辺を同じグラフを読む別の計算が数え続ける | `rule-enforced-per-surface` | 支持線・ゼミ前ブリーフ・反証条件の入力を列挙して同じ除外を通した。隣接リストはグラフが外した集合を受け取る（再判定しない） |
| 判定を 2 か所で持つ | `duplicate-canonical-sources` | `is_claim_sequence_edge` と `claim_sequence_node_ids` の 1 つ |
| 仮説のまま直す | `extent-decided-by-proxy-signal` | 辺あり / なしの読み取りを先に置き、テストで run の形を確かめた |
| 同じ文面のカードを見分けられない | `wording-mismatch` | 結ばれた論文の題名の事実文（範囲内だけ） |

当たらない族: 外部 API（呼ばない）/ 行の削除（しない）/ 数値の表示（段階語だけ）。

#### 16.10.2 残した課題

- 実データでの読み取り（`compare_support_lines_with_and_without` を Cepheus TeX で 1 回）は運転側（是正者は砂場 DB に触れない）。
- 支持線の段階は保存していない（読み時の計算）ので再計算は要らないが、負荷の再計算（§16.9）はまだ運転側の作業。
- PDF 版の序論が依存「高」に残る理由は未確定（IK-1030 の残り）。

#### 16.11 第 25 周 第 11 波（2026-10-31）: 到達可能性に「本文・文献の読み直し」（IK-1148）

到達可能性（SL3・人間専用語彙）は覆る条件向けの観測の語だけで、定義の疑義から作る「本文の式の前後と付録を
読み直して確かめる」提案に当てはまる語が無かった。値 `by_document`（「本文・文献の読み直しで確かめられる」）を
`REACHABILITY_LEVELS` に additive に足し、`verification_proposals.reachability` の CHECK を migration 097 で同じ
語彙に揃えた（既存行・既存値の意味は不変）。人間専用の規律（worker が書かない・LLM 候補から剥ぐ）は値が増えても
同じ。SL-4 の要約の優先は reachable > by_document > next_generation > unreachable > unassessed で、
「到達可能な反証条件がある項目だけ」の絞り込みは従来どおり reachable のみ。JS の表は逐語ミラー
（`test_doubt_vocab_mirror.py`）。

| 変更の性質（pre-mortem） | 型 | 扱い |
|---|---|---|
| 別の対象向けの語彙の流用 | `wording-mismatch` | 値を足す（欄ごとのラベルの分岐は作らない — 正本を 2 つにしない） |
| 語彙表の新設・CHECK と語彙の二重 | `duplicate-canonical-sources` | 正本は `core/doubt/schema.py`、CHECK と JS は一致検査で追随 |

---

*出典: vision_expansion_proposals_2026-08.md 提案3（結合案「賭け金の台帳」「観測の一点吊り
検査」「独立支持経路の台帳」と、討論で確定した閉世界誤謬対策・Duhem 区別・到達可能性区分）。
D層内部の実測事実（§2）は 2026-08-13 の偵察による。*
