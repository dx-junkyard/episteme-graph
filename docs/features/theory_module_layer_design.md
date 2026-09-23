# 理論モジュール層（Theory Module Layer — 式の操作に「接点の狭さ」で境目を入れる中間層）と claim チェーンの位置づけ

> **状態: Phase 0 実装中**（2026-09-23 起票・同日オーナー裁定。migration なし・LLM 0 回の読み時導出を
> Phase 0 とする。§9 の O-1〜O-3 は推奨案で確定（O-1 (c) / O-2 (b-読) / O-3 (b)）、O-4 は概念レジストリの
> O-6 に従属。実測 3 は §3.3 に記録済み。実装記録は §12）

**正本**: 本ドキュメント。
**関連**: [グラフ対話レビュー](graph_dialogue_review_design.md)（GR1〜GR8 — 表示先の画面。
本層はその層トグルに「理論モジュール」を足す）/
[グラフの論文層](graph_paper_layer_design.md)（PL1〜PL8 — 読み時射影の作法と、章アウトラインに
主張を論文順で並べる既存の受け皿）/
[知識オブジェクト層](knowledge_objects_design.md)（KO1〜KO10 — 保存する場合の stable_key と
supersede の作法）/
[概念レジストリ](concept_registry_design.md)（KR1〜KR10 — 同一性候補の受け皿と O-6）/
[論文の再現性レビュー 2026-09-19](../architecture/knowledge_reproduction_review_2026-09-19.md)
（E-1 PDF 由来の式の欠陥・§5.2「main グラフの集約粒度」— 本書はその後継の検討）/
[段階ラベル・共有語彙表](label_vocab_design.md)。

**オーナー判断（本書の前提・確定済み）**

| # | 判断 | 採用 | 根拠 |
|---|---|---|---|
| — | モジュールの境目を何で切るか | **構造（接点の狭さ）で切るのを主にする**。component_assembly の部品（LLM 原案）は照合相手であって主軸にしない | 外枠の仕様が実際の理論と合っているかを先に確かめ、中の作りは後から詳細に合わせる方が整合を取りやすく、シンプルな仕様を見極められる（オーナー、2026-09-23） |
| — | 目的 | **理論の構造を見抜き、共通構造を適切に抽象化して他への転用を楽にする** | コース側で束ねる用途（学ぶ単位 = [learning_units](learning_units_design.md)）ではない。論文の章立て（論文層）でもない |
| — | 進め方 | ① 2609.15375v1 の TeX 再取り込み → ② 外枠の試作 → ③ claim チェーンの位置づけ見直し（本書）を順に行う | ①〜③ とも 2026-09-23 に完了（① は §3.3、② は §3.2 に実測として記録） |
| O-1〜O-3 | §9 の 3 判断 | **推奨案を採用**（2026-09-23「すすめてください」） | O-1 (c) Phase 0 は読み時導出・Phase 1 の同一性候補の前に knowledge object 化 / O-2 (b-読) claim チェーン由来ノードをキャンバスから外し主張の並びは論文層で見る / O-3 (b) main は残しグラフレビューの初期表示だけモジュール図 |

---

## 1. 目的 — 「4 と 470 の間」が無い

グラフレビュー画面（`frontend/public/js/admin-graph-review.js`）の理論操作グラフには、表示層が
主グラフ（`graph_layer="main"`）と式の詳細（`graph_layer="equation_detail"`）の 2 つしかない。

- 主グラフは theory stage の固定集約である（`src/episteme_graph/agents/component_graph/normalizer.py::_group_records`）。
  stage 語彙は 7 つ（`THEORY_STAGES`）なので、論文の大きさに関係なく最大 7 ノードになる。
- 式の詳細は導出の 1 step = 1 ノードで、論文によっては数百ノードになる。
- 両者の間に「少数の入力を受けて少数の結果を返す部分系」を表す層が無い。

2609.15375v1 では主グラフが 4、式の詳細が 470 だった（§3.1）。stage は「どの種類の操作か」を
表すが、「どこからどこまでが一つのまとまりか」を表さない。理論を他の論文・分野へ転用するときに
要るのは後者である。回路にたとえると、stage は「抵抗・容量・トランジスタ」の部品種別で、
本層が作りたいのは「電源部・増幅段・フィルタ段」のブロック図である。

本書は 2 つの主題を持つ。

- **主題 A（§4〜§6）**: 式の操作 DAG に「接点の狭さ」で境目を入れ、理論モジュールを決定論・
  非LLM で導出する中間層。
- **主題 B（§7）**: claim チェーンの位置づけの見直し。現状は章内の主張の列を導出チェーンの顔で
  式の詳細層に流し込んでおり、式の層が無い論文で詳細層が数百に膨れ、表示名とも合わない。

主題 B は主題 A の前提でもある。主張の列を式の依存として数えると、構造で切っても章分けしか
出ない（§3.1）。

---

## 2. 不変条項（TM1〜TM10）

vision §6 の 14 原則（[vision.md](../vision.md) §6）に照らして置く。原則の例外はここに書く。

| # | 条項 | 照らす原則・帰結 |
|---|---|---|
| TM1 | **A層非改変（Phase 0）**。本層は backend で artifact と `graph_json` を読むだけで、agent の出力・`graph_json`・#308 の main ラベル規律に触れない。A層に手を入れる選択肢は §7 に「A層改変案」として分けて書き、オーナー判断を経る | 原則 13 / W1 |
| TM2 | **決定論・非LLM・読み時導出**。同期パスで LLM も embedding も呼ばない。同じ入力からは同じモジュールが出る（併合の同点は決定論順で割る） | 原則 9 / PL2 |
| TM3 | **推測の依存を使わない**。モジュールの成員は「入力式と出力式の両方を持つ step」だけにする。claim チェーンの「前の主張 → 次の主張」は論文中の順序であって依存ではないので、式の依存として数えない。`graph_layer="debug"` / `inferred` の step も使わない | 原則 2 / 8 / PMN-2 |
| TM4 | **境目は構造で切る**。分割の入力は式の依存と接点の本数だけにする。stage・章・component_assembly の部品は分割の入力にせず、色分け・参照・照合に留める | オーナー判断（本書の前提） |
| TM5 | **domain-independent**。ラベルの語は stage の訳語・操作動詞の訳語・論文データ（主張の本文・式の印字番号）からだけ取る。特定分野・特定論文の語をコードに書かない。新しい訳語表を作らない（`backend/core/element_vocab.py` の既存表を使う） | 原則 13 / #308 / label_vocab 設計 |
| TM6 | **数値非表示**。併合の閾値 k・接点の本数・工程の型の多重度・構造の指紋・モジュールの数を UI と DTO に出さない。接点の式・成員はリストの列挙で見せる（件数バッジにしない）。段階ラベルが要るなら `backend/core/label_vocab.py` に置く | 原則 4 / PL4 |
| TM7 | **候補まで・確定は人間**。構造の指紋の一致は同一性候補の供給源であり、同一性の確定ではない。確定は教員の操作だけで、一括確定なら `decision_context` を記帳する | 原則 1（2026-09-04 改訂）/ KN-3 |
| TM8 | **情報を落とさない・欠落を明示する**。併合できない step・循環に関わる step・共有の基礎・結果の吸い込み口（sink）・claim の列は、捨てずに事実文か別区画で残す。artifact の欠落は例外にせず `facts[]` に事実文を 1 行足す | 原則 3 / 8 / PL8 |
| TM9 | **権限 = document viewable**。グラフと同じ `_ensure_document_viewable` を通す。fail-closed | 原則 11 / PL6 |
| TM10 | **内部 ID を表示しない**。`eq_op_*` / `theory_op_*` / `eq_tex_*` / モジュールの読み時 ID をラベルに使わない。式は印字番号（「式 (12)」）、番号の無い式は「番号なし: 本文先頭」で示す | PL7 |

