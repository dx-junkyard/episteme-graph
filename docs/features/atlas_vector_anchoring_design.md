# 分野マップのベクトル係留層（Atlas Vector Anchoring, VA層）

> **状態: 実装済み（正本）**（2026-08-29 起票・同日実装。migration **074** —
> `atlas_anchor_embeddings` + `atlas_anchor_aliases`。実装記録は §12。
> Docker E2E は未実施）

**正本**: 本ドキュメント。
**関連**: [知識ランドスケープ](knowledge_landscape_design.md)（LS1〜LS10 — 配置の意味論と
数値非表示の規律を全面継承）/ [カテゴリギャップ候補](category_gap_candidates_design.md)
（KN-3/AB4 — AI が骨格を書かない構造的禁止・候補→確定フロー）/
[論文ディスカバリー層](paper_discovery_design.md)（PD1〜PD8 — 発見層の LLM 接触
allowlist・keyphrase 供給）/ [コーパス回遊層](corpus_roaming_design.md)（CR7 —
学習者起点で外部 API を呼ばない）/
[分野の地図バインディング](atlas_binding_lifecycle_design.md)（凍結・retire の
ライフサイクル）/ [LLM トークン使用量推計](llm_usage_metering_design.md)（U層）。

---

## 1. 目的 — 有限語彙の骨格をベクトル空間に係留する

分野の地図（atlas 骨格）は、凍結版ごとに region / concept が閉世界で確定した
**有限要素の語彙**である。一方、論文は既に pgvector（3072次元・text-embedding-3-large）
の空間に住んでいる（チャンク埋め込み・論文重心 `ranking.document_centroid`）。
両者を**同じ空間**で扱えないため、現状は次のギャップがある:

1. **配置（landscape_placement）が LLM 単独**: 骨格全体を閉世界提示するため、
   ノード数が増えるとプロンプトが肥大し、無関係ノードへの幻覚的配置の余地が残る。
2. **語彙の表記ゆれ検出が手作業**: cartridge の aliases は手書きで、コーパスに現れる
   同義語・略語・和英ゆれを拾う回路がない。カテゴリギャップ候補に「既存概念の
   言い換え」が新概念候補として混入する。
3. **取り込み前の着地予測がない**: ディスカバリーで見つけた候補論文が「取り込むと
   地図のどこに落ちそうか」を事前に知る手段がない。

本層は各骨格ノードに**プロトタイプベクトル**（label + 確定済み別名 + 確定配置の
evidence 引用の合成テキストの埋め込み）を与え、次の3機能を実現する:

- **配置の前段絞り込み**（§6）: 論文重心 × アンカー cosine の top-k で LLM への
  閉世界提示を絞る。LLM の evidence_quote verbatim 検査（LS層の要）は不変。
- **別名（alias）レジストリ**（§7）: ギャップ候補のうち既存アンカーにベクトル近傍な
  ものを「別表記の可能性」として注記し、教員の1操作で**確定別名**として登録する。
  確定別名はプロトタイプと keyphrase 供給に還流する — **語彙の標準化がコーパスの
  成長の副産物になる**。
- **着地予測**（§8）: ディスカバリー検索（関連度順）の候補に「取り込むとこの領域に
  落ちそう」の段階ラベルを付す。

**教員の確定操作（配置 confirm・別名登録）がベクトル空間の座標系を育てる**。
承認作業を不要にするのではなく、裁定1回の価値を最大化する層である（§0 討議録参照 —
オーナー裁定: 共有骨格への昇格の弁は残す）。

## 2. 不変条項（VA1〜VA9）

- **VA1 ベクトルは候補生成器 — 確定は常に人間**: ベクトル類似から骨格ノード・配置・
  別名が自動確定する経路を作らない。前段絞り込みは LLM への提示範囲の調整であって
  配置の確定ではない。別名登録・ギャップ裁定・配置 confirm は従来どおり教員の明示操作。
