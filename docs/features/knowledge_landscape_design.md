# 知識ランドスケープ（Knowledge Landscape）設計書

- 文書バージョン: 1.0
- 作成日: 2026-08-04
- ステータス: **実装済み（正本・凍結）** — v1 全実装（2026-08-05、migration **065**
  `065_landscape_placements.sql`、コミット `407c5b0`）。本文書が知識ランドスケープ機能の正本で、
  以後は §15 実装記録の追記のみ（Phase 2〜4 は §12 のとおり非スコープ）
- 入力仕様: `/Users/Shared/issues/episteme_graph_knowledge_landscape_astrophysics_spec.md`
  （知識ランドスケープ／宇宙物理基準地図 仕様書 v0.1。以下「入力仕様書」）
- 関連文書: `field_atlas_overlay_spec.md`（分野の地図。※原本消失につき現存するのは
  2026-08-14 の再構成版 — 旧§番号との対応は保証されない）/ `atlas_binding_lifecycle_design.md` /
  `personal_knowledge_network_design.md` / `discussion_mode_design.md` /
  `element_deliberation_workspace_design.md`

---

## 0. 不変条項（LS1〜LS10）

既存レイヤーの不変条項（A層非改変・candidate-only・evidence-based・P4・fail-closed 等）を
継承したうえで、本機能固有の条項を定める。ガードレールテスト
`backend/tests/test_landscape_guardrails.py` が構造的に守る。

| # | 条項 | 意味 |
|---|---|---|
| LS1 | **地図は正解ではなく投影** | 基準地図を「唯一の正しい分類」として提示しない。1論文は複数領域へ異なる観点（perspective）で配置されるのが既定。UI 文言でも断定しない |
| LS2 | **A層非改変** | 配置層は A層成果物（artifacts / theory_claims）を読むだけ。成果テーブルに列を足さず、専用テーブル `landscape_placements` に積む |
| LS3 | **AI配置は inferred 止まり、確定は教員** | LLM/アルゴリズム由来の配置は `status='inferred'`。`confirmed` にできるのは教員の明示操作のみ。`rejected` も行削除せず保持（P4）。再解析は inferred のみ `superseded` にし、教員の confirmed/rejected は不変 |
| LS4 | **evidence-based・根拠へ遡れる** | すべての配置に `reason`（なぜ関係するか）+ `evidence`（原文逐語 quote、可能なら claim 参照）を必須付与。quote は入力素材への verbatim 包含を validator がハード検査（捏造ガード） |
| LS5 | **数値を見せない** | weight・confidence の生数値を API/UI に出さない（教員含む。D層 load_score と同じ原則）。表示は段階ラベル（強い関連/関連/弱い関連）と出所ラベルのみ。DB には生値を保持 |
| LS6 | **fail-closed** | document 可視性ゲート（`resolve_document_access`）、受講ゲート（`get_accessible_course_data`）、読む骨格は凍結版のみ。データ無し・API失敗は非表示へ縮退（フィクスチャ退避しない） |
| LS7 | **地図の安定性** | アンカー座標は骨格凍結版が正本。配置の追加・削除で骨格座標を動かさない。骨格の変更は既存の draft→freeze フロー（レビュー・版管理・freeze-impact）のみ |
| LS8 | **コーパスの正直さ** | 表示には使用骨格の版・対象コーパス（何件の論文から生成されたか）・生成方式（AI推定/教員確認）を明示する。論文数の多さを重要性と誤解させる表現をしない |
| LS9 | **同期パスに LLM を入れない** | 配置生成はパイプラインステージ（非同期）または教員の明示操作（再提案）のみ。読み取り API はすべて非LLM |
| LS10 | **配置不能は失敗ではなく信号** | どの領域にも配置できない論文は `unplaced` として正直に記録し、教員レビューに提示する（入力仕様書 §3.5 / FR-015。地図構造更新の入力として扱う） |

---

## 1. ビジョンと現状のギャップ

### 1.1 入力仕様書の核

入力仕様書が求めるのは「学問を固定分類する機能」ではなく:

1. 粗く安定した**基準地図**（初期の方向感覚。宇宙物理 v0.1 = 10アンカー×三軸）
2. 論文から抽出された知識要素が積み重なって現れる**知識地形**
3. 同じ知識グラフからの**複数ビュー**（分野/問い/方法/議論/系譜/応用/個人探索）
4. すべての配置・関係から**根拠へ遡れる**構造
5. 配置困難・未回答・違和感を**地図構造の更新信号**として扱う仕組み

### 1.2 現状資産の棚卸し（仕様概念 → 既存実装の対応表）

episteme-graph には入力仕様書の構成要素の**大半がすでに存在する**。欠けているのは
「document ⇄ 基準地図」の配置層と、宇宙物理の基準骨格そのものである。

| 入力仕様書の概念 | 既存実装 | 状態 |
|---|---|---|
| KnowledgeComponent（§10.2, claim/method/assumption…） | A層 `theory_components` / `theory_claims` / equations / figures / SymbolRegistry | **あり**（新モデル不要） |
| KnowledgeRelation（§10.3） | TheoryOperationGraph（edge語彙+source_backing）/ claim リンク / W層 identity_links / DSL CorePredicate | **あり** |
| 基準地図（§6, ReferenceDomain） | `atlas_skeletons`（draft/freeze/版管理/楽観ロック/lifecycle）+ 凍結版レビューUI | **あり**（宇宙物理ドメインが無いだけ） |
| 根拠・信頼状態（§12） | `source_backed / partially / inferred / review_required` 語彙（A層〜W層で共通） | **あり** |
| 地図の安定性（§6.7） | 骨格凍結版 + アンカー座標固定 + freeze-impact | **あり** |
| 議論ビュー（§8.4） | D層（epistemic_ledger / challenges）+ discuss opening（fragile points） | **あり**（地図への投影が未接続） |
| 個人探索マップ（§8.6） | discuss 開幕（thesis/支持構造/脆い一手）+ W層 context lens + わたしの地図 | **一部あり** |
| 未回答・違和感記録（§FR-015） | tension / structure_anchor / atlas_report | **あり**（配置不能の記録経路が無い） |
| **DomainPlacement（§10.4）** | — | **無い（本設計の中心）** |
| **宇宙物理基準地図 v0.1（§6）** | particle_physics 骨格のみ | **無い** |
| EmergentRegion / コーパス地図（§10.5, §13） | — | 無い（Phase 3） |
| 問い/方法/系譜ビュー（§8.2/8.3/8.5） | — | 無い（Phase 2〜3） |