---

## 3. 実測

本節の数値は設計の根拠としての実測記録である。UI に数値を出さない原則（TM6）とは別に扱う。

### 3.1 実測 1 — 2609.15375v1（PDF 経路・採用 run）

- 主グラフは 4 ノードで、すべて theory stage の固定集約である。式チェーンが 0 本なので、この 4 ノードは
  claim チェーンの step（主張の種別 → 操作動詞 → edge_type → stage の写像）から作られている。
- 式の詳細は 470 ノードで、**すべて claim チェーン由来**だった（式チェーンは 0 本）。
- claim チェーンの生成（`src/episteme_graph/agents/derivation_chain/agent.py::_build_claim_chains`）は、
  章ごとに主張を出現順に並べ、前の主張を次の step の入力（`input_claim_ids` / `required_claim_ids`）に
  置くだけである。結果は章の数と同じ 15 本の一直線の path になった。
- 1 step = 1 主張で重複は無い。主張の DAG は 470 ノード・455 辺・連結成分 15・分岐ゼロだった。
- 式レコード 64 個はすべて `derivation_excludes_inconsistent_equation`（confidence / consistency policy）で
  導出から除外されていた。equation_semantics の記録には「実際の PDF の数式が取れず周囲の散文から推測した」
  旨があり、confidence は 0.27 前後だった。再現性レビューの E-1（PDF 経路では式・導出の層が再現されない）が
  そのまま出ている。
- 詳細ノード 470 のうち `parent_component_id` を持つものは 0 だった。component_assembly の部品は 18 個。
- この DAG に「接点の狭さ」でモジュールの切り出しを掛けると、各 path は接点 2 のまま丸ごと潰れ、
  15 モジュール = 15 章に戻った。**構造がデータに無いので、構造で切っても章分けしか出ない。**
- 全教材を横断すると、式チェーンを持つのは TeX 経路で取り込んだ arXiv-2407.01221v2 の 1 本だけだった
  （11 チェーン・23 step・main 5 / detail 23）。PDF 経路の 10 本はすべて式チェーン 0 で、詳細層は
  94〜623 の主張の列だった。

### 3.2 実測 2 — arXiv-2407.01221v2（TeX 経路・外枠の試作）

試作は読み取りのみ・LLM 0 回のスクリプトで行った（スクリプトはリポジトリに置いていない。手順は
§5 の規則が正本で、Phase 0 のゴールデンテストが同じ結果を再現することを受け入れ条件にする）。

- チェーンは equation_chain 10 本 + system_level 1 本。step は chain × step で 23、重複を除くと 21
  （1 つの step が 3 本のチェーンに重複して現れる）。式の DAG は 31 式 / 53 レコードで、source 11・
  sink 1（`eq_skewness_bias_ex`）だった。
- system_level チェーンの唯一の step は「29 式 → 1 式」の一括 `eliminate` である。これを step として
  モジュールの成員に含めると、全式が「外で消費されている」扱いになり接点が膨らむ。→ **system_level の
  step はモジュールの成員ではなく、結果の吸い込み口（sink）として扱う。**
- 入次数 0（どの step も生まない）で、3 つ以上の step に消費される式があった（`eq_delta_bias_real` は
  7 step、`eq_tex_b24` と `eq_tex_b64` は各 5 step に消費される）。これらは至る所で入力に使われる
  **共有の基礎**で、配線でいう電源レールに当たる。接点として数えると全モジュールの接点が広がる。
  → **共有の基礎は接点に数えない。**
- 上の 2 つを入れたうえで、隣接する step を「接点（外から受ける式 + 外へ出す式）の合計 ≤ k を保つ限り、
  併合後の接点が最小になる対から」貪欲に併合した。k=2 で 13 モジュール、**k=3 で 8 モジュール**に
  なった。
- k=3 の 8 モジュールのうち基礎側の 7 つは、理論の区切りと一致した: フーリエ空間の密度揺らぎの定義 /
  摂動核 F_n の定義 / Horndeski 理論の核係数への制約 / バイアス模型の ansatz / バイアス核 Z_2, Z_3 と
  銀河密度の展開 / 平滑化場とその分散・スペクトルモーメント / 線形パワースペクトルと最低次の分散。
  物理の読み手が切る区切りと同じだった。
- 一方で計算本体（角度積分の機構 b64〜b76 + 核の陽な形 b78〜b85 + 歪度の結果）は、10 step が
  1 モジュールに固まった。k=2 ではこれが 6 つに割れるが、接点 5 のまま併合できずに残る単独 step
  （不格好な断片）も出る。→ **モジュールは 2 段（外枠 k=3、内側は k=2 で再分割）が自然**という観察。
- 導出リンクに循環が 1 つあった（`eq_tex_b78 ↔ eq_tex_b80`）。データ品質の観察として記録する。
  本層では循環に関わる step を併合の対象から外し、事実文で報告する（§5.5）。
- モジュール内の step の edge_type の多重集合（例「defines×2」「substitutes×5 / normalizes / defines×3 /
  approximates」）は、分野語を含まない**構造の指紋**になる（§6）。

上の例示の物理用語は実測の記録であり、コードにも UI の固定文にも書かない（TM5）。UI のラベルは
論文データから組む（§5.6）。

### 3.3 実測 3 — 2609.15375v1（TeX 再取り込み後）

同じ論文を arXiv の e-print（TeX アーカイブ）から取り込み直し（2026-09-23、cartridge と
モデルは PDF 版の run と同一）、同じ読み取りスクリプトで数えた。

| 項目 | PDF 版（§3.1） | TeX 版 |
|---|---|---|
| 主グラフ（main） | 4 | 5 |
| 式の詳細（equation_detail） | 470 | 385 |
| 式レコード | 64（全件が導出から除外） | 83 |
| 式チェーン / system_level / claim チェーン | 0 / 0 / 15 | 13 / 4 / 12 |
| 重複を除いた式 step | 0 | 35（chain×step では 81。1 step が最大 3 チェーンに現れる） |
| 式 DAG | なし | 47 式・source 12・sink 12・連結成分 4（最大 56 式の成分に大半が入る） |
| detail ノードのうち `parent_component_id` あり | 0 / 470 | 92 / 385 |
| detail ノードの裏付け | 全件 partially_source_backed | source_backed 23 / partially 362 |

観察:

- **式の層は TeX 経路で立った**。式チェーン 13 本・重複を除いて 35 step の式 DAG が得られ、
  §5 の規則を掛けられる状態になった。PDF 版で全滅していた原因が経路にあることが同一論文で確認できた。
- **claim チェーンは TeX 版でも detail の大半を占める**。385 のうち claim チェーン由来は約 300
  （`infer_intermediate_claim` 231 を含む）で、式 step は約 80。式の層が立っても、主題 B（§7）を
  解かない限り「式の詳細」層は主張の列に埋まる。
- **§5.3 の「共有の基礎」規則（入次数 0 かつ消費 3 以上）では、この論文の計算本体が割れすぎた**。
  k=3 で 15 モジュールになるが、そのうち 5 個は接点 4〜5 の単独 step で、隣と併合できずに残った断片
  だった。原因は `eq_tex_b210`（線形バイアス関係の導入。10 step に消費される）と `eq_tex_b194`
  （平均密度の定義。5 step に消費）が**入次数 1 のため共有の基礎に数えられず**、接点として計上された
  ことにある。どちらも「一度導いて以降は前提として至る所で使う」式で、性格は共有の基礎と同じ。
