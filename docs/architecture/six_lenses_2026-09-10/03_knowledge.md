> **調査記録（2026-09-10）**: ビジョン×UXギャップ調査「六つのレンズ」のレンズ3（知識オブジェクトの生涯）詳細報告。統合と優先付けは [../vision_ux_gap_six_lenses_2026-09-10.md](../vision_ux_gap_six_lenses_2026-09-10.md)。読み取り専用調査で、file:line は HEAD d5ac3cb 時点。「推測」と明記した箇所は未確認。

# レンズ: 知識オブジェクト（claim・式・component・配置・台帳）の生涯

基準: `docs/vision.md` §1〜§6（特に §2.1〜§2.8・§5.4・§6 の14原則）。
調査は 2026-09-10 時点の HEAD（`d5ac3cb`）。**推測**と明記していない記述はコードまたは
migration SQL の実物に基づく。

---

## 0. 結論の要約

1本の論文が取り込まれると、claim・式・component は**その論文の中で**豊かに構造化され、
台帳・疑義・反証条件・配置という「疑い方」の器も揃っている。しかし生涯を通して見ると、
3つの構造的な断絶がある。

- **時間が無い。** 再解析は `theory_claims` / `theory_components` を **物理 DELETE して
  新 UUID で作り直す**（`backend/core/document_pipeline/persistence.py:567`,
  `:704`）。FK CASCADE により**学習者の再構成産出物**（`backend/db/036_reconstruction_loop.sql:25`
  → `:50`）と**教員の説明・承認・引用**（`backend/db/021_endorsement_sharing.sql:18`,
  `:47`, `:69`）が一緒に消える。FK の無い台帳・疑義・同一性リンクは死んだ UUID を指す
  孤児として残る。原則3（知識オブジェクトに `DELETE FROM` を作らない）は新しい層すべてで
  ガードレールに守られているのに、**知識オブジェクトそのものを持つ最古の層だけが例外**に
  なっている。
- **近傍が論文の中に閉じている。** 論文 A の claim と論文 B の claim を結ぶ関係を保存できる
  表が1つも無い。`theory_component_links` には `conflicts_with` / `analogous_to` の語彙が
  DDL にあるが（`backend/db/013_theory_components.sql:115`）、**書き込み経路が存在しない**
  （pipeline の文書内 dependency のみ）。D層・SL層の計算（支持経路・反実仮想・負荷度）は
  すべて `theory_component_graphs` の union で、**論文をまたぐ辺は一本も無い**
  （`backend/core/doubt/dependency.py:104-175`）。
- **検証が片側しか無く、検査器の区別が無い。** `verification_scopes` は「どこで確かめたか」
  の配列だが、`refuted` はスコープも根拠も不要な単一状態
  （`backend/api/routes/doubt.py:600-651`。`directly_verified` はスコープ1件必須なのに
  `refuted` は0件でよい）。§2.8 が要求する5つの検査器区分（機械的再実行 / 分析妥当性 /
  経験的再現 / 外的妥当性 / 解釈妥当性）を記帳する列は**どこにも無い**。claim type は
  agent 側の34値が persist 時に DB CHECK の17値へ丸められ、外れた型は `diagnostic_claim`
  に潰れる（`backend/core/document_pipeline/persistence.py:520-528`）ので、
  「claim type ごとの検証契約」の材料自体が DB に届いていない。

---

## 1. claim の状態空間と欠落

### 1.1 いま表現できる区別

| 軸 | 保存場所（根拠） | 語彙 | 備考 |
|---|---|---|---|
| 出典裏付け | `theory_claims.support_status`（`backend/db/013_theory_components.sql:44`、**CHECK 無し**） | 実際に書かれる値は **`"source_backed"` 固定**（`backend/core/document_pipeline/persistence.py:594`）。教員 API が受理するのは `source_backed / source_inferred / domain_inferred / design_inferred / unsupported`（`backend/api/routes/theory_components.py:204`）。agent 側は `source_backed / partially_source_backed / derived / inferred / review_required / external / unknown`（`src/episteme_graph/agents/claim_object_builder/schema.py:7-15`） | **三者が互いに不一致**。DB に CHECK が無いので破綻が検出されない |
| 教員承認 | `theory_claims.review_status` | `teacher_approved / rejected / needs_revision / teacher_review_required`（`backend/api/routes/theory_components.py:3037`） | 遷移は `POST /api/admin/claims/{id}/review` |
| claim type | `theory_claims.claim_type`（CHECK 17値・`backend/db/013_theory_components.sql:75-81`） | agent オントロジーは34値（`src/episteme_graph/agents/claim_object_builder/schema.py:94-124`） | 型外は `diagnostic_claim` へ丸め（`persistence.py:520-528`）。`prior_work` / `main_result` / `interpretation` / `method` / `structural_property` は**保存時に消える** |
| 原文と正規化 | `text` / `normalized_text` | — | §2.5 に沿う。ただし persist 時は両方に同じ span text を入れており（`persistence.py:590-591`）、正規化の差分は実質空 |
| atomicity | **DB 列なし**（artifact のみ） | `atomic / composite / split_required` | `claim_objects` は永続化されない（`backend/api/routes/theory_components.py:2417` のコメントが明言） |
| 検証の強さ | `epistemic_ledger.verification_status`（`backend/db/029_epistemic_ledger.sql:34`） | `directly_verified / indirectly_supported / untested / refuted / unknown` | `UNIQUE(target_id, target_type)` = claim につき台帳1行 |
| 検証の範囲 | `epistemic_ledger.verification_scopes`（配列） | 各要素 = `condition / domain / precision / system` + `evidence_ids` + `recorded_by` + `reason`（`backend/core/doubt/schema.py:160-190`） | §2.2 の心臓部。**空欄が正常**（§2.3）として実装されている |
| 反証条件（予告） | `epistemic_ledger.falsification_conditions` / `falsification_candidates`（`backend/db/067_stakes_ledger.sql:28-33`） | `observation_value / auxiliary_hypothesis / not_formulable` + `reachability`（人間専用） | 「何が起きれば覆るか」 |
| 合意の強さ | `consensus_explicit` / `consensus_behavioral` | — | 検証軸と別列。§2.2 に沿う |
| 疑義 | `challenges`（`backend/db/031_challenges.sql`） | 4型 × `open / answered / withdrawn / led_to_verification` | 帰属必須・理由必須。**target は claim / assumption のみ**、challenger は user |
| 検証提案 | `verification_proposals`（032 + 067） | `proposed / in_progress / completed / withdrawn` + `reachability` + `external_check` | `external_check` は**必須の自由文**（`backend/api/routes/doubt.py:1752`） |
| 同一性 | `element_identity_links`（`backend/db/048_element_identity_links.sql:28`） | instance → `library_entries` ハブへの `candidate / confirmed / rejected` | instance ↔ instance は**設計的に作らない** |
| 配置 | `landscape_placements`（`backend/db/065_landscape_placements.sql:29`） | perspective 6 × status 5 × `skeleton_version` | claim は `evidence[].claim_id` として間接参照されるだけ |
| 版 | `shared_versions`（037） | document 単位で `analysis_run_id` をピン | **claim 単位の版は無い**。document snapshot の実体は `{n_claims, n_components, has_graph}` の件数 manifest（`backend/core/versioning/releases.py:88-107`） |
| 学習者側 | `reconstruction_items.claim_id`（036:25） / `interest_traces` の構造帰属 | — | 本人のみ可視（§5.2） |

