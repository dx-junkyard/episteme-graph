# 管理画面 UX 課題（2026-08-01 起票 / 同日 方針確定・全件実装完了）

オーナー指摘の6件を実装調査のうえ分類した。**Q1〜Q3・I1〜I3 とも実装済み**（2026-08-01、
Fable 5 指揮 + Opus 5 サブエージェント A〜H。backend 7,659 + src 1,669 tests pass・0 fail。
docker E2E と migration 064 の実適用確認は未実施 — docker 復帰後に）。

| # | 対象 | 区分 | 状態 |
|---|---|---|---|
| Q1 | 教材アップロード「解析モデル」パネルの開閉が分かりにくい | すぐ直せる | **実装済み**（付録参照） |
| Q2 | 原稿スタジオ「AI アシスタント」ボタン（右下ロボットとの重複疑い） | すぐ直せる | **実装済み**（別機能 → 「AI で書き換え」にリネーム） |
| Q3 | コース設定「音声生成」が即実行に見える | すぐ直せる | **実装済み**（確認パネルは既存 → 「音声生成…」） |
| **I1** | vision モデル指定の二重化とモデルカタログの意味論 | 方針確定 | **実装済み**（手番 A） |
| **I2** | アップロード済み教材「操作」列のボタン過多 | 方針確定 | **実装済み**（手番 B） |
| **I3** | パーツ表示の統一（起票元＝理論グラフのノード詳細ペイン） | 方針確定 | **実装済み**（手番 C〜H・全 Phase） |

---

## §0 実施計画（何をどうするのか）— 全手番 実施済み

I1〜I3 の決定事項を実施順に1箇所へまとめたもの。詳細と根拠は各節。
**A〜H すべて 2026-08-01 に実装済み。** 実装で確定した主な追加事項:

- **C**: 語彙正本は `frontend/public/js/element-vocab.js`（`window.ElementVocab`:
  kindLabel / elementTypeLabel / statusLabel）。旧6辞書は撤去済み（再定義はテストが禁止）。
- **D**: カードは `frontend/public/js/element-card.js`（`window.ElementCard`:
  render / bind / mount、VARIANT_EDITABLE / VARIANT_READONLY）。追加 opts =
  itemActions（ITEM 行の操作）/ laneTitles / metaBadges / reviewNotes。notes は
  editable のみ描画。readonly は candidate ITEM 非描画・confidence 非表示の二重ガード。
- **E**: グラフ詳細は「ローカル DTO 即時表示 → サーバ文脈の遅延マージ」（nodeId 照合の
  競合ガード付き）。ステージ日本語辞書は `LS_THEORY_STAGE_LABELS_JA`（旧 #337 の
  `LS_STAGE_LABELS_JA` を §3.4(b) の訳語で置換・統合）。DB UUID ⇄ graph node ID の
  不一致は `lsGraphCenterOnItem`（グラフ内選択 → 失敗時 W層オープン）で解決。
  エッジ詳細の「確信度」生数値は撤去（W8 と同旨）。
- **H**: migration **064**（deliberation_sessions / element_annotations の element_type
  CHECK に evidence / derivation を追加）。できること／できないことの表は
  `element_deliberation_workspace_design.md` **§16** が正本。学習者投影は
  `core/element_context.py` が navigable を強制 false（app.js のホワイトリストと二重）。

**後続候補の実施状況（2026-08-01 追補）**: element-card.js の改善3件は**実装済み** —
① `hideHead` / `hideBody` opts（原稿スタジオ根拠リンクで使用。外殻カードとの
種別チップ・タイトル・要約の再掲を解消）② ITEM の `evidence_refs` を
カード本体が editable 限定の折りたたみ（`element-card-item-refs`）で描画
（W層の mount 後 DOM 後付け `_augmentContextEvidenceRefs` は撤去。グラフ詳細ペインにも
自動で効く = P2 の一貫性向上）③ `roleLabel` opts（根拠リンクは document スコープの
正確な主語「この論文での役割」に復帰。W層は既定の「この文脈での役割」のまま）。

**残る後続候補（未実施・必要になったら）**: E が残した旧グラフ詳細 CSS 約25ルールの削除
（background chip 起票済み）/ 面②位置づけ4レンズの evidence 対応
（技術的には可能・v1 は縮退のまま）。

