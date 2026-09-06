# カートリッジシステム

[← ドキュメント目次](../README.md) ｜ [← PDF 解析 Agent 詳細](agents.md)

カートリッジは、**ドメイン固有の語彙・ルール・検証定義**を JSON で外部化したものです。
Agent のコードにドメイン知識をハードコードせず、ここから読み込むことで、
コアロジックを domain-independent に保ったままドメイン適応できます。

- 配置: `backend/cartridges/<cartridge_id>/`
- 既定カートリッジ: `particle_physics`（`EPISTEME_DEFAULT_CARTRIDGE_ID`、既定値も `particle_physics`）
- パス解決: `src/episteme_graph/agents/cartridge_paths.py`（`EPISTEME_CARTRIDGES_DIR` → 自動探索）
- **同梱されている実カートリッジは `particle_physics` の 1 件のみ**（2026-09-03 時点。
  実物は `ls backend/cartridges/` が正）。新しい分野は「カートリッジ一式を作る」ほかに、
  骨格だけの**骨格専用ドメイン**を足す道もある（下記 §5）。

---

## 1. カートリッジのファイル構成

`backend/cartridges/particle_physics/` の例（読み込むファイル名は `cartridge.json` の
`files` マニフェストが宣言する。**ただし `library/` と `examples/` はマニフェストに載らず、
ディレクトリ走査で読まれる** — `core/library/seed.py` が `cartridge_directory(id)/"library"` を
直接見る）:

| ファイル | 役割 |
|---|---|
| `cartridge.json` | メタデータ（`cartridge_id` / `version` / `target_domain` / `source_policy`）+ ファイルマニフェスト |
| `ontology.json` | `concept_types` / `aliases` / `notation_patterns` / `normalization_rules` / `extraction_hints` |
| `validation_rules.json` | block typing / claim field / component field の妥当性チェック |
| `component_types.json` | 許可される component type 語彙 |
| `relation_types.json` | dependency / connector 語彙 |
| `support_statuses.json` | サポートステータス定義（source_backed, inferred, review_required …） |
| `maturity_levels.json` | 成熟度レベル定義（`maturity_levels` + `maturity_sources`） |
| `extraction_prompt.md` / `review_prompt.md` | ドメイン向けプロンプト断片 |
| `examples/*.json` | プロンプトの grounding 用サンプルコンポーネント（**マニフェスト非経由**・ディレクトリ走査） |
| `atlas/skeleton.yaml` | 分野の地図（Field Atlas）の骨格シード。起動時に一度だけ DB（`atlas_skeletons`）へ冪等取込され、以降は DB が正本 |
| `library/*.json` | L層ナレッジライブラリのシード（例: `apparatus_seed.json`）。同じく起動時に冪等取込され、パイプラインが読むのは**凍結版のみ**。**マニフェスト非経由**で `library/` ディレクトリを走査する |

> `atlas/` と `library/` は A層のパイプラインが直接読むものではなく、それぞれ
> [分野の地図](../features/field_atlas_overlay_spec.md) と
> [画像パイプライン + L層](../features/image_pipeline_knowledge_library_design.md) の
> **シード兼フォールバック**です。運用中の編集は DB 側（draft → 凍結）で行い、JSON/YAML を書き戻しません。

---

## 2. CartridgeContext と CartridgeLoader

**正本は `src/episteme_graph/agents/cartridge_loader.py` / `cartridge_context.py`**（2026-07 の整理で
約 10 個の同一コピーを統合）。各 Agent ディレクトリの `cartridge_loader.py` はここからの薄い
再エクスポートで、import パスの共通インターフェースだけを保っています。読み込まれた JSON 群は
`CartridgeContext`（dataclass）に詰められ、prompt builder / validator / repairer に渡されます。

```python
@dataclass
class CartridgeContext:
    cartridge_id: str
    ontology: dict
    validation_rules: dict
    aliases: dict | None = None                     # canonical → [aliases]
    notation_patterns: list | None = None           # 記号の表記ゆれ
    normalization_rules: list | None = None         # 例: Greek 記号 → Unicode
    extraction_hints: dict | list | None = None     # LLM 向けドメイン語彙ヒント
```

> **例外 2 件**: `component_assembly`（`component_types` / `relation_types` が必須）と
> `component_graph`（`relation_types` が必須）は必須フィールドが増えるため、正本を import せず
> 自分の `schema.py` に固有の `CartridgeContext` を持ちます。共通化すると既存の呼び出し側・テストが
> 使っている位置引数の順序が黙って変わるため、意図的に分けたままです（各ファイルの docstring 参照）。

