# 学習者向け claim / equation 文脈API — 要素中心コンテキストの学習者提示

> 状態: バックエンド実装済み(2026-07-31)。実装記録は §8。
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
    `source_scope.legacy_ids`)の**両対応**
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
  "label": str,
  "relation_label": str,       // 「を定量化する」等の読み手向け動詞句(主語は常に focus)
  "relation_status": "source_backed"|"confirmed",   // candidate は現れない
  "navigable": bool            // true のとき同APIで再フェッチできる(= 旅の続き)
}
```

### レスポンス 200(縮退)

```json
{"available": false, "note": "この要素の文脈情報は現在表示できません。"}
```

要素は解決できた(= 権限は通った)が W層 context lens が投影を返せない / 例外だった
場合。フロントは文脈欄ごと非表示にすればよい(LE8)。

## 4. フィルタ仕様

`core/deliberation/context_lens.py` の生の投影に対し、`core/element_context.py` が
以下の変換を行う。**この4点が学習者向けAPIと教員向けAPIの唯一の差分**である。

| 対象 | 規則 | 理由 |
|---|---|---|
| `upper` / `lower` の各 ITEM | `relation_status == "candidate"` を**除外** | candidate は教員確定前の AI 提案(W2)。学習者に確定情報として出さない。component 版 `_build_graph` と同じ原則 |
| `focus.contextual_role` / `contextual_role_status` | `contextual_role_status ∈ {source_backed, confirmed}` かつ role が非空のときのみ残す。candidate / unidentified は**キー自体を落とす** | 推測で穴埋めしない。「未同定」という内部状態語彙を学習者に見せる必要も無い |
| 全レスポンス | `confidence` キーを**再帰除去**(`component_context.strip_confidence`) | W8。数値は段階ラベルすら出さない |
| ITEM の `evidence_refs` / `relation` / `document_id`、`focus.provenance` | **落とす** | 内部参照ID(evidence_id / step_id / span_id)と内部語彙キーを学習者に出さない。`focus.document_id` のみ残す(component 版が `in_paper.document.id` を既に出しているため整合) |

レーン上限は component 版と同じ 20 件(candidate 除外後の件数に対して適用)。
`notes`(「figure_table_semantics artifact が無いため図との関係を判定できません」等)は
事実文なのでそのまま通す。

## 5. 実装構成

```
backend/core/element_context.py                 ← 新規(FastAPI 非 import)
  SUPPORTED_ELEMENT_TYPES = ("claim", "equation")
  build_element_context(element_type, element_id, course_document_ids) -> dict | None
  _resolve_claim()      ← theory_claims を doc スコープ SQL で1行解決(UUID/legacy_ids)
  _resolve_equation()   ← コース document 集合を equation_records で線形走査
  _visible_items() / _project_item() / _project_focus()   ← §4 のフィルタ

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
  増えて問題になるなら equation_id → document_id の索引化を検討する(§7)。

## 6. 受け入れ基準

- [x] 未受講コース / コース外 document の要素 / 未知の element_type がすべて 404
- [x] agent 側 claim ID(`claim_span_007`)が DB UUID と同様に解決できる
- [x] 同じ agent 側 ID がコース外文書にも存在するとき、コース内文書の行だけが解決される
- [x] `relation_status == "candidate"` の関係がレスポンスに一切現れない
- [x] `contextual_role_status` が candidate / unidentified のとき role が現れない
- [x] `confidence` キーがレスポンスのどの深さにも現れない
- [x] `evidence_refs` がレスポンスに現れない
- [x] context lens が None / 例外のとき 200 + `available:false`
- [x] `core/element_context.py` が FastAPI / routes を import しない
- [x] `core/element_context.py` に書き込み(INSERT/UPDATE/DELETE)経路が無い

## 7. 非スコープ

- **学習者からの疑義・注釈・deliberation**: D層の疑義投稿は地位勾配を理由に意図的に
  学習者へ開いていない(`doubt_layer_issues.md` §8-3)。W層の対話・候補注釈は教員のみ
  (W5)。本APIは読み取り専用で、この境界を動かさない。
- **figure / component の本APIへの統合**: component は既に専用API
  (`/components/{id}/context`)を持ち DTO 構造が異なる。figure は学習者向け配信API
  (Phase 4)が別系統で存在する。統合するとしても本APIの契約を確定させた後の別issue。
- **フロントエンド実装**: チップのクリック展開・「旅」の遷移UIは後続。本文書は
  バックエンド契約の確定までを扱う。
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
| `backend/tests/test_element_context_core.py` | 新規。core の単体(解決・フィルタ・縮退・ガードレール) |
| `backend/tests/test_element_context_api.py` | 新規。route のゲート・契約形・実 core 通しでの非漏洩 |

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

- フロントエンド(チップ展開・旅の遷移)は未実装。
- equation の document 走査が線形。
- `notes` の文面は教員向け context lens のものをそのまま通しており、「旧 run のため
  artifact が無く」等の運用語彙が学習者に出る余地がある。文面の学習者向け言い換えは
  フロント実装時に実文を見て判断する(現状は事実文であり誤情報ではないため保留)。