| 手番 | やること | 対象ファイル | backend 変更 | 依存 |
|---|---|---|---|---|
| **A** | **vision 指定を1本化**（§1.5）。ステージ別テーブルから vision 行を外し、変更パネル上部の専用 select 1本だけにする。ステージ別テーブルは「テキスト系ステージの微調整」になる。 | `admin-llm-models.js` | なし | なし |
| **B** | **教材「操作」列を2層化**（§2.3）。行は「パイプラインを実行 ▼」＋「⋯」の2つ。残り8操作を「⋯」へ畳み、削除は区切り線の下＋赤字。異常時（`status=failed`）の解析再開・PDF再登録はステータス列のインライン導線へ移す。`data-ui-anchor` は menu-item へ付け替え。 | `admin.js` + `ADMIN_UI_ANCHORS` + マニュアル節 | なし | なし |
| **C** | **種別語彙を1本化**（§3.3 Phase 0）。正本1箇所を作り、フロント6辞書を委譲に置換。**表示文字列は変えない＝挙動不変**。食い違っていた訳語（論理要素/コンポーネント、主張/claim、図/図・画像）がここで揃う。 | `admin-lecture-studio.js` / `app.js` / `deliberation.js` | なし | なし |
| **D** | **統一パーツカードを実装**（Phase 1、編集可バリアント）。入力契約は `context_lens` の DTO 形（`{focus, upper, lower, notes}` + ITEM）に固定。カードは独自の取得をしない。 | 新規1ファイル | なし | C |
| **E** | **理論グラフ詳細ペインを差し替え**（Phase 2、**最大の受益点**）。クライアント側の場当たり解決 `lsGraphBuildResolver` を捨て、`GET /api/admin/deliberation/elements/{type}/{id}/context` を呼ぶ。DTO に `navigable` / `relation_label` があるため P3（辿れる）が自動で付く。§3.1(4)(a)〜(f) が同時に解消。ステージ名の日本語化（§3.4(b)）と出所バッジの上部移動（§3.4(c)）もここ。 | `admin-lecture-studio.js` | なし | D |
| **F** | **残る admin 画面を寄せる**（Phase 3）。原稿スタジオ根拠リンク・要素インベントリ・W層「深く検討」の要素表示を同カードへ。 | `admin-lecture-studio.js` / `deliberation.js` | なし | D |
| **G** | **学習者側に編集不可バリアントを適用**（Phase 4）。`core/element_context.py` の既存フィルタ（candidate 除去・confidence 除去・内部ID遮断）をそのまま使うため新規実装はほぼ無い。 | `app.js` | なし（既存 API） | D |
| **H** | **`evidence` / `derivation` を「中心にできる」要素にする**（Phase 5、§3.4(d) で実施決定）。現状は `element_id=None` / `navigable=false` で「表示され関係名も付くが再探索の起点にできない」。`refs.py` に解決を追加して P5 を完成させる。 | `core/deliberation/refs.py` ほか | **あり** | E・F・G |

**独立して着手できるのは A / B / C の3つ**（互いに依存なし・backend 変更なし）。
D 以降は C を前提に一列。H のみ backend 変更を伴い、着手前に W層設計書
（`docs/features/element_deliberation_workspace_design.md`）への追記が必要。

### 前提として確認済みの事実（§3.1(3)(3b)）

- **取得層はすでに共通化されている。** `GET /api/admin/deliberation/elements/{type}/{id}/context`
  が単一要素の「パーツ＋近傍」正規化DTOを返す。ITEM の `element_type` は11種で
  **evidence / derivation を含む**。
- **編集可／不可の2バリアントも前例がある。** `core/element_context.py` は
  「W層 `context_lens` の投影を学習者向けにフィルタして返すだけ」。
- **分裂しているのは描画層だけ。** よって作業の性質は「取得の統一」ではなく
  「受け側を既にある正規化DTOに寄せる」。

---

## §1 (I1) vision モデル指定の二重化とモデルカタログの意味論

### 1.1 事実確認：同じ1ステージを2つのUIが指している