### 1.2 表現できない区別（§2.2 / §2.7 / §2.8 が要求するもの）

| # | 要求される区別 | 現状 | 根拠 |
|---|---|---|---|
| A | **「別の論文（の claim）がこの claim を反証した／支持した」** | 表現できない。claim↔claim の関係表が無い。`challenges.target` は claim だが `challenger` は user であり、「論文が疑義を立てる」形は無い。`theory_component_links.conflicts_with` は DDL にあるが書き込み経路ゼロ | `backend/db/013_theory_components.sql:115`／`grep theory_component_links` の全ヒットは persistence と削除処理のみ |
| B | **「この claim は条件 c では成立しない」（部分反証）** | 表現できない。`refuted` は claim 全体に1つの状態で、`verification_scopes` の双対にあたる「反証されたスコープ」の配列が無い。しかも `PUT verification-status` は自由文 `reason` だけで根拠・スコープを一切要求しない | `backend/api/routes/doubt.py:366-368`（リクエストは2フィールド）、`:600-651`（`directly_verified` だけスコープ1件必須） |
| C | **「後の版で撤回された／訂正された」** | 表現できない。claim に版も `superseded` も無く、再解析で行ごと消えて新 UUID になる | `backend/core/document_pipeline/persistence.py:567` |
| D | **「この2つは同じではないと判断した」** | ハブ経由の `rejected` しか無く、instance↔instance の非同一は書けない。さらに `decide()` は却下時に**理由を保存しない**（status / decided_by / decided_at のみ更新） | `backend/core/deliberation/identity_links.py:225-243` |
| E | **「この claim は引用文献由来（コーパス外）である」** | agent 語彙に `support_status="external"`（“引用文献からの主張”）があるが、persist は常に `source_backed` を書き、教員 API の許容集合にも `external` が無い | `src/episteme_graph/agents/claim_object_builder/schema.py:13` vs `persistence.py:594` vs `theory_components.py:204` |
| F | **検査器の種別（§2.8 の5区分）** | 語彙ゼロ。`verification_scopes` の4軸はすべて「どこで」であって「どの検査で」ではない。反証側の Duhem 区別（観測値／補助仮説）だけが唯一の検査器的区別 | `backend/core/doubt/schema.py:160-190`, `:399`（`FALSIFICATION_KINDS`） |
| G | **claim type ごとの検証契約** | 型が保存時に丸められ（1.1）、台帳は型に一切依存しない。`definition` も `main_result` も同じ5値で扱われる | `persistence.py:520-528`, `backend/core/doubt/schema.py:47-63` |
| H | **承認そのものの撤回** | claim の `review_status` は上書きのみで、「一度承認したが取り下げた」を状態として持たない。C層 endorsement には `revoked` があるので（021:57）**層によって非対称** | `backend/api/routes/theory_components.py:3037-3063` |
| I | **同じ claim の複数の読み（§5.3 の並存）** | component には複数 explanation が並存するが、claim には並存の器が無い | `backend/db/021_endorsement_sharing.sql:18`（FK は component_id のみ） |

### 1.3 状態空間を横断して壊すもの — 再解析の破壊性

`POST /api/admin/documents/{id}/reanalyze`（`backend/api/routes/admin.py:797`）は
通常のパイプラインを最後まで走らせ、persist ステージ
（`backend/core/document_pipeline/orchestrator.py:2161-2196`）が次を実行する。

```
DELETE FROM theory_claims           WHERE document_id = :doc_id   -- persistence.py:567
DELETE FROM theory_component_links  WHERE document_id = :doc_id   -- persistence.py:700
DELETE FROM theory_components       WHERE document_id = :doc_id   -- persistence.py:704
```

FK CASCADE の連鎖（すべて migration の実物）:

| 消える側 | 連鎖 | 意味 |
|---|---|---|
| `theory_claims` | → `reconstruction_items`（`036_reconstruction_loop.sql:25` CASCADE）→ `learner_reconstructions`（`:50` CASCADE） | **学習者の産出物・自己確認・改訂履歴が物理削除**。migration 036 の冒頭コメント自身が「学習者の成果物・自己確認・異議も行削除しない」と書いている（`036:15`）のに、上流の削除で消える |
| `theory_components` | → `component_explanations`（`021_endorsement_sharing.sql:18` CASCADE）→ `component_endorsements`（`:47`）/ `component_citations`（`:69`） | **教員の独自解釈・承認・引用の帰属が物理削除**。C層は「取り消しは行削除ではなく `revoked=TRUE`」を明文の設計原則にしている（`021:57` 前後）が、上流の削除がそれを無効化する |
| FK 無しの層 | `epistemic_ledger` / `challenges` / `element_identity_links` / `landscape_placements.evidence[].claim_id` / `element_annotations` | 死んだ UUID を指す**孤児として残る**（見えないまま蓄積）。台帳は `UNIQUE(target_id, target_type)` なので、新 UUID には検証状態ゼロの新しい行が作られる＝**検証履歴が黙って初期化される** |

補足: 原則3 の「`DELETE FROM` を作らない」はガードレールで守られているが、その検査対象は
各層のツリー（例 `backend/tests/test_doubt_guardrails.py:158` は `core/doubt` 配下）で、
`core/document_pipeline/persistence.py` は**どのガードレールの対象にもなっていない**。

また V層の document Release は `analysis_run_id` をピンするだけ
（`backend/core/versioning/releases.py:44-85`）なので、再解析後に旧版を開いても
「その版の claim 行」は既に存在しない。フロントもそれを正直に書いている
（`frontend/public/js/versioning.js:53-58`「解析成果ビューの版固定表示は今後対応予定」）。

---

## 2. 論文間の関係 — 保存できるもの / 欠けているもの

### 2.1 保存できる論文間の関係（4種のみ、いずれも間接）

| 関係 | 実装 | 粒度 | 制約 |
|---|---|---|---|
| 同一性（instance ↔ 共通部品ハブ） | `element_identity_links`（048）→ `library_entries` | 要素 → ハブ | **instance↔instance は作らない**（ハブ経由限定と明記）。traversal は `confirmed` のみ（`backend/core/deliberation/identity_links.py:299-312`） |
| 骨格ノードの共起 | `core/atlas_edges/derive.py:196` `derive_co_occurrence_pairs` | **概念 ↔ 概念**（論文ではない） | live 配置を持つ distinct document が2件以上。辺は骨格の concept 間に立ち、論文間の辺にはならない |
| 分野地図上の同居 | `landscape_placements`（065） | document → 骨格ノード × perspective | 「同じ領域に置かれた」以上の意味を持たない。関係の向きも種別も無い |
| 外部の引用リスト | `paper_discovery_reference_cache`（077） | arXiv ID → 参照 arXiv ID の配列 | 発見（レンズC）専用。**内部 `documents` / `theory_claims` に結び付かず、stance（支持/反証/拡張）も無い**（`backend/db/077_paper_discovery_reference_cache.sql:33-40`） |

### 2.2 欠けているもの

1. **claim 間・component 間の学術的関係**（supports / contradicts / extends / uses-as-input /
   distinct-from）。DDL の `conflicts_with` は死語彙。