### 1.3 本当に欠けているもの = v1 で作るもの

1. **配置層（Placement Layer）**: `landscape_placements` テーブル + LLM 配置候補生成
   （パイプラインステージ）+ 教員レビュー + 学習者表示。入力仕様書 §10.4 DomainPlacement の実装
2. **宇宙物理基準地図 v0.1**: 10アンカー骨格の決定論シード（既存 atlas 基盤に載せる）
3. **2つの UX 面**: 教員=配置レビュー（教材管理）、学習者=論文の位置づけ（地図オーバーレイ+出典タブ）
4. **コーパスの正直さ表示**（LS8）

複数ビュー・知識地形・スキーマ進化は既存資産からの投影として Phase 2〜4 に定義する（§12）。

---

## 2. 四層モデルの写像

```text
入力仕様書                     episteme-graph 実装
─────────────────────────────────────────────────────────
第1層 安定した基準地図        atlas_skeletons（凍結版）+ 宇宙物理骨格シード【v1】
第2層 論文から現れる知識地形  landscape_placements の蓄積【v1】→ EmergentRegion【Phase 3】
第3層 目的別ビュー            perspective 別フィルタ【v1】→ 問い/方法/議論ビュー【Phase 2〜3】
第4層 学習者の探索地図        論文の位置づけ + discuss開幕 + わたしの地図【v1 は接続のみ】
```

機能名は「知識ランドスケープ」、コード接頭辞は `landscape_` / `Landscape`（既存の
atlas-（分野の地図）・doubt-（前提の地図）と衝突させない）。学習者向け UI 文言は
「論文の位置づけ」を使う。

---

## 3. v1 スコープ

### やること（Phase L0 + L1、本セッションで実装）

- **L0**: 宇宙物理基準骨格（`backend/atlas_domains/astrophysics/`）+ バンドルドメインの
  シード経路 + 骨格上限の引き上げ（MAX_REGIONS 7→12、フロント LIMITS 同期）
- **L1**: migration 065 `landscape_placements` / `backend/core/landscape/` /
  パイプラインステージ `landscape_placement` / admin API + 学習者 API /
  教員レビューモーダル（教材管理）/ 学習者「論文の位置づけ」（オーバーレイのレイヤー +
  出典タブのセクション）/ 監査 / ガードレール / マニュアル3点セット

### やらないこと（v1 非スコープ、§12 のロードマップに定義）

- 橋渡し概念（Bridge Concept）の一級ノード化（骨格スキーマ進化が必要）
- region への domain_type（target/foundation/method）の形式付与（レイアウトで表現）
- EmergentRegion（自動クラスタ）/ コーパス別地図 / MapSnapshot 比較 / 年代スライダー
- 問いビュー・方法ビュー・系譜ビューの専用画面
- 新 Predicate 候補のスキーマ進化ワークフロー
- G層ルール（`material.landscape_unreviewed`）— Phase 2 で追加
- W層 positioning「分野の地図」レンズへの配置行の合流 — Phase 2
  （`backend/core/deliberation/positioning.py` は前セッションの未コミット変更があるため v1 では触らない）
- 学習者起点の配置異議申し立て（atlas_report と同型の導線）— Phase 2

---

## 4. UX 設計

### 4.1 教員 UX

**フロー（すべて既存導線への追加。新タブは作らない）:**

1. 教材（論文PDF）をアップロード → 解析パイプラインが走り、`landscape_placement`
   ステージが凍結骨格のあるアクティブ全ドメインに対して配置候補（inferred）を生成する
2. 教材管理タブ → 行の「⋯」メニュー → **「位置づけ（分野マップ）…」** → モーダル:
   - ドメイン別にグループ化された配置一覧。各行 = アンカーラベル / 観点ラベル
     （対象から・問いから・方法から…）/ 関連の段階ラベル / 出所（AI推定・教員確認済み）/
     reason / evidence quote（原文逐語）
   - 各行に **[確認] [却下] [再検討]**（status 遷移。監査記帳）
   - **[AIで再提案]**（手動再実行。日次上限あり・429 は事実文）
   - 配置不能（unplaced）だったドメインは「この論文は◯◯の地図に配置できませんでした:
     <AIの理由>」を事実文で提示（LS10）
   - 事実行: 使用骨格の版 / 生成日 / 使用モデル（run 記録から）
3. コースの分野バインドは**既存の学習マップ編集（atlas-binding）をそのまま使う**
   （宇宙物理骨格が凍結済みになるため、既存 propose が astrophysics を候補に含める）
4. 骨格そのものの編集・凍結・retire も**既存の分野の地図タブ**のまま（本機能は骨格を触らない）

**教員に見えるもの/見えないもの:** 配置の生 weight・confidence は見えない（LS5）。
自分が閲覧できる document の配置だけ見える（LS6）。他教員の確認状況は status ラベルとして見える。

### 4.2 受講者 UX

**フロー:**

1. コースを受講 → 地図ボタン（既存 `#atlas-btn`）でオーバーレイを開く
2. オーバーレイに新トグル **「論文の位置」**（コースのソース論文に配置がある時だけ表示）:
   ON にすると L1 地図上の配置先アンカー付近に 📄 マーカーが載る
3. マーカーをクリック → その場のポップオーバーに「この論文がなぜこの領域と関係するか」:
   論文タイトル / 観点ラベル / 関連の段階ラベル / **出所ラベル
   （「AIによる推定（未確認）」「教員確認済み」）** / reason / 原文 quote（LS4/LS5/LS8）