「図面・画像を解析する」でオプトインする vision 解析の実体は、パイプラインの
`apparatus_semantics` ステージ**1つだけ**（`LLM_STAGE_NAMES` 中で vision=true はこれのみ）。
これを指す UI が現在**2箇所**ある。

| UI | 書き込むキー | 保存スコープ | 表示 |
|---|---|---|---|
| 変更パネル > 「図の解析（vision）」select | scene `pipeline.vision` | **user 既定として永続保存**（`PUT /my-policies/pipeline.vision`） | `analyze_images` チェック時のみ表示 |
| 変更パネル > ステージ別に指定する > 「図の装置同定（vision）」行 | feature `pipeline:apparatus_semantics` | **その実行だけ（run-only）**・保存しない | チェック状態に関わらず**常に**表示 |

解決順は `orchestrator._resolve_stage_override_model()` が
`pipeline:apparatus_semantics` → `pipeline.vision` の順（ステージ別が勝つ）。

### 1.2 同期するのか（オーナーの問い）への回答

**片方向にだけ同期する。** 対称ではない。

- scene 側（select）を変えて保存すると、ステージ別行の「継承（〇〇）」表示にも反映される
  （`pipeline-stages` API の `effective` が同じ解決チェーンを通るため）。
- ステージ別行を変えても select 側には**何も起きない**（run-only なので保存されない）。
- 両方指定した場合はステージ別が勝つ。**この優先関係は画面上どこにも書かれていない。**

### 1.3 見つかった具体的な穴

- **(a) 優先関係が不可視**: 上下に並ぶ2つの select が「上書き関係」だと分からない。
  片方は永続・片方は今回だけ、という寿命の違いも文言にない。
- **(b) 効かない設定が置ける**: 「図面・画像を解析する」が **off** でも
  「図の装置同定（vision）」行は表示・選択できる。off のとき `apparatus_semantics` は
  `skipped_by_option` でスキップされるので、選んだモデルは黙って無視される。
- **(c) 名前が対応していない**: 同じものが「図の解析（vision）」と「図の装置同定（vision）」で
  別名になっている。

### 1.4 モデルカタログ（gpt-image-1 / gpt-image-2 について）

**`gpt-image-1` は画像“生成”モデル（text→image）で、画像“理解”には使えない。**
本システムの vision 経路は `core/llm.py::generate_structured_with_images()` で
「画像を入力して JSON を返す」multimodal chat 呼び出しなので、必要なのは
capability に `vision` を持つ chat モデル（現行カタログでは `gpt-4o` / `gpt-5.2`）。
`gpt-image-1` を選べるようにしても 400 系で失敗する。→ **カタログ設計は現行のままが正しい。**

ただし別の懸念として、`backend/config/llm_models.json` の
`gpt-5.4` エントリは `note` が「vision 対応なら capabilities に "vision" を追加してください」
というテンプレート文のまま残っている（=カタログが雛形状態）。実デプロイのモデルIDと
capability の突合は別途要確認。

### 1.5 決定（2026-08-01 承認）

**「1つの設定＝1つのUI」に統一する。**

1. ステージ別テーブルから **vision 行（図の装置同定）を外す**。vision の指定は変更パネル
   上部の専用 select 1本だけにする。→ §1.3 の (a) 優先関係の不可視 / (b) 効かない設定が
   置ける / (c) 名前の不一致 が同時に解消する。
2. ステージ別テーブルの意味は「**テキスト系ステージの微調整**」に単純化される。
3. `analyze_images` が off のときは vision select ごと非表示（現状の挙動を維持）。
4. モデルカタログは**現行方針のまま**（vision capability を持つ chat モデルのみ）。
   `gpt-image-1` 系は画像生成モデルなので選択肢に入れない（§1.4）。

失うのは「画像解析 off のまま vision モデルだけ今回限りで変えたい」ケースのみ。

**別途要確認（本件とは独立）**: `backend/config/llm_models.json` の `gpt-5.4` エントリの
`note` がテンプレート文のまま残っている。実デプロイのモデルIDと capability の突合。

---

## §2 (I2) アップロード済み教材「操作」列のボタン過多

### 2.1 事実確認

1行の「操作」セルに最大**9個**の操作が横並びする（`admin.js::renderMaterials`）。