2. **導出の論文越え**。`DerivationChainRecord` は `document_id` を持ち、式 ID は文書ローカル
   （`src/episteme_graph/agents/derivation_chain/schema.py:97-98`）。式が「他論文の式から来た」
   ことを言う唯一の語彙は `link_status="external_reference"` だが、**参照先を書く列が無い**
   （`src/episteme_graph/agents/equation_semantics/schema.py:157-161`）。
3. **D層・SL層の計算が橋を渡らない**。`build_dependency_graph` は
   `theory_component_graphs` を course で union するが、**identity link を一切参照しない**ので
   ノード集合は論文ごとに離散のまま（`backend/core/doubt/dependency.py:104-175`）。したがって
   - `support_paths`（独立支持経路, SL-3）は**同一論文内の経路しか数えない**
     （`backend/core/doubt/support_paths.py:1-20` の説明どおり graph_json を読む）。
     「別の2本の論文が独立に支えている」は原理的に `single` に見える。
   - 反実仮想の伝播（`backend/core/doubt/counterfactual.py:113-121`）も論文内で止まる。
   - 負荷度（下流到達集合サイズ）も同様。
4. **§2.1 の「近傍」は論文の中に閉じている。** 唯一の橋（confirmed identity link）は
   P-2 の「旅」の表示（`core/personal_graph/journey.py` 系）と W層の文脈レンズにしか使われず、
   **疑いの計算には使われていない**。「近傍との関係でのみ成り立つ」と言いながら、
   検証・反証・支持の判断は1論文の中だけで閉じて行われている。

---

## 3. 時間 — 「共同体がいつ何を受け入れたか」を後年たどれるか

**結論: たどれない。台帳は書かれているが、読み手がいない。**

- `theory_review_events` を SELECT するコードは backend 全体で **7 箇所**。内訳は
  レート制限カウンタ（`backend/api/routes/atlas.py:1064`）、重複記帳の抑止
  （`backend/core/help_kb/audit.py:83`）、改版 run の採否履歴
  （`backend/core/document_pipeline/persistence.py:1860`）、パイプライン内部の
  投影オーバーレイ（`:1917`、HTTP 非露出）、KPI 件数集計3件
  （`backend/core/doubt/metrics.py:65, :109, :122`）。
- **オブジェクト単位の時系列 API はゼロ**。「この claim は誰にいつ承認され、いつ疑義が
  立ち、いつ反証条件が書かれたか」を1本で返す経路は無い。索引
  `idx_theory_review_events_entity (entity_type, entity_id)`
  （`backend/db/013_theory_components.sql:107`）は既にあるので、土台だけはできている。
- 唯一 HTTP に出る監査時系列は改版 run の `decisions`
  （`backend/api/routes/revisions.py:326`）。UI は `frontend/public/js/admin.js:11812-11820`
  で `old_status → new_status — comment` を並べるが、**日時を描画していない**。
- V層は版の**並び**は返すが**中身も差分も返さない**。`GET /shared/{ot}/{oid}/releases` は
  メタ7フィールドのみ（`backend/core/versioning/releases.py:231-259`、docstring に
  「snapshot は含めない・一覧軽量化」）。差分 API は無い（`core/versioning` 全体で
  `diff` が0ヒット）。
- 過去 run の成果物比較は **revision run に限って**成立する（`build_diff_report`,
  `backend/core/document_pipeline/revision/diff.py:243-`）。通常の解析 run 同士を比べる
  経路は無く、UI も `run_type === "revision"` で一覧をフィルタしている
  （`frontend/public/js/admin.js:11743`）。
- 分野骨格の `changelog` は**日時フィールドを持たない**
  （`backend/core/atlas.py:129-132`）。旧凍結版を読む API も無い
  （`load_frozen_skeleton` は最新1件のみ、`backend/core/atlas_store.py:71-85`）。
- D層の台帳は現在状態だけを返す。スコープ要素の `recorded_by` / `recorded_at` が唯一の
  「いつ誰が」の痕跡で、スコープの訂正・削除の履歴は残らない
  （`PATCH .../scopes/{scope_id}` は要素を上書きする）。

つまり §2.6 が言う「一次史料としての側面」は、**書き込み側だけ実装されていて読み出し側が
存在しない**。しかも 1.3 のとおり、参照先の知識オブジェクト自体が再解析で消えるため、
イベントの `entity_id` は時間が経つほど解決不能になる。


---

## 4. 閉世界の正直さ（§2.4）— コーパス外を持ち込む経路

**言明の側は厳格、記帳の側は空。**

「このコーパスの中では検証記録がありません」しか言わない語彙統制（SL1）は
`core/doubt/support_paths.py` の固定事実文とガードレールで実装されており、ここは強い。
しかし**コーパスの外にある知識を明示的に持ち込んで記帳する経路は、事実上3本しかなく、
どれも一級市民ではない**。

| 経路 | 実装 | 限界 |
|---|---|---|
| ① カートリッジ同梱の L層シード | `backend/core/library/seed.py:122-133`（`created_by="bundled_import"`） | **開発者がリポジトリに置く**もの。教員が実行時に「これは教科書の標準結果」と足せない |
| ② 教員が白紙から `library_entries` を作る | `frontend/public/js/admin.js:2140` → `POST /api/admin/library/entries` | 書ける列は `UPDATABLE_FIELDS`（`backend/core/library/schema.py:98-106`）だけで、**出所（書名・DOI・章節）を入れる列が無い**。自由文 `summary` / `body` に埋めるしかない |
| ③ SL層の `external_check` | `verification_proposals.external_check`（`backend/db/067_stakes_ledger.sql:44`）。作成時**必須**（`backend/api/routes/doubt.py:1752`） | **自由文1本**。検証提案にしか付かず、claim・component・台帳スコープには付かない。再利用も検索もできない |

そのうえで、**`standardization_status='standard'`（＝教科書レベル）を付ける根拠が
コーパス外に実在しない**。三角測量の証拠①は LLM の事前知識
（`LLMPriorKnowledge.claimed_canonical_name` / `breadth='textbook'`,
`backend/core/deliberation/standardization/schema.py:45-64`）であり、これは
「LLM がそう記憶していた」以上の意味を持たない。W層 commit は
`library_entries.standardization_status` を書くだけで、**外部出典を1件も要求しない**
（`backend/core/deliberation/annotations.py:409-425`）。§2.5 が「LLM 単独主張は幻覚とみなす」
と言い、実装も `aggregate.decide()` で単独主張を `unknown` に落としてはいるが、
**教員が commit するときに外部の一次情報を記帳する場所が無い**ため、確定後は
「なぜ standard と言えるのか」が検査不能になる（原則8「出所の正直さ」の外側）。

また `theory_claims` 側では `support_status="external"`（“引用文献からの主張”）という
語彙が agent にあるのに、DB には常に `source_backed` が書かれ、教員 API も `external` を
受理しない（1.2-E）。**「この論文が引用した先行研究の主張」と「この論文自身の主張」が
DB 上で区別できない**。

（`documents` 表には `doc_type='textbook'` / `isbn` / `doi` / `publisher` / `license` /
`trust_level` があり（`backend/db/init.sql:44-57`）、教科書を**取り込めば**コーパス内に
なる。欠けているのは「取り込まないまま参照する」経路である。）

---

## 5. 足場と主張は別の台帳（§2.7）

**格納は完全に分離されている。還流経路が2本ある。**

分離側（守られている）:

- `student_material` / `spoken_script` は `learning_courses.data` JSONB
  （`backend/core/course_data.py:105-106`）、`display_text` / `spoken_text` は `chunks` の
  **別列**（`backend/db/init.sql:97-99`。原文は `chunks.text`）、教材図は独立テーブル
  `course_teaching_figures`（`backend/db/063_teaching_figures.sql:29-51`。A層
  `document_figures` に列を足さない方針が migration に明記）。
- `element_explanations` の `generic` は **L層凍結版への confirmed identity link がある
  要素にしか作れない**（validator が `library_excerpt` 無しの generic をハードエラーに
  する）。学習者配信は `contextual` の approved のみ
  （`backend/api/routes/lecture.py:775-834`、特に `:790-793`）。
- 説明の approve は状態遷移のみで claim/component 本文に書き戻さない
  （`backend/core/element_explanations.py:447-465`）。編集も旧行 `superseded` + 新行 INSERT。
- L層への昇格入力は装置候補 / 白紙 / 同梱シードの3つだけで、**教材図・学習者向け説明が
  `library_entries` に入る経路は構造的に無い**（`_authorize_exemplar_images` が
  `document_figures` の実在行からのみ minio_key を解決、`backend/api/routes/library.py:113-155`）。

還流側（穴）:

1. **W層 `meaning` commit が `theory_components.summary` を無条件上書きする。**
   `UPDATE theory_components SET summary = :value`
   （`backend/core/deliberation/annotations.py:311-334`）。注釈 body の検査は
   kind 語彙・evidence 非空・reason 非空だけ（`:129-166`）で、**読者層・文体の検査は無い**。
   対話プロンプトにも読者層の縛りが無い（`backend/core/deliberation/dialogue.py:106-129`）。
   したがって教員が対話で「学部生向けに言い換えて」と言い、その文を `meaning` として
   commit すれば、**平易化された足場が研究記録の本文になり、旧 summary はどこにも
   残らない**（`element_annotations` 行と監査イベントは残るが、旧本文は保存されない）。
2. **受講者質問 → component 候補。** `POST /admin/theory-components/candidates/from-query`
   （`backend/api/routes/theory_components.py:4488-4513`）は質問＋回答テキストから
   `theory_components` を新規 INSERT し、LLM 生成の応答文を `summary` に入れる
   （`:4459-4477`）。状態は `candidate` に固定されるが、**学習者対応の文章が研究記録
   テーブルに実書き込みされる**点は同じ。

（潜在。実効しない）`backend/core/theory_components.py:200, :449, :552` は evidence quote
と LLM 入力本文のフォールバック鎖に `display_text`（教材化テキスト）を含む。唯一の
呼び出し元が `raw_text = chunks.text` を必ず埋めるため現状は到達しないが、鎖の順序や
呼び出し元が増えると足場が根拠文に混ざる。**（到達しないという評価は呼び出し元1件の
確認に基づく。他経路の網羅は未確認。）**

---

## 6. 検査器相対性（§2.8）

**記帳する場所は存在しない。**

- 「機械的再実行 / 分析妥当性 / 経験的再現 / 外的妥当性 / 解釈妥当性」に対応する語彙は
  backend 全体でゼロ（`core/doubt/` に `reproduc` / `rerun` / `dataset` / `code` の
  ドメイン語が1つも無い）。
- `verification_scopes` の4軸（`condition` / `domain` / `precision` / `system`,
  `backend/core/doubt/schema.py:160-190`）はすべて「**どこで**確かめたか」であり、
  「**どの検査で**確かめたか」の軸が無い。
- 唯一の検査器的区別は反証側の Duhem 区別
  （`FALSIFICATION_KINDS = observation_value / auxiliary_hypothesis / not_formulable`,
  `backend/core/doubt/schema.py:399`）。§2.8 の要求のうち「機械検査の成功を真に昇格
  させない」に相当する構造は、`directly_verified` が人間専用であること
  （`HUMAN_ONLY_VERIFICATION_STATUSES`）として部分的に実装されている。
- **claim type ごとの検証契約は材料が無い。** 型は保存時に17値へ丸められ（1.1）、
  台帳の語彙は型に依存しない。
- **計算・データ・コードを伴う論文**に対してシステムができることはゼロ。`documents` に
  コード・データセット・環境の列は無く（`backend/db/init.sql:44-69`）、
  `document_analysis_runs` が記録するのは **episteme-graph 自身の解析 run**であって
  論文側の計算ではない。式は `equations.json` に構造として持つが、
  「この式を数値評価すると論文の表の値になるか」を試す枠も、その結果を記帳する列も無い。

---

## 提案

### 提案1: 知識オブジェクトを「置換」から「版」へ — 安定 ID と supersede

**近づける vision の箇条**: §2.6（知識は時間の中で変化する）/ §2.7（切断・撤回は記帳される）/
原則3（情報を落とさない・知識オブジェクトに `DELETE FROM` を作らない）/ 原則14（監査必須。
`entity_id` が解決できることが前提）/ §5.2（学習者の成果物を削除しない）。

**現状（証拠）**: 再解析が `theory_claims` / `theory_components` を物理 DELETE し新 UUID で
再作成する（`backend/core/document_pipeline/persistence.py:567`, `:700`, `:704`）。
CASCADE で `learner_reconstructions`（`backend/db/036_reconstruction_loop.sql:25`→`:50`）と
`component_explanations` → `component_endorsements` / `component_citations`
（`backend/db/021_endorsement_sharing.sql:18`, `:47`, `:69`）が物理削除される。FK の無い
`epistemic_ledger` / `challenges` / `element_identity_links` / `element_annotations` は
死んだ UUID を指す孤児になる。加えて W層 commit が `theory_components.summary` を旧本文を
残さず上書きする（`backend/core/deliberation/annotations.py:311-334`）。
「`DELETE FROM` を作らない」ガードレールは各層のツリー単位で書かれており
（例 `backend/tests/test_doubt_guardrails.py:158`）、**`core/document_pipeline` は
どのガードレールの対象でもない**。

**なぜ体験を損なうか**: 学習者は、自分が書いた再構成の答えと自己確認が、教員が一度
再解析ボタンを押しただけで消える環境で産出している。§3 が要求する「産出から始まる理解」は
産出物が残ることを前提にしており、消える砂場では次の訪問で自分の軌跡を辿れない。
教員にとっては、承認・独自解釈・他教員からの承認と引用という、最も時間をかけた仕事が
一瞬で消え、しかも**消えたことが通知されない**（V層の通知は発行・削除予約にしか繋がない）。
再解析は解析品質を上げるための正しい操作なのに、それを押すことが罰になる構造で、結果として
教員は再解析を避けるか、承認を後回しにする。研究の側では、台帳・疑義・反証条件が指す
`target_id` が黙って解決不能になるため、§2.6 が言う「共同体がいつ何を受け入れたかの一次史料」が
**再解析のたびに切断される**。

**機能のかたち**:
- データ構造: `theory_claims` / `theory_components` に
  `stable_key TEXT`（`document_id` + agent の `span_id` / `component_id` の決定論ハッシュ。
  既存 `source_scope.legacy_ids` から導出できる）、`superseded_at TIMESTAMPTZ`、
  `run_id UUID`、`body_revision INT` を追加。`UNIQUE(document_id, stable_key) WHERE
  superseded_at IS NULL` の部分インデックス（`landscape_placements` の live 一意制約と
  同じ作法、`backend/db/065_landscape_placements.sql:60-63`）。migration **1本**（列追加のみ）。