4. 出典タブに **「分野の中の位置づけ」** セクション: コースのソース論文ごとの配置の
   テキスト一覧（コースがバインドされたドメインを先頭に、他ドメインも表示）
5. 地図の下部に事実行:「この表示は登録済み論文 N 件の解析から生成されています
   （骨格版 X・AI推定を含む）」（LS8）

**受講者に見えるもの/見えないもの:** `confirmed`（教員確認済み）と `inferred` /
`review_required`（AIによる推定・未確認、と明示ラベル）は見える。`rejected` / `superseded`
は見えない。数値は一切見えない。評価に使われない（配置は論文のメタデータであり学習者の
行動データではないため、k-匿名の対象外）。

### 4.3 教員と受講者の整合性（矛盾のなさ）

両者は**同一の `landscape_placements` テーブルの同一行**を読む。役割差は status 操作権限と
表示フィルタのみ:

| 事象 | 教員側 | 受講者側 |
|---|---|---|
| AIが配置を生成 | inferred としてレビュー一覧に出る | 「AIによる推定（未確認）」ラベルで見える |
| 教員が [確認] | confirmed に遷移（監査） | ラベルが「教員確認済み」に変わる |
| 教員が [却下] | rejected（行は保持） | **消える** |
| 教員が [再検討] | review_required | 「確認待ち（AI推定）」ラベル |
| 再解析 | inferred のみ superseded→新候補。confirmed/rejected 不変 | 教員確認済みは揺れない |
| 骨格の凍結更新 | 既存 freeze-impact で影響提示 | 新版骨格上に既存配置（node_id 一致分）が出続ける |
| 論文がどこにも置けない | unplaced として提示（構造更新の信号） | 何も出ない（誤誘導しない） |

AI推定を受講者に見せる根拠: 入力仕様書 AC-005 は「AI推定と原文根拠の**区別**」を要求して
おり抑制を要求していない。分野の地図の既存原則「出所の正直さ」、D層の「未検証と検証済みを
同じ精度で併記」と同型。ただし出所ラベルは必ず表示し、教員が却下したものは即座に消える。

---

## 5. データモデル（migration 065）

`backend/db/065_landscape_placements.sql`（冪等。lint 規約準拠）:

```sql
CREATE TABLE IF NOT EXISTS landscape_placements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    domain_key TEXT NOT NULL,
    skeleton_version TEXT NOT NULL DEFAULT '',
    node_id TEXT NOT NULL,
    node_kind TEXT NOT NULL DEFAULT 'region'
        CONSTRAINT landscape_placements_node_kind_check CHECK (node_kind IN ('region','concept')),
    perspective TEXT NOT NULL
        CONSTRAINT landscape_placements_perspective_check CHECK (perspective IN
            ('subject','question','method','theory','observation','application')),
    weight REAL NOT NULL DEFAULT 0.5,
    reason TEXT NOT NULL DEFAULT '',
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'inferred'
        CONSTRAINT landscape_placements_status_check CHECK (status IN
            ('inferred','confirmed','rejected','review_required','superseded')),
    provenance TEXT NOT NULL DEFAULT 'llm'
        CONSTRAINT landscape_placements_provenance_check CHECK (provenance IN
            ('llm','deterministic','human')),
    run_id UUID,
    created_by TEXT NOT NULL DEFAULT 'pipeline',
    reviewed_by UUID,
    reviewed_at TIMESTAMPTZ,
    review_note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 生きている行（superseded 以外）は (document, domain, node, perspective) で一意
CREATE UNIQUE INDEX IF NOT EXISTS uq_landscape_placements_live
    ON landscape_placements (document_id, domain_key, node_id, perspective)
    WHERE status <> 'superseded';
CREATE INDEX IF NOT EXISTS idx_landscape_placements_document
    ON landscape_placements (document_id, status);
CREATE INDEX IF NOT EXISTS idx_landscape_placements_domain
    ON landscape_placements (domain_key, status);
```

設計判断:

- **FK は `documents(id)` に CASCADE**。既存の document 削除2経路
  （`core/versioning/deletion.py::_purge_document` / `routes/admin.py::delete_material`）は
  どちらも最終的に `documents` 行を削除するため、明示 DELETE の追加なしで孤児が出ない
  （document_figures の孤児ギャップを踏襲しない）。document_id は常に UUID
  （パイプライン ctx.document_id）を格納し source_path は使わない。
- **weight / confidence は DB のみ**（LS5）。evidence 要素の形:
  `{"quote": str, "claim_id": str|null}`（v1。Phase 2 で chunk_id 遡及を追加）。
- **再解析セマンティクス**（LS3）: ①当該 document の `status='inferred'` 行を全て
  `superseded` に遷移 ②新候補のうち、生きている行（confirmed/rejected/review_required）と
  同一キーのものは挿入をスキップ ③残りを `inferred` で挿入。教員の判断は AI が上書きできない。

---

## 6. 宇宙物理基準地図 v0.1（骨格シード）

### 6.1 シード機構: バンドルドメインディレクトリ（atlas_store の追加拡張）

カートリッジ一式（ontology.json 等）を持たない**骨格専用ドメイン**のファイルシード経路を
新設する。`backend/atlas_domains/<domain_key>/skeleton.yaml`（+ 任意 `domain.json` =
`{"name": "...", "description": "..."}`）。

`backend/core/atlas_store.py` への追加（既存挙動は不変・すべて追加分岐）:

1. `ATLAS_BUNDLED_DOMAINS_DIR`（`backend/atlas_domains/`）定数
2. `_bundled_skeleton(domain_key)`: 既存のカートリッジ経路で見つからない場合、
   `atlas_domains/<key>/skeleton.yaml` を読む（YAML → `atlas.parse_skeleton`。
   FileNotFoundError は None のまま）