```
[パイプラインを実行 ▼] [PDF再登録] [解析再開] [図・画像] [検出要素]
[共有設定] [版の管理] [解析コスト見積り] [削除]
```

- 先頭の「パイプラインを実行 ▼」だけが既にドロップダウン（`ls-action-menu` パターン）。
  **同じ画面内にグルーピングの前例がある。**
- 「共有設定」と「版の管理」は別機能だが名前が近く、title 属性で
  「（共有設定とは別機能）」と注記して区別している＝ラベルだけでは区別できていない。
- 「解析再開」は `status=failed` のときだけ増えるので、行によって幅が変わる。
- ボタンは個別に inline style を持ち、`admin-action-btn` に揃っていない（見た目の不統一）。

### 2.2 制約（再編時に必ず守るもの）

- `data-ui-anchor` の**双方向網羅テスト**（`test_admin_help_inspect_ui_static.py`）:
  `KNOWN_ADMIN_UI_ANCHOR_IDS` の全IDに frontend 側の担体が必要／frontend の全
  `data-ui-anchor` 値が KNOWN に登録済みでなければならない。**1属性1ID。**
  → ボタンを消す・畳む場合も anchor 担体は残す（メニュー項目に付け替える）。
- マニュアル3点セット（`docs/manual/teacher/1x-admin-*.md` の節 + `ADMIN_UI_ANCHORS` +
  `data-ui-anchor`）の同時更新が必要。「操作要素1つ=1節」規約。
- 現在の materials 行 anchor: `materials.row-pipeline-run` / `row-pdf-reupload` /
  `row-resume-analysis` / `row-figures` / `row-inventory` / `row-share` / `row-version` /
  `row-estimate` / `row-delete` / `row-retry-stage`。

### 2.3 決定（2026-08-01 承認）

**頻度で2層に分ける。行に出しっぱなしにするのは2つだけ。**

1. 行に残すのは **「パイプラインを実行 ▼」＋「⋯」メニュー** の2つ。
2. 「⋯」に畳む: 図・画像 / 検出要素 / 共有設定 / 版の管理 / 解析コスト見積り /
   PDF再登録 / 解析再開 / 削除。**削除は区切り線の下＋赤字**で分離する。
3. **異常時の導線はメニューに畳まない**。`status=failed` の解析再開・PDF再登録失敗は
   **ステータス列側のインライン導線**（既存の「ステージ再実行」リンクと同じ形）へ寄せる。
   → 畳むことで異常に気づけなくなるのを防ぐ。
4. ボタンの個別 inline style をやめ `admin-action-btn` に揃える。

既存メニュー実装（`ls-action-menu` + `bindMaterialPipelineMenus`）を再利用し、
`data-ui-anchor` は menu-item へ**付け替える**（§2.2 の双方向網羅テストを満たすため
担体を消さない）。マニュアル3点セットも同時更新する。

#### 2.3 追補（2026-09-06 オーナー指示）— 高頻度2入口をアイコンで行に戻す

「2つだけ」の原則は維持したうえで、利用頻度が高い **グラフレビュー** と **近い論文を探す
（論文レーダー）** の2入口だけを `⋯` メニューから**行のアイコンボタン**へ昇格した。
行に常に出るのは「パイプラインを実行 ▼」→ グラフアイコン → 📡 → `⋯` の4つ（2アイコンは
document_id のある行のみ）。

- **アイコン**: レーダーは従来どおり 📡。グラフレビューは inline SVG のノード・辺図形
  （`currentColor`、`.material-row-icon`）。候補に挙げた 🕸（蜘蛛の巣に読める）・⚛（物理の
  記号に読める）・🔗（URL に読める）・📊（統計グラフに読める）は意味の取り違えが起きるため
  採用しなかった。ラベルは `title` / `aria-label` で持つ（アイコンのみのボタンにテキストを
  併記しない）。
- **不変**: `data-ui-anchor`（`materials.row-graph-review` / `materials.row-radar`）・
  クリックハンドラ・document_id ガード・モーダル側。`⋯` メニューは残りの操作をそのまま持つ。