- 永続化: `persist_qualified_claims` / `persist_components` から `DELETE` を撤去し、
  ①同 `stable_key` の live 行は **UPDATE**（本文が変わったら `review_status` を
  `teacher_approved` → `needs_revision` に落として教員に再確認を促す。承認を黙って
  引き継がない）②今回出てこなかった行は `superseded_at = now()`（行は残す）
  ③新規のみ INSERT。**UUID が変わらない**ので下流の全参照が生き残る。
- 正本モジュール: `core/candidate_flow.py` に
  `CandidateVocabulary(candidate="teacher_review_required", accepted="teacher_approved",
  dismissed="rejected", superseded="superseded")` を宣言し、`select_supersedable` で
  「人間が確定した行は AI 再生成で倒さない」を既存規則どおりに適用
  （`backend/core/candidate_flow.py:75-133`, `:223`）。
- W層 `meaning` commit は `theory_components.summary` を上書きする前に旧本文を
  `element_annotations` の before スナップショット（Admin Copilot の
  `assistant_actions` と同じ作法）か `theory_review_events.metadata` に残す。
- UX: グラフレビュー画面（`admin-graph-review.js`）に「前回の解析から変わった主張」
  フィルタと、supersede された行の薄色表示。**新画面を作らない**。
- LLM: なし（決定論のみ）。

**14原則との照合**: 原則3・原則14・§2.6 を直接強める。原則13（層は下層を改変しない）とは
衝突する — persist は A層の下請けであり、ここだけは触らざるを得ない。**A層の agent コード
（`src/episteme_graph/agents/`）には手を入れず、`core/document_pipeline/persistence.py`
のみを変える**という限定を設計書に明記し、原則13 の明示例外として記録する。原則10
（完了フラグを持たない）には抵触しない（`superseded_at` は導出できない事実の記帳であり
状態フラグではない）。ガードレールを `core/document_pipeline` にも広げる
（`assert_source_forbids(persistence, ["DELETE FROM theory_"])`）。

**規模**: L（persist 2関数の書き換え + 列追加 + 下流の live 絞り込み + 既存テストの修正）。

---

### 提案2: claim 間関係台帳 — 論文をまたぐ支持・反証・非同一

**近づける vision の箇条**: §2.1（近傍との関係でのみ成り立つ）/ §2.2（検証と合意は別軸）/
§2.7（切断は結合と対等・帰属と理由を伴う）/ 原則7（リンクであってマージではない）。

**現状（証拠）**: 論文をまたぐ関係を保存できる表が無い。`theory_component_links` の
`conflicts_with` / `analogous_to` は DDL にあるが（`backend/db/013_theory_components.sql:115`）
書き込み経路がゼロ（`grep theory_component_links` は persistence と削除処理のみ）。
同一性は `element_identity_links` のハブ経由のみで instance↔instance を作らない
（`backend/db/048_element_identity_links.sql:28` 前後の設計コメント）。決定的なのは、
D層・SL層の計算が `theory_component_graphs` の union で**論文をまたぐ辺を1本も持たない**
こと（`backend/core/doubt/dependency.py:104-175`）。結果として
`support_paths`（独立支持経路, `backend/core/doubt/support_paths.py`）は同一論文内の
経路しか数えず、反実仮想の伝播も負荷度も論文の外に出ない。

**なぜ体験を損なうか**: 学習者には「この主張は別の論文でも独立に支えられている」という、
分野を歩き始めるときに最も知りたい事実が一度も提示されない。学習者が見る「支持線」は
実は「この1本の論文の中での支持線」であり、閉世界の注記が付いていても**閉じている範囲を
論文だと明示していない**ので、コーパス規模の話だと誤読しうる。教員には、2本の論文が
同じ主題で食い違っていることを気づいた瞬間に記帳する場所が無く、その発見はゼミの口頭に
消える（`challenges` は人が claim に立てるものであって、論文が論文に立てるものではない）。
研究の側では、SL層が測る「一点吊り」（single support line）が**論文内の一点吊り**を
指しており、賭け金の台帳の中心的な主張が実際より狭い範囲でしか成り立っていない。

**機能のかたち**:
- データ構造: 新表 `claim_relations`（migration **1本**）。
  `source_ref` / `target_ref` を `(document_id, element_type, element_id)` の3列ずつで持つ
  （`element_identity_links` の4列一意と同じ作法。FK はポリモーフィックなので張らない）。
  `relation ∈ {supports, contradicts, extends, uses_as_input, distinct_from}`、
  `status ∈ {candidate, confirmed, dismissed}`、`evidence` JSONB（逐語引用必須）、
  `reason` NOT NULL CHECK(<>''), `created_by` / `decided_by` / `decided_at`,
  `neighborhood_scope`（提案7と共有。NULL = 分野全体）。
- 候補生成: **LLM を使わない**。①VA層の保存済みアンカーベクトルと配置共起
  （`core/atlas_edges/derive.py` と同じ材料・追加 embedding ゼロ）②confirmed identity link を
  共有する claim 同士 ③`external_reference` を持つ式（提案5と組み合わせ）。候補は
  「関係があるかもしれない対」までで、**relation の種別は人間が選ぶ**。
- 正本モジュール: 遷移は `core/candidate_flow.py` の `CandidateFlow` に接続
  （`atlas_edges` が本番初適用の前例、`backend/core/atlas_edges/store.py`）。ラベルは
  `core/label_vocab.py` に `CLAIM_RELATION_LABELS` を1表だけ足す。
- 消費: `core/doubt/dependency.py::build_dependency_graph` に
  `confirmed` な `supports` / `uses_as_input` 関係を辺として注入する。これだけで
  `support_paths` / `counterfactual` / `load_calculator` が論文を渡る（各モジュールは非改変）。
- API: `GET|POST /api/admin/relations/{element_type}/{element_id}`（TEACHER 以上、
  document viewable の fail-closed）+ `POST /relations/{id}/decide`。**一覧の全体表示は
  作らない**（要素起点でのみ）。
- UX: W層モーダルと グラフレビューの要素詳細に「他の論文との関係」区画。学習者側は
  支持線の事実文が「このコーパスの中では、2本の論文が独立に支えています」に自然に伸びる
  （文言は `core/doubt/support_paths.py` の固定文を1つ足すだけ）。
- LLM: なし。

**14原則との照合**: 原則1（候補まで・確定は人間）を満たす（非LLM 候補 + 人間の種別選択）。
原則6（egocentric）— 全体グラフ画面を作らず要素起点でのみ露出することで守る。ここが最大の
抵触リスクで、「関係の一覧ダッシュボード」を作った瞬間に神の視点になる。原則4（数値非表示）—
関係の本数を出さず段階の事実文だけにする。§2.7 の `distinct_from`（これは同じではない）を
最初から語彙に入れることで、切断を結合と対等にする。

**規模**: L（表1つ + 候補導出 + 遷移 + dependency への注入 + UI 2箇所）。ただし
`dependency.py` への注入だけを先に入れる MVP（関係は手入力のみ）なら M。

---

### 提案3: 反証の対称化 — 「どこで覆ったか」の配列

**近づける vision の箇条**: §2.2（検証を単一ブールにしない）/ §2.8（公開は真偽の認証ではなく
現在の検証状態を正確に伝える行為）/ 原則2（evidence-based）/ 原則8（出所の正直さ）。