3. `import_bundled_skeletons(session)`: カートリッジ走査に加えて `atlas_domains/` の
   サブディレクトリを走査。skeleton の冪等シードは既存 `idempotent_seed_import` と同じ
   `(domain_key, frozen, version)` 存在チェック。`domain.json` があり
   `atlas_domain_meta` に行が無ければ name/description を upsert（既存行は上書きしない）
4. `list_domains(session)`: bundled 枝に `atlas_domains/` 由来のドメインを含める

これにより既存の分野の地図の全機能（学習マップ編集の propose/バインド・オーバーレイ・
ミニマップ・retire/restore・freeze-impact）が宇宙物理ドメインに**無改修で**効く。

### 6.2 上限の引き上げ

- `backend/core/atlas.py`: `MAX_REGIONS = 7 → 12`（`MAX_CONCEPTS_PER_REGION = 6` は据え置き。
  骨格側を6個以内で設計する）
- `frontend/public/js/atlas-overlay.js`: `LIMITS.l1Regions: 7 → 12`
- 既存骨格（particle_physics は4領域）への影響なし。上限超過骨格が valid になるだけで
  縮小方向の互換性は保たれる

### 6.3 骨格内容（`backend/atlas_domains/astrophysics/skeleton.yaml`）

入力仕様書 §6.3 の10アンカーを region、§6.6 の展開候補を concept（各領域≤6個に選定）、
§6.4 の関係を edges（`---` は `adjacent`、横断基盤 FUN/OBS/FPH への接続は `related`）に写像。

```yaml
atlas_skeleton:
  version: '2026.1'
  cartridge: astrophysics
  status: frozen
  generated_by: reference_map:astrophysics_v0.1_spec_2026-08-04
  reviewed_by: [faculty:reference_import]
  changelog:
    - version: '2026.1'
      note: '宇宙物理基準地図 v0.1（入力仕様書 §6 の10アンカーを初期座標として取込）'
      credits: []
```

| region id | ラベル | 対応 | layout (x, y, w, h) | concepts（≤6） |
|---|---|---|---|---|
| cosmology | 宇宙論・大規模構造 | COS | 0.03, 0.05, 0.30, 0.26 | 初期宇宙・インフレーション / 宇宙背景放射 / 宇宙膨張 / 暗黒物質 / 暗黒エネルギー / 大規模構造形成 |
| galaxies | 銀河・銀河団 | GAL | 0.36, 0.05, 0.28, 0.26 | 銀河形成・進化 / 活動銀河核 / 超大質量ブラックホール / 銀河団 / 重力レンズ |
| ism_star_formation | 星間物質・星形成 | ISM | 0.67, 0.05, 0.30, 0.26 | 分子雲 / ダスト / 星形成 / フィードバック |
| high_energy | 高エネルギー天体・コンパクト天体 | HEX | 0.36, 0.36, 0.28, 0.26 | ブラックホール / 中性子星 / 超新星 / ガンマ線バースト / 宇宙線 / 降着・ジェット |
| stars | 恒星・恒星進化 | STR | 0.67, 0.36, 0.30, 0.26 | 恒星内部構造 / 恒星大気 / 連星 / 変光星 / 元素合成 / 晩期進化 |
| fundamental_physics | 基礎物理・重力・素粒子宇宙 | FPH | 0.03, 0.36, 0.30, 0.26 | 一般相対論 / 重力波 / 素粒子・場 / 暗黒成分の物理 / 初期宇宙の物理 |
| sun_heliosphere | 太陽・太陽圏 | SOL | 0.36, 0.67, 0.28, 0.26 | 太陽磁場・太陽大気 / 太陽風 / 宇宙天気 |
| planetary | 惑星系・系外惑星 | PLA | 0.67, 0.67, 0.30, 0.26 | 惑星形成 / 系外惑星 / 原始惑星系円盤 / 惑星大気 / 生命可能性 |
| fundamental_astronomy | 基礎天文学・天体力学 | FUN | 0.03, 0.67, 0.30, 0.12 | 位置天文学 / 軌道力学 / 距離尺度 |
| observation_methods | 観測・装置・データ解析 | OBS | 0.03, 0.82, 0.30, 0.13 | 多波長観測 / 時間領域天文学 / 分光・測光 / 統計推論 / シミュレーション / 装置・検出器 |

- concept の layout は**領域内相対座標**（既存規約）。実装時に各領域内で重ならないよう配る
- seed_status は確立概念を `{value: verified, reviewed: true}`、暗黒物質・暗黒エネルギー・
  暗黒成分の物理・生命可能性を `{value: assumed, reviewed: true}` とする
- edges: `cosmology—galaxies` / `galaxies—ism_star_formation` / `ism_star_formation—stars` /
  `stars—high_energy` / `stars—planetary` / `stars—sun_heliosphere`（以上 adjacent）、
  `cosmology—fundamental_physics` / `high_energy—fundamental_physics` /
  `galaxies—fundamental_physics` / `planetary—fundamental_astronomy` /
  `sun_heliosphere—fundamental_astronomy` / `galaxies—fundamental_astronomy` /
  `observation_methods—{cosmology, galaxies, ism_star_formation, stars, high_energy, planetary, sun_heliosphere}`（以上 related）
- 橋渡し概念（§6.5）と region の三軸型付け（§6.2）は v1 では骨格に**入れない**
  （レイアウトの帯配置で横断基盤を表現。形式化は Phase 2）

---

## 7. 配置エンジン（パイプラインステージ `landscape_placement`）

### 7.1 ステージ登録（orchestrator）

- `PIPELINE_STAGES`: `discuss_opening` の直後・`course_mapping` の直前に
  `"landscape_placement"` を挿入
- `LLM_STAGE_NAMES` に追加。`_PIPELINE_STEPS` に同一相対位置で
  `PipelineStageDef("landscape_placement", _stage_landscape_placement)` を登録
  （`test_document_pipeline.py` の完全一致テストが順序を固定）