### ライフサイクル
```
Agent 初期化      : CartridgeLoader(cartridge_base_dir=None)
   ↓
run(cartridge_id) : loader.load(cartridge_id) → CartridgeContext
   ↓
入力構築          : ontology / extraction_hints をプロンプトへ注入
出力検証          : validation_rules でスキーマ準拠を確認
修復              : aliases / normalization_rules で正規化・修正
   ↓
フォールバック    : cartridge_id=None もしくはロード失敗時は空 context で続行
```

> **cartridge-aware だが cartridge-dependent ではない**: すべてのカートリッジ参照は Optional。
> カートリッジが無くても Agent は単独動作します。

---

## 3. ビルトイン語彙との関係

カートリッジで拡張する前提となる「組込み語彙」は `backend/core/schema.py` にあります。

### OntologyType（`backend/core/schema.py` の `class OntologyType`）
- 汎用（OSL）: `Agent` / `Event` / `Resource` / `Intentional Moment`
- 数学: `MathematicalObject`（テンソル・群・多様体・作用素）
- 物理: `PhysicalPhenomenon` / `Particle` / `Symmetry` / `Theorem`
- 理論: `TheoreticalFramework`（QFT/QED/QCD/標準模型）

### CorePredicate（`backend/core/schema.py` の `class CorePredicate`）
`CAUSES` / `INHIBITS` / `CORRELATES` / `DEFINES` / `MEASURES` / `TRANSFORMS` / `REQUIRES` / `CONTAINS` / `EQUIVALENT`

これらは固定 enum ですが、運用中に不足が判明すれば **動的スキーマ進化**で DB 側に拡張語彙を追加できます
（→ [動的スキーマ進化](schema-evolution.md)）。カートリッジは「ドメイン固有の語彙・別名・検証ルール」、
スキーマ進化は「グラフ DSL の基本語彙そのものの拡張」という役割分担です。

> **接続の実態（2026-09-03 時点）**: A層の PDF 解析 Agent が読むドメイン語彙は**カートリッジだけ**で、
> 動的スキーマ進化が DB に足した語彙は A層 Agent のプロンプトには注入されません
> （消費者は提案生成・Shadow Testing・管理 API の語彙一覧のみ）。詳細は
> [動的スキーマ進化 §2](schema-evolution.md#2-スキーマレジストリschema_registrypy)。

---

## 4. 設計原則（再掲）

1. **domain-independent なコア** — Agent コードに分野・論文固有のキーワードを書かない。
2. **語彙はカートリッジから** — concept type / relation / component type / support status / maturity は JSON 定義。
3. **不変・読み取り専用** — 実行時にカートリッジを書き換えない。
4. **バージョン管理** — 各 JSON は個別にバージョンを持てる。

---

## 5. 骨格専用ドメイン（`backend/atlas_domains/`）

「分野の地図の骨格だけあればよく、抽出用の語彙定義（ontology / validation_rules …）は要らない」
分野のための軽量な経路です。カートリッジ一式を作らずに地図の座標系だけを増やせます。

```
backend/atlas_domains/<domain_key>/
  skeleton.yaml   # 凍結骨格（トップレベルキー atlas_skeleton。status: frozen が必須）
  domain.json     # 任意。表示名 name / description
```

- 起動時に `core/atlas_store.py` が `backend/cartridges/*/atlas/skeleton.yaml` と併せて走査し、
  **冪等にシード**する。受理するのは凍結済み（`status: frozen`）でレビュー済みのものだけ、
  `domain.json` のメタは既存 DB 行を上書きしない。YAML が壊れていても起動は止めない（fail-soft）。
- カートリッジ経路が見つからなかった場合のフォールバックとして働くので、既存の
  `cartridges/<id>/atlas/skeleton.yaml` 経路は不変。
- 同梱されているのは `astrophysics`（宇宙物理。2026-09-03 時点で 10 領域 / 49 概念 / 19 エッジ。
  実物は `backend/atlas_domains/astrophysics/skeleton.yaml` が正）。
- **カートリッジ ID と地図の domain_key は同じ名前空間**なので、`particle_physics` のように
  カートリッジを持つ分野はその `atlas/skeleton.yaml` が骨格になり、`astrophysics` のように
  骨格だけの分野はここに置く。詳細 → [知識ランドスケープ 設計書](../features/knowledge_landscape_design.md)。

---

[← PDF 解析 Agent 詳細](agents.md) ｜ 次へ: [DSL と理論操作グラフ →](theory-graph.md)