- 規則を「**入次数を問わず、消費する成員 step が 4 以上**」に変えると k=3 で **10 モジュール**になり、
  断片が消える。基礎側 5 個（推定量の近似 / 質量差の導出 / バイアス制約 / 面密度と臨界面密度の定義 /
  重み関数の定義）と結果側 3 個は理論の区切りとして読める。計算本体（相互相関 → ペア数の観測量、
  b184〜b224）は 17 step の 1 モジュール（接点 0 = 共有の基礎と結果だけで閉じている）になり、
  2407.01221v2 と同様に**内側の 2 段目**が要る形になった。同じ規則を 2407.01221v2 に掛けると
  8 モジュールのまま変わらない（`eq_tex_b97` が基礎に加わるが分割は同じ）。
- 導出リンクの循環がここにもある（`eq_tex_b190 → b192 → b186 → b190`）。§5.5 の扱いが要る。
- 内側 2 段目の k=2 は、この論文では計算本体を 15 個前後の 1〜2 step の断片に割った。内側の規則は
  接点だけでは足りず、2407.01221v2 と本論文の 2 例で磨く（Phase 1）。

§5.3 の規則はこの実測を受けて「入次数を問わない」形に改める（§5.3 の追記）。

### 3.4 関連する観察（本書の主題ではない）

- PDF 経路 7 本の式レコード数が揃って 64 だった。旧上限（再現性レビュー E-2 で既定を「上限なし」に
  変える前）の名残と思われる。これらの run は是正前のもので、再解析すれば変わる見込み（仮説・未確認。同一論文の TeX 版 run では 83 だった — §3.3）。

---

## 4. 全体像

### 4.1 四つの表示の関係

| 表示 | 実体 | 何を表すか | 本書での扱い |
|---|---|---|---|
| 主グラフ（stage） | `graph_json` の main 層（既存） | どの種類の操作か（7 語彙の集約） | データは不変（TM1）。他の消費者（discuss 開幕・近傍関係ビュー・文脈レンズ・参照の健全性）も不変。画面上の位置づけは §9 O-3 |
| **理論モジュール**（本層） | 読み時導出（Phase 0） | どこからどこまでが一つの部分系か・何を受けて何を返すか | 新設。stage はモジュール内の色分け |
| 式の詳細 | `graph_json` の equation_detail 層（既存） | 式の操作 1 step = 1 ノード | 式の step だけを残す方向（§7） |
| 主張の列 | 現状は equation_detail 層に混入 | 章内の主張の出現順 | 論文層の章アウトラインへ移す方向（§7） |

### 4.2 読む向き

- **外枠から中へ**: 外枠モジュール（k=3）→ 内側モジュール（k=2）→ 成員の式 step（式の詳細層のノード）。
- **モジュールから論文へ**: 接点の式（印字番号）から論文層の「論文での対応」へ。
- **モジュールから転用へ**（Phase 1）: 工程の型と接点の形が一致する別論文のモジュールを、同一性候補
  として示す。

構造を読む画面の主役はモジュール図に移す。ただし主グラフ（stage）は `graph_json` の正本として残す。
二つを同時に満たすための配置は §9 O-3 に推奨付きで置く。

---

## 5. モジュール導出の規則（決定論・非LLM）

正本は実装後のコードとし、本節は仕様として書く。実装場所は `backend/core/theory_modules/`
（`schema.py` / `builder.py`。FastAPI・sqlalchemy・LLM 非 import の純関数）を想定する。

### 5.1 入力

- `artifacts["derivation_chain"]["chains"][]`（採用 run。`document_run_artifacts(..., policy="adopted")`
  を route で 1 回読む）。
- `graph_json`（`_normalize_stored_component_graph` 経由。step ↔ 式の詳細ノードの対応と stage のため）。
- `artifacts["equation_semantics"]`（式の印字番号・復元由来の印）。
- `artifacts["claim_object_builder"]`（出力式に結び付く atomic claim。理論対象のラベルのため）。
- `artifacts["component_assembly"]`（照合用。分割の入力にはしない = TM4）。

### 5.2 成員にする step

1. `chain_type ∈ {equation_chain, mixed_chain}` のチェーンの step のうち、`input_equation_ids` と
   `output_equation_ids` の両方が空でないものだけを対象にする（TM3）。
2. `chain_type == "claim_chain"` の step は対象にしない（主張の列であって式の依存ではない — §7）。
3. `chain_type == "system_level"` の step は成員にせず、**sink**（結果の吸い込み口）として別に持つ。
4. 同じ step が複数チェーンに現れるときは `(operation, 入力式の集合, 出力式の集合)` で重複を除き、
   元の出現は合成 ID `{derivation_id}:{step_id}`（`derivation_step_ref`）で全部残す（TM8）。

### 5.3 共有の基礎

`SHARED_FOUNDATION_MIN_CONSUMERS`（コード定数・既定 **4**）以上の成員 step に消費される式を
**共有の基礎**とする。入次数は問わない（式が他の step から導かれたものでもよい）。共有の基礎は接点に
数えない。各モジュールは自分が使う共有の基礎を別リストで持ち、画面では「共通に使う式」として別区画に
出す。

実測 2 の試作は「入次数 0 かつ消費 3 以上」で行ったが、実測 3（§3.3）で「一度導いて以降は前提として
至る所で使う式」（入次数 1・消費 10）が接点に計上され、計算本体が接点 4〜5 の単独 step に割れた。
規則を入次数不問・消費 4 以上に変えると 2609.15375v1（TeX）で 15 → 10 モジュール、2407.01221v2 は
8 のまま。Phase 0 のゴールデンテストはこの規則で数え直した値（2407.01221v2: 外枠 8）を受け入れ条件に
する。閾値 4 は 2 例からの暫定値で、変えるときは本書に実測を足して変える（§5.4 の k と同じ扱い）。

### 5.4 接点と併合

- 成員 step の集合 S について:
  - **外から受ける式** = S の step が消費し、S の中では生まれない式（共有の基礎を除く）。
  - **外へ出す式** = S の中で生まれ、S の外の成員 step に消費される式。加えて、どの step
    （sink を含む）にも消費されない式は論文の結果として外へ出す式に数える。**sink にだけ消費される
    式は外へ出す式に数えず**、sink へ渡す式として sink 側に記録する。**sink 自身も生む式**（系レベルの
    一括操作が最終結果を出し、同じ式を成員 step も出す場合）は sink 側の結果として扱い、外へ出す式に
    数えない（実装で確定。字面どおり数えると 2407.01221v2 の計算本体が 4+6 に割れて外枠 9 になる）。
  - **接点の本数** = 外から受ける式 + 外へ出す式。
- 初期状態は 1 成員 step = 1 モジュール。
- 隣接（一方が生む式を他方が消費する）するモジュールの対のうち、併合後の接点の本数が k 以下のものを
  候補にし、併合後の接点が最小の対を併合する。同点は ①併合後の成員数が少ない ②成員の出現順
  （`(derivation_id, step_index)` の最小値）が早い の順で割る（TM2）。
- 併合によってモジュール間に循環ができる対は候補にしない。
- 候補が無くなるまで繰り返す。
- **外枠**は `OUTER_INTERFACE_LIMIT = 3`、**内側**は外枠モジュールごとにその成員だけで
  `INNER_INTERFACE_LIMIT = 2` で同じ手続を回す。内側が 2 つ以上に割れた外枠モジュールだけに内側を持たせる。
- k は環境変数で緩めないコード定数とする（SA7 と同じ扱い）。値を変えるときは本書に実測を足して変える。

試作（§3.2）との一致は Phase 0 のゴールデンテストで確かめる。arXiv-2407.01221v2 の artifact を
fixture にし、外枠 8 を再現することを受け入れ条件にする（k=2 の値は §5.3 の規則変更後に数え直して固定する）。数え方が試作と食い違ったら、
本節を実測に合わせて直し、実装記録に残す。

### 5.5 循環と併合できない step