- **Copilot 道案内**: 行アイコンを指す capability は `material_row_menu` ステップを挟まない
  （`test_admin_assistant.py::test_row_icon_capabilities_do_not_require_the_menu`）。
  レーダーの論理アンカーは `paper_radar_row_menu` → `paper_radar_row_button` に改名。
- **3点セット**: `docs/manual/teacher/11-admin-materials.md`（`#row-actions` / `#row-more-menu` /
  `#radar-open`）・`26-admin-graph-review.md#graph-review-open`・`docs/admin_operations/materials.md`
  を同時更新。ADMIN_UI_ANCHORS の ID・件数は変えていない。

---

## §3 (I3) パーツ表示の統一 — 理論グラフのノード詳細ペインを起点に

起票のきっかけは「文書構造 > 理論グラフ > グラフ でノードを選ぶと、右の解説領域が
何を説明しているのか分からない」こと。調査の結果、原因は個別画面の作りではなく
**同じ要素を描くコードが系統ごとに分岐していること**だったため、
**システム全体のパーツ表示方針**として扱う（2026-08-01 オーナー指示）。

### 3.0 方針（確定）

グラフ構造を持つ意味は「どこに出しても辿れること」にある。したがって:

- **P1 種別語彙は1つ**。要素種別の表示名は単一の正本を参照する。同じ要素が画面に
  よって別名になることを許さない。
- **P2 パーツの表示形式は出現箇所によらず同一**。グラフ全体を見せるか1パーツだけ
  見せるかの違いはあってよいが、**パーツ1個の描き方は同じ**にする。
- **P3 どこに出してもグラフ近傍が辿れる**。パーツを表示する以上、そのパーツが何に
  支えられ何につながるかへ移動できること。これがグラフ構造を持つ意味そのもの。
- **P4 差異は編集権限の有無だけ**。「必要なものを全て備えたパーツ表示」を
  **編集可（教員・管理者）／編集不可（学習者）の2バリアント**で作り、システム全体で
  それを使う。画面ごとの独自表示を新規に作らない。
- **P5 evidence と derivation も備える**。グラフ固有だからと語彙から外さない
  （外すと P1〜P3 がその2種別で破れる）。

### 3.1 現状のギャップ（実測）

#### (1) 種別語彙が6系統に分裂し、訳語が食い違っている

| 定義箇所 | 対象 | 実際の訳語 |
|---|---|---|
| `admin-lecture-studio.js` `LS_EVIDENCE_KIND_LABELS` | 原稿スタジオ 根拠リンク | component=**論理要素** / claim=**主張** / equation=数式 / figure=図 / source=出典 |
| `admin-lecture-studio.js` グラフ詳細の ref 行ラベル（引数直書き） | 理論グラフ 詳細ペイン | **式** / **claim** / **evidence** / **derivation** / **関連要素** |
| `app.js` `MATERIAL_EVIDENCE_KIND_LABELS` | 学習者 教材内チップ | 1と同一内容の**コピー** |
| `deliberation.js` `ELEMENT_TYPE_LABELS` | W層「深く検討」 | theory_component=**コンポーネント** / theory_claim=**claim** |
| `deliberation.js` `INVENTORY_TYPE_CHIP_LABELS` | 要素インベントリ フィルタ | theory_component=論理要素 / figure=**図・画像** |
| `deliberation.js` `INVENTORY_TYPE_BADGE_LABELS` | 要素インベントリ 行 | figure=**図** |

同じ `theory_component` が「論理要素」「コンポーネント」、同じ `theory_claim` が
「主張」「claim」と**画面によって別名**になっている。P1 違反の実証。

#### (2) 「辿れるか」が出現箇所ごとにばらばら

| 出現箇所 | 近傍への移動 |
|---|---|
| 理論グラフ 詳細ペイン | **可**（`lsGraphNavigateToNode` — チップから選択+フォーカス） |
| 原稿スタジオ 根拠リンクカード | **不可**（操作は「ドラフトの該当箇所へ」「深く検討」のみ） |
| W層「深く検討」 文脈レンズ | **可**（上位/中心/下位レーン + パンくず中心移動, #498） |
| 学習者 component チップ展開 | **可**（context API の `graph` 1-hop） |
| 学習者 claim / equation チップ | 文脈 API はあるが**グラフ射影なし** |
| 要素インベントリ | **不可**（一覧 → 深く検討へ渡すだけ） |