- `_stage_landscape_placement(ctx)` は `_stage_discuss_opening` と同型の**非致命**ステージ:
  resume artifact 対応 / try-except で失敗を `{"status":"completed","error":...}` に縮退 /
  `ctx.save_artifact` / `finish_target_stage`
- 実体は `_build_landscape_placement(ctx)` →
  `core.landscape.builder.build_and_store_placements(...)` の薄い委譲
  （admin の手動再提案と同一コードパス）
- モデル解決: agent がコンストラクタでモデルを受けるため
  `resolve_scene_model(f"{SCENE_PIPELINE}:landscape_placement").model` を明示解決
  （`_discuss_opening_model` と同型）

### 7.2 skip 語彙（`_stage_artifact_indicates_llm_skip` 準拠）

| 条件 | payload |
|---|---|
| 凍結骨格のあるアクティブドメインが 0 件 | `{"skipped_reason": "no_frozen_skeleton", "llm_calls": 0}` |
| thesis/claims 素材が無い | `{"skipped_reason": "no_source_material", "llm_calls": 0}` |
| 日次上限到達 | `{"skipped_by_limit": true, "skipped_reason": "daily_call_limit_reached"}` |
| LLM 失敗（repair 2回後） | agent の `make_skipped(llm_call_failed)` を正直に記録・行は書かない |

### 7.3 agent 契約（`src/episteme_graph/agents/landscape_placement/`）

discuss_opening を雛形とした標準構成（`__init__.py / agent.py / cartridge_loader.py /
input_builder.py / prompt.py / llm_client.py / schema.py / validator.py / repair.py /
examples/`）。LLM 1コール/document（全ドメインを1コールで相対評価）。

**入力**（`LandscapePlacementInput`）: `document_id / cartridge_id / paper_title /
central_question / central_thesis / paper_goal / headline_claim /
claim_summaries: [{claim_id, text}]`（source_backed かつ atomic な claim 上位20件）/
`domains: [{domain_key, domain_name, nodes: [{node_id, label, kind, region_id}]}]` /
`max_placements`。`has_material()` = thesis/goal/claims のいずれかが非空。
`source_texts()` = verbatim 検査のヘイスタック（thesis・goal・question・headline・claim 本文）。

**出力**（`LandscapePlacementResult`）: `placements: [{domain_key, node_id, perspective,
weight, reason, evidence_quote, claim_id?, confidence}] / unplaced_domains:
[{domain_key, reason}] / llm_call_count / skipped_reason / validation_issues / review_notes`。

**validator（hard error → repair 最大2回）**: 出力形 / `node_id` が提示ドメインの実在ノード /
perspective 語彙 / weight ∈ [0,1] / `reason` 非空 / `evidence_quote` の verbatim 包含
（`normalize_for_quote_match` = 空白正規化のみ）/ `claim_id` が提示 claim に実在 /
1件も使える配置が無い（unplaced 申告も無い）場合。**warning（ブロックしない）**:
`max_placements` 超過（決定論 truncate + `truncated` 記録）/ 同一キー重複（先勝ち dedupe）。

**プロンプト方針**: 複数領域×複数観点への配置が既定であること・根拠の逐語引用・
**無理に配置しない**（適合しないドメインは unplaced_domains に理由付きで申告）を明示。
reason は日本語の事実文。

### 7.4 コスト・モデル（M層/U層）

- env: `LANDSCAPE_MAX_CALLS_PER_DAY`（既定 20）/ `LANDSCAPE_MAX_PLACEMENTS_PER_DOCUMENT`
  （既定 8）/ `LANDSCAPE_PLACEMENT_LLM_MODEL`（fast tier 既定）。`.env.example` に記載
- CostGate は `core/landscape/builder.py` にモジュールレベルで1個
  （パイプラインと手動再提案が同じ日次予算を共有）。`daily_remaining` 事前チェック →
  実行後 `count_extra_daily(amount=llm_call_count)` の事後計上パターン
- `llm_policy.PIPELINE_STAGE_LABELS["landscape_placement"] = "分野マップ配置候補の生成"`
- `llm_policy._FEATURE_DIRECT_ENV["pipeline:landscape_placement"] =
  ("LANDSCAPE_PLACEMENT_LLM_MODEL", "fast")`
- `llm_usage/schema.py KNOWN_FEATURES` に `"pipeline:landscape_placement"`
  （scene 解決は `pipeline:*` の既存規則で自動。非 vision）

---

## 8. `backend/core/landscape/` モジュール契約（FastAPI 非 import）

| ファイル | 役割・公開関数 |
|---|---|
| `schema.py` | 語彙の正本: `PERSPECTIVES` / `PERSPECTIVE_LABELS`（subject=対象から, question=問いから, method=方法から, theory=理論から, observation=観測から, application=応用から）/ `STATUSES` / `LIVE_STATUSES`（superseded 以外）/ `LEARNER_VISIBLE_STATUSES`（confirmed, inferred, review_required）/ `PROVENANCES` / `NODE_KINDS` / `weight_label(w)`（>=0.7 strong / >=0.4 medium / else weak）+ `WEIGHT_LABELS`（強い関連/関連/弱い関連）/ `provenance_label(status)`（confirmed=教員確認済み, inferred=AIによる推定（未確認）, review_required=確認待ち（AI推定））/ `STATUS_LABELS` |
| `store.py` | `supersede_and_insert_candidates(session, document_id, run_id, candidates) -> dict`（§5 の再解析セマンティクス。戻り値 `{created, superseded, skipped_existing}`）/ `list_for_document(session, document_id, include_history=False)` / `list_for_documents(session, document_ids, *, statuses)` / `get_placement(session, placement_id)` / `update_status(session, placement_id, new_status, *, reviewer_id, note="")`（許可遷移: live↔live のみ、superseded への手動遷移と superseded からの復帰は不可）。**DELETE 文を書かない** |
| `builder.py` | `build_and_store_placements(document_id, *, artifacts=None, run_id=None, created_by="pipeline") -> dict`。骨格列挙（`atlas_store.list_domains` + `load_learner_skeleton` + retired 除外）→ agent 入力構築（artifacts 未指定時は `core.deliberation.refs.document_run_artifacts`）→ CostGate → agent 実行 → `store.supersede_and_insert_candidates`。戻り値 = ステージ payload（§7.2 の skip 語彙を含む） |
| `projection.py` | `skeleton_node_index(domains_with_skeletons) -> {(domain_key, node_id): {label, kind, region_id}}` / `admin_placement_dto(row, node_index)` / `learner_landscape_dto(placements, node_index, domains, course_domain_key)`。**weight / confidence の生値をDTOに含めない**（キー自体を出さない） |

