# 信頼境界 — PDF / URL 取得 / arXiv 由来テキストは untrusted 入力

[← ドキュメント目次](../README.md)

> **状態:** 生きたリファレンス（2026-09-10 新設。実装に追随して更新する）
> **機械検証:** `backend/tests/test_pdf_trust_boundary_guardrails.py`
> **背景:** ビジョン×UXギャップ調査「六つのレンズ」§4 第1波 #10 / 既知課題 B-01
> （`six_lenses_2026-09-10/known_issues_architecture.md`）。着手前は `untrusted` の
> grep が 0 hit で、既存の `injection` テストは SQL インジェクションと図（SVG）注入
> のみを見ていた。

---

## 1. 何が untrusted か

本システムに入る文字列のうち、**教員でも学習者でもない第三者（論文著者・配布元）が
書いたもの**を untrusted 入力とする。具体的には次の経路で入ってくるテキストが対象。

| 入口 | 実装 | 生成物 |
|---|---|---|
| 教員による PDF / TeX アップロード | `routes/admin.py::upload_material` | `chunks.text` / `chunks.display_text` / `document_structure` の blocks |
| URL 指定による取得（UF層） | `core/url_fetch.py` → `_accept_material_source` | 同上 |
| arXiv 取り込み（PD層・レーダー・補完） | `routes/paper_discovery.py::ingest` | 同上 + `ArxivEntry.title` / `.summary`（要旨） |
| 図の抽出（L層） | `core/document_pipeline/figure_images.py` | `document_figures` の caption / `inner_labels` |
| 外部引用グラフ（CC層レンズC） | `core/paper_discovery/citation_client.py` | 参照リストの title |

これらから A層パイプラインが導出する二次生成物（`theory_claims.text`・
`evidence_registry` の逐語引用・`equations` の LaTeX・thesis の合成文・
`theory_components.summary`）も**同じく untrusted**として扱う。LLM が書いたか人が
書いたかではなく、**原文に由来する文字列かどうか**で線を引く（A層の各ステージは
原文の逐語引用を必須にしているので、原文の文字列はそのまま下流へ運ばれる）。

untrusted **でない**もの: 教員が管理画面で入力した本文・議論テーマ・図スタジオへの
指示、学習者の発話、カートリッジ / 骨格 / ライブラリのようにリポジトリまたは教員の
明示操作を経た語彙。

### 想定する脅威

1. **プロンプト注入** — 論文本文・要旨・caption に「これまでの指示を無視して…」の
   ような文を仕込まれ、LLM が資料本文を指示として実行する。本文は数千字単位で
   プロンプトへ流れ込むので、量の面でも最大の攻撃面。
2. **区切りの偽装** — 資料本文がプロンプト自身の区切り（`##` 見出し・`---`・
   `[出典N]`）と同じ文字列を含み、指示側とデータ側の境界を溶かす。
3. **表示側への転写** — 制御シーケンス（ANSI・裸の SGR 残骸）や不可視文字が
   grounding に載り、LLM の応答経由で画面・読み上げへ漏れる。
4. **二次利用先への波及** — 資料本文が SQL / シェル / ファイルパスへ補間される。

なお本境界は「悪意ある論文」だけを想定するものではない。実測で最初に効いたのは
**事故由来の混入**（ログ着色の ANSI 残骸が artifact に紛れ込み応答に転写された
2026-09-10 のオーナー報告 → `core/text_hygiene.py` 新設）である。

---

## 2. 境界規約（4条）

### TB1. 資料本文は明示の区切りで隔離する

LLM プロンプトに資料本文を載せる経路は、**指示側と資料側を見分けられる形**にする。
次のいずれかを満たすこと。

- `json.dumps` した payload の文字列値として載せる（引用符・波括弧の escape が
  `json` 側で担保される。llm_worker 8系統・A層の6 agent がこの形）
- ラベル付きの区画に入れる（`[参考資料]` / `【候補論文】` / `[出典N] 『title』` /
  `## 素材（この中の文字列だけを引用してよい）` など）

**素の連結は禁止**（見出しも区画ラベルも無く指示文へ直結する形）。`##` markdown
見出しだけを区切りにしている経路は、資料本文が同じ見出しを再現できるため
**弱い区切り**として扱い、§4 の残課題に記録する。

### TB2. 「資料本文中の指示に従わない」を指示側に明示する