- 式の依存に循環（強連結成分に 2 つ以上の式）があるとき、その循環に関わる step は**強連結成分ごとに
  1 つの初期モジュールにまとめ**、そのうえで §5.4 の併合に参加させる（循環する手順は互いに依存して
  いるので、切り離すより 1 まとまりとして扱うのが構造として正しい。切り離すと循環が計算本体の中にある
  2 論文で本体が割れ、2407.01221v2 が 11・2609.15375v1 が 16 になった — 実装で確定）。`facts[]` に
  「導出のつながりに循環があるため、次の式を含む手順はひとまとまりとして扱っています: 式 (…)」を
  1 行足す。
- 接点が単独で k を超え、どの隣とも併合できない step も単独のモジュールとして残す。欠落にはしない（TM8）。

### 5.6 モジュールが持つもの

| 属性 | 中身 | 出所 |
|---|---|---|
| 成員 | 重複を除いた step（合成 ID の全出現・対応する式の詳細ノード ID） | 5.2 |
| 接点 | 外から受ける式 / 外へ出す式 / 使う共有の基礎 / 要求される前提（成員 step の `required_claim_ids` と `assumption_ids` の和） | 5.3 / 5.4 |
| 工程の型 | 成員 step の edge_type（`classify_operation`）の多重集合。**UI には出現順に重複を除いた動詞の列だけ**を出す（多重度は内部指紋 = TM6） | `component_graph/schema.py` |
| stage の色 | 成員の stage（`stage_for_edge_type`）。モジュールの色は最多の stage（同数は `THEORY_STAGES` の順で早い方）。詳細ペインには含まれる stage を全部並べる | 同上 |
| 理論対象 | 外へ出す式に結び付く atomic claim の本文。本文由来の主張を先に使い、式から機械合成した主張（`equation_claim_synthesis` の定型文「… defines $S$.」「…, $T$ depends on …」）は本文を並べず**対象の記号 $S$ / $T$ だけを取り出して列挙**する（実測: 本文由来の主張は `equation_ids` を持たないため、実際には合成主張の記号が理論対象になる）。どちらも無ければ式の印字番号。主張の `equation_ids` がモジュールの扱う式の範囲をはみ出すもの（系レベルの主張）は使わない | `equation_claim_synthesis` の定型文 |
| ラベル | 「工程の型 + 理論対象」。工程の型は動詞の訳（`element_vocab.OPERATION_LABELS`）、stage 名は `element_vocab.THEORY_STAGE_LABELS`。分野語はコードに無く、論文の主張本文からだけ入る（TM5）。`label` は `$…$` を含み詳細ペインで数式として描く。ノードのラベルは `visual_label`（`$…$` をプレーンテキストに落としたもの。ギリシャ文字・装飾コマンドの最小変換のみ） | 5.6 |
| 裏付け | 成員の `source_backing_status` の最も弱いもの。復元由来の式（`must_not_treat_as_source_extracted`）しか裏付けの無い成員を含むなら `partially_source_backed` 止まり（再現性レビュー R-1 と同じ規則） | 既存 |
| 部品との照合 | 外へ出す式を `output_equation_ids` に持つ component_assembly の部品名。「照合用（AI の原案）」と明示して並べるだけで、一致度は出さない（TM4 / TM6） | component_assembly artifact |

### 5.7 モジュールの読み時キー

Phase 0 は保存しないが、画面の選択状態と Phase 1 の参照のために決定論のキーを持つ。

- `module_key = "m1:" + sha256(document_id + 成員 step が生む式の equation stable_key の昇順列 + level)[:32]`。
- 式の stable_key は内容由来（KO2）なので、再解析で同じ式が出ればキーも同じになる。derivation step の
  stable_key は `derivation_id` と step の位置を材料に含む（KO2 の明示例外）ため、材料に使わない。
- 規則の版（`m1`）を先頭に置く。§5.4 の規則や k を変えたら版を上げ、旧キーとの対応は取らない
  （Phase 0 は保存しないので、対応を取る必要が無い）。

---

## 6. 転用 — 構造の指紋と同一性候補（Phase 1）

- **構造の指紋** = （工程の型の多重集合, 外から受ける式の本数, 外へ出す式の本数, 前提の有無）。
  分野語を含まない。内部でだけ使い、UI・DTO に出さない（TM6）。
- 別論文のモジュールと指紋が一致したら、同一性の**候補**にする。受け皿は既存の
  `library_entries`（`entry_type` は `theory` か `method`）と `identity_candidates` の経路で、新しい
  格納庫を作らない。`mapping_justification` には構造の一致を表す語彙を 1 つ足す（語彙表への追加。
  `core/schema.py::MAPPING_JUSTIFICATIONS` と語彙表シードを同じ変更で揃える）。
- 小さすぎるモジュール（成員が少ない・工程の型が 1 種類）は一致しやすく情報が少ないので候補にしない。
  下限はコード定数にし、実測で決める。
- 候補は `review_status='candidate'` で始まり、確定は教員の操作だけ（TM7）。候補行を `library_entries`
  に置いてよいかは概念レジストリの O-6（裁定待ち）に従う（§9 O-4）。
- 同一性リンクは確定した人間の判断を指すので、参照先のモジュールが再解析で黙って消えると判断が宙に
  浮く（辞書の `reexecution-overwrites-human-decision` 族）。保存の要否はこの点で決まる（§9 O-1）。

---

## 7. 主題 B — claim チェーンの位置づけ

### 7.1 現状

- derivation_chain は、使える式が 1 本も無いとき claim チェーンだけを作る（`derivation_equation_only_fallback`）。
  2026-09-19 以降は、式チェーンが覆わない章を claim チェーンで補う併走もある
  （`derivation_claim_chain_complement`）。したがって TeX 経路の論文でも、再解析後は詳細層に主張の列が
  混ざり得る。
- component_graph の正規化は `chain_type` を区別しない。claim チェーンの step も
  `component_type="EquationOperationNode"` / `graph_layer="equation_detail"` のノードになる。
- claim チェーンの step は「前の主張」を次の step の `input_claim_ids` / `required_claim_ids` に置く。
  論文中の順序が、依存の顔で記録されている。
- 表示名は「式の詳細」、`element_vocab.CHAIN_TYPE_LABELS` の claim_chain の訳は「主張の導出」で、
  どちらも実体（章内の主張の出現順）と合わない。
- 論文層（`core/graph_paper_layer`）の章アウトラインは、すでに章ごとに主張を論文順で並べ、主張に
  結ばれたノードのチップを吊っている。主張の列の受け皿は既にある。

### 7.2 選択肢

| 案 | 中身 | A層 | 影響 |
|---|---|---|---|
| **(a-読)** 新しい層として読み時に分ける | backend の射影で、`linked_derivation_ids` が claim_chain だけに属する詳細ノードを `claim_sequence` として区別し、グラフレビューの層トグルに「主張の列」を足す | 非改変 | `graph_json` を読む他の消費者（参照の健全性・文脈レンズ・グラフ全体対話の grounding・export）は `equation_detail` のまま読むので、画面と他所で層の意味が食い違う。辺は引き続き描かれ、順序が依存に見える |
| **(a-A)** 新しい層を A層で付ける | component_graph の正規化が claim_chain 由来のノードに `graph_layer="claim_sequence"` を付ける | 改変 | 層語彙の契約変更。`graph_layer` を読む約 10 箇所（フロントの層フィルタ・`reference_health.CHECKED_GRAPH_LAYERS`・学習者向け遮断・孤児検出・export）の追随と、保存済みグラフの再解析が要る |
| **(b-読)** 主張の列をグラフのノードにしない（読み時） | グラフレビューのキャンバスは claim_chain 由来の詳細ノードを描かない。主張の列は論文層の「論文の順」の章アウトラインで見る（既存の受け皿）。式の詳細層が空になる教材では、その事実を事実文で出す | 非改変 | キャンバスから順序の偽の辺が消える。主グラフ（stage）は不変。他の消費者も不変。詳細ノードは graph-native ID で承認対象ではないため、承認の導線は失われない |
| **(b-A)** 主張の列をグラフに入れない（A層） | component_graph が claim_chain の step から詳細ノードを作らない。あわせて claim チェーンが順序を `required_claim_ids` に書くのをやめる | 改変 | 最も正直だが、式の層が無い論文では主グラフの stage ノードも消える（2609.15375v1 の main 4 はすべて claim チェーン由来）。derivation step の知識行（`knowledge_derivation_steps`）の意味も変わる |
| **(c)** 現状維持・表示名だけ変える | 「式の詳細」を「詳細（式の手順・主張の並び）」に、「主張の導出」を「主張の並び」に変える | 非改変 | 最小の変更。数百ノードの一直線 path がキャンバスに残り、構造で切っても章分けしか出ない問題は解けない |