- **VA2 数値非表示**: cosine 生値・類似度スコアは DB / 内部計算のみ。表示は段階ラベル
  （正本は `core/label_vocab.py` の `ANCHOR_NEARNESS_SCALE`）。例外は運用カバレッジの
  事実（「49ノード中49件 索引済み」等のインフラ状態）のみ — これは評価数値ではない。
- **VA3 埋め込み呼び出しは凍結時・教員起点・パイプラインのみ**: 学習者起点で
  embedding API を呼ぶ経路を作らない（CR7 継承）。呼び出し地点は ①freeze 後の
  best-effort 再構築 ②教員の明示 refresh ③ギャップレビュー画面の注記導出
  （教員起点・キャッシュ付き）④配置前段絞り込み（パイプライン内。論文重心は
  既存チャンク埋め込みの平均で追加呼び出しゼロ）に限る。
- **VA4 fail-soft**: アンカーベクトル不在・埋め込み失敗で全機能が従来動作へ縮退する
  （freeze を止めない / 配置は全骨格提示 / 着地予測・近傍注記は静かに非表示）。
  エラーで既存フローを壊さない。
- **VA5 埋め込みモデルは chunks と同一**: pgvector 3072 次元と結合しているため
  モデル切替非対応（M5 準拠・scene なし）。feature は `embedding:atlas_anchors`
  （`llm_policy.scene_for_feature` の `embedding:` プレフィックスで自動的に scene 対象外）。
- **VA6 情報を落とさない**: 別名は status 遷移（confirmed / dismissed）で行削除 API
  なし。アンカーベクトルの (domain, version) 単位の全置換再構築のみ設計明示の例外
  （help_kb `vector.py` のスナップショット同期と同じ扱い — 導出データであり正本ではない）。
- **VA7 間引きの正直さ**: 前段絞り込みで LLM 提示から外したノード数は
  `stage_outputs` の `vector_prefilter` に必ず記録する（silent truncation 禁止）。
- **VA8 閉世界の正直さ**: 着地予測・近傍注記は骨格版を明示し、「この骨格（版N）の
  中で最も近い」の言明のみ。「この分野ではXに属する」と断定しない。
- **VA9 骨格への書き込み経路を作らない**: 本層のコードから `atlas_skeletons` への
  INSERT/UPDATE は存在しない（KN-3/AB4 継承。ガードレールで構造的に固定）。
  別名は骨格の**外**の独立テーブルであり、骨格 draft/freeze フローに触れない。

## 3. DB（migration 074）

```sql
CREATE TABLE IF NOT EXISTS atlas_anchor_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_key       TEXT NOT NULL,
    skeleton_version TEXT NOT NULL,
    node_id          TEXT NOT NULL,
    node_kind        TEXT NOT NULL CHECK (node_kind IN ('region', 'concept')),
    source_text      TEXT NOT NULL,
    source_hash      TEXT NOT NULL,
    embedding        vector(3072),
    built_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (domain_key, skeleton_version, node_id)
);

CREATE TABLE IF NOT EXISTS atlas_anchor_aliases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_key       TEXT NOT NULL,
    node_id          TEXT NOT NULL,
    alias            TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'confirmed'
        CHECK (status IN ('confirmed', 'dismissed')),
    source           TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('gap_signal', 'manual')),
    evidence         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by       UUID,
    decided_by       UUID,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (domain_key, node_id, normalized_alias)
);
```

設計上の要点:
- pgvector 拡張は init.sql で導入済み。行数は高々 12 regions + 72 concepts / domain
  なので **index なし**（058 manual_sections と同じ判断）。FK なし（骨格は JSONB
  スナップショットでノード行が存在しないため）。
- `skeleton_version` は骨格凍結版の刻印。読み取り側は常に**現行凍結版の行だけ**を使う
  （版が変わると自動的に stale = 不使用になり、freeze フックが新版を作る）。
- `source_hash` は合成テキストの sha256。refresh 時に不変なら再埋め込みをスキップ
  （コスト節約・冪等）。
