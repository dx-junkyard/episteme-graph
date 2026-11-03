# 根の是正 2026-09-30（面ごとの修正を 4 つの構造に畳む）

> **状態:** 完了済みのスナップショット（2026-09-30）。4 つの根はそれぞれ設計書（下表）が正本で、本書は
> 「なぜ面ごとの修正をやめたか」と「どの面の修正がどの構造に置き換わったか」の記録。やることリストとして
> 読まない（残したものは §6）。課題ナレッジは IK-0579〜IK-0582（§5a の追補は IK-0595）。

## 1. 起点

ペルソナ通し受講の第 14〜16 波で重ねた個別の是正は 60 件を超えたが、原因を辿ると同じ 4 つの根に
繰り返し当たっていた。オーナーの指示（原文は 「.personalize/entries/P-0014-fix-root-causes-not-symptoms.md」）:

> 60 件超の個別修正が同じ 4 つの根に繰り返し当たっていた。面ごとに塞ぐのをやめ、全ルートが通る 1 箇所と
> 横断ガードレールで根を直す。

面ごとの是正は、その面では正しい。しかし規則（遮断の語彙・論文の選び方・応答の形・入力の形式）が
面ごとに書かれている限り、規則を持たない次の面から同型の欠陥が出る。4 つの根はいずれも
**規則の置き場所**の問題で、直し方は共通して「全ルートが必ず通る 1 箇所に規則を置き、新しい面が
その 1 箇所を迂回できないことを横断ガードレールで固定する」だった。

## 2. 根 1 — 内部 ID・数値・英語生成文の遮断が面ごと

| 項目 | 内容 |
|---|---|
| 面ごとに塞いでいた場所 | 要素文脈（ITEM v2 の主張・式レーン）/ 出典本文（出典ポップアップの式ラベル）/ 記号 lookup / 再構成の出題 / ゼミ前ブリーフ / 支持線の事実文 / グラフ全体対話の grounding / 理論モジュール / 論文の順（論文層） |
| 何が根だったか | 遮断器が画面ごとに別々にあり、内部 ID の語彙が 4 系統で食い違っていた（`eq_blk_` / `eq_tex_` / `sym_` / `k1:` を持たない系統、論文の式番号を内部 ID と誤認する系統）。遮断器の無い面もあった |
| 置き換えた構造 | 語彙の正本 `backend/core/display_projection.py` と、学習者向けルーターの `route_class=LearnerDisplayRoute`（`backend/api/display_route.py`）。戻り値は必ず 1 回 `project_for_learner` を通る。旧遮断器は同じ語彙オブジェクトを参照する |
| 正本 | [表示投影層](../features/display_projection_design.md)（DP1〜DP6） |
| 横断ガードレール | `backend/tests/test_display_projection_guardrails.py`（全学習者ルートの route class・語彙の同一性・リーク走査） |

## 3. 根 2 — 「いまどの論文の話か」が会話の状態として無い

| 項目 | 内容 |
|---|---|
| 面ごとに塞いでいた場所 | トピックチャットの RAG → discuss → 予想と並置（DIFF）→ 前提の説明 → 構造帰属 worker の 5 経路が独自の規則で論文を選んでいた。加えて記号・式 ⚓ の論文間の取り違え、「いまここの周り」の範囲表示 |
| 何が根だったか | 焦点の論文を決める関数が無く、各経路が「トピックの論文を先に」「コース横断で最初の 1 件」などを別々に書き、優先規則も衝突時の扱いも食い違っていた |
| 置き換えた構造 | 解決関数 1 つ（`resolve_focus_document`・段順は明示 → トピック → 直前の引用 → 画面の選択）・優先規則 1 つ（`prefer_focus`）・衝突は fail-closed（`resolve_in_focus`。推測で最初の論文を選ばない）。正本は `backend/core/focus_document.py` |
| 正本 | [焦点論文](../features/focus_document_design.md)（FD1〜FD5） |
| 横断ガードレール | `backend/tests/test_focus_document_guardrails.py` |

## 4. 根 3 — PDF 経路の解析品質（上流）

| 項目 | 内容 |
|---|---|
| 面ごとに塞いでいた場所 | 記号レジストリに主記号が無い / 部品が代替品（`comp_fallback`）だけ / 式の断片化 / 生成文が英語のまま / 本文と無関係な式の付録 / 二重の主張 / 80 字で切れたラベル |
| 何が根だったか | 取り込みの既定が PDF で、組版結果から式・導出・図表を復元する損失が以降の全ステージに乗っていた（同一論文で式チェーンが PDF 経路 0 本 / TeX 経路 13 本）。表示側の読み替え・除外は損失を隠すだけで戻さない。生成言語も run の条件として運ばれていなかった |
| 置き換えた構造 | 取り込みの既定を TeX に。TeX が読めないときだけ同じリクエストで PDF へ倒す同期プリチェック（1 件あたりの取得は最大 2 回・HTTP 429 では 2 回目を取らない・実際にバイト列を返した URL を出所に）。正本は `backend/api/source_resolution.py`。生成言語は run option（`document_analysis_runs.options.language`） |
| 正本 | [論文レーダー §15.9](../features/paper_radar_design.md) / [URL 取得 §10](../features/url_material_upload_design.md) / [discuss 開幕素材 §14](../features/discuss_opening_authoring_design.md) |
| 横断ガードレール | `backend/tests/test_source_resolution.py`（フォールバック・429 で止まる・取得 ≤ 2 回・事実文の逐語一致） |