### 7.3 推奨

**(b-読) を Phase 0 で採る。(b-A) は実測 3 の後に別の判断として問う。**

- 主張の列は論文の順序であり、論文層がすでに論文の順序を背骨に持っている。キャンバスに描くと、
  順序が依存の辺に見える（原則 8 出所の正直さ）。
- A層を触らずに、画面の問題（470 ノード・表示名の不一致・構造で切ると章に戻る）が解ける。
- 本層（モジュール）は §5.2 で claim チェーンを成員にしないので、(b-読) と同じ線引きになる。
- (c) だけでは主題 A が成り立たない。(a-読) は画面と他の消費者で層の意味が割れる。
- claim チェーンの `required_claim_ids` に順序を書くこと自体は、依存の捏造に近い（辞書の
  `fallback-fabricates-missing-link`）。ただし直すには derivation step の知識行の意味を変える必要があり、
  式の層が TeX 再取り込みでどこまで再現されるか（実測 3）を見てから判断する方が後戻りが少ない。

(b-読) で足すもの:

- グラフレビューの層フィルタで、`linked_derivation_ids` がすべて claim_chain に属する詳細ノードを
  キャンバスから外す。判定は backend の射影（`build_theory_modules` と同じ入力）で行い、フロントは
  射影が付けた目印だけを見る（判定をフロントに再実装しない）。
- 式の詳細層が空になった教材では、キャンバスの代わりに事実文を出す:「この教材では式の導出を再現できて
  いません。主張の並びは『論文の順』で見られます。」
- `element_vocab.CHAIN_TYPE_LABELS` の claim_chain の訳を「主張の並び」に直す（語彙表の訂正。逐語ミラー
  `frontend/public/js/element-vocab.js` と、訳を固定している既存テスト
  `backend/tests/test_deliberation_context_lens.py` を同じ変更で揃える）。

---

## 8. 実装計画

### 8.1 Phase 0 — 読み時導出 API + 層トグル「理論モジュール」

**API**: 新しいエンドポイント `GET /api/admin/documents/{document_id}/theory-modules` を
`routes/theory_components.py` に足す（TEACHER + `_ensure_document_viewable`）。

既存の `GET .../component-graph` への additive にしない。理由は次の 3 つ。

- component-graph は原稿スタジオも使い、`ComponentGraphResponse` の型とテストが多い。応答の肥大と副作用を
  避ける（論文層と同じ判断 — [graph_paper_layer_design.md](graph_paper_layer_design.md) §8）。
- モジュールの導出には derivation_chain 以外の artifact も要り、計算を遅延取得に分けたい。
- 失敗時に `available:false` + 事実文で 200 を返す縮退を、グラフ本体と独立に持てる。

DTO の骨子（数値キー・confidence・指紋を持たない）:

```jsonc
{
  "document_id": "…",
  "available": true,                     // 式の step が 0 → false + 事実文
  "facts": ["…"],                        // TM8 の事実文（循環・欠落・式の層が無い 等）
  "rule_version": "m1",
  "modules": [
    {
      "module_key": "m1:…",               // 5.7。表示しない
      "level": "outer" | "inner",
      "parent_module_key": null | "m1:…",
      "label": "…",                       // 工程の型 + 理論対象
      "process_verbs": ["定義", "代入"],   // 出現順・重複なし（多重度は出さない）
      "theory_object": "…",
      "stage_keys": ["theory_basis"], "dominant_stage": "theory_basis",
      "source_backing_status": "source_backed" | "partially_source_backed" | "review_required",
      "inputs":     [ { "equation_id": "…", "display_label": "式 (3)" } ],
      "outputs":    [ { "equation_id": "…", "display_label": "式 (7)" } ],
      "foundation": [ { "equation_id": "…", "display_label": "式 (1)" } ],
      "required_claims": [ { "claim_id": "…", "text": "…(≤200字)" } ],
      "assumption_ids": ["…"],
      "members": [ { "step_refs": ["deriv_…:step_002"], "node_ids": ["eq_op_0012"],
                     "operation_label": "代入", "input_labels": ["式 (3)"], "output_labels": ["式 (4)"] } ],
      "components_for_comparison": [ { "name": "…" } ],   // 照合用（AI の原案）
      "isolated_reason": null | "cycle" | "interface_too_wide"
    }
  ],
  "edges": [ { "source": "m1:…", "target": "m1:…", "equation_labels": ["式 (7)"] } ],
  "sinks": [ { "operation_label": "消去", "input_labels": ["式 (7)", "…"], "output_labels": ["式 (20)"] } ],
  "claim_sequence_node_ids": ["eq_op_…"]  // (b-読) の目印。キャンバスから外す詳細ノード
}
```

**UI**（`admin-graph-review.js`・ES5・GR8 委譲）:

- 層トグルに「理論モジュール」を足す。原稿スタジオと共有の `graphView.layerOptions` は変えず、
  グラフレビュー側でボタンを 1 つ足す（スタジオ側の挙動を変えない）。**このボタンには件数を付けない**
  （TM6。既存の層ボタンの件数表示は本書の範囲外）。
- モジュール図はモジュールをノード、モジュール間の式の受け渡しを辺にした合成グラフとして描く。描画は
  `graphView` の既存関数に合成ノードを渡して行い、新しい描画経路を作らない（GR8）。ノードの色は
  `dominant_stage`、枠は `source_backing_status` の既存の表現（通常 / 細線 / 点線）。sink は別の形で
  1 つずつ描く。辺のラベルは式の印字番号だけ。
- 外枠モジュールを選ぶと詳細ペインに、ラベル・工程の動詞列・含まれる stage・外から受ける式・外へ出す式・
  共通に使う式・要求される前提・部品との照合（「照合用（AI の原案）」）・内側モジュールの一覧・成員の
  step を出す。成員の step を押すと式の詳細層に切り替えて当該ノードへ寄せる（論文層の章チップと同じ
  `focusNodeOnce` の経路）。
- `available:false` のときは、ボタンを押すと事実文だけを出す（「この教材では式の導出が再現されて
  いないため、理論モジュールを組めません。」）。
- (b-読) の目印を層フィルタに適用する（§7.3）。
- 画面文脈アダプター（[assistant_screen_adapter_design.md](assistant_screen_adapter_design.md)）の
  `getScreenContext` に `view.layer = "module"` と選択中の `module_key` を足すかは Phase 0 では行わず、
  解決器を 1 本足すときに同設計書へ節を足す。

**管理UI 3 点セット**: 操作要素が増えるので、`docs/manual/teacher/26-admin-graph-review.md` に節
（例 `{#theory-modules}`）+ `backend/core/help_kb/admin_ui_anchors.py` の KNOWN と ADMIN の両方 +
`data-ui-anchor`（例 `graph-review.module-view`）を揃える。件数の正は `test_admin_help_ui_anchors.py`。

**ガードレール**（候補名）:

- `test_theory_module_core.py`: 成員の選別（claim_chain を入れない・system_level を sink に）/ 重複除去 /
  共有の基礎 / 接点の数え方 / 同点の決定論順 / 循環の単独化 / 外枠と内側 / `module_key` の決定性 /
  arXiv-2407.01221v2 fixture での外枠 8 のゴールデン（§5.3 の規則で数え直す） / 2609.15375v1 相当の fixture で
  `available:false`。