---

## 9. API 契約

### 9.1 admin（`backend/api/routes/landscape.py`、`_require_teacher`、main.py から直接登録）

| Method + path | ゲート | 動作 |
|---|---|---|
| `GET /api/admin/landscape/documents/{document_ref}/placements` | `resolve_document_access` の view | 配置一覧（live + `?include_history=1` で superseded も）。`document_ref` は UUID / source_path 両対応 |
| `PATCH /api/admin/landscape/placements/{placement_id}` | 当該 document の edit | body `{status: confirmed\|rejected\|review_required\|inferred, review_note?}`。監査 `AUDIT_ENTITY_LANDSCAPE_PLACEMENT`（entity_id=placement_id, old/new=status）。422 不正遷移 |
| `POST /api/admin/landscape/documents/{document_ref}/placements/propose` | edit | 手動再提案（§7 と同一ビルダー・同一日次予算）。200 = ステージ payload。429 上限 / 422 素材なし・骨格なし |
| `GET /api/admin/landscape/overview?domain_key=` | teacher | 本人可視 document（`services.list_visible_document_ids`）の live 配置をノード別に集約。корпус事実（可視件数・配置済み件数・骨格版）を含む |

**admin DTO**（`PlacementAdminDTO`）: `{id, domain_key, domain_name, node_id, node_label,
node_kind, region_id, perspective, perspective_label, weight_label, status, status_label,
provenance, reason, evidence: [{quote, claim_id}], review_note, created_at, reviewed_at}`。
生 weight / confidence なし（LS5）。

### 9.2 学習者（learning_router、`/api/learning`）

```
GET /api/learning/courses/{course_id}/landscape
```
- ゲート: `get_accessible_course_data`（受講/所有）。対象 document =
  `services.list_course_source_document_ids(course_data)`（コースのソースのみ。
  本人可視性はコース経由開示の既存原則に従う）
- レスポンス:
```json
{
  "course_id": "...",
  "course_domain_key": "astrophysics" | null,
  "domains": [ {"domain_key": "...", "domain_name": "...", "frozen_version": "...", "is_course_map": true} ],
  "documents": [ { "document_id": "...", "title": "...",
      "placements": [ { "domain_key": "...", "node_id": "...", "node_label": "...",
          "region_id": "...", "node_kind": "region",
          "perspective_label": "対象から", "weight_label": "強い関連",
          "status": "inferred", "provenance_label": "AIによる推定（未確認）",
          "reason": "...", "evidence": [ {"quote": "..."} ] } ] } ],
  "corpus": { "source_document_count": 3, "placed_document_count": 2 }
}
```
- 表示 status は `LEARNER_VISIBLE_STATUSES` のみ。evidence から claim_id を**落とす**
  （学習者DTOは quote のみ。Phase 2 で chunk 遡及を追加）。数値キーなし
- `course_domain_key` は `core.course_data.course_cartridge_id(course_data)`
  （素の dict アクセス禁止）
- 配置ゼロ・骨格なしでも 200 で空構造を返す（フロントは非表示に縮退。fail-closed）

### 9.3 監査・スキーマ定数

- `backend/core/schema.py`: `AUDIT_ENTITY_LANDSCAPE_PLACEMENT = "landscape_placement"` +
  `AUDIT_ENTITY_TYPES` タプルへ追加
- `backend/tests/test_audit_entity_catalog_guardrails.py` の `_AUDIT_CALLER_FILES` に
  `api/routes/landscape.py` を追加（リテラル禁止検査の対象化）

---

## 10. フロントエンド契約

### 10.1 学習者: `landscape-layer.js`（新規、index.html の personal-map.js の後に読込）

`personal-map.js` の3フックパターンを踏襲した `window.LandscapeLayer`:

- `mountControls(sheet)`: オーバーレイに「論文の位置」トグルを追加（配置データが
  ある時だけ表示。二重マウントガード）
- `onLevelRendered(level, canvas)`: L1 のみ。既存 `.landscape-layer` を除去 → トグル ON なら
  `AtlasOverlay.data.levels["1"]` の region/node 座標に 📄 マーカー `<g class="landscape-layer">`
  を **1レイヤー追加**（既存ノード・エッジ・ミニマップに触らない）。同一ノード複数論文は
  オフセットで並べ、4件超は「+N」に集約
- `onOverlayClosed()`: 状態リセット
- マーカークリック → 自前ポップオーバー（`src-popup` と同型の外側クリック/Esc クローズ）:
  論文タイトル / perspective_label / weight_label / provenance_label / reason / quote
- データ: `GET /api/learning/courses/{courseId}/landscape`（`window.AtlasContext.courseId`、
  コース単位キャッシュ、失敗は null=非表示。ポーリング禁止）
- atlas-overlay.js への変更は PersonalMap と並ぶ**3行のフック呼び出し追加のみ** +
  `LIMITS.l1Regions` の引き上げ

### 10.2 学習者: 出典タブ「分野の中の位置づけ」セクション（app.js）

