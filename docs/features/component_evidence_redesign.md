# component 根拠カードの再設計 — 引用チップ化とグラフ文脈の学習者提示

> 状態: 実装済み(2026-07-21)。実装記録は §8。
> 対象: 授業用ドラフト/学習UI教材内の `![[component:id]]` 埋め込みカード。
> 関連文書: `docs/features/hierarchical_context_explanation_design.md`(ギャップ (d)(e)(f))、
> `docs/features/knowledge_network_vision.md` §1-6、`docs/features/exposition_layer_design.md`、
> `docs/features/personal_knowledge_network_design.md`。

---

## 1. 問題

授業用ドラフトの本文中に `![[component:id]]` がブロックカードとして展開されるが、
このカードは目立つ割に教育的情報を運んでいない。観察された症状:

- タイトルと本文が同一の文の繰り返しになる
- フッターに `support / title_similarity` という内部メタデータが表示される
- 日本語教材の中に未翻訳の英語文と英語バッジ("component" / "support")が出る
- 文中(「これは◯◯に対応する前提です」)にブロック要素が割り込み、文が壊れる

## 2. 原因分析(コードで確認した事実)

### 2.1 カードの生成・描画経路

1. ドラフト生成プロンプト(`backend/core/course_content_builder.py` の
   `_COURSE_CONTENT_DRAFT_PROMPT`)が `![[component:id]]` 埋め込みを許可する。
2. フロント(`frontend/public/js/app.js` / `admin-lecture-studio.js`)が
   「kindバッジ + title + summary + role/confidence」のブロックカードに展開する。

### 2.2 各症状の直接原因

| 症状 | 原因 | 箇所 |
|---|---|---|
| title と summary の重複 | evidence_links 分岐で `title = summary or raw_id` としているため構造的に必ず同文が2回出る | `course_content_builder.py` `_topic_evidence`(866行付近) |
| `support / title_similarity` | role の "support" はハードコード。confidence は `_best_mapping` が返す「トピック全体とマッピング出力の照合方法」であり、この component 個別の確からしさですらない。パイプライン来歴の漏出 | `course_content_builder.py` `_best_mapping` / `_topic_evidence` |
| 英語表示 | component の label/summary/teaching_takeaway は `component_assembly` エージェントが英語・語数制限付きで生成し、日本語化する層が無い | `src/episteme_graph/agents/component_assembly/prompt.py` |
| 中身の空疎さ | 下記 2.3 の投影問題 | — |

### 2.3 なぜ下位・上位概念が参照できないか(核心)

**データは存在する。** `component_assembly/schema.py:183-210` の ComponentRecord は
下位方向に `inputs / outputs / preconditions / cautions / internal_flow /
linked_claim_ids / linked_equation_ids / linked_evidence_ids / linked_derivation_ids`、
数式の役割分類(input/intermediate/output/constraint/definition)まで持つ。
上位方向も `document_id`(論文)と component graph の
`parent_component_id / member_component_ids` がある。

参照できない理由は3層の重なり:

1. **投影で捨てられる**: `_content_blocks`(`course_content_builder.py:1070` 付近)が
   `learning_courses.data` に保存するのは
   `component_id / label / summary / teaching_takeaway` の4フィールドのみ。
   この投影はもともと「論理要素」1行箇条書きリスト用に設計されたもので、
   根拠カードが後からそれを参照解決用DTOに転用した。
2. **snapshot自己完結の運用**: `build_topic_evidence_items` は「トピックに公開済みの
   参照だけから決定論的に組み立てる」設計で、閲覧時にパイプライン成果物や
   theory graph テーブルへクエリしない。カードからグラフをたどる導線が無い。
3. **ID体系の分断**: snapshot内は agent側ID(`ComponentRecord.component_id`)、
   theory graph API(`routes/theory_components.py`)は DB UUID。
   マッピング(`document_pipeline/persistence.py` の `id_map` / `legacy_ids`)は保存時のみ
   使用され、`learning.py:891-900` に ID不一致が既知の未修正課題として注記されている。