P3 は半分しか成り立っていない。

#### (3) evidence / derivation は「表示できるが中心にできない」

> 【訂正 2026-08-01】本節は当初「evidence / derivation が W層語彙に無い」と記載して
> いたが、これは不正確だった。**2つの語彙を混同していた**ので下記に是正する。
> この訂正により、旧 Phase 4（backend 語彙拡張）は前提条件ではなく**任意の最終段**になる。

W層には**役割の違う語彙が2つ**ある。

| 語彙 | 正本 | 用途 | evidence / derivation |
|---|---|---|---|
| **表示・近傍の語彙（11種）** | `core/deliberation/context_lens.py` の ITEM | パーツを描き、関係を名付け、近傍を辿る | **含む**（`rests_on_evidence` / `belongs_to_derivation` として実際に出力。他に `section` / `thesis` / `symbol` / `stage` / `part` も持つ） |
| **解決対象の語彙（5種）** | `core/deliberation/refs.py` の `element_type` | 「深く検討」の対象として同定・注釈を積む | **含まない**（`figure` / `theory_component` / `theory_claim` / `equation` / `shared_part` のみ） |

したがって現状は「evidence / derivation が無い」のではなく、
**表示され関係名も付くが、`element_id=None` / `navigable=false` のため
その要素を中心にした再探索の起点にできない**という状態。

→ **P5 のうち「表示・関係の明示」は既存DTOで今すぐ満たせる。**
「中心にできる」ようにする部分だけが `refs.py` の拡張を要する（§3.3 Phase 4）。

#### (3b) 取得はすでに共通化されている — 分裂しているのは描画層だけ

共通化の障害は「取得方法が画面ごとに違うこと」**ではない**。層で分けると:

| 層 | 状態 |
|---|---|
| **取得（サーバ）** | **共通化済み。** `GET /api/admin/deliberation/elements/{element_type}/{element_id}/context`（`routes/deliberation.py::get_element_context`）が単一要素について `{focus, upper, lower, notes}` を返す。ITEM は `element_type` / `label` / `relation` / `relation_label` / `relation_status` / `evidence_refs` / `navigable`。**「パーツ＋近傍」の正規化DTOが既に存在する。** |
| **権限バリアント** | **前例が既にある。** `core/element_context.py` は「W層 `context_lens` の投影を学習者向けにフィルタして返すだけ」に責務を絞った実装（candidate 除去・confidence 除去・内部ID遮断）。**P4（差異は編集権限の有無だけ）が既に1箇所で成立している。** |
| **描画（フロント）** | **ここだけが分裂。** 4ファイル・6語彙辞書（上記 (1)）で別々に描いている。 |

**理論グラフ詳細ペインはサーバDTOを使っていない唯一の画面**で、
`lsGraphBuildResolver()` によるクライアント側の場当たり解決
（「その時ロード済みの chunk / claim / equation」から名前を引く）に依存している。
引けなければ生ID のまま出る。行ラベルの日英混在も、ここで引数に直書きしているため。
**同じノードについて W層「深く検討」はサーバ側で整えた情報を既に持っており、
二重実装のうち劣化した側が表に出ている**。これが §3.1(4) の症状の根本原因。

→ 作業の性質は「取得の統一」ではなく「**受け側を既にある正規化DTOに寄せる**」。

#### (4) 起票元の症状（ノード自身の説明部分）

パーツ表示の統一とは別レイヤーの、詳細ペイン固有の不具合。

- **(a)** 「選択中の要素」という枠の見出しがない。未選択時の `N nodes / M edges` が
  選択で全差し替えになるため、ペインがグラフ全体の説明か選択物の説明か区別できない。
- **(b)** 見出しが英語のステージ名（`THEORY_STAGE_LABELS` = `Theory basis` /
  `Equation system` / `Elimination` / `Diagnostic / application` …）。日本語UIの中で
  ここだけ英語で、しかも抽象語なので内容を示さない。
- **(c)** ロールバッジ（`lsGraphRoleLabel`）の直下に「役割: 〇〇」（同じ関数）。
  同じ語が縦に2度並ぶ。