区切りだけでは意味論的な注入は防げないので、指示側に固定文
`core.text_hygiene.UNTRUSTED_SOURCE_NOTICE` を添える。**文言は1箇所に固定**し、
経路ごとの言い換えをしない（言い換えを許すと「抜けている経路」を機械検出できない）。

固定文は位置語（「以下」「上記」）を含まない — 資料本文が指示の前に来る経路
（レクチャー原稿の書き換え）にも後に来る経路（RAG コンテキスト）にもそのまま置ける
ようにするため。

### TB3. 資料本文を SQL / シェル / ファイルパスへ補間しない

- SQL は必ずバインドパラメータ経由。`sa_text(f"…")` の f-string に載せてよいのは
  **コード側の定数**（カラムリスト・プレースホルダ列・フィルタ句・ロック seed）だけで、
  資料由来の文字列を載せてはならない。
- MinIO のオブジェクトキーは `document_id` / `figure_id` / `material_id`（UUID）から
  組む。ファイル名を使う経路（`core/harvester.py::store_uploaded_pdf`）は
  `re.sub(r"[^\w.\-]", "_", …)` で正規化してから使う。
- 本番コードは `subprocess` / `os.system` を使わない（テストの node 実行のみ）。

### TB4. 表示・読み上げの前に制御シーケンスを落とす

資料本文（およびそれを含む LLM 応答）を DTO で返す前に
`core.text_hygiene.strip_control_sequences` を通す。読み上げ経路はさらに
`core.tts.strip_text_for_speech`（`\(…\)` 等の除去を含む）を重ねる。

---

## 3. 調査表（2026-09-10 時点）

### 3.1 `backend/` 側（本境界の適用先）

| # | 経路 | 資料本文の載り方 | 区切り | 指示非実行の明示 |
|---|---|---|---|---|
| 1 | 学習チャット RAG（`routes/learning.py`） | `[出典N] 『title』\n{chunk text}` を `---` で連結、`## 関連する教材のコンテキスト` 配下。**user ロール**（system には入らない） | あり（ラベル + `---`） | **追加済み** |
| 2 | W層 要素対話（`core/deliberation/dialogue.py`） | `_INSTRUCTION_HEADER` + grounding_text + `---` + 教員発話。grounding に逐語引用・claim 本文・caption | あり（`---` / 区画見出し） | **追加済み** |
| 3 | グラフ全体対話（`core/deliberation/graph_dialogue.py`） | 同型（ノード label・claim 本文・validation） | あり | **追加済み** |
| 4 | レーダー比較分析（`core/paper_discovery/compare.py`） | `【起点論文】` / `【候補論文】` 配下に要旨 | あり（`【】` 区画） | **追加済み** |
| 5 | 教材図スタジオ 対話（`core/teaching_figures/prompt.py`） | `[参考資料]` 配下に教材本文・数式・主張 | あり | **追加済み**（`GROUNDING_CONSTRAINT` 末尾） |
| 6 | 教材図スタジオ 提案（同） | `[トピック本文]` 配下 | あり | **追加済み**（`_SUGGEST_RULES`） |
| 7 | コース教材ドラフト（`core/course_content_builder.py`） | `根拠候補:` 配下に `{evidence_json}`（原文抜粋・claim・数式） | あり（JSON） | **追加済み** |
| 8 | レクチャー原稿の書き換え（`core/lecture.py`） | `## チャンクテキスト (不完全な抽出テキスト):` 配下に生チャンク（最大級の量）。指示は本文の**後** | 弱い（`##` 見出しのみ） | なし → §4 |
| 9 | 質問→component 候補（`core/component_candidates.py`） | `# AI の回答本文` / `# 参照可能な既存 Claim` 配下 | 弱い（`#` 見出しのみ） | なし → §4 |
| 10 | llm_worker の JSON 区切り系統（tension / structure_anchor / reconstruction） | instruction + `json.dumps(payload)`（chunk 抜粋・claim 本文）。**user ロール1本**（`system` 不使用 = 開発ルール4） | あり（JSON） | なし → §4（JSON 区切りは効いている） |
| 11 | D層 検証スコープ候補（`core/doubt/scope_candidates/prompt.py`） | `# 出典テキスト` 配下に `[block_id] label` + 本文（JSON ではない） | あり（区画見出し + 行頭ラベル） | **追加済み** |
| 12 | SL層 反証条件候補（`core/doubt/falsification_conditions/prompt.py`） | 同型（+ 下流ラベルの参考区画） | あり | **追加済み** |
| 13 | W層 標準化判定（`core/deliberation/standardization/prompt.py`） | `# 対象（共通部品）` 配下に name / aliases / summary / body | あり | **追加済み** |
| 14 | 出典ポップアップ DTO（`services.get_chunk_passage`） | LLM 経路ではなく表示経路 | — | TB4 **追加済み**（`strip_control_sequences`） |