### 2.4 思想か慣性か

**承認ゲートは思想、グラフ不在は慣性。** この2つは分離できる。

- 意図的原則として文書化されているのは「学習者には承認済みのものだけを出す」
  (E2原則・fail-closed配信・freeze/revisionモデル)。
- 「snapshotにグラフ構造を含めない」はどの設計文書にも原則として書かれておらず、
  むしろ `hierarchical_context_explanation_design.md` のギャップ (d)(e) が
  これを欠陥として登録している。
- ビジョン文書(`knowledge_network_vision.md` §1-6)は、学習者が局所ネットワークから
  「関心のあるネットワークをたどる旅に出る」ことを体験の中核に置いており、
  学習者のグラフ参照はビジョンの要請である。現状はビジョン未達の暫定状態。
- 「閲覧時にDBを引かない」も厳密な公理ではない。C層説明エンドポイント
  (`learning.py:2705`)や図の学習者配信API(Phase 4)が
  「コーススコープ認可 + fail-closed のオンデマンドAPI」として既に承認済みパターン。
  守るべき公理は「承認済みのみ・コーススコープ・fail-closed」であって
  「snapshot完結」ではない。

## 3. 方針決定

**方向A: 引用への格下げを採用する。**

根拠カードの機能的役割は教育内容ではなく grounding(出典アンカー)である。
ドラフト生成プロンプト自身が「根拠が無ければ埋め込みを使わず本文の言葉だけで
説明する」と定めており、本文は常にカード無しで自立する。よって:

- 教科書の主役は説明の物語。component/claim への参照は小さなインラインチップ
  (例:「⚓ 理想共鳴条件(理論グラフ)」)にし、クリックで詳細に展開する。
- それ自体が内容を持つ埋め込み(数式・図)のみブロックカードを維持する。
- チップの展開先でこそ、下位概念(構成要素)と上位概念(論文・グラフ上の位置)を
  提示する。「目立つのに中身が無い」の解消は、カードの装飾ではなく
  グラフ文脈への接続によって行う。

前例: 図(figure)も全く同じ状態(投影から欠落・学習者到達不可、同ギャップ一覧 (f))から
「evidence供給 → 埋め込み解決 → 学習者配信」の Phase 4 で解決された。
同じ型を component に適用する。

## 4. 実装計画(3段階)

### Phase 1 — snapshot内クロスリンク(下位方向・最小コスト)

- `_content_blocks` の components 投影を拡張する。裸IDではなく説明文を運ぶ
  (詳細は §5.1): `preconditions / inputs / outputs` の text 付き項目、
  `dependencies` の reason 付き項目、数式の役割分類、`narrative_role`、`document_id`。
- フロントは component/claim をインラインチップ+クリック展開に変更。
  展開時、同じトピックの evidence_items 内で ID を解決してポップオーバー表示
  (equation/claim は多くの場合既に evidence_items に居る)。
- 新API不要。freeze時に固定されるため承認モデルと完全に整合。
- 同時に直す小修正:
  - `_topic_evidence` で component の title に summary を流用しない
    (label か「論理コンポーネント」にする)
  - 学習UIでは `ls-material-embed-meta`(role/confidence)を出さない。
    admin側のみ、日本語ラベル(例:「対応付け: タイトル類似」)で表示
  - kindバッジの日本語化(component→論理要素 等)

### Phase 2 — コーススコープの component 文脈API(上位+下位・オンデマンド)

- 新設: `GET /api/courses/{course_id}/components/{component_id}/context`
- agent ID → DB UUID の解決は legacy_ids 突合。
  `core/deliberation/decomposition.py` の `_agent_id_candidates_for_focus` が
  同じ問題を解決済みなので流用する。
- 返却形は §5.3 の ComponentContext DTO(instance / shared_part の二面構造)。
- fail-closed: コースのソース文書に属する要素のみ・candidate 除外(E2原則)・
  confidence 生値は返さない・shared_part は確定リンク+L層エントリが揃う場合のみ。

