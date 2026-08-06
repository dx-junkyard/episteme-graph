# 学習者向け claim / equation 文脈API — 要素中心コンテキストの学習者提示

> 状態: バックエンド + フロントエンド実装済み(2026-07-31)。実装記録は §8、
> レビュー指摘対応は §8.4。
> 対象: 学習UI教材内の `![[claim:id]]` / `![[equation:id]]` 埋め込み要素からの文脈展開。
> 関連文書: `docs/features/component_evidence_redesign.md`(component 版の先行実装・
> Phase 2/3)、`docs/features/element_context_lens_design.md`(W層の要素中心コンテキスト
> レンズ = 本APIが投影する下地)、`docs/features/knowledge_network_vision.md` §1-6
> (「関心のあるネットワークをたどる旅」)、
> `docs/features/hierarchical_context_explanation_design.md`(ギャップ (d)(e))。

---

## 1. 問題

`component_evidence_redesign.md` Phase 2/3 で、学習者は **component** についてだけ
「この論文の中でどういう役割を持ち、何を前提・入出力とし、どの claim/equation に
裏付けられ、周辺構造とどうつながっているか」をオンデマンドで開けるようになった
(`GET /api/learning/courses/{course_id}/components/{component_id}/context`)。

一方、教材本文に同じ密度で現れる **claim** と **equation** には文脈導線が無い。
学習者が数式チップや主張チップに触れても、得られるのは snapshot に凍結された
本文抜粋(`build_topic_evidence_items` の excerpt)だけで、

- その主張が中心命題のどこを支えているのか
- その式がどの式から導かれ、どの式につながるのか
- その式がどの主張を定量化しているのか