`renderSourcesTab()` の「登録済み教材」の直後に追加。コースのソース論文ごとに
`node_label（perspective_label・weight_label）` のチップ + 出所ラベル + `<details>` で
reason / quote。コーパス事実行（LS8）。データは LandscapeLayer と同一 fetch を共有
（`window.LandscapeLayer.getData(courseId)` を公開）。データ無しはセクションごと非表示。
セクション見出しに `data-ui-anchor="sources.paper-placement"` を付与し、
`backend/core/help_kb/ui_anchors.py`（学習者側 KNOWN/UI_ANCHORS）+
`docs/manual/student/02-student.md` に節を追加（学習者側はカウント固定テスト無し）。

### 10.3 教員: 教材管理の配置レビュー（admin.js 内・新規ファイルなし）

- 行の「⋯」パネルに `ls-menu-item` **「位置づけ（分野マップ）…」**
  （`data-ui-anchor="materials.row-landscape"`、`.admin-landscape-doc-btn`）
- `openLandscapeModal(documentId, title)`: 既存モーダル定型（overlay cssText / 背景クリック
  クローズ / `#landscape-modal-body`）。overlay に
  `data-ui-anchor="materials.landscape-modal"`
- モーダル内容: ドメイン別グループ → 配置行（node_label / perspective_label / weight_label /
  status チップ / reason / quote 折りたたみ）+ [確認][却下][再検討]（PATCH）+
  ヘッダに [AIで再提案]（`data-ui-anchor="materials.landscape-propose"`、実行中 disabled、
  429 は事実文表示）+ unplaced ドメインの事実文 + 骨格版・生成日の事実行
- **3点セット**: ①`data-ui-anchor` 3件 ②`KNOWN_ADMIN_UI_ANCHOR_IDS` +
  `ADMIN_UI_ANCHORS`（`teacher/11-admin-materials.md#...`）に3エントリ
  ③`docs/manual/teacher/11-admin-materials.md` に節（`###` + 明示 `{#anchor}`、
  disabled になり得る再提案ボタンは「ボタンが無効になっている場合」行を必ず持つ）。
  `test_admin_help_ui_anchors.py:83` のカウントを 241 → **244** に更新（changelog コメント追記）

### 10.4 CSS

- 学習者側マーカー/ポップオーバー最小限を `atlas.css` に追加（`?v=` キャッシュバスタ更新）。
  admin 側はインラインスタイル定型のまま

---

## 11. テスト計画

| ファイル | 検査内容 |
|---|---|
| `backend/tests/test_landscape_guardrails.py` | core/landscape が FastAPI 非import（`guardrail_helpers`）/ agent ツリーが fastapi・backend を直 import しない / store に `DELETE FROM landscape_placements` が無い / builder が書くのは inferred のみ（`status='confirmed'` を含む INSERT が無い）/ routes/landscape.py に DELETE メソッドルートが無い / migration CHECK 語彙 = schema.py 語彙の完全一致 / 学習者 projection 出力に `weight` / `confidence` キーが無い（実データ試験）/ `.env.example` に3キー存在 / PIPELINE_STAGES に登録・ラベル存在 |
| `backend/tests/test_landscape_store.py` | supersede セマンティクス（confirmed 不変・rejected キー再挿入スキップ・superseded 履歴保持）/ update_status 遷移制約 / 一意制約 |
| `backend/tests/test_landscape_api.py` | 権限 fail-closed（view/edit/受講）/ PATCH 監査記帳 / propose 429 / 学習者 DTO の status フィルタと数値非漏洩 / document_ref の UUID・source_path 両対応 |
| `backend/tests/test_landscape_stage.py` | skip 3経路 / 非致命性 / resume artifact / モデル解決が policy 経由 / 永続化件数 |
| `backend/tests/test_landscape_ui_static.py` | landscape-layer.js の公開API・フック結線・fail-closed・ポーリング禁止・数値非表示 / app.js 出典タブ結線 / admin.js メニュー項目・モーダル・anchor 3件 / `LIMITS.l1Regions: 12` |
| `src/tests/agents/landscape_placement/` | validator（verbatim・node実在・語彙・truncate）/ repair / make_skipped / examples 整合 |
| 既存テストの更新 | `test_admin_help_ui_anchors.py`（カウント 244）/ atlas 骨格上限変更の影響（`test_atlas*` の 7 上限をピンするテストがあれば 12 に追随）/ `test_llm_model_phase4.py` は自動追随（LLM_STAGE_NAMES 同期のみ） |

---

## 12. ロードマップ（Phase 2〜4）

v1 の配置層を土台に、入力仕様書の残り要素を段階導入する。各フェーズは着手時に個別設計文書を切る。

- **Phase 2（ビューと遡及の深化）**: 出典タブ→chunk への evidence 遡及（claim_id→chunk 解決）/
  問いビュー（thesis_reconstruction の central_question を問いノードとして集約・
  placements の perspective='question' と接続）/ 方法ビュー（perspective='method' の
  横断集約）/ W層 positioning「分野の地図」レンズへの配置行の合流 / 橋渡し概念の骨格
  スキーマ拡張（`SkeletonRegion.domain_type` + `bridge_concepts`）/ G層
  `material.landscape_unreviewed`（recommended）/ 学習者の配置異議（atlas_report 同型）
- **Phase 3（知識地形）**: EmergentRegion（コーパスからのクラスタ・橋・空白検出、
  基準地図とは別レイヤー・candidate-only）/ コーパス別地図（Course/Global/Personal）/
  MapSnapshot と版比較 / 議論ビュー（D層 ledger の地図投影）
- **Phase 4（スキーマ進化）**: 配置不能・未回答の蓄積からの新領域候補・新 Predicate 候補
  提案（人間レビュー必須）/ 過去論文の再配置バッチ / 他分野基準地図の追加
  （シード機構は v1 で汎用化済み）

---

## 13. 実装分担（本セッションの Wave 計画）

Fable 5 が指揮・設計・統合、Opus 5 サブエージェントが実装。ファイル所有権を排他にして並列化する。