- 別名は**版非依存**（gap decisions の `cluster_key` と同じ裁定 — 却下・登録が版更新で
  蒸発しない）。`normalized_alias` は `core/atlas_gaps/schema.py::normalize_label` を
  正本として流用。status の既定は `confirmed`（登録＝教員の確定操作そのものだから。
  candidate 状態は v1 に存在しない — 近傍注記は読み時導出で保存しない）。
- 削除 API なし。dismissed → 再登録は同一行の status 遷移（UNIQUE が重複行を防ぐ）。

## 4. core モジュール（`backend/core/atlas_vectors/`、FastAPI 非 import）

```
backend/core/atlas_vectors/
  __init__.py
  schema.py    → プロトタイプ合成テキストの正本 (build_anchor_source_text) / 語彙
  store.py     → DB 読み書き（replace_domain_embeddings / load_anchor_vectors /
                 alias CRUD（status遷移のみ）/ coverage_status）
  builder.py   → build_anchor_embeddings(domain_key, *, session=None) — 現行凍結版の
                 全ノードを合成→ハッシュ比較→変化分のみ 1 バッチ embed→全置換保存。
                 CostGate 日次ゲート + usage_context("embedding:atlas_anchors")
  query.py     → nearest_anchors(vector, anchors) / prefilter_domains(...) /
                 landing_for_vector(...) — 純計算（DB は store 経由・LLM 非接触）
  annotate.py  → annotate_gap_clusters(session, domain_key, clusters) — ギャップ
                 クラスタ label の埋め込み（教員起点・in-process キャッシュ・日次ゲート
                 共有）→ 近傍注記 near_anchor を付与。fail-soft
```

**プロトタイプ合成テキスト**（`build_anchor_source_text`、決定論）:

```
{label}
別名: {confirmed aliases を normalized 昇順}          ← あれば
領域: {親 region label}（concept の場合）
根拠: {当該ノードの confirmed landscape_placements の evidence quote、
       placement id 昇順・最大5件・各200字}            ← あれば
```

label 以外は全て任意 — 確定情報が増えるほどプロトタイプが精密になる（「教員の裁定が
座標系を育てる」の実体）。region のプロトタイプは label + 配下 concept label 列挙。

**論文重心**は `core/paper_discovery/ranking.py::document_centroid` を再利用する
（DB のみ・LLM 非接触の純関数。発見層の LLM 接触 allowlist は不変）。

## 5. 構築トリガーと API

- **freeze フック**: `routes/atlas.py::freeze_atlas_skeleton` の post-commit
  best-effort 区画（overlay refresh 等と同列）に daemon thread で
  `build_anchor_embeddings(cartridge_id)` を追加。失敗しても freeze は成功のまま
  （VA4）。凍結レスポンスは不変。
- **手動 refresh**: `POST /api/admin/cartridges/{cartridge_id}/atlas/vectors/refresh`
  （`_require_teacher`）。既存凍結骨格（freeze フック以前に凍結済みの分）のバック
  フィル手段。retired ドメインは 409。監査 `AUDIT_ENTITY_ATLAS_VECTOR`。
- **status**: `GET /api/admin/cartridges/{cartridge_id}/atlas/vectors/status` →
  `{domain_key, skeleton_version, total_nodes, embedded_nodes, built_at, stale}`
  （stale = 現行凍結版と索引版の不一致）。骨格なしは `{available: false}`。
- 新ルーター `backend/api/routes/atlas_vectors.py`（`prefix="/cartridges"`、main.py
  から `prefix="/api/admin"` で直接登録 — atlas_gaps と同型）。別名 API（§7）も同居。
- **起動時の自動バックフィルはしない**（起動時に embedding API を叩かない。
  運用の主経路は freeze フック、非常口が手動 refresh）。

env（`core/config.py` Settings 方式）:
- `ATLAS_VECTOR_MAX_CALLS_PER_DAY`（既定 50 — 構築・注記の embedding バッチ回数の
  日次上限。CostGate in-memory、超過は fail-soft で従来動作）

## 6. 配置の前段絞り込み（landscape_placement プレフィルタ）