は辿れない。**データは既に存在する**: W層の要素中心コンテキストレンズ
(`core/deliberation/context_lens.py`、Issue #498)が claim / equation について
`focus` / `upper` / `lower` / `notes` の投影を持っており、`_build_claim` は
thesis 支持構造・掲載セクション・親/子 claim・裏付け component・定量化する式・
図・evidence・導出所属を、`_build_equation` は定量化する claim・中心命題・
理論コンポーネント・入力式/出力式・記号・導出所属を組み立てている。
しかしこれは教員(TEACHER 以上)向けの `/api/admin/deliberation/...` からしか
読めない。

つまり欠けているのは投影ロジックではなく、**学習者向けの権限ゲートと
「学習者に出してよい情報」への絞り込み**である。

## 2. 方針と不変条項

component 版で確立した「守るべき公理は *承認済みのみ・コーススコープ・fail-closed* で
あって *snapshot完結* ではない」(`component_evidence_redesign.md` §2.4)をそのまま
継承し、claim / equation へ横展開する。

| # | 不変条項 | 実装上の担保 |
|---|---|---|
| LE1 | **コース公開(freeze) = ソース文書内 1-hop 近傍の露出承認** | component 版 §6 のガバナンス決定をそのまま適用。露出範囲は「コースの sources に含まれる document 内の1階層」に限る |
| LE2 | **candidate を学習者に出さない** | `upper` / `lower` から `relation_status == "candidate"` を除外。`focus.contextual_role` も source_backed / confirmed のときだけ残す |
| LE3 | **数値を見せない(W8)** | レスポンスを再帰走査して `confidence` キーを除去(`component_context.strip_confidence` を共有) |
| LE4 | **裸の内部IDを出さない** | ITEM の `evidence_refs`(evidence_id / step_id)・`relation`(内部語彙キー)・`focus.provenance`(`theory_claims:<uuid>` 等)を落とす |
| LE5 | **404 統一の fail-closed** | 受講不可・コース外 document・未知の element_type / element_id はすべて 404。存在の有無を漏らさない |
| LE6 | **A層/W層のコードを変更しない** | `src/episteme_graph/agents/` と `core/deliberation/` は読むだけ。本APIは投影のフィルタ層 |
| LE7 | **書き込みAPIを作らない** | `core/element_context.py` に INSERT/UPDATE/DELETE 経路は無い。学習者からの疑義・注釈は本APIのスコープ外(§7) |
| LE8 | **文脈が無いことは異常ではない** | 要素は解決できたが投影が得られない場合は 500 でも 404 でもなく 200 + `available:false` + 事実文 |

### なぜ component 版の DTO を再利用しないのか

component の文脈 DTO は `instance`(ComponentRecord 由来の rich 投影: preconditions /
inputs / outputs / cautions / equations の役割分類 / dependencies)・`shared_part`
(L層の共通部品)・`graph`(W層 1-hop)の三層構造を持つ。claim / equation には
ComponentRecord に相当する固有の rich 投影が無く、**上位・下位の関係そのものが
提示価値の中心**である。したがって本APIは W層 context lens の投影を素直に
`focus` / `upper` / `lower` / `notes` の形で通す1層構造にし、component 版の
`graph` ブロックと同じ射影規則(candidate 除外・内部ID除去)を適用する。
`focus.generic`(confirmed な同一性リンク先の active な L層エントリ)は context lens が
既に組み立てているため、component 版の `shared_part` に相当する情報はそこから得られる。

## 3. API 契約

```
GET /api/learning/courses/{course_id}/elements/{element_type}/{element_id}/context
```

- `element_type ∈ {"claim", "equation"}`。それ以外は **404**
  (W層の内部語彙 `theory_claim` も 404 — 公開語彙は短い `claim` だけ)
- `element_id`:
  - `claim` … `theory_claims.id`(DB UUID) と agent 側 ID(`claim_span_007` 等、
    `source_scope.legacy_ids`)の**両対応**。ただし agent 側 ID がコース内で複数行に
    一致する場合は解決しない(下記「曖昧時 fail-closed」)
  - `equation` … `equations.json` の `equation_id`(`eq_2_7` 等)

### ゲート(3条件・fail-closed)

component 文脈 API / 学習者向け図配信 API(Phase 4)と同一:

1. `get_accessible_course_data(user_id, course_id)` が空 → 404 `Course not found`
2. 要素の document がコースの document 集合(`_course_document_ids(course_data)`)に
   含まれる。**SQL 内 `document_id = ANY(:doc_ids)` で強制**(後付けの Python
   フィルタにしない)。equation は独立テーブルを持たないため、走査対象自体を
   コース document 集合に限る
3. 要素が解決できる → できなければ 404 `Element not found`

条件2を SQL 側で強制する理由: agent 側 ID(`claim_span_007` / `eq_2_7`)は論文ごとに
独立採番されるため文書間で衝突しうる。コース外文書の同名要素へ誤って一致する余地を
構造的に断つ(`component_context._resolve_component_row` と同型)。

#### 曖昧時 fail-closed(claim の agent 側 ID)

コース document スコープに絞っても **コース内で複数一致する** ケースが残る。claim の
agent 側 ID は `claim_object_builder/builder.py::_make_claim_id` が span_id から作るが、
span_id は block ごとに振り直されるため文書内でも文書間でも反復し、`legacy_ids` は
重複回避のカウンタ接尾辞を持たない。したがって複数文書をソースに持つコースでは
`claim_span_001` が**別論文の別 claim** に一致し得る。

そこで解決規則を次のように確定する(W層 `core/deliberation/refs.py::_resolve_by_legacy_id`
が「単一 document 必須」で同じ問題を断っているのと整合させる):

| 一致の形 | 扱い |
|---|---|
| `theory_claims.id`(UUID)完全一致 | 一意なので**即決**(他文書が同じ文字列を `legacy_ids` に持っていても曖昧扱いにしない) |
| `legacy_ids` 一致が **ちょうど1行** | 解決 |
| `legacy_ids` 一致が **複数行**(別 document にまたがる / 同一 document 内で複数) | **解決しない**(`None` → 404 → フロントは既存の「文脈情報はまだありません。」へ縮退) |

「たまたま最初に返った行」を *出典に裏付け* バッジ付きの文脈として学習者に見せない
(誤った論文の主張を確定情報として提示するのは、文脈が出ないことより有害)。候補取得の
`ORDER BY` は `(id::text = :raw_id) DESC, document_id ASC, created_at ASC, id::text ASC` で
**決定的**にし、実行計画依存の非決定を排除する。

### レスポンス 200(成功)

```json
{
  "available": true,
  "element_type": "claim",
  "element_id": "<解決済みID: claim は DB UUID / equation は equation_id>",
  "focus": {
    "element_type": "claim",
    "element_id": "<解決済みID>",
    "document_id": "<documents.id>",
    "label": "…",
    "intrinsic_summary": "…",
    "contextual_role": "…",            // source_backed / confirmed のときのみ
    "contextual_role_status": "source_backed",  // 同上
    "generic": {                        // confirmed 同一性リンク + active な L層エントリのみ
      "entry_id": "…", "name": "…", "summary": "…",
      "standardization_status": "field_standard"
    }
  },
  "upper": [ITEM, ...],
  "lower": [ITEM, ...],
  "notes": ["…"],
  "provenance": "course_freeze"
}
```

```
ITEM = {
  "id": str | null,            // null = 表示のみ(非ナビゲーション)
  "element_type": "theory_claim"|"theory_component"|"equation"|"figure"|
                  "section"|"thesis"|"derivation"|"symbol"|"evidence"|"stage"|...,
  "label": str,                // 裸の内部ID形は一般ラベルへ置換済み(§4)
  "relation_label": str,       // 「を定量化する」等の読み手向け動詞句(主語は常に focus)
  "relation_status": "source_backed"|"confirmed",   // candidate は現れない
  "navigable": bool            // true のとき本APIまたは component 文脈APIで
                               // 学習者が再フェッチできる(= 旅の続き)
}
```

`element_type` は W層の内部語彙をそのまま通す(フロントが自前で表示語彙・
`data-evidence-ref` の kind へ変換している)。一方 `navigable` は**学習者側の実フェッチ
可能性**で作り直す(W層の `_NAVIGABLE_ELEMENT_TYPES` は教員向け deliberation モーダルで
開けるかどうかの値で、学習者向けの取得口とは一致しない):

| element_type | navigable | 学習者の取得口 |
|---|---|---|
| `theory_claim` | ✓ | 本API(`element_type=claim` として) |
| `equation` | ✓ | 本API |
| `theory_component` | ✓ | `/api/learning/courses/{id}/components/{cid}/context` |
| `figure` / `section` / `thesis` / `derivation` / `symbol` / `evidence` / `stage` / `part` | — | 学習者向けの文脈取得口が無い(図は配信APIのみで文脈APIが無い) |

`id` が空の項目は型に関わらず `navigable:false`。

### レスポンス 200(縮退)

```json
{"available": false, "note": "この要素の文脈情報は現在表示できません。"}
```

要素は解決できた(= 権限は通った)が W層 context lens が投影を返せない / 例外だった
場合。フロントは文脈欄ごと非表示にすればよい(LE8)。

**`context_lens.build()` の内部 fail-soft 形も縮退に含める**: `build()` は builder が
`None` を返した / 例外だった場合にも **dict** (`_degenerate_result`)を返す(`focus.label`
= 生の element_id、レーン空、専用 note 1件)。これは投影ではなく「読めなかった」という
表明なので `available:true` で出してはならない(出すと `focus.label` が生 UUID になる)。
W層は変更しない(LE6)ため、`element_context` 側で**形状から検出**する。判定は保守的に
次の4条件**すべて**を要求し、正常だが疎な投影(上位・下位が空でもラベル・本文が
引けている投影)を誤って縮退させない:

1. `notes` に degenerate 専用の文言が含まれる(`context_lens.py` 内でこの文言を使うのは
   `_degenerate_result` の1箇所だけ = 実質的なマーカー)
2. `focus.label` が解決済み element_id の生値そのもの
3. `focus.intrinsic_summary` が空
4. `upper` / `lower` がともに空

## 4. フィルタ仕様

`core/deliberation/context_lens.py` の生の投影に対し、`core/element_context.py` が
以下の変換を行う。**これが学習者向けAPIと教員向けAPIの唯一の差分**である。

| 対象 | 規則 | 理由 |
|---|---|---|
| `upper` / `lower` の各 ITEM | `relation_status == "candidate"` を**除外** | candidate は教員確定前の AI 提案(W2)。学習者に確定情報として出さない。component 版 `_build_graph` と同じ原則 |
| `focus.contextual_role` / `contextual_role_status` | `contextual_role_status ∈ {source_backed, confirmed}` かつ role が非空のときのみ残す。candidate / unidentified は**キー自体を落とす** | 推測で穴埋めしない。「未同定」という内部状態語彙を学習者に見せる必要も無い |
| 全レスポンス | `confidence` キーを**再帰除去**(`component_context.strip_confidence`) | W8。数値は段階ラベルすら出さない |
| ITEM の `evidence_refs` / `relation` / `document_id`、`focus.provenance` | **落とす** | 内部参照ID(evidence_id / step_id / span_id)と内部語彙キーを学習者に出さない。`focus.document_id` のみ残す(component 版が `in_paper.document.id` を既に出しているため整合) |
| ITEM の `label` | **裸の内部ID形なら element_type 別の一般ラベルへ置換**(下表)。`relation_label` は保持し項目自体は落とさない | W層はラベル(caption / claim 本文 / 記号)を引けなかった項目に内部IDをそのまま `label` として入れる(`_build_claim` の図 DB UUID・`ev_0001`・subclaim の agent 側ID、`_build_equation` の `synth_claim_0001`、thesis の `support:<section>:<idx>`)。W層は変更しない(LE6)ので学習者向け射影で遮る |
| `focus.contextual_role` | 内部IDトークンを含むなら**キーごと落とす** | 役割文は W層 `_derive_contextual_role` が `upper[0]` のラベルから合成するため、ラベル解決に失敗した項目がそのまま「この論文での役割」に出る(「synth_claim_0001を定量化する」)。candidate / unidentified と同じ「推測で穴埋めしない」縮退 |
| ITEM の `navigable` | 学習者の実フェッチ可能性で**再計算**(§3 の表) | W層の値は教員向けの可否。そのままでは `navigable:true` の契約(再フェッチできる)が成立しない |

#### label 規則の詳細

「裸の内部ID形」= 次のいずれかに単独で一致する label。

- UUID(`ffffffff-ffff-…`)
- `ev_0001` / `evidence_0001`(evidence_registry の採番)
- `synth_…`(合成 claim `synth_claim_0001`)
- `claim_…`(claim 生ID `claim_span_001` / `claim_0004`)
- `span_001`(rhetorical_role の span ID)
- `support:` 接頭(thesis の support node ID)
- `node_` 接頭(グラフノードID)
- label が ITEM の `id` の生値そのもの

置換先(関係語は保持するので「図 / を根拠とする」の形で意味は残る):

| element_type | 一般ラベル |
|---|---|
| `theory_claim` | 関連する主張 |
| `theory_component` | 関連する論理要素 |
| `equation` | 関連する数式 |
| `figure` | 図 |
| `evidence` | 本文の根拠箇所 |
| `section` | 掲載セクション |
| `thesis` | 中心命題 |
| `derivation` | 導出の流れ |
| `symbol` | 記号 |
| `stage` | 理論の段階 |
| `part` | 構成部品 |
| その他 | 関連する要素 |

**裁定(v1)**: `eq_2_7` 形(equation の式番号)は**置換しない**。論文の式番号に由来し
学習者にも可読で、`![[equation:eq_2_7]]` として教材本文にも現れる識別子だからである
(「内部ID」ではなく「論文の呼び名」として扱う)。

**既知の限界(v1)**: `導出「der_001」のステップ「step_003」` のように内部IDが日本語の
事実文に**埋め込まれた**ラベルは置換対象外(裸のID形ではないため)。W層の文面生成側で
直すべき問題なので LE6 の下では触らず、必要になったら別issueで W層に手を入れる。

レーン上限は component 版と同じ 20 件だが、**適用順序は「W層の cap → 学習者フィルタ」**
である。W層 `context_lens._cap_lane` が candidate を含んだまま 20 件へ切り、そのあとで
本モジュールが candidate を除外するため、**実際の表示件数は 20 件未満になり得る**
(candidate が上位20件に多く含まれていた場合)。これは W層を変更しない(LE6)ことを
優先した結果で、「candidate 除外後に 20 件」を保証するには W層の cap にフィルタを
渡す必要があるため v1 では採らない。

`notes`(「figure_table_semantics artifact が無いため図との関係を判定できません」等)は
事実文なのでそのまま通す。

## 5. 実装構成

```
backend/core/element_context.py                 ← 新規(FastAPI 非 import)
  SUPPORTED_ELEMENT_TYPES = ("claim", "equation")
  build_element_context(element_type, element_id, course_document_ids) -> dict | None
  _resolve_claim()      ← theory_claims を doc スコープ SQL で解決(UUID即決 /
                          legacy_ids は1行に定まるときのみ・複数一致は None)
  _resolve_equation()   ← コース document 集合を線形走査
                          (document ごとに document_run_artifacts を1回読み、
                           equation_records(doc, artifacts=…) へ渡す)
  _is_degenerate_lens() ← W層の fail-soft 形の検出(§3 の縮退)
  _visible_items() / _project_item() / _project_focus()   ← §4 のフィルタ
  _is_internal_id_label() / _generic_item_label()          ← §4 の label 規則

backend/core/component_context.py               ← 変更(strip_confidence を公開名に昇格)
backend/api/routes/learning.py                  ← 変更(endpoint 追加)
```

- `build_element_context` の戻り値は3値: `None`(解決不能 → route が 404) /
  `{"available": False, ...}`(縮退) / 完全な DTO。route はゲート判定と 404 マッピング
  だけを持ち、DTO の合成はすべて core に閉じる。
- ElementRef は `core/deliberation/schema.ElementRef` を直接構築する
  (`scope='document'` + 内部語彙 `theory_claim` / `equation` + 解決済み document_id)。
  `refs.resolve()` を経由しないのは、解決の際に**コース document スコープを
  SQL 条件として渡す必要がある**ため(`refs.resolve()` は単体の存在確認しか行わない)。
  構築後に `ref.validate()` を通し、context lens には教員向けと同じ ref を渡す。
- equation の document 走査は v1 では線形(コースの sources は通常数件)。document 数が
  増えて問題になるなら equation_id → document_id の索引化を検討する(§7)。走査中の
  `document_run_artifacts`(巨大 JSONB の SELECT)は document ごとに1回に抑える
  (`refs.equation_records(doc, artifacts=…)` の再利用引数を使う)。**既知の限界**:
  解決後に呼ぶ `context_lens.build()` が内部で同じ artifact を読み直す。W層を変更しない
  (LE6)ため v1 では受け入れる(1要求あたりの重複は1回)。

## 6. 受け入れ基準

- [x] 未受講コース / コース外 document の要素 / 未知の element_type がすべて 404
- [x] agent 側 claim ID(`claim_span_007`)が DB UUID と同様に解決できる
- [x] 同じ agent 側 ID がコース外文書にも存在するとき、コース内文書の行だけが解決される
- [x] **同じ agent 側 ID がコース内の2文書(または同一文書内の2行)に存在するときは
  解決せず 404**(曖昧時 fail-closed。別論文の claim を「出典に裏付け」として出さない)
- [x] **UUID 指定は複数文書環境でも常に当該行に解決される**(他文書が同じ文字列を
  `legacy_ids` に持っていても曖昧扱いにしない)
- [x] `relation_status == "candidate"` の関係がレスポンスに一切現れない
- [x] `contextual_role_status` が candidate / unidentified のとき role が現れない
- [x] **役割文に内部ID(`synth_claim_0001` / UUID / `support:` 等)が混ざるとき role が
  現れない**
- [x] **ITEM の label が裸の内部ID形のとき一般ラベルへ置換される**(項目自体は
  `relation_label` 付きで残る)。`eq_2_7` 形と通常のラベルは不変
- [x] `confidence` キーがレスポンスのどの深さにも現れない
- [x] `evidence_refs` がレスポンスに現れない
- [x] `navigable:true` は claim / equation / theory_component のみ(figure ほかは false)
- [x] context lens が None / 例外 / **内部 fail-soft 形(`_degenerate_result`)**のとき
  200 + `available:false`(疎だが正常な投影は縮退させない)
- [x] `core/element_context.py` が FastAPI / routes を import しない
- [x] `core/element_context.py` に書き込み(INSERT/UPDATE/DELETE)経路が無い

## 7. 非スコープ

- **学習者からの疑義・注釈・deliberation**: D層の疑義投稿は地位勾配を理由に意図的に
  学習者へ開いていない(`doubt_layer_issues.md` §8-3)。W層の対話・候補注釈は教員のみ
  (W5)。本APIは読み取り専用で、この境界を動かさない。
- **figure / component の本APIへの統合**: component は既に専用API
  (`/components/{id}/context`)を持ち DTO 構造が異なる。figure は学習者向け配信API
  (Phase 4)が別系統で存在する。統合するとしても本APIの契約を確定させた後の別issue。
- **フロントエンド実装**: チップのクリック展開・「旅」の遷移UIは `app.js` に実装済み
  (コミット `737a725`。§8.1)。本文書の主題はバックエンド契約なので、UI の詳細仕様は
  `component_evidence_redesign.md` のチップ設計に従う。
- **Phase 2 アウトライン(管理画面側)との関係**: 教員向けの根拠リンク/アウトライン改善
  (`evidence_pane_context` Phase 2)は同じ context lens を下地とするが、あちらは
  **教員向けなので candidate を出す**(確定作業のための候補提示が目的)。本APIの
  フィルタを教員向け経路に適用しない/その逆もしない。フィルタは学習者向け core
  (`element_context.py`)に閉じており、W層 core は共有しても射影規則は共有しない。
- **snapshot への焼き込み**: 本APIはオンデマンド(閲覧時 DB 参照)。`learning_courses.data`
  の投影を増やす方向は取らない(component 版 §2.4 の判断を継承)。
- **equation_id の索引化**: §5 の線形走査で足りなくなったときに検討。

## 8. 実装記録(2026-07-31)

### 8.1 変更ファイル

| ファイル | 変更 |
|---|---|
| `backend/core/element_context.py` | 新規。要素解決 + §4 フィルタ + DTO 組み立て(FastAPI 非 import・読み取り専用) |
| `backend/core/component_context.py` | `_strip_confidence` を公開名 `strip_confidence` へ昇格(旧名は別名として維持し既存参照を壊さない)。W8 の実装を2箇所に増やさないため |
| `backend/api/routes/learning.py` | `get_course_element_context` を `get_course_component_context` の直後に追加。import 2行 |
| `docs/features/learner_element_context_design.md` | 本文書(新規) |
| `backend/tests/test_element_context_core.py` | 新規。core の単体(解決・曖昧時 fail-closed・フィルタ・label 規則・navigable・縮退・ガードレール) |
| `backend/tests/test_element_context_api.py` | 新規。route のゲート・契約形・実 core 通しでの非漏洩(candidate / confidence / 裸の内部ID) |
| `frontend/public/js/app.js` + `css/styles.css` | チップのクリック展開・上位/下位レーン描画・教材内ジャンプ・「旅」の再フェッチ |
| `backend/tests/test_learner_element_context_ui_static.py` | 新規。フロント側の契約(API パス・許可 element_type・縮退文言・内部語彙の非表示)の静的検査 |

### 8.2 設計上の確定判断

1. **`element_type` の公開語彙を `claim` に短縮**した(W層内部語彙 `theory_claim` は
   404)。学習UIの埋め込み記法 `![[claim:id]]` と一致させ、内部語彙を URL に漏らさない。
2. **route での element_type 検証を先に置く**(受講ゲートより手前)。未対応型に対して
   コース照会の DB アクセスすら行わない。
3. **`focus.document_id` は残す**。component 版が `instance.in_paper.document.id` を
   既に学習者へ出しており、揃えないと後続のフロントが2系統の識別子を扱うことになる。
4. **`available` は成功時も含めて必ず返す**。フロントが `available` の有無で分岐せず
   常に真偽で判定できるようにする。

### 8.3 残課題

- equation の document 走査が線形。解決後の `context_lens.build()` が同じ artifact を
  読み直す(§5 の既知の限界)。
- `notes` の文面は教員向け context lens のものをそのまま通しており、「旧 run のため
  artifact が無く」等の運用語彙が学習者に出る余地がある(現状は事実文であり誤情報では
  ないため保留)。
- 内部IDが日本語の事実文に埋め込まれたラベル(`導出「der_001」のステップ「step_003」`)は
  label 置換の対象外(§4 の既知の限界)。
- レーン上限の適用順序が「W層 cap(candidate 込み) → 学習者フィルタ」で、表示件数が
  20 件未満になり得る(§4)。

### 8.4 レビュー指摘対応(2026-07-31)

| # | 指摘 | 対応 |
|---|---|---|
| 1 | agent 側 claim ID の解決が非決定で、複数文書ソースのコースでは**別論文の claim** が「出典に裏付け」付きで返り得た(`ORDER BY (id::text = :raw_id) DESC LIMIT 1`) | 候補を決定的 `ORDER BY` で取得し、UUID 一致は即決 / legacy_ids 一致は1行に定まるときのみ解決 / 複数一致は `None` の**曖昧時 fail-closed**(§3) |
| 2 | W層がラベル解決に失敗した項目の内部ID(図の DB UUID / `ev_0001` / `synth_claim_0001` / `support:…`)が label としてそのまま学習者に出ていた。`focus.contextual_role` も `upper[0]` の label 由来なので同じIDが役割文に出ていた | label の内部ID形を一般ラベルへ置換(関係語は保持)、役割文に内部IDトークンを含むときは role をキーごと落とす(§4)。`eq_2_7` は対象外の裁定 |
| 3 | `context_lens.build()` の内部 fail-soft 形(`_degenerate_result`、**dict**)が `available:true` + `focus.label = "<uuid>"` で通っていた(最頻の失敗形で LE8 が不発) | 4条件の保守的な形状判定 `_is_degenerate_lens()` で `available:false` に写像(§3) |
| 4 | equation 走査が document ごとに巨大 JSONB を二重読みしていた | `document_run_artifacts` を1回読み `equation_records(doc, artifacts=…)` へ渡す(§5) |
| 5 | `navigable:true` の契約(再フェッチできる)が equation 以外で成立していなかった | 学習者の実フェッチ可能性で再計算(§3 の表) |
| 6 | 設計書のレーン上限の記述と実挙動が食い違い、§8.3 のフロント未実装記述が古かった | §4 に適用順序と理由(LE6)を明記、フロント実装済みに更新 |