| Wave | Agent | スコープ | 主な所有ファイル |
|---|---|---|---|
| A-1 | BE基盤 | migration 065 / core/landscape/ 4モジュール / atlas_store バンドルドメイン拡張 / astrophysics 骨格 / MAX_REGIONS / AUDIT定数 / store・schema 単体テスト | `backend/db/065_*.sql`, `backend/core/landscape/*`, `backend/core/atlas_store.py`, `backend/core/atlas.py`, `backend/core/schema.py`, `backend/atlas_domains/*`, `test_landscape_{store,guardrails(core部分)}.py` |
| A-2 | 配置ステージ | src agent 一式 / orchestrator 登録 / llm_policy / llm_usage / .env.example / src テスト / stage テスト | `src/episteme_graph/agents/landscape_placement/*`, `backend/core/document_pipeline/orchestrator.py`, `backend/core/llm_policy.py`, `backend/core/llm_usage/schema.py`, `.env.example`, `src/tests/agents/landscape_placement/*`, `test_landscape_stage.py` |
| B-1 | API | routes/landscape.py / main.py 登録 / services ヘルパ / audit caller 登録 / API テスト | `backend/api/routes/landscape.py`, `backend/api/main.py`, `test_landscape_api.py`, `test_audit_entity_catalog_guardrails.py`(_AUDIT_CALLER_FILES) |
| B-2 | 学習者FE | landscape-layer.js / atlas-overlay.js フック+LIMITS / app.js 出典タブ / index.html / atlas.css / 学習者 anchors+manual / 静的テスト | `frontend/public/js/landscape-layer.js`, `frontend/public/js/atlas-overlay.js`, `frontend/public/js/app.js`, `frontend/public/index.html`, `frontend/public/css/atlas.css`, `backend/core/help_kb/ui_anchors.py`, `docs/manual/student/02-student.md`, `test_landscape_ui_static.py`(学習者部分) |
| B-3 | 教員FE | admin.js メニュー+モーダル / admin_ui_anchors.py / teacher manual / カウント更新 / 静的テスト | `frontend/public/js/admin.js`, `backend/core/help_kb/admin_ui_anchors.py`, `docs/manual/teacher/11-admin-materials.md`, `test_admin_help_ui_anchors.py`, `test_landscape_ui_static.py`(admin部分) |
| C | 統合 | ガードレール最終化 / 全スイート / CLAUDE.md / メモリ | 横断 |

競合回避の取り決め: `backend/core/deliberation/labels.py` / `positioning.py` /
`frontend/public/js/element-card.js` / `backend/core/symbol_notation.py` /
`backend/tests/test_element_labels.py` / `test_learning_ui_phase3_static.py` /
`docs/features/element_context_presentation_redesign.md` は**前セッションの未コミット変更
があるため触らない**。

---

## 14. 入力仕様書の受け入れ条件との対応

| AC | v1 での充足 |
|---|---|
| AC-001 主要領域の一画面表示 | 宇宙物理骨格10領域（既存オーバーレイ L1。上限引き上げ） |
| AC-002 複数領域配置+理由 | placements（複数行・perspective別）+ reason 表示 |
| AC-003 問い/方法/主張/根拠/前提/未解決の表示 | 既存資産（discuss開幕・出典タブ・D層）+ 本機能の位置づけ表示で構成 |
| AC-004 根拠箇所へ遡及 | evidence quote（原文逐語）を配置ごとに表示。chunk 遡及は Phase 2 |
| AC-005 AI推定と明示情報の区別 | provenance_label（AIによる推定/教員確認済み）を全表示面で必須 |
| AC-006 論文追加でアンカー不動 | 骨格凍結版が座標の正本。配置は骨格を変更しない（LS7） |
| AC-007 基準地図⇄生成地図の切替 | v1 は基準地図+配置レイヤーのトグル。コーパス地図は Phase 3 |
| AC-008 分野ビューと方法ビューの位置差 | perspective 別の配置行として保持・表示（専用ビューは Phase 2） |
| AC-009 表現困難な関係の候補保存 | unplaced_domains の記録+教員提示（LS10）。Predicate 提案は Phase 4 |
| AC-010 コーパス・生成日・版の確認 | corpus 事実行 + skeleton_version + run の `_stage_models`（LS8） |

---

## 15. 実装記録（2026-08-05 実装 / 2026-09-03 コード照合）

- **migration**: `backend/db/065_landscape_placements.sql`（`landscape_placements`。
  想定番号ではなく**実際に採番された番号**。コミット `407c5b0`）。
- **core**: `backend/core/landscape/`（`schema.py` = perspective / status 語彙とラベルの正本 /
  `store.py`（DELETE 文なし・空 candidates は SQL 非発行）/ `builder.py`
  （`build_and_store_placements` — パイプラインと教員の手動再提案が同一経路・同一 CostGate）/
  `projection.py`）。
- **agent**: `src/episteme_graph/agents/landscape_placement/`。パイプラインステージ
  `landscape_placement`（`discuss_opening` の直後）。
- **API**: `backend/api/routes/landscape.py` — 教員 `GET /api/admin/landscape/documents/{ref}/placements` /
  `PATCH .../placements/{id}` / `POST .../placements/propose` /
  `GET /api/admin/landscape/courses/{id}/placements` / `POST .../placements/accept`
  （リリース前の確認ウィザードの実体。`release_review_flow_design.md`）/
  `GET /api/admin/landscape/overview`、学習者 `GET /api/learning/courses/{id}/landscape`。
  **DELETE ルートは無い**。
- **基準骨格の同梱**: `backend/atlas_domains/astrophysics/`（骨格専用ドメインの新経路）。
- **UI**: 学習者 = `landscape-layer.js`（オーバーレイの「論文の位置」トグル + 出典タブの
  「分野の中の位置づけ」）、教員 = 教材管理の `⋯`「位置づけ（分野マップ）…」
  （UI アンカー `materials.row-landscape` / `landscape-modal` / `landscape-propose`）。
- 本層の上に積まれた後続層（いずれも別設計書が正本）: カテゴリギャップ候補（migration 066）・
  VA層の配置プレフィルタ（074）。