`core/landscape/builder.py::build_and_store_placements` で、
`collect_placement_domains()` の後に:

```python
prefiltered, prefilter_facts = atlas_vectors.query.prefilter_domains(
    session, centroid, domains, top_k=settings.landscape_vector_prefilter_topk)
```

- centroid は `document_centroid(session, document_id)`（既存チャンク埋め込みの平均 —
  **追加 embedding 呼び出しゼロ**）。
- ドメインごとに concept ノードを cosine 上位 `top_k` に絞る。**region は常に全提示**
  （gap 検出の親 region 選択肢を狭めない）。ベクトルを持たない concept は**保持**
  （比較不能なものを落とさない — 慎重側）。centroid なし / アンカーベクトルなし /
  `top_k=0` は無加工で素通し（VA4）。
- 結果は stage payload に `vector_prefilter: {"applied": bool, "omitted": int,
  "domains": {domain_key: omitted_count}}` として記録（VA7）。
- env: `LANDSCAPE_VECTOR_PREFILTER_TOPK`（既定 32、0 = 無効）。
- **ギャップ検出との関係**: 提示を絞ると「既存概念の言い換えを新概念にしない」判定の
  閲界が狭まるため、プロンプトには「提示は関連上位への絞り込みであり骨格の全体では
  ない」旨の一文を加え、gap 検出の重複除外は従来どおり読み時導出
  （`derive_candidates` の凍結版突合）が最終防衛線となる。

## 7. 別名レジストリ（語彙標準化の回路）

**検出（読み時・保存しない）**: ギャップレビューキュー
`GET /api/admin/cartridges/{id}/atlas/gap-candidates` のレスポンス組み立て時
（route 層）に `annotate.annotate_gap_clusters(...)` を通す。クラスタの
`proposed_label` を 1 バッチで埋め込み（in-process キャッシュ key =
(domain, skeleton_version, normalized_label)、日次ゲートは §5 と共有）、現行凍結版
アンカーとの cosine が最上位帯（`ANCHOR_NEARNESS_THRESHOLD_NEAR` 以上）のとき、
クラスタに注記を付与する:

```python
"near_anchor": {"node_id", "node_label", "region_label",
                "nearness_label", "skeleton_version"}
```

生スコアは載せない（VA2）。ゲート超過・埋め込み失敗・アンカー不在では注記キー自体が
無い（VA4・既存レスポンスと完全後方互換）。

**登録（教員の確定操作）**:
- `GET /api/admin/cartridges/{id}/atlas/aliases?include_dismissed=` — 一覧
  （node_id ごとにグループ）。
- `POST /api/admin/cartridges/{id}/atlas/aliases` — body
  `{node_id, alias, source: "gap_signal"|"manual", evidence?: {cluster_key?, note?}}`。
  現行凍結版に node_id が実在しなければ 422。既存 dismissed 行があれば confirmed へ
  復帰（upsert）。登録成功後、当該ノードのプロトタイプを best-effort で単ノード
  再埋め込み（1件 1バッチ・失敗しても登録は成功のまま）。
- `POST /api/admin/cartridges/{id}/atlas/aliases/{alias_id}/dismiss` — status 遷移
  （行削除しない、VA6）。
- 全て `_require_teacher`・監査 `AUDIT_ENTITY_ATLAS_VECTOR`
  （action: `alias_register` / `alias_dismiss` / `vectors_refresh`）。

**還流（標準化の実効）**:
1. プロトタイプ合成テキストに confirmed 別名が入る（§4） — 以後の前段絞り込み・
   着地予測・近傍注記の精度が上がる。
2. `core/paper_discovery/vocab.py` に第4サプライヤ `_alias_phrases`（confirmed 別名）
   を追加し、`KEYPHRASE_SOURCES` を `("skeleton","cartridge","component","alias",
   "manual")` の5語彙に拡張する（PD3 の供給源に「教員確定の別名」が加わる。
   関連テスト・設計書 paper_discovery_design.md の語彙記述も追随）。