- `test_theory_module_guardrails.py`: core が fastapi / sqlalchemy / core.llm / embedding を import しない /
  DTO に `confidence` / `weight` / 指紋・k・本数に当たるキーが再帰的に無い / 入力 dict を mutate しない /
  `graph_json` を書き換えない / 特定分野語の denylist（ラベル組み立てに固定の分野語が無い）/
  k が環境変数から読まれない。
- `test_theory_module_api.py`: 権限ゲート（viewable）/ artifact 欠落時の fail-soft / 既存 component-graph
  応答の不変。
- `test_theory_module_ui_static.py`: ES5・graphView 委譲・内部 ID 非描画・件数非表示・`available:false`
  の縮退文言・(b-読) の目印の適用・アンカー。

**開発チェックリスト §5-1**: 既存ルーターへのエンドポイント追加なので `docs/backend/api.md` に 1 行足す。
パイプラインステージ・migration の追加は無い。

### 8.2 Phase 1 — 内側 2 段目の磨き込みと、工程の型による同一性候補

- 実測 3 と Phase 0 の運用を見て、k・共有の基礎の閾値・同点規則を本書に実測付きで調整する。
- 構造の指紋（§6）の計算と、別論文のモジュールとの一致による同一性候補の生成。候補は
  `identity_candidates` の経路に規則を 1 本足す形にし、非LLM・非致命のまま保つ。
- 保存の要否（§9 O-1）を着手前に決める。保存するなら knowledge object の作法（stable_key・supersede・
  `_live` ビュー・再係留）に従う。
- ナレッジライブラリタブの「同一性の候補」区画に、モジュール由来の候補を出す（既存の区画・アンカーを
  使い、新しい画面を作らない）。

### 8.3 非スコープ

- 学習者向け表示（理論モジュールは教員のレビュー画面だけに出す）。
- LLM によるモジュールの命名・要約・境目の提案（境目は構造で切る = 本書の前提）。
- 主グラフ（stage）の集約規則の変更（#308 の規律は変えない）。
- PDF 由来の式の vision OCR（再現性レビュー §5.2 の別件）。
- モジュールをコース topic の単位にすること（学ぶ単位層の責務）。
- 全論文を横断してモジュールを一枚に描く画面（原則 6 egocentric のみ）。

---

## 9. オーナー判断

問うのは①不変条項の解釈変更 ②人の権利・制度 ③後戻りしにくい構造 の 3 種だけとする
（[improvement_cycle.md](../architecture/improvement_cycle.md) §2.2）。実装方式（新エンドポイント・
k の値・同点規則・ラベルの組み方）は本書で決め、問わない。

**裁定（2026-09-23）**: O-1〜O-3 は下表の推奨どおり採用。O-4 は O-6 の裁定に従属（新しい判断を立てない）。

| # | 判断 | 種別 | 選択肢 | 推奨 |
|---|---|---|---|---|
| O-1 | 理論モジュールを保存するか、読み時導出にするか | ③ 後戻りしにくい構造（知識オブジェクトの種類 `KNOWLEDGE_OBJECT_KINDS` を 1 つ増やすか） | (a) 常に読み時導出（同一性候補も読み時キー `module_key` で指す）/ (b) Phase 0 から stable_key 付きの knowledge object として保存 / (c) Phase 0 は読み時導出、Phase 1 の同一性候補の着手前に knowledge object として保存する | **(c)**。Phase 0 の目的は外枠の仕様が理論と合うかの確認で、規則は実測で変わる。保存すると規則を変えるたびに supersede と再係留が要る。一方で Phase 1 の同一性リンクは人間の確定を指すので、再解析や規則の変更で参照先が黙って消えないよう、KO3（supersede）と KO8（再係留）の保護が要る。(a) はそれを持てない |
| O-2 | claim チェーンの扱い | ③（`graph_layer` の意味は約 10 の消費者との契約）+ ①（A層を触るか） | §7.2 の (a-読) / (a-A) / (b-読) / (b-A) / (c) | **(b-読) を Phase 0 で採り、(b-A)（claim チェーンが順序を `required_claim_ids` に書くのをやめ、グラフのノードにもしない）は実測 3 の後に改めて問う**。理由は §7.3 |
| O-3 | 主グラフ（stage）を残すか置き換えるか | ①（#308「main は theory stage のバックボーン」の解釈）+ ③（main 層を読む消費者） | (a) main をそのまま残し、モジュールは追加の表示にする / (b) main を残したうえで、グラフレビューの初期表示をモジュール図にする（モジュールが導出できる教材だけ。導出できなければ従来どおり main）/ (c) main をモジュールに置き換える | **(b)**。`graph_json` の main 層・#308 のラベル規律・他の消費者（discuss 開幕・近傍関係ビュー・文脈レンズ・参照の健全性）は変えない。構造を読む画面では、境目を持つモジュール図の方が目的（構造を見抜く）に合う。(c) は消費者の全面追随が要り、PDF 経路の論文では置き換え先が空になる |
| O-4 | 構造の指紋から作る同一性候補を `library_entries` の candidate 行に置いてよいか | ①（L層「昇格は人間の操作のみ」の読み替え） | 概念レジストリの **O-6（裁定待ち）に従属させる**。新しい判断を立てない | O-6 の裁定に従う。O-6 が否なら、候補を別表に置く設計に差し替える（影響は Phase 1 の保存先だけ） |

---

## 10. 設計時の検査（pre-mortem）

本書の変更には「派生物」「ID や版」「再実行」「語彙表の新設」が含まれるので、
[課題ナレッジの辞書](../issue_knowledge/dictionary.md) の該当する族を先に当てた。

| 族 | 当て方 | 本書での扱い |
|---|---|---|
| 派生物と配線（`derivative-and-wiring`） | 読み時導出は古い派生物を配らない。ただし「作ったが画面に配線しない」（`available-but-unwired`）に注意 | Phase 0 で API と層トグルを同時に出す |
| 同一性の表現（`identity-representation`） | モジュールのキーが版をまたいで安定するか（`id-not-stable-across-versions`） | 5.7 で位置由来の材料を避け、式の stable_key と規則の版から作る |
| 再実行と削除（`reexecution-and-deletion`） | 再解析で人間の確定（同一性リンク）が宙に浮かないか | O-1 の推奨 (c)。Phase 1 の前に supersede の作法へ移る |
| 正本の分裂（`canonical-source-split`） | stage の訳語・操作動詞の訳語を新しく作らないか | `element_vocab` の既存表だけを使う（TM5） |
| 確定の担当（`human-decision-authority`） | 指紋の一致を確定として扱わないか | 候補まで（TM7）・O-4 |
| 契約（`contract-between-stages`） | 位置・順番で代替物を埋めて根拠に見せないか（`fallback-fabricates-missing-link`） | claim チェーンの順序を式の依存に数えない（TM3・§7） |

---

## 11. 開発チェックリスト §5 対応

- 5-1: Phase 0 の実装時に `docs/backend/api.md` を更新する（既存ルーターへの追加）。設計段階では該当なし。
- 5-2: 状態ヘッダあり（設計中）。
- 5-4: migration は Phase 0 に無い。Phase 1 で保存する場合も、番号は実装時に採番する。
- 5-5: 本書と参照先はリポジトリ内。試作スクリプトは正本ではなく、規則の正本は §5。
- 5-6: 件数は実測の時点付き記録（§3）か、テスト参照にする。
- §6（課題ナレッジ）: 本書の起票に合わせて 2 件を記帳した
  （[IK-0353](../issue_knowledge/entries/IK-0353-claim-sequence-occupies-equation-detail-layer.md) /
  [IK-0354](../issue_knowledge/entries/IK-0354-structure-cut-recovers-chapters-when-equations-lost.md)）。