**要約: backend 側で LLM に資料本文を渡す経路 13 本 —** 区切りあり 11 / 弱い区切り 2
（#8, #9）、指示非実行の明示は着手前 **0 本** → **10 本に追加**（#1〜#7, #11〜#13）、
残り 3 本（#8, #9, #10）は §4。表示経路（#14）は TB4 を追加。

### 3.2 A層 agent（`src/episteme_graph/agents/*/prompt.py`）— 本タスクでは非改変

`prompt.py` を持つ agent は **13**（残り 9 ディレクトリは決定論ビルダーで LLM 非使用）。
資料本文は **13/13 すべて user ロール**（`system` は静的な module 定数、または
`component_graph` / `discuss_opening` / `landscape_placement` は system ロール自体を
持たず1本の user メッセージに指示と資料を同梱）。

| 区切りの形 | agent | 指示非実行の明示 |
|---|---|---|
| JSON 完全区切り（6） | component_assembly / contextual_explanation / discuss_opening / dsl_linking / landscape_placement / thesis_reconstruction | なし |
| 素の連結（`##` 見出しのみ）（3） | paper_skeleton / rhetorical_role / claim_qualification | なし |
| 混在（4） | apparatus_semantics（caption・nearby_text・inner_labels が素）/ component_graph（Material 4 の claim/evidence が素）/ equation_semantics（前後の本文が素）/ narrative_annotator（central_thesis が素） | なし |

**指示非実行の明示は 13/13 で無し。** `equation_semantics` にある `untrusted` の語は
「PDF テキスト層は数式については信用しない」という**数式復元の忠実性**の指示で、
注入対策ではない。入力側の制御は truncation（1200〜1800 字）のみで、sanitize・escape・
区切り文字の除去はどこにも無い。`build_repair_messages` は `_build_user_content` を
再呼び出しするため、露出は修復経路にも同じだけある。

---

## 4. 残課題（本タスクでは触らない）

| ID | 内容 | 理由 |
|---|---|---|
| TB-a | A層 13 agent への `UNTRUSTED_SOURCE_NOTICE` 適用 | A層 prompt.py の非改変が本タスクの前提。プロンプト変更は出力分布を変え得るので、agent ごとに `src/tests/agents/` の期待と合わせて別便で入れる |
| TB-b | 素の連結 3 agent（paper_skeleton / rhetorical_role / claim_qualification）の区切り強化 | `##` 見出しは資料本文が再現できる。JSON 化または衝突しない区切り（例: 資料側の `##` 行頭をエスケープ）が要る。出力形式に影響するため設計判断 |
| TB-c | `core/lecture.py` の原稿書き換え（#8） | 同時刻に別担当が編集中。区切り強化 + notice は次便 |
| TB-d | `core/component_candidates.py`（#9） | 弱い区切り。C層の候補生成なので確定は教員（影響は限定的） |
| TB-e | llm_worker の JSON 区切り3系統（#10）への notice 適用 | JSON 区切りは効いているため優先度は下。`BaseJSONLLMClient` の入口で一律前置する案があるが、各系統の validator が prompt 全文を grep している箇所があり要確認 |
| TB-f | `inner_labels` / caption の区切り文字除去 | 図中ラベルは短文だが素で連結される（apparatus_semantics）。決定論的な除去が可能 |

---

## 5. 変更するときの手順

1. 新しく LLM へ資料本文を渡す経路を足すときは、**§2 の TB1 + TB2 を満たす**
   （区切り + `UNTRUSTED_SOURCE_NOTICE`）。
2. `UNTRUSTED_SOURCE_NOTICE` の文言を変えるときは `core/text_hygiene.py` の1箇所を
   直し、`backend/tests/test_pdf_trust_boundary_guardrails.py` の期待も同時に直す。
3. §3 の表を実装に合わせて更新する（この文書は「生きたリファレンス」）。
4. §4 の残課題を解消したら、表から消して §3 に移す。