**UI 導線**: ギャップ候補カードに注記行「既存の『{node_label}』の別表記の可能性が
あります（骨格 版{version} 内）」+ ボタン「別名として登録」。押下で alias POST →
成功後に既存の gap 却下 API（decide dismiss）を理由自動填入
（「既存概念『{label}』の別名として登録」）で呼ぶ2段動作。骨格ノード候補として
残したい場合は何もしなければ従来どおり。

## 8. 着地予測（ディスカバリー検索）

`POST /api/admin/discovery/search` の `order: "relevance"` 経路のみ（v1）:
候補アブストは既に `rank_candidates` が 1 バッチで埋め込んでいる。同じベクトルを
現行凍結版アンカーと照合し、最上位・中位帯のとき候補 dict に付与する:

```python
"landing": {"node_label", "region_label", "nearness_label", "skeleton_version"}
```

- **追加 embedding 呼び出しゼロ**（既存バッチのベクトルを流用）。実装は
  `ranking.rank_candidates` に optional `anchor_context` を渡す形にし、発見層の
  LLM 接触 allowlist（ranking.py / compare.py のみ）を不変に保つ。
  atlas_vectors.store の**読み**（DB のみ）は allowlist 違反ではない。
- date 順検索・radar は v1 非対象（radar は seed のドメイン帰属が多義のため §11）。
  → **2026-08-29 解消（radar のみ）**: seed のドメイン帰属は既存の
  `corpus.document_domain_keys`（複数所属時はコース作成日の新しい順の先頭）で決着し、
  論文レーダーにも同じ `landing` を配線した。実装先は
  [論文レーダー](paper_radar_design.md) §12。date 順検索は非対象のまま。
- 下位帯・アンカー不在・骨格なしはキー自体を付けない（VA4/VA8）。UI は候補行に
  1行の事実文で表示（「地図上の近い領域: {region} / {node}（版{version}）」）。

## 9. 段階ラベル（label_vocab 正本）

```python
ANCHOR_NEARNESS_THRESHOLD_NEAR = 0.55   # ラベル×ラベル（gap 近傍注記・別名ヒント）
ANCHOR_NEARNESS_THRESHOLD_MID = 0.40
ANCHOR_NEARNESS_SCALE = GradedScale(
    (0.55, 0.40), ("かなり近い", "近い可能性", "遠い"))

ANCHOR_LANDING_THRESHOLD_NEAR = 0.36    # 論文テキスト×アンカー（着地予測・新しい面）
ANCHOR_LANDING_THRESHOLD_MID = 0.30
ANCHOR_LANDING_SCALE = GradedScale(
    (0.36, 0.30), ("かなり近い", "近い可能性", "遠い"))
```

閾値・ラベルの正本は `core/label_vocab.py` のみ（重複定義はガードレールが検出）。
ギャップ近傍注記は最上位帯のみ表示、着地予測は上位2帯を表示（最下帯は非表示 =
「なんとなく関連」を出さない — help_kb の保守的足切りと同じ思想）。

**スケールが2表あるのはレジームが違うため（2026-08-29 実測校正）**: gap 近傍注記は
gap クラスタ label × アンカー合成テキスト（双方日本語の短文）で 0.55/0.40 が妥当。
着地予測・新しい面（`landing_for_vector` / `new_facet_labels`）は英語アブスト・チャンク
重心 × 日本語ラベル中心のプロトタイプという**言語間・長短文比較**で cosine の絶対水準が
一段下がる。実測（astrophysics 骨格 59 アンカー × 実レーダー候補 20 件）では主題の合う
候補の最良アンカーが 0.34〜0.38（最近接アンカーは意味的に正しい: CMB 複屈折→cmb）、
主題の違う候補が 0.21〜0.29 で、旧閾値 0.55/0.40 ではこのレジームで一度も発火しなかった。
0.36/0.30 は境界の雑音帯（0.28〜0.34）を「近い可能性」止まりにする保守側の校正値。
実測での見直し前提は両表とも継承する。

## 10. ガードレール