---

## 12. 実装記録

### 12.1 backend（Phase 0, 2026-09-23）

読み時導出の core・API・テストを実装した。migration なし・LLM 0 回・embedding 0 回・保存なし。

**作ったもの**

| ファイル | 中身 |
|---|---|
| `backend/core/theory_modules/schema.py` | 定数（`RULE_VERSION = "m1"` / `OUTER_INTERFACE_LIMIT = 3` / `INNER_INTERFACE_LIMIT = 2` / `SHARED_FOUNDATION_MIN_CONSUMERS = 4`。**環境変数から読まない**）・`FORBIDDEN_KEYS`・事実文・式の表示ラベル（`label` → 「式 (N)」、無ければ「番号なし: 要約（`semantics.summary`）の先頭 40 字」、レコードが無ければ「番号なし」） |
| `backend/core/theory_modules/builder.py` | `build_theory_modules(*, document_id, artifacts, graph_json) -> dict`（FastAPI / sqlalchemy / `core.llm` / embedding 非 import の純関数。入力を mutate しない・例外を外に出さない）。A層語彙（`classify_operation` / `stage_for_edge_type` / `THEORY_STAGES` / `derivation_step_ref`）は関数内 import で、読めない環境では stage を空にして縮退する（分割は stage に依存しない = TM4） |
| `backend/api/routes/theory_components.py` | `GET /api/admin/documents/{document_id}/theory-modules`（TEACHER + `_ensure_document_viewable`・`response_model=None`）と `build_theory_modules_for_document(document_id)`。artifact は `document_run_artifacts`（adopted）、graph は `_normalize_stored_component_graph(_stored_component_graph(...))`。保存済みグラフが無くても component 一覧からの組み立てには落とさない（詳細ノードを持たないため）。builder 例外は `available:false` + 事実文で 200。既存 `component-graph` は不変 |
| `backend/tests/test_theory_module_{core,guardrails,api}.py` | 下記 |
| `docs/backend/api.md` | エンドポイント 1 行 |
| `backend/tests/test_indicator_catalog_guardrails.py` | 新 GET を「単一オブジェクトの取得」除外に 1 行登録（IG4 の網羅検査。論文層と同じ扱い） |

**DTO**: §8.1 の骨子 + additive 4 点 — `sinks[].source_module_keys`（その sink へ式を渡す外枠モジュール。UI が
sink への辺を推測で結ばないため）/ `edges[].level`（`outer` / `inner`。inner の辺は同じ親の兄弟どうしだけ）/
`members[].stage_key` / top-level `foundations[]`（`equation_id` / `display_label` / `producer_module_keys` /
`consumer_module_keys`。共有の基礎を生むモジュールが接点の外に消えないように = TM8）。`assumption_ids` の要素は
derivation_chain artifact の `assumption_ids` がそのまま持つ**前提の本文**（200 字で丸め）で、ID ではない。
`available:false` のときも `claim_sequence_node_ids` は埋める（(b-読) の目印は式の層の有無と独立）。
DTO には数値の値が 1 つも無い（ガードレールが再帰走査で固定）。

**fixture ごとの実測**（規則は下記の逸脱込み）

| fixture | 外枠 | 外枠の成員数 | 内側 | 共有の基礎 | 循環 | 主張の並びの目印 |
|---|---|---|---|---|---|---|
| 2407.01221v2（TeX） | **8** | 1,1,1,1,2,2,2,10（基礎側 7 + 計算本体 1） | 5（計算本体の中。うち 1 つが循環のまとまり、2 つが接点超過の単独） | `eq_delta_bias_real` / `eq_tex_b24` / `eq_tex_b64` / `eq_tex_b97` | b78 ↔ b80 の 2 手順を 1 まとまりにして計算本体へ | 0 |
| 2609.15375v1（TeX） | **10** | 1,1,1,1,2,2,2,3,5,17（計算本体 17・接点 0） | 3（面密度まわりの 3 手順の外枠の中） | 実測 3 の 6 式と一致 | b190 → b192 → b186 → b190 の 5 手順を 1 まとまりにして計算本体へ | 329 |
| 2609.15375v1（PDF） | — | `available:false` + 事実文 2 行 | — | — | — | 470（詳細ノード全件） |

- 外枠モジュール間の辺はどちらの TeX 論文でも 0 本だった。基礎側のモジュールは共有の基礎（入次数 0 の式を含む）を
  介して計算本体とつながり、結果は sink が受け取るため、「一方が生む式を他方が消費する」外枠どうしの対が残らない。
  モジュール図の配線は `foundations[]` と `sinks[].source_module_keys` から描くことになる（UI §12.2）。
- 成員 step の数は 2407 が 20（chain×step 22 から重複除去。§3.2 の 21 は sink の 1 step を含む数）、2609 が 35。
  成員はすべて式の詳細ノードに対応が付いた。

**設計からの逸脱**（§5 を実測に合わせて直すときの材料。試作は保存されていないので、ゴールデンが再現する規則の組を
変形の組み合わせで探して決めた）

1. **§5.5 循環: 単独化ではなく縮約**。§5.5 どおり循環に関わる step を併合の対象から外すと、循環が計算本体の中に
   あるため本体が割れ、2407 は外枠 11（`1×7, 2×3, 7`）、2609 は 16 になった（基礎側 7 + 計算本体 1 の構造が崩れる）。
   循環する式の強連結成分ごとに、その成分の中で入力と出力を結ぶ step を **1 つの初期モジュールに縮約**してから
   §5.4 の併合を回すと 8 / 10 が再現した。縮約したまとまりが隣とまとまらずに残ったときだけ
   `isolated_reason: "cycle"` を付け、事実文は「次の式を含む手順はひとまとまりとして扱っています: 式 (…)」にした。
   §5.4 の「併合によってモジュール間に循環ができる対は候補にしない」はそのまま効かせている（縮約でモジュール間の
   循環が消えるので、2609 でもこの検査が本体の併合を妨げない。縮約しないままこの検査を効かせると 2609 は 16）。
2. **§5.4 外へ出す式: sink が生む式は数えない**。2407 では結果の式（`eq_skewness_bias_ex`）を成員 step と
   system_level の sink の**両方**が生む。§5.4 の字面では「どの step にも消費されない式」なので外へ出す式に数えられ、
   計算本体は 4 + 6 の 2 つに割れて外枠 9 になる（併合後の接点 4 > 3）。sink が生む式は sink の結果として sink 側に
   記録し、モジュールの外へ出す式に数えないと 8 になる（2609 は変わらず 10）。
3. **同点規則の「出現順」**: `(derivation_id, step_index)` の文字列順ではなく、artifact の「チェーンの並び × step の
   並び」の出現順にした。両方の fixture とも、同点規則をどう割っても（最初に見つかった対でも）外枠は同じだった。
4. **隣接は共有の基礎も含めて数える**（§5.4 の「一方が生む式を他方が消費する」の字面どおり。接点の本数には数えない）。
   基礎を隣接から除くと 2407 は 14、2609 は 16 になり、試作と一致しない。
5. **辺にも共有の基礎を載せる**（受け渡しの事実。接点に数えないだけ）。
6. **理論対象の主張の選び方**: 主張の `equation_ids` がモジュールの扱う式（生む式 ∪ 消費する式）からはみ出す主張は
   使わない（論文全体の式系をまとめる系レベルの主張が全モジュールの理論対象になってしまうため）。残りは結び付く式が
   少ない順 → 外へ出す式の並び → artifact 順。本文に内部 ID（旧版の合成主張「Equation (eq_tex_b14) defines …」）が
   あれば、印字番号に置き換えられるときだけ使い、置き換えられなければ式の表示ラベルへ落とす（TM10）。外へ出す式が
   無いモジュール（結果を sink へ渡すだけ・基礎だけを生む）は sink へ渡す式 → 生む式を代表にする。