**現状（証拠）**: `verification_scopes` は「確かめられた範囲」の配列で、記帳には4軸のうち1つ
以上・根拠・理由が必須（`backend/api/routes/doubt.py:464-490`）。ところが反証側は
`verification_status='refuted'` という**単一状態**しかなく、`PUT verification-status` の
リクエストは `{verification_status, reason}` の2フィールドのみ
（`backend/api/routes/doubt.py:366-368`）。`directly_verified` はスコープ1件以上を要求する
のに（`:621-627`）、`refuted` は**スコープゼロ・根拠ゼロで記帳できる**。
`falsification_conditions`（067）は「何が起きれば覆るか」の**予告**であって、
「実際にこの条件で覆った」の記録ではない。

**なぜ体験を損なうか**: 現実の反証はほぼ常に部分的（「この近似は高密度領域では破れる」）
なのに、システムでは claim 全体が `refuted` になるか何も起きないかの二択になる。教員は
部分的な破れを記帳する場所が無いので、正直な選択肢は「記帳しない」になり、台帳は
反証について沈黙する。学習者に出る事実文も「この内容には反証の記帳があります」
（`backend/api/routes/doubt.py:2225`）という全否定の一行だけで、**どの範囲で覆ったのかが
分からないまま主張全体を捨てる**という、§2.2 が最も避けたい読み方を促す。研究の側では、
全称検証を構造的に禁止した設計が、全称反証は素通しにしているという非対称が残る。

**機能のかたち**:
- データ構造: `epistemic_ledger` に `refutation_records JSONB NOT NULL DEFAULT '[]'`
  を1列追加（migration **1本**。067 が `falsification_conditions` を足したのと同じ作法で、
  **既存列の意味は変えない** = SL10）。各要素 =
  `{record_id, condition, domain, precision, system, evidence_ids, relation_id,
  recorded_by, reason, recorded_at}`。`relation_id` は提案2の `claim_relations` 行
  （＝どの論文が反証したか）への任意参照。
- API: `POST|PATCH /api/admin/doubt/ledger/{t}/{id}/refutation-records`
  （`add_ledger_scope` と対称のバリデーション: 4軸1つ以上 + 根拠 + 理由）。
  `PUT verification-status` を改め、`refuted` への遷移も
  **refutation_record 1件以上を要求**する（`directly_verified` と対称。422 の文言は
  既存の事実文体に合わせる）。
- UX: 台帳カードを「確かめられた範囲」／「覆った範囲」の2段に。学習者向け事実文
  （`_learner_fact_line`）も対称に伸ばす:
  「この内容は『◯◯』の範囲で覆ったと記帳されています。」
  **範囲の記帳が無い `refuted` は既存データにしか存在しない**ので、その行は従来文言のまま。
- ラベル: `core/label_vocab.py` に段階表を足さない（記帳の有無と範囲の逐語だけ。数値なし）。
- LLM: なし。既存の `falsification_conditions` LLM 候補 worker は非改変。

**14原則との照合**: 原則2・原則8 を強める。原則3（情報を落とさない）— records は追記のみ、
訂正は PATCH で要素を書き換えるのではなく新要素 + `supersedes` にする（既存 scope の
PATCH が上書きになっている弱点を繰り返さない）。原則1 — LLM に反証を書かせない
（`HUMAN_ONLY_*` と同じガードを `refutation_records` にも掛ける）。抵触リスク: 記帳の
ハードルを上げると反証がさらに記帳されなくなる。**根拠は `evidence_quote` の自由文でも
可**とし（`add_ledger_scope` と同じく `evidence_ids` か `evidence_quote` のどちらか）、
心理的コストを結合側と揃える。

**規模**: S/M（列1つ + API 2本 + 既存 PUT の条件追加 + UI 1区画）。

---

### 提案4: 検証契約 — claim type を守り、検査器の軸を持つ

**近づける vision の箇条**: §2.8（検証可能性は検査器に相対的・claim type ごとの検証契約）/
原則2 改訂（claim type に応じて quote だけでなくデータ・方法・環境・反証条件を結ぶ）/
原則10（完了ではなく時点付きの verification state を導出する）。

**現状（証拠）**: agent の claim type オントロジーは34値
（`src/episteme_graph/agents/claim_object_builder/schema.py:94-124`）だが、DB CHECK は17値
（`backend/db/013_theory_components.sql:75-81`）で、外れた型は
`_normalize_claim_type` が `diagnostic_claim` に丸める
（`backend/core/document_pipeline/persistence.py:520-528`）。`prior_work` / `main_result` /
`interpretation` / `method` / `structural_property` / `background` は**保存されない**。
検査器の軸は語彙ゼロ（§6）。台帳の5値は型に依存しない
（`backend/core/doubt/schema.py:47-63`）。

**なぜ体験を損なうか**: 学習者には「これは論文の主結果」「これは先行研究の紹介」
「これは著者の解釈」の区別が届かない。すべて `diagnostic_claim` として同じ顔で並ぶので、
論文を読む最初の技能——**主張の種類を見分けること**——を鍛える材料がシステム側で潰れている。
教員は、定義と主結果に同じ検証語彙（`directly_verified` など）を当てるよう迫られ、
「定義は検証するものではない」という当然の直観と UI が噛み合わないため記帳が止まる。
研究の側では、§2.8 が要求する「必要な証拠・独立性・未充足条件を先に宣言する」検証契約が、
型が消えているため書きようがない。

**機能のかたち**:
- データ構造 (a): `theory_claims.claim_type` の CHECK を agent オントロジーに揃える
  （migration **1本**、DO $$ ガード付きの CHECK 差し替え。013 に前例あり）。
  `_normalize_claim_type` の丸めを撤去し、未知値のみ `unknown` に落とす。
- データ構造 (b): `verification_scopes[]` / `refutation_records[]` に
  `instrument TEXT` を追加（既存要素は空 = `unknown`。SL10 の作法で既存列の意味は不変）。
  語彙の正本は `core/doubt/schema.py` に
  `VERIFICATION_INSTRUMENTS = (machine_rerun, analytic_validity, empirical_replication,
  external_validity, interpretive_validity)` + `core/label_vocab.py` に日本語表1つ。
  **機械検査の成功を真に昇格させない**規則を明文化: `instrument='machine_rerun'` だけの
  スコープでは `directly_verified` に昇格できない（`HUMAN_ONLY_*` と同型の API 側 422）。
- データ構造 (c): claim type ごとの検証契約を**カートリッジの JSON**に置く
  （`backend/cartridges/<id>/verification_contracts.json`。既存の
  `validation_rules.json` と同じ読み込み経路）。内容は
  「この型では通常どの instrument が要るか / 何が未充足だと事実として書くか」。
  **自動判定・自動ゲートにしない**（原則1・原則10）。台帳 UI は
  「この型では通常◯◯の検査が要ります。現在の記帳は△△です。」を事実文として並べるだけ。
- UX: 台帳カードのスコープ行に検査器チップ。claim カード（グラフレビュー・出典タブ）に
  claim type の日本語ラベル（`core/element_vocab.py` に既存の語彙表を拡張。新表を作らない）。
- LLM: なし。既存の claim_qualification が既に型を出しているので、**保存で捨てるのを
  やめるだけ**。