`test_atlas_vectors_{core,api,guardrails}.py` + `test_atlas_vectors_ui_static.py`:
- core が FastAPI / routes を import しない
- `atlas_vectors/` から `atlas_skeletons` への INSERT/UPDATE 不在（VA9）
- 公開 DELETE API 不在・alias が status 遷移のみ（VA6。replace_domain_embeddings の
  内部 DELETE は設計明示の例外として関数単位で許可）
- cosine / similarity 生値が API レスポンス（status の件数以外）に漏れない（VA2）
- 閾値・ラベルが label_vocab 以外に重複定義されない
- freeze フックが try/except + daemon thread（freeze を止めない、VA4）
- 学習者向けルート（/api/learning）から atlas_vectors への参照ゼロ（VA3）
- prefilter が region を落とさない・ベクトルなし concept を落とさない・
  `vector_prefilter` 記録キーの存在（VA7）
- KEYPHRASE_SOURCES 5語彙と alias サプライヤの整合
- 監査 action 語彙の固定

## 11. 非スコープ（v1）

- ~~radar への着地予測~~ → **2026-08-29 実装済み**（seed のドメイン帰属は既存
  `corpus.document_domain_keys` で解決。実装先は
  [論文レーダー](paper_radar_design.md) §12 へ移管 — 本層は
  `query.landing_for_vector` / `query.new_facet_labels` の提供側）。
  **date 順検索**への着地予測は v1 非スコープのまま（候補ベクトルを作らない経路のため
  追加 embedding が要る = §8 の「追加呼び出しゼロ」を満たさない）
- 配置候補そのものをベクトルで生成すること（配置は LLM + evidence verbatim のまま。
  ベクトルは絞り込みと注記のみ）
- ベクトル近傍からの alias **candidate 行**の自動生成（v1 は読み時注記のみ・保存
  しない。運用実測で注記の的中が高ければ candidate_flow 接続を検討）
- 配置共起のグラフ埋め込み（node2vec / PMI — 骨格エッジ提案。将来の別設計書）
- 学習者向け表示（コーパス地図・personal map への露出は既存機構のまま）
- skip-gram 等の自前埋め込み学習（コーパス規模が2〜3桁不足 — 討議で見送り確定）
- embedding モデルの切替・次元変更

## 12. 実装記録（2026-08-29、v1 全 Phase 同日実装）

Fable 5 指揮 + Opus 5 サブエージェント4体（core / API / 統合 / フロント+マニュアル）の
並列実装。設計からの逸脱・確定事項:

- **migration 074** `074_atlas_vector_anchoring.sql` — §3 のとおり2表（named CHECK・
  シードなし・index/FK なし）。
- **core** `backend/core/atlas_vectors/{schema,store,builder,query,annotate}.py`。
  §4 からの変更点: `query.prefilter_domains(centroid, domains, anchors_by_domain, *,
  top_k)` は **session を取らない純関数**（DB 読みは `store.anchors_for_domains` を
  呼び出し側で先に実行 — query.py の DB 非接触をガードレールで固定するため）。
  builder と annotate の日次ゲートは共有（`builder.check_daily_gate` — 層で1本の
  embedding 予算）。`builder.build_anchor_embeddings` は commit まで行い失敗は raise
  （fail-soft 化は呼び出し側 = freeze フック / refresh API の責務）。
  `builder.anchors_with_labels(session, domain_key)` が着地予測・注記用の正本ヘルパー
  （現行凍結版に無いノードを落とし、版文字列を返す — VA8）。
- **API** `routes/atlas_vectors.py`（5本・全て `_require_teacher`・atlas_gaps と同型）。
  status の `stale` は「現行版のカバレッジ 0 行 かつ 他版の行が存在」の慎重側判定。
  別分野の alias id は 404（他分野の登録内容を漏らさない）。refresh の失敗は監査に
  記帳しない（起きなかった構築を残さない）。freeze フックは post-commit 区画の
  daemon thread `atlas-anchor-embed`（二重 try/except・凍結レスポンス不変）。
  ルーター一覧は `docs/backend/api.md` に追随済み（admin 子ルーター 21本）。