7. **`module_key` の材料**: Phase 0 の artifact には式の stable_key が無いので `equation_id` の昇順列を使った
   （schema の docstring に「Phase 1 で式の stable_key に差し替える」と明記）。同じ材料が衝突したら `#2`… を付ける。
8. **TM3 の debug / inferred**: step に対応する詳細ノードが**すべて** debug 層か `inferred` のときだけ成員から外し、
   事実文を 1 行足す。対応ノードが無い step（グラフ未構築）は外さず、裏付けは `review_required` に倒す。

**観察（本書の主題ではない）**: 式の `label` は TeX の `\label` キー（`eq:F2` / `delta-fourier`）がそのまま入って
いる教材があり、表示は「式 (eq:F2)」になる。論文層の `equation_display_label` と同じ挙動で、印字番号の解決は別件。

**テスト**（`backend/.venv` で実行）: `test_theory_module_core.py` 57 件（3 fixture のゴールデン・成員選別・重複除去・
共有の基礎・接点・同点の決定論・循環の縮約・外枠と内側・`module_key` の決定性・入力非破壊・表示ラベルの内部 ID
遮断）/ `test_theory_module_guardrails.py` 21 件（core の import 境界・別プロセスでの推移的 import・DTO の禁止キーと
数値の不在・入力非改変・閾値がコード定数で env を読まない・分野語 denylist・core に訳語表を作らない・SQL / 書き込み
経路なし）/ `test_theory_module_api.py` 15 件（権限ゲートが DB より前・TEACHER・fail-soft 4 経路・実 builder との結線・
component-graph 不変）。backend 全件 16,601 passed / 27 skipped。


**追記（2026-09-23・統合時）**: 理論対象の選び方を §5.6 の改訂どおりに直した。本文由来の主張は実測で
`equation_ids` を持たないため、合成主張の定型文（「In an equation of this paper, $T$ depends on …」）が
そのまま理論対象になっていた。合成主張からは記号 $S$ / $T$ だけを取り出して列挙し（`_symbol_of_synthesized`）、
ノード用に `$…$` をプレーンテキストへ落とした `visual_label` を DTO に足した（`plain_math`。UI の
`moduleDisplayLabel` はこれを先に使う）。fixture の外枠 8 / 10 は不変。

### 12.2 UI（Phase 0, 2026-09-23）

グラフレビュー画面（`frontend/public/js/admin-graph-review.js`・ES5）に理論モジュール図と (b-読) の目印を
配線した。backend の DTO（§8.1 + additive: `sinks[].source_module_keys` / `edges[].level` /
`members[].stage_key` / top-level `foundations[]`）を描くだけで、境目・claim チェーンの判定はフロントに
再実装していない。

- **層トグル**: `graphView.layerOptions` は変えず、グラフレビュー側で「理論モジュール」ボタンを 1 つ足した
  （`data-ui-anchor="graph-review.module-view"`・件数なし = TM6）。層トグル自体が出ない単層グラフでは出さない
  （戻り先のボタンが無くなるため）。モジュール図の表示は `state.moduleView` で持ち、`state.layer` には入れない
  （`getScreenContext` の層語彙・関数本体は非変更 = §8.1 の判断）。
- **取得**: モーダルを開いたときに `GET .../theory-modules` を 1 回だけ遅延取得（論文層と同じ型・別教材の遅延応答は
  破棄・ポーリングなし）。失敗は事実文「理論モジュールを取得できませんでした。」で、グラフ表示・承認操作は止めない。
- **モジュール図（GR8）**: 外枠モジュール（`level != "inner"`）と sink を合成ノードにして `graphView.layoutPositions` /
  `visNodeSpec` / `visEdgeSpec` / `networkOptions` に渡す。色は `dominant_stage` を合成ノードの `label` に置いて既存の
  配色規則に通し、枠は `source_backing_status` の既存表現（通常 / 細線 / 点線）。sink は `shape: "database"`（円筒）。
  辺のラベルは `equation_labels` だけ。両 TeX fixture とも外枠どうしの `edges`（level=outer）は 0 本だったため、
  `foundations[]` の producer → consumer（あるモジュールが導き、他のモジュールが使う共通の式）も点線の受け渡しとして
  描く（ペアごとに式ラベルを束ねる。導くモジュールの無い共通の式は辺にせず詳細ペインの「共通に使う式」に出す）。sink への辺は `source_module_keys` があるときだけ引く（表示ラベルの一致で
  推測しない = TM3）。キャンバスのラベルは DTO の `label` から `$` を外して切り詰め・折り返したもので、読み時キーは
  vis の id にだけ使い描かない（TM10）。ドラッグ位置は既存の端末保存（`eg_graph_review_layout:`）に相乗り。
- **詳細ペイン**: ラベル / 裏付け / `isolated_reason` の事実文 / 工程（`process_verbs` を「→」で連結）/ 理論対象 /
  含まれる段階（`element-vocab.js` の theory stage 訳語）/ 外から受ける式 / 外へ出す式 / 共通に使う式 / 要求される前提 /
  「照合用（AI の原案）」/ 内側のまとまり（`parent_module_key` 一致）/ 中の手順。`assumption_ids` は名前に反して前提の
  本文（derivation_chain の記録を 200 字で丸めたもの）なので、`required_claims` と並べて「要求される前提」に本文として出す。
  中の手順は `node_ids[0]` を持つものだけボタン（`data-ui-anchor="graph-review.module-member"`）にし、押すと
  `state.layer = "equation_detail"` + `focusNodeOnce` で当該ノードへ寄せる（論文層の章チップと同じ経路）。sink を選ぶと
  受け取る式・結果の式を出す。
- **(b-読)**: 「式の詳細」「すべて」の層で `claim_sequence_node_ids` のノードと接する辺をキャンバス・未レビュー件数・
  論文要素マークの凡例から外す（`visibleGraphView()`）。`available:false` の教材にも適用する。未取得・取得失敗時は
  目印を持たないので従来どおり全部描く（fail-to-current）。外した結果「式の詳細」が空になる教材はキャンバス位置に
  事実文「この教材では式の導出を再現できていません。主張の並びは『論文の順』で見られます。」を出す。層ボタンの件数は
  `layerOptions` のまま（§8.1 のとおり本書の範囲外）。
- **初期表示（O-3 (b)）**: グラフと理論モジュールの両方が届いた時点で一度だけ判定し、`available:true` かつ外枠が 1 つ
  以上で、教員がまだ層もノードも選んでいなければモジュール図に切り替える。届くまでは従来の主グラフを描く。
- **モジュール図の間の他操作**: 未レビュー件数は出さず、「次の未レビューへ」は事実文で層の切替を案内する。「論文の順」
  のノードチップを押したら、そのノードの層へ切り替えてから選ぶ。
- **管理UI 3 点セット**: アンカー `graph-review.module-view` / `graph-review.module-member`（KNOWN / ADMIN の両方）+
  マニュアル `docs/manual/teacher/26-admin-graph-review.md` の `{#theory-modules}` / `{#theory-module-member}` + 担体。
  層の切り替え節に (b-読) の説明を追記。件数の正は `test_admin_help_ui_anchors.py`。
- **キャッシュ**: `admin.html` の `admin-graph-review.js` / `styles.css` の `?v=` を `theory-modules-20260923-1` に上げた。
- **ガードレール**: `backend/tests/test_theory_module_ui_static.py`（ES5・graphView 委譲・新描画経路なし・内部 ID 非描画・
  件数非表示・縮退文言の逐語・目印の適用と fail-to-current・フロントで chain_type を判定しない・初期表示の規則・
  成員 step の遷移・`getScreenContext` 非変更・アンカー担体・マニュアル節・`?v=`）。
