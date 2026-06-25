# カートリッジシステム

[← ドキュメント目次](../README.md) ｜ [← PDF 解析 Agent 詳細](agents.md)

カートリッジは、**ドメイン固有の語彙・ルール・検証定義**を JSON で外部化したものです。
Agent のコードにドメイン知識をハードコードせず、ここから読み込むことで、
コアロジックを domain-independent に保ったままドメイン適応できます。

- 配置: `backend/cartridges/<cartridge_id>/`
- 既定カートリッジ: `particle_physics`（`EPISTEME_DEFAULT_CARTRIDGE_ID`）
- パス解決: `src/episteme_graph/agents/cartridge_paths.py`（`EPISTEME_CARTRIDGES_DIR` → 自動探索）

---

## 1. カートリッジのファイル構成

`backend/cartridges/particle_physics/` の例:

| ファイル | 役割 |
|---|---|
| `cartridge.json` | メタデータ + ファイルマニフェスト |
| `ontology.json` | concept types / aliases / notation_patterns / normalization_hints / extraction_hints |
| `validation_rules.json` | block typing / claim field / component field の妥当性チェック |
| `component_types.json` | 許可される component type 語彙 |
| `relation_types.json` | dependency / connector 語彙 |
| `support_statuses.json` | サポートステータス定義（source_backed, inferred, review_required …） |
| `maturity_levels.json` | 成熟度レベル定義 |
| `extraction_prompt.md` / `review_prompt.md` | ドメイン向けプロンプト断片 |
| `examples/*.json` | プロンプトの grounding 用サンプルコンポーネント |

---

## 2. CartridgeContext と CartridgeLoader

各 Agent ディレクトリに `cartridge_loader.py`（共通インターフェース）があり、JSON 群を読み込んで
`CartridgeContext`（不変の dataclass）に詰めて、prompt builder / validator / repairer に渡します。

```python
@dataclass
class CartridgeContext:
    cartridge_id: str
    ontology: dict
    validation_rules: dict
    aliases: dict | None = None              # canonical → [aliases]
    notation_patterns: list | None = None    # 記号の表記ゆれ
    normalization_rules: list | None = None  # 例: Greek 記号 → Unicode
    extraction_hints: list | None = None     # LLM 向けドメイン語彙ヒント
```

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

### OntologyType（`schema.py:12`）
- 汎用（OSL）: `Agent` / `Event` / `Resource` / `Intentional Moment`
- 数学: `MathematicalObject`（テンソル・群・多様体・作用素）
- 物理: `PhysicalPhenomenon` / `Particle` / `Symmetry` / `Theorem`
- 理論: `TheoreticalFramework`（QFT/QED/QCD/標準模型）

### CorePredicate（`schema.py:34`）
`CAUSES` / `INHIBITS` / `CORRELATES` / `DEFINES` / `MEASURES` / `TRANSFORMS` / `REQUIRES` / `CONTAINS` / `EQUIVALENT`

これらは固定 enum ですが、運用中に不足が判明すれば **動的スキーマ進化**で DB 側に拡張語彙を追加できます
（→ [動的スキーマ進化](schema-evolution.md)）。カートリッジは「ドメイン固有の語彙・別名・検証ルール」、
スキーマ進化は「グラフ DSL の基本語彙そのものの拡張」という役割分担です。

---

## 4. 設計原則（再掲）

1. **domain-independent なコア** — Agent コードに分野・論文固有のキーワードを書かない。
2. **語彙はカートリッジから** — concept type / relation / component type / support status / maturity は JSON 定義。
3. **不変・読み取り専用** — 実行時にカートリッジを書き換えない。
4. **バージョン管理** — 各 JSON は個別にバージョンを持てる。

---

[← PDF 解析 Agent 詳細](agents.md) ｜ 次へ: [DSL と理論操作グラフ →](theory-graph.md)