- **(d)** `description` 空のとき fallback で見出しと同じ文字列が「説明」欄に再掲される
  （「Equation system」の説明が「Equation system」）。main node は集約なので空になりやすい。
- **(e)** 根拠リンクの行ラベルが内部語彙のまま（上記 (1) の2行目）。
- **(f)** `source_backing_status`（source_backed / partially / inferred）が
  「要確認事項」の中＝**一番下**。出所の正直さの原則からすると先に見えるべき。

### 3.2 統一パーツカードの仕様

既存の原稿スタジオ根拠リンクカード（`lsCourseEvidenceCardHtml`）を**下敷き**にする
（P2 の実装母体として最も完成している）。構成:

```
[種別チップ] タイトル                                    ▸   ← ヘッダ（開閉トグル）
  本文（要約／数式は KaTeX／図はサムネイル）
  [出所バッジ] 役割 / 対応付け                              ← meta 行
  ── 近傍（遅延読み込み）───────────────────────
    ← 支えるもの   [チップ][チップ]                        ← P3。クリックで中心移動
    → 支えられるもの [チップ]
  ── 操作 ─────────────────────────────────
    [この要素を中心にする] [深く検討]                       ← 編集可バリアントのみ
```

#### 2バリアントの差分（P4）

差分は**編集権限の有無から導かれるもののみ**とし、各層に散っている既存の非表示規則を
このコンポーネントに集約する（新しい規則を発明しない）。

| 要素 | 編集可（教員・管理者） | 編集不可（学習者） |
|---|---|---|
| 種別チップ・タイトル・本文 | 表示 | 表示 |
| 近傍（P3） | 表示 | 表示 |
| `role` / `confidence`（対応付け来歴） | 表示 | **非表示**（既存不変条項） |
| confidence の生数値 | 非表示（段階ラベルのみ, W8） | 非表示 |
| `status='candidate'` の注釈・候補 | 表示（候補として明示） | **非表示**（承認済みのみ） |
| `review_status` / 要確認事項 | 表示 | **非表示** |
| 出所バッジ（source_backed 等） | 表示 | 事実文のみで表示 |
| 「深く検討」「中心にする」等の操作 | 表示 | 非表示（読み取り専用） |

#### 語彙の正本（P1・P5）

種別 → 表示名を1箇所に置き、フロント3ファイル（`admin-lecture-studio.js` /
`app.js` / `deliberation.js`）の6辞書をそこへ委譲する。新規2種別の訳語は
`evidence`=「根拠箇所」/ `derivation`=「導出」（§3.4(a) で確定）。
内部キーは backend の `element_type` に合わせる
（`theory_component` / `theory_claim` / `equation` / `figure` / `shared_part` /
`evidence` / `derivation` / `source`）。

### 3.3 段階

§3.1(3b) のとおり**取得層は既に共通化されている**ため、作業の実体は
「フロントの描画を1つに寄せる」。backend 変更は最終段の Phase 4 のみで、
しかもそれは任意（§3.4(d)）。

| Phase | 内容 | 影響範囲 | backend 変更 |
|---|---|---|---|
| 0 | 語彙の正本を1箇所に作り、既存6辞書を委譲に置換する（**表示文字列は変えない＝挙動不変のリファクタ**）。食い違っていた訳語だけがこの時点で統一される。 | フロント3ファイル | なし |
| 1 | 統一パーツカードを実装（編集可バリアント）。**入力契約は `context_lens` の DTO 形（`{focus, upper, lower, notes}` + ITEM）に固定する** — カードは独自の取得をしない。 | 新規1ファイル | なし |
| 2 | 理論グラフ詳細ペインを差し替える。**クライアント側の場当たり解決（`lsGraphBuildResolver`）を捨て、`GET /api/admin/deliberation/elements/{type}/{id}/context` を呼ぶ**。DTO に `navigable` / `relation_label` があるため **P3（辿れる）は自動的に付く**。§3.1(4)(a)〜(f) を同時に解消。 | `admin-lecture-studio.js` | なし |
| 3 | 原稿スタジオ根拠リンク・要素インベントリ・W層「深く検討」の要素表示を同カードへ寄せる。 | `admin-lecture-studio.js` / `deliberation.js` | なし |
| 4 | 編集不可バリアントを学習者側（教材内チップ展開・出典タブ）へ適用。**`core/element_context.py` の既存フィルタをそのまま使うため新規実装はほぼ無い**。 | `app.js` | なし（既存 API） |
| 5（任意） | `evidence` / `derivation` を `refs.py` の解決対象に追加し、**その2種別も「中心にできる」ようにする**（P5 の完成形）。カード上で「深く検討」も成立する。 | `core/deliberation/refs.py` ほか | **あり** |