## 5. 根 4 — 対話の「型」がプロンプト任せ

| 項目 | 内容 |
|---|---|
| 面ごとに塞いでいた場所 | 前提の逆質問のターンで質問を落とす / 毎回問い返す / 帰属の確認プロンプトの再掲 / 鏡が同意の前置きを復唱 / 前の往復の訂正が次の往復で覆る |
| 何が根だったか | 答えの位置・確認の数・問い返しの数・鏡の範囲を LLM の自制と継ぎ足しのプロンプト規則に任せていたため、規則同士が衝突し（ルール 2 と 6）、確認の区画が 1 往復に最大 4 つ並んだ |
| 置き換えた構造 | 生成後・保存前の 1 点でサーバが応答の骨格を組み立てる（先に答える / 確認は後ろに 1 つまで / 問い返しは 1 つまで / 鏡は核心語 / 直前の訂正を持ち越す）。正本は `backend/core/dialogue_shape.py`、配線は `backend/api/routes/learning.py::_learning_chat_core` |
| 正本 | [応答の骨格](../features/dialogue_response_shape_design.md)（RS1〜RS5） |
| 横断ガードレール | `backend/tests/test_dialogue_shape_guardrails.py` |

## 5a. 根 5 — 「どの document 行か」の解決が経路ごと（2026-10-02 追補）

| 項目 | 内容 |
|---|---|
| 面ごとに塞いでいた場所 | 教材詳細（一覧の document_id で 404）/ 教材状態の投影 / レーダーの seed / 分野コーパス / G層の教材 ref / ゼミ前ブリーフの seed（いずれも両形を独自 SQL で照合）。公開範囲の更新・教材削除・原稿スタジオのパイプライン実行・構造再解析・降下路・分野コーパスの一覧・コース経由の可視性・削除の巻き添えコース（いずれも source_path 形だけで照合） |
| 何が根だったか | 教材の ID は `documents.id`（UUID）と `source_path`（material_id）の 2 つで、コースの `sources[].material_id` にはどちらの形も入る。IK-0550 で判別を 2 箇所に寄せた後も旧来の 6 ファイルが独自の `id::text = :ref OR source_path = :ref` を持ち（`LIMIT 1` だけで id 優先の規則が無い・教材詳細は逆に source_path を優先）、source_path 形だけの経路は UUID 形の sources を見落としていた |
| 置き換えた構造 | 解決は `backend/core/document_refs.py` の 1 SQL（`resolve_document_rows` / `resolve_document_row` / `list_documents_for_refs` / `canonical_document_ids`。id 一致を優先・同じ source_path は新しい行・列は許可表のみ）。API 層の単数・権限込みは従来どおり `services._resolve_document` / `services.resolve_document_access`。**権限は解決した documents.id 1 行に対して各経路が掛ける**（解決器は権限を見ない）。コース経由の可視性と削除の巻き添えは両形を当てる |
| 正本 | `backend/core/document_refs.py`（モジュール docstring）・課題ナレッジ IK-0595 |
| 横断ガードレール | `backend/tests/test_document_id_resolution_guardrails.py`（両形照合は正本 2 ファイルのみ・source_path 形だけの照合は理由付き allowlist の関数のみ・寄せた経路が document_refs を通る）+ `backend/tests/test_document_refs.py`（legacy allowlist は空） |

## 6. 残したもの

- **`project_for_teacher` は未配線**。教員向けの管理ルートは各層の射影（PL / TM / W層）が引き続き正本で、
  表示投影層の教員版は関数だけある（表示投影 §4）。
- **バッチ取り込みキューは生成言語を運べない**。キュー表に options 列が無いため、取り込み後に再解析で選ぶ
  （discuss 開幕素材 §14 の非スコープ）。
- ~~早期 return の経路は応答の骨格の対象外~~ → 2026-10-02 に HELP / 学習相談 / 地図 / 要素説明の全経路へ
  `verbatim_shape_dto` か `assemble` を通した（IK-0594・応答の骨格 §8.2）。
- ~~`routes/learning.py::_ordered_source_document_ids` は source_path 形だけで引いたまま~~ → 同日 `core.document_refs.
  resolve_document_refs` へ寄せ、allowlist の「未是正」行を消した（IK-0595）。
- ~~IK-0545（回答の引用と引用先チャンクの一致検査）は未着手~~ → 同日 `core/citation_check.py` で非LLM の逐語一致検査を
  後処理に 1 箇所置き、不一致は事実文 1 行（保存文は不変）。
- **PDF 経路そのものの式復元の強化は次段**。既定を TeX にしたのは上流の根を避ける手当てで、PDF しか無い
  論文の損失は残る。

## 7. 次に同型が出たとき

同じマスク・同じ優先条件・同じ固定文を別のルートに書き足そうとしたら、それは面の欠陥ではなく
本書の 4 つ（§5a を足して 5 つ）の構造のどれかを迂回している兆候である。書き足す前に、該当する 1 箇所に規則を足し、
横断ガードレールが新しい面を拾うことを確かめる。解決原理は
[課題ナレッジの解決原理](../issue_knowledge/principles.md) の `route-all-surfaces-through-one-point`。