### Phase 3 — 学習者向けローカルグラフビュー(「旅」の実装)

- チップ →「グラフで見る」→ コーススコープの 1-hop 近傍グラフ表示。
- `personal_knowledge_network_design.md` の P層計画(わたしの地図・経路探索)に接続。

### 中期課題(段階外)

- component_assembly に日本語 `teaching_takeaway` を持たせるか、
  ドラフト生成時に翻訳を挟む(英語露出の根治)。

## 5. component 文脈の提示仕様 — instance / shared の二面構造

### 5.1 前提: 接続の「説明文」は既に生成されている

グラフへの導線を渡すだけでは不十分だが、説明文を新規生成する必要もない。
パイプラインは接続レベルの自然文を既に持っている:

| 方向 | 説明の実体 | 出所 |
|---|---|---|
| 上位(論文との接続) | `narrative_role`(この段階が論文の主張に何を寄与するか、1〜2文)/ `transition_text`(エッジ)/ `graph_summary`(中心主張→支持構造、3〜5文) | `narrative_annotator`(issue #360) |
| 下位(支持構造) | `inputs / outputs / preconditions / cautions` の各項目 = `ComponentFieldRef`(`text` + claim_ids/equation_ids)。`dependencies` の各項目 = `reason` 文付き。数式は役割分類済み(input / intermediate / output / constraint / definition) | `component_assembly/schema.py`(ComponentFieldRef / ComponentDependency / *_equation_ids) |
| 組成理由 | `reason`(なぜこの component として組んだか)/ `internal_flow` | ComponentRecord |
| 解釈 | C層承認済み explanation | `component_explanations` |

**Phase 1 の投影対象の補正**: `linked_*_ids` の裸IDだけでなく、
preconditions/inputs 等の `text`、dependencies の `reason`、数式の役割分類、
および course build 時に narrative artifact から引く `narrative_role` を
components 投影に含める。「接続を見せる」のではなく「接続の説明文を見せる」。

### 5.2 二面構造の根拠

「共通部品としての仕様」と「この論文の中での位置づけ」の分離は、
`knowledge_network_vision.md` 修正②「正規化は置換でなく追加 —
論文ごとの表現を潰すとその論文との接続が悪くなる」と同型であり、
受け皿は実装済み:

- **L層 `LibraryEntry`**(`backend/core/library/schema.py`, migration 042):
  name / aliases / summary / standardization_status / source_component_ids /
  source_document_ids。draft 正本 + 凍結版履歴。
- **`element_identity_links`**(W-β, migration 048): instance ↔ shared_part、
  candidate → 人間確定、evidence・確定者付き。

### 5.3 ComponentContext DTO(Phase 2 API の返却形)

```
{
  "instance": {                        # 面1: この論文の中で(常に存在)
    "component": { label, summary, component_type, teaching_takeaway },
    "in_paper": {                      # 上位方向
      "document": { title, section },
      "narrative_role": "…",           # narrative_annotator 由来
      "graph_summary_excerpt": "…"     # 任意
    },
    "supports": {                      # 下位方向 — 型付き+説明文付き
      "preconditions": [ { text, refs } ],
      "inputs":        [ { text, refs } ],
      "outputs":       [ { text, refs } ],
      "equations":     [ { id, label, role } ],   # role=input|intermediate|output|constraint|definition
      "claims":        [ { id, excerpt } ],
      "dependencies":  [ { type, target_label, reason } ]
    },
    "explanation": { … },              # C層承認済みのみ・無ければ省略
    "provenance": "course_freeze"
  },
  "shared_part": {                     # 面2: 共通部品として(nullable)
    "entry": { name, aliases, summary, standardization_status,
               other_documents_count },
    "link":  { confirmed_by, evidence },
    "provenance": "identity_link_confirmed + library_entry"
  }
}
```

提示規則:

- **面1は常に出す**(コースfreezeが承認根拠)。narrative_role が無い場合は
  teaching_takeaway → summary の順でフォールバック。
- **面2は「確定 identity link + active/凍結 LibraryEntry」が揃う場合のみ出す**
  (fail-closed)。candidate リンクは出さない。未整理の場合、面2の枠自体を
  表示しない(「未整理です」等のノイズも出さない)。
- 面1と面2を**同一カード内で混ぜない**。学習者が「この論文での役割」と
  「一般的な部品仕様」を別の知識として受け取れることが目的。

### 5.4 UI 提示

- **チップ(ポップオーバー)**: instance.narrative_role(位置づけ一文)+
  supports の摘要(前提n件・数式n件など)。
- **展開パネル**: 「この論文では」「共通部品として」の2タブ(またはペイン)。
  下位接続は text/reason 付きの文リストとして表示し、裸のID・エッジ一覧は出さない。
- **「グラフで見る」**は展開パネルからの追加導線(Phase 3)であり、
  説明の代替ではない。
- E層(exposition layer・未実装)は将来、両面の入門者向け再叙述の供給源となる。
  E層実装までは narrative_role / teaching_takeaway / C層説明で構成する。

## 6. ガバナンス決定事項

component/claim 自体はパイプライン生成物であり、教員が個別承認したものではない。
ただし現状のカードでも既に受講者に露出している。よって次のように定義する:

- **コース公開(freeze)= ソース文書内 1-hop 近傍の露出承認** とみなす。
- それを超える探索(他論文への同一性リンク先など)は、
  C層承認済み説明が存在する場合のみ提示する。これにより E2原則を維持する。

## 7. 受け入れ基準(案)

- 学習UIの教材本文で component/claim がブロックカードとして文中に割り込まない
- チップ展開で「この論文の中での位置づけ」(narrative_role)が文として読める
- 下位接続が text / reason / 数式役割付きの説明として表示され、裸のIDリストが出ない
- 「共通部品として」面は確定リンク+L層エントリがある場合のみ表示される
- チップ展開で構成要素(数式・claim)と所属論文が確認できる
- `title_similarity` 等の内部照合メタデータが学習UIに一切出ない
- 未承認(candidate)の説明・コース外文書の要素が学習者に出ない(fail-closed テスト)
- admin ドラフト画面では従来どおり grounding 検証情報(対応付け方法等)を確認できる

---

## 8. 実装記録(2026-07-21 実装済み)

Phase 1〜3 とガバナンス(§6)を実装。migration 不要(既存テーブルの読みのみ)。
backend フルスイート 5,237 pass / 20 skipped(node あり環境では 5,248 pass / 9 skipped)。

### 8.1 設計書からの確定差分

- **Phase 2 API パスは `/api/learning/courses/{course_id}/components/{component_id}/context`**。
  §4 の `/api/courses/...` 表記は `/api/learning` プレフィックス省略と判断
  (C層 explanations・図の学習者配信 API と同じ learning ルータ配置)。
- **Phase 3 は独立エンドポイントにしない**。context レスポンスの `graph` ブロック
  (nullable)に同梱し、W層 `core/deliberation/context_lens.py` の 1-hop 近傍を
  `relation_status != 'candidate'` で射影(各レーン最大20・例外時は null 縮退)。
  展開パネルは1フェッチで両タブ+グラフを賄う。
- **narrative_role の実データ経路は2系統**: Phase 1 投影(course build 時)は
  `stage_outputs._artifacts.narrative_annotator` の `node_narratives` を agent 側
  component_id でそのまま join(§2.3 の ID 分断は DB 側の話で、artifact 間 join には
  生じない)。Phase 2 API は `theory_component_graphs.graph_json["narrative"]
  ["node_narratives"]`(キーは保存時に DB UUID 化済み)を読み、
  `thesis_context.role_in_thesis`(migration 055・決定論)→ teaching_takeaway →
  summary の順でフォールバック。
- **teaching_takeaway は theory_components の DB 列に存在しない**(通常 persist 経路は
  teacher_notes を空で書く)ため、Phase 2 は component_assembly artifact を併読する。
- claim の title=summary 重複はバックエンドでは変更せず、チップ化(title のみ表示・
  本文はポップオーバー)により表示上の重複が構造的に消える形で解消。
- 中期課題(§4 段階外: 日本語 teaching_takeaway 生成による英語露出の根治)は未実装のまま。

### 8.2 変更ファイル

| ファイル | 内容 |
|---|---|
| `backend/core/course_content_builder.py` | Phase 1: narrative_annotator join(`_collect_structured_content`)、`_content_blocks` components 投影拡張(narrative_role / document_id / preconditions・inputs・outputs・cautions / dependencies / equations 役割分類 / claims)、`_topic_evidence_links` component への label 保存、`build_topic_evidence_items` の title=label 化 + rich 投影 `supports` マージ(旧投影データは劣化許容の後方互換) |
| `backend/core/component_context.py` | 新規(Phase 2/3 core・FastAPI 非import)。document スコープを SQL 内 `ANY(:doc_ids)` で強制する component 解決(DB UUID / legacy_ids 両対応)、artifact 併読、shared_part(confirmed link + active エントリのみ)、graph(context_lens 1-hop・candidate 除外)、`_strip_confidence` 再帰除去 |
| `backend/api/routes/learning.py` | `GET /courses/{course_id}/components/{component_id}/context` 追加(+82行のみ)。図配信 Phase 4 と同型の fail-closed。C層承認済み説明(teacher_approved・course スコープ・1件)は route 側でマージ |
| `frontend/public/js/app.js` | component/claim のインラインチップ化(`renderMaterialEvidenceChip` + registry + document 委譲リスナー1本)、学習UIの `ls-material-embed-meta`(role/confidence)全廃、kind バッジ日本語化、ポップオーバー(Phase 1: narrative_role / supports 文リスト / KaTeX 数式+役割バッジ)、文脈パネル(Phase 2: 「この論文では」「共通部品として」2タブ)、SVG 1-hop グラフ + ノードクリックで再フェッチ(Phase 3「旅」) |
| `frontend/public/css/styles.css` | `.ls-material-evidence-chip` 系・ポップオーバー・タブ・グラフのスタイル追加(既存 `.ls-material-embed` 系は非変更) |
| `frontend/public/js/admin-lecture-studio.js` | プレビューの component/claim チップ化(既存 `lsFocusEvidence` 右ペイン配線を維持)、kind/メタの日本語化(`LS_EVIDENCE_*_LABELS`。例「根拠 / 対応付け: タイトル類似」)。**admin はメタを消さない**(§7) |
| テスト | 新規: `test_component_context_core.py`(33) / `test_component_context_api.py`(6) / `test_component_evidence_chips_ui_static.py`(23) / `test_component_evidence_admin_ui_static.py`(17)。更新: `test_course_content_builder.py` / `test_topic_material_evidence_items.py` / `test_learning_material_embed_resolution.py`(キー契約は不変) |

### 8.3 受け入れ基準(§7)の充足

- component/claim はチップ描画でブロック割り込み解消(equation/figure/source はカード維持)
- チップ展開で narrative_role が文として読める(無ければ takeaway → summary 縮退)
- 下位接続は text / reason / 数式役割付き文リスト。excerpt の引けない claim ID は非表示(裸ID禁止)
- 「共通部品として」タブは confirmed identity link + active L層エントリが揃う場合のみ表示(null は枠ごと非表示)
- 展開パネルで構成要素(数式・claim)と出典論文タイトルを確認できる
- 学習UIから `title_similarity` 等の照合メタデータを完全排除(静的テストで固定)
- candidate 説明・コース外文書 component は 404 / 除外(fail-closed テストで固定)
- admin は grounding 検証情報を日本語ラベルで維持

### 8.4 残課題

- docker 実機での E2E(実コース+実パイプライン成果での表示確認)
- 中期課題: component_assembly の日本語 teaching_takeaway(英語露出の根治)
- document 成果閲覧の版ピン留め(V層)と context API の整合は現状 HEAD 読み(既知の限界)