**14原則との照合**: 原則2・§2.8 を直接実装する。原則13 — agent は非改変で、DB CHECK と
persist の丸めだけを直す。原則10 との整合に注意: 契約は「充足/未充足」の完了フラグを
保存せず、記帳から**毎回導出**する。抵触リスク: 契約を UI で強く見せると原則12
（押し付けない）に触れる。**未充足を警告色にせず事実文にする**（§2.3「空欄は発見」）。

**規模**: M（migration 2本相当を1本にまとめられる + 語彙2表 + カートリッジ1ファイル +
台帳 UI 1区画）。

---

### 提案5: コーパス外参照の一級記帳（`external_references`）

**近づける vision の箇条**: §2.4（コーパスは分野ではない・閉世界の正直さ）/ §2.5（標準化は
三角測量。LLM 単独主張を幻覚とみなす）/ 原則8（出所の正直さ）/ §6.2 の候補「入口の正直さ」。

**現状（証拠）**: コーパス外を持ち込む経路は3本しかなく、①は開発者専用のシード
（`backend/core/library/seed.py:122-133`）、②は出所列の無い白紙エントリ
（`backend/core/library/schema.py:98-106`）、③は検証提案に付く自由文
`external_check`（`backend/api/routes/doubt.py:1752`）。
`standardization_status='standard'` の commit は外部出典を1件も要求しない
（`backend/core/deliberation/annotations.py:409-425`）。claim 側の
`support_status='external'` は死語彙（1.2-E）。

**なぜ体験を損なうか**: 学習者に対してシステムは「このコーパスの中では検証記録が
ありません」と正直に言う。しかし教員が「それは教科書に載っている標準的な結果だよ」と
知っている場合、その知識を置く場所が無いので、**正直さが役に立たない正直さ**になる
（晴れ間が本当の発見候補なのか、単に教科書を取り込んでいないだけなのかが区別できない）。
教員は同じ説明を口頭で繰り返し、次の学期にも残らない。研究の側では、`emerging_common`
——§2.5 が「本システムの発見的価値の在り処」と呼ぶもの——の判定が、「外部標準が無い」
という**確かめていない前提**の上に立ってしまう。

**機能のかたち**:
- データ構造: 新表2つ（migration **1本**）。
  `external_references`（`kind ∈ {textbook, review, standard, dataset, code,
  private_communication, other}`、`citation_text` NOT NULL CHECK(<>'')、
  `identifier`（DOI/ISBN/URL、任意）、`locator`（章節・式番号、任意）、
  `recorded_by` NOT NULL、`created_at`。行削除 API なし）と
  `external_reference_links`（`reference_id` FK + `target_type ∈ {claim, component,
  library_entry, ledger_scope, verification_proposal}` + `target_id` +
  `stance ∈ {defines, supports, contradicts, standard_result, provides_data}` +
  `reason` + `recorded_by`）。
- API: `POST|GET /api/admin/external-references`（TEACHER 以上）+
  `POST /external-references/{id}/link`。既存 `external_check` はこの表への参照に
  移行できるが、**自由文の後方互換は残す**（既存行を壊さない）。
- ガバナンス: `standardization_status` を `standard` / `field_standard` に commit する
  ときは `external_reference_links` を1件以上要求（422。三角測量の証拠①を
  「LLM がそう言った」から「人間が出典を記帳した」へ引き上げる）。
  `theory_claims.support_status` に `external` を復活させ、`prior_work` 型の claim に
  参照を張れるようにする（提案4 の型復活とセット）。
- UX: 台帳カード・共通部品カード・claim カードに「コーパスの外」区画（出典の逐語表示のみ・
  リンク先が外部 URL の場合もクリック導線は作らない＝閉世界の言明を外部に依存させない）。
- LLM: なし。**LLM に外部文献を書かせない**（幻覚した引用が一級市民になるため。
  記帳は人間のみ、`HUMAN_ONLY_*` と同型のガード）。

**14原則との照合**: 原則8・§2.4・§2.5 を強める。原則1 — LLM 書き込み経路を作らないことで
守る。原則11（fail-closed）— 参照の実在検証はできない（外部を叩かない）ので、
**「教員が記帳した」以上の権威を与えない**ラベル（「教員の記帳（コーパス外）」）を固定する。
抵触リスク: 外部参照が増えると閉世界の言明が緩む。**言明の語彙は変えない**
（「このコーパスの中では検証記録がありません」はそのまま。外部参照は隣に並置する別の行）。

**規模**: M（表2つ + API 2本 + commit ゲート1つ + UI 3箇所の区画）。

---

### 提案6: オブジェクトの年表（object timeline）

**近づける vision の箇条**: §2.6（共同体がいつ何を受け入れたかを後年たどる）/ 原則14（監査・
帰属）/ §5.4（制度監査領域の独立監査に再検査を許す）/ §6.1（独立監査者は再検査できる）。

**現状（証拠）**: `theory_review_events` を SELECT するコードは backend 全体で7箇所しかなく、
オブジェクト単位の時系列を返す API はゼロ（レート制限カウンタ
`backend/api/routes/atlas.py:1064`、重複抑止 `backend/core/help_kb/audit.py:83`、
改版 run 履歴 `backend/core/document_pipeline/persistence.py:1860`、内部投影 `:1917`、
KPI 件数3件 `backend/core/doubt/metrics.py:65, :109, :122`）。索引
`idx_theory_review_events_entity (entity_type, entity_id)` は既にある
（`backend/db/013_theory_components.sql:107`）。UI で監査イベントを見せるのは改版モーダル
1箇所だけで、そこでも**日時を描画していない**（`frontend/public/js/admin.js:11812-11820`）。
V層は版の並びを返すが中身も差分も返さない（`backend/core/versioning/releases.py:231-259`、
`core/versioning` に `diff` は0ヒット）。骨格 changelog は日時フィールドを持たない
（`backend/core/atlas.py:129-132`）。

**なぜ体験を損なうか**: 教員が「この主張、去年は承認されていた気がするが誰がいつ疑義を
立てたのか」を確かめる方法が無い。結果として、同じ議論がゼミごとに再演され、
**弁を押した人の判断が共有財にならない**（§5.1 が要求する「監査記録から何を見て何を
選べたかが再構成できること」＝原則1 改訂の条件が、記録はあるのに読めないという形で
未達）。学習者には見せない情報だが、教員間の信頼——同じ論文を別の教員が触ること——が
成り立つ前提が欠けている。研究の側では、§2.6 の「一次史料」が事実上アクセス不能な
ログファイルになっている。

**機能のかたち**:
- 実装: `backend/core/object_timeline.py`（FastAPI 非 import・非LLM・読み時導出）。
  `theory_review_events`（`entity_type`, `entity_id` で引く）に加えて、時刻を持つ関連行を
  マージする: `component_endorsements.created_at` / `challenges.created_at` /
  `verification_scopes[].recorded_at` / `falsification_conditions[].recorded_at` /
  `landscape_placements.reviewed_at` / `element_identity_links.decided_at` /
  `shared_versions.created_at` / `element_explanations.reviewed_at`。
  出力は `{at, actor_label, kind, fact_line}` の配列（**数値なし・段階ラベルのみ**）。
- API: `GET /api/admin/objects/{entity_type}/{entity_id}/timeline`（TEACHER 以上 +
  対象 document の viewable ゲート）。migration **不要**。
- `decision_context`（`core/decision_context.py`）を持つイベントは「何を見て、どんな代替が
  あって、拒否できたか」を折りたたみで展開する（原則1 改訂の再構成可能性がここで初めて
  読める形になる）。