- Phase 0 は挙動不変なので単独で先行できる。
- **Phase 2 が最大の受益点**（起票元の症状がここで消え、同時に二重実装が1つ減る）。
- Phase 5 は backend の語彙拡張を伴うため、着手前に W層設計書
  （`element_deliberation_workspace_design.md`）への追記が必要。

### 3.4 決定（2026-08-01 承認）

- **(a) 新規2種別の訳語**: `evidence` → **「根拠箇所」** / `derivation` → **「導出」**。
- **(b) ステージ名の日本語訳**: `THEORY_STAGE_LABELS` を下表で当てる。
  変換は**表示層のみ**で行い、CLAUDE.md の「main label は短い stage label」原則は保つ
  （DB・API・agent 出力は英語のまま）。

  | 内部 stage | 現在の表示（英語） | 新しい表示（日本語） |
  |---|---|---|
  | `theory_basis` | Theory basis | 理論的前提 |
  | `observation_model` | Observation model | 観測モデル |
  | `observable_construction` | Observable construction | 観測量の構成 |
  | `equation_system` | Equation system | 式系 |
  | `elimination` | Elimination | 消去 |
  | `consistency_relation` | Consistency relation | 整合関係 |
  | `diagnostic_application` | Diagnostic / application | 診断・応用 |

- **(c) 出所バッジ**: meta 行（上部）に出す。事実文のみ・煽らない方針は維持し、
  ⚠ アイコンの多用は避けて段階ラベルで表す。
- **(d) Phase 5（`evidence` / `derivation` を「中心にできる」要素にする）**: **実施する**
  （P5「evidence と derivation も備える」の完成形として最終段に置く）。
  ただし Phase 0〜4 は backend 変更なしで先行でき、Phase 5 のみ `refs.py` 拡張を伴う。

---

## 付録：すぐ直せる3件の中身（記録用）

- **Q1 解析モデルパネル**: `[変更]` は `toggleMaterialsPanel()` のトグルなので再クリックで
  閉じるが、ラベルが「変更」のまま／パネル内に閉じるボタンが無い／パネル冒頭の見出し
  「解析モデル」が直上のサマリ行と重複、の3点で閉じ方が伝わっていない。
  「▸ ステージ別に指定する（詳細）」は `<div>` に `cursor:pointer` だけでボタン体裁がない。
  → ラベルを `変更`/`閉じる` でトグル、パネル内見出しの重複を解消、トグルをボタン体裁に。
- **Q2 「AI アシスタント」ボタン**: 右下ロボット（Admin Copilot・全タブ横断の道案内/代行）とは
  **別物**。`ls-ai-assistant-btn` は `ls-assistant-modal`＝「書き換え指示」を入力して
  原稿/トピック本文を AI 書き換えするモーダル（`ls-rewrite-prompt` → `AIで提案`）。
  → 削除ではなく**リネームで衝突解消**（例「AI で書き換え」）。モーダル見出し
  `<h3>AI アシスタント</h3>` も同時に変更。
- **Q3 「音声生成」**: 既に即実行ではなく `ls-audio-lang-modal`（見出し「音声を生成します」）を
  開く。コース名・対象スライド数・現状の生成済み枚数・読み上げ言語（日本語/English）・
  言語切替時の警告・`[生成を開始]` を持つ確認パネル（Issue #491）。
  → 実装追加は不要。メニュー項目を「音声生成…」にしてダイアログが続くことを示すだけでよい。
  なお準備未完了時はボタンが無効になり理由が近傍に出る仕様のため、
  **無効状態しか見ていない場合はモーダルに到達しない**（この点も体験上の分かりにくさ）。