- **配置プレフィルタ**: `landscape/builder.py::_apply_vector_prefilter`（全体
  try/except・失敗時は素通し）。`vector_prefilter` facts は off/失敗でも
  `applied: False` で必ず記録（VA7）。プロンプト注記は
  `LandscapePlacementInput` の `DomainOption.prefiltered`（入力契約への追加は
  この1フィールドのみ）→ `input_builder.PREFILTER_NOTE`（正本1箇所）→
  `prompt.py` が注記持ちドメインがあるときだけ「絞り込み提示について」節を挿入。
- **別名還流**: `KEYPHRASE_SOURCES` は5語彙 `skeleton|cartridge|component|alias|manual`
  に拡張（`manual` 末尾不変・alias サプライヤは cartridge と component の間 =
  教員確定語彙の信頼順位）。paper_discovery_design.md の PD3 記述も追随済み。
- **着地予測**: `ranking.rank_candidates(..., anchor_context=)` — 既存1バッチの候補
  ベクトル流用で追加 embedding ゼロ。`landing` キーは `node_label / region_label /
  nearness_label / skeleton_version` のみ（node_id / node_kind / 生スコアは載せない）。
  発見層 LLM 接触 allowlist（ranking.py / compare.py）は不変。
- **着地予測の radar 配線（2026-08-29 追補）**: §11 で v2 送りにしていた論文レーダーにも
  同じ `landing` を配線した（seed のドメイン帰属は既存 `corpus.document_domain_keys` で
  決着 — 複数所属時はコース作成日の新しい順の先頭）。`band_candidates` が作った候補
  ベクトルを流用するため**追加 embedding ゼロ**、`radar.py` の import 境界
  （`core.llm` / `atlas_vectors` 非 import）も不変で、route 層が `_anchor_context` を
  resolver として `run_radar_search(anchor_context_resolver=...)` に注入する。併せて
  `query.new_facet_labels`（最上位帯で近いアンカーのうち、seed 教材の
  `landscape_placements` に無い node のラベル）を本層に追加した — 読みのみ・非LLM・
  生スコア非漏洩（VA2）。仕様の正本は [論文レーダー](paper_radar_design.md) §12。
- **UI**: 分野の地図タブ「ベクトル索引」「登録済みの別名」区画 + gap カード注記と
  「別名として登録」（成功時のみ既存 gap 却下を理由自動填入で併用する2段動作）+
  discovery 候補行の着地1行（サーバ提供ラベルのみ・JS への段階ラベル直書きは
  ui_static テストが `ANCHOR_NEARNESS_SCALE.labels` から導出した denylist で禁止）。
  管理UIアンカー 300→**303**（`atlas.vector-refresh` / `atlas.aliases` /
  `atlas.gap-alias-register`）+ マニュアル `teacher/17-admin-atlas.md` 4節 +
  `teacher/11-admin-materials.md` 追記の3点セット済み。
- **監査**: `AUDIT_ENTITY_ATLAS_VECTOR`（カタログ39語彙目）。
  U層 feature は `embedding:atlas_anchors`（scene なし = M5、KNOWN_FEATURES 登録済み）。
- **テスト**: `test_atlas_vectors_{core(69),guardrails(40),api(37),ui_static(41)}.py` +
  既存スイートの追随（landscape stage プレフィルタ7件 / gap 注記3件 / alias
  サプライヤ4件 / landing 9件 / agent 側 prefilter note 検査）。
- **既知の限界**: ①`normalize_label` は記号列を空扱いしない（`"!!!"` は有効な別名
  として通る — store 契約どおり）②stage テストの `_FakeSession` 環境では
  プレフィルタが常に fail-soft 経路（warning ログのみ・挙動は正しい）
  ③CostGate は in-memory・プロセスローカル（既存層と同じ許容）。
- **Docker E2E（実 DB での migration 074 適用・freeze フック実走・実 embedding）は
  未実施** — docker 環境復帰後の確認事項。