- UX: グラフレビューの要素詳細と W層モーダルに「この要素に起きたこと」区画。
  **一覧・ダッシュボードは作らない**（オブジェクト起点のみ）。
- LLM: なし。

**14原則との照合**: 原則14・§2.6 を直接実装。原則6（egocentric）— オブジェクト起点なので
神の視点にならない。原則5（監視しない）が最大の注意点: **学習者由来のイベントを
年表に混ぜない**（`interest_traces` 系の kind は `core/trace_registry.py` の登録簿で
露出3宣言が管理されているので、年表を4つ目の消費面として登録簿に宣言し、
学習記録 kind は構造的に除外する）。§5.4 の三領域分離をここで守る。
前提として提案1（`entity_id` が解決し続けること）が要る。

**規模**: S/M（core 1モジュール + API 1本 + UI 1区画。migration なし）。

---

### 提案7: 切断の可視化 — 近傍つきの却下記録

**近づける vision の箇条**: §2.7（切断は結合と対等の一級市民 / 免疫記憶は近傍ごと /
砂場では切断の理由が見える）/ 原則8 の切断側 / 原則6（共有地全体の負例集合を作らない）。

**現状（証拠）**: 却下は保持されるが、**近傍の概念が無く、理由の記帳も不揃いで、
学習者からは完全に見えない**。
- `atlas_gap_decisions.cluster_key` は `TEXT NOT NULL UNIQUE`
  （`backend/db/066_category_gap_signals.sql` の (b) 節）＝**分野に1件**。ある教員の
  「見送り」は分野全体で恒久的に候補を抑止する（restore まで）。
- `element_identity_links.decide()` は却下時に **理由を保存しない**
  （`backend/core/deliberation/identity_links.py:225-243` は status / decided_by /
  decided_at のみ UPDATE）。一方 gap と landscape は理由必須という非対称。
- 学習者に配信される配置は `LEARNER_VISIBLE_STATUSES` で絞られ `rejected` を出さない
  （`backend/core/landscape/schema.py:100`, `backend/api/routes/landscape.py:844`）。
  同一性リンクの `rejected` も学習者側の旅（`confirmed_links_for_document`,
  `backend/core/deliberation/identity_links.py:299-312`）に現れない。

**なぜ体験を損なうか**: 学習者は「AI がここに置こうとしたが、教員がこういう理由で違うと
判断した」という、分野の判断基準が最も濃く現れる場面を一度も見ない。見えるのは
教員が通した結合だけなので、地図は「正解の集合」に見える（§2.1 が避けたい読み方）。
教員にとっては、自分の研究室の事情で切った結合が**他の研究室の候補まで消す**（分野単位の
`cluster_key`）ため、切ることに躊躇が生まれ、免疫記憶が育たない。研究の側では、
「近傍 A・条件 c で切断された」という来歴が持てず、異端が別の近傍から戻ってくる経路が
塞がれる。

**機能のかたち**:
- データ構造: `landscape_placements` / `atlas_gap_decisions` / `atlas_edge_decisions` /
  `element_identity_links` に `neighborhood_scope TEXT`（`group_id` か `course_id`、
  NULL = 分野全体）を1列ずつ追加（migration **1本**）。`atlas_gap_decisions` は
  `UNIQUE(cluster_key)` を `UNIQUE(cluster_key, neighborhood_scope)` に張り替える
  （NULL 込みの一意は部分インデックス2本で表現）。
- 判断: 候補の抑止は**同一 neighborhood のみ**。既存行は `NULL`（分野全体）のまま
  移行するので挙動は不変、新規の却下時に UI が「この分野全体 / このグループだけ」を
  選ばせる（既定は所属グループ）。
- 理由: `identity_links.decide()` に `reason` を必須引数として追加し、
  gap / landscape と揃える（`reason` NOT NULL の CHECK は既存行があるため付けず、
  API 層で 422）。
- UX: 要素カード・地図の詳細ペインに「切られた結合」区画 —
  「『◯◯』との対応は、△△（グループ名）で、理由『…』により見送られています。」
  **教員には帰属付き、学習者には帰属を伏せて理由と近傍だけ**（§5.3 の可視性勾配 /
  原則14 の「帰属の開示が遅れることは匿名ではない」）。学習者側は既存の
  `landscape` / `threads` レイヤーに事実行を足すだけで新画面を作らない。
- LLM: なし。

**14原則との照合**: §2.7・原則8 を直接実装。原則3 — 行削除は増やさない（列追加のみ）。
原則4（数値を見せない）— 切断の件数を出さない・「よく切られる要素」を作らない。
抵触リスク: 却下を学習者に見せると、教員の判断が個人攻撃的に読まれうる。**主語を型に
する**（D層の疑義カードが「主語は常に型」としているのと同じ規律、
`backend/db/031_challenges.sql` の設計コメント）ことで回避する。

**規模**: M（列4つ + 一意制約の張り替え + 抑止条件の変更 + UI 2箇所）。

---

## 見送った案

### (a) コーパス全体の統合ナレッジグラフ画面
論文間関係（提案2）を入れると「全部つないだ図を1枚見たい」という要求が必ず出る。しかし
原則6（egocentric）と KN-1「神の視点を作らない」は §2.1 の知識観そのもので、
可視化の便宜のために曲げるものではない。提案2 では要素起点の1ホップ表示に限定し、
一覧 API 自体を作らないことにした。

### (b) LLM による論文間の supports / contradicts の自動抽出
技術的には最も簡単で、最も危険。§2.7 が言う「未検証の主張を作るコストだけが崩落した」
非対称がそのまま現れる領域で、AI が生成した「Aは Bを反証する」の候補は、教員が全部
確かめない限りコーパスを汚染する。しかも反証の判定はまさに §2.8 の「解釈妥当性」に属し、
機械検査で代替できない。提案2 では候補生成を**既存の非LLM 材料**（アンカーベクトル・
配置共起・identity link）に限り、relation の種別は必ず人間が選ぶ形にした。

### (c) claim ごとの「検証成熟度スコア」／台帳のカバー率指標
台帳の充実度を1つの数値にすると教員向けダッシュボードが作れて運用は楽になるが、
原則4（個人を比較・ランキングする数値を作らない）と §2.3（空欄は発見であって欠損では
ない）に真正面から反する。「記帳が少ない教員」を可視化してしまう副作用もある。
提案4 では契約の充足を数値化せず、事実文の並置に留めた。

### (d) 学習者の再構成・自己確認を検証の証拠として研究記録へ流用する
`learner_reconstructions` には「学習者が独立にこの導出を再現できた」という、検証の
補助証拠になりうる情報が実際に溜まっている。しかし §5.2 は研究への流用に五条件
（授業だけで完結すること・明示 opt-in・独立者による品質確認・名前付き credit・
撤回経路）を課しており、そのどれも現状の実装に無い。条件を満たさないまま経路だけ
作ると、§5.4 の三領域分離が最初に壊れる場所になる。**先に必要なのは提案1（学習者の
産出物が再解析で消えないこと）**であり、流用の議論はその後。

### (e) W層の `meaning` commit を禁止する
`theory_components.summary` の上書き（§5 の還流経路1）は、禁止すれば消えるが、
教員が対話で得た理解を本文に反映する正当な経路でもある。禁止ではなく
**旧本文を残す**（提案1 に含めた）ほうが原則3 に沿い、原則12（押し付けない）にも触れない。
