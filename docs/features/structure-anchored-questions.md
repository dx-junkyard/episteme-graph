# 構造帰属型の問い記録（Structure-Anchored Questions）— 設計提案

> **ステータス:** 実装済み（migration 025）。実装は `backend/core/structure_anchor/`
> （agent / worker / validator / repair 等の独立モジュール一式）+
> `backend/api/routes/learning.py` の anchors API（digest / confirm / dismiss）+
> `app.js` の doubt_type 選択 UI。現行仕様の要約は CLAUDE.md
> 「構造帰属型の問い記録（Structure-Anchored Questions, B層, migration 025）」を参照。
> 本書は起草時の設計提案として保持する（以下の本文は提案時点の記述）。
> **目的:** 学習チャットの「問い」を質問文そのままではなく、
> **「提示された情報構造のどこに、どう引っかかったか」** として記録し、
> 受講者の考えを正確に把握して効果的な学習機会につなげる。

---

## 1. 背景と問題意識

現状、学習チャットの問いは `interest_traces.payload.text` に **質問文を丸ごと**
記録している（`record_interest_trace`, `backend/api/services.py`）。
「この問いに戻る」も、この text を再送信するだけだった（→ 別途、元の往復への
ジャンプに改修済み）。

しかし「学習者が何につまずいたか」を捉えるには、質問文の保存では不十分:

- 同じ文面でも、**定義が分からない**のか、**なぜ成り立つかが分からない**のか、
  **既有知識と衝突している**のかで、必要な支援がまったく違う。
- 質問文には「どこ（構造上の位置）」が明示されないことが多い
  （「ここがよく分からない」等）。

そこで、問いを **2軸に分解して記録する**ことを提案する:

1. **構造への帰属（どこに）** — 提示情報のどの構成要素に対する問いか。
2. **疑いの様相（どう引っかかったか）** — 疑問の型（定義・導出の飛躍・前提・既有知識との衝突…）。

---

## 2. 前提：帰属先の「構造」はすでに資産化されている

問いを帰属させる先の語彙を**新規に発明する必要はない**。本リポジトリには
複数の粒度の構造がすでに存在する。

| 構造要素 | 出所 | 粒度 | 現状の取得可否 |
|---|---|---|---|
| セグメント / チャンク | `position_anchor`・`cited_sources[].chunk_id` | 粗い（場所） | **記録済み** |
| atomic claim / 数式 / 導出ステップ | claims.json / equations.json / DerivationChain | 細かい（どの命題・どの式変形） | 取得可 |
| theory stage（theory_basis / equation_system / elimination…） | TheoryOperationGraph（#308, domain-neutral 語彙） | 上位（理論構成のどの段階） | 取得可 |

> **設計原則の維持:** 帰属語彙は domain-independent に保つ（特定分野・特定論文の
> 用語をハードコードしない）。theory stage の語彙は `schema.THEORY_STAGES` を使う。

---

## 3. 「どこに」を結びつける4つの方法（併用可能）

### A. 発話時の明示アンカー（学習者が構造を指す）

教材区画でテキスト選択 →「ここについて質問」、または数式・claim ブロックを
タップして質問。`LearningChatRequest` には既に
`chunk_id` / `element_id` / `element_type` / `element_label` フィールドがあり、
`EXPLAIN_GRAPH_ELEMENT` アクションの基盤が流用できる。

- ✅ ground truth。推論不要で最も正確。
- ❌ 「指してから聞く」動線を取ったときしか得られない。自由入力の質問は拾えない。

### B. 非同期LLM帰属（TensionMining と同型のパターン）

質問＋提示中の教材コンテキスト（セグメント本文、cited chunks、そのトピックに
紐づく claims/equations）を入力に、**非同期バッチで**「この質問は
claim X / 導出ステップ Y / stage Z への疑問らしい」候補を生成する。
**既存の tension アーキテクチャ（prefilter → 非同期LLM → 本人確定）を
そのまま踏襲**できる。

- ✅ 摩擦ゼロ。全質問をカバー。P6（応答を遅延させない）に整合。
- ❌ 推論なので誤帰属がある → **P1 に倣い `candidate` 止まり**、本人の軽い
  確定/訂正で確定する。

### C. 対話への埋め込み（回答末尾で疑問の在り処を確認する）

回答生成時に「この説明で、**◯◯の定義**は解消しましたか？ それとも
**なぜ△△が成り立つか**の方でしたか？」と1タップ選択肢を末尾に付ける。
選択がそのまま帰属の確定になる。

- ✅ **教育学的に最も強い。** 「自分の疑問がどこにあるか」を言語化させること
  自体が自己説明（self-explanation）効果を持つ学習行為で、メタデータ収集と
  学習支援が一体化する。
- ❌ 毎回やるとうるさい。`tension_hint` が立ったとき、または帰属 confidence が
  低いときに限定するなどのゲートが必要（P7: 演技化させない）。

### D. 行動シグナルの補強（B の事前確率として）

どのセグメントで滞留したか、レクチャーのどこをリプレイしたか、どの出典
ポップアップを開いたか。単独では弱いが、B の帰属の prior として有効。

---

## 4. 疑いの様相（doubt taxonomy）

「どこ」だけでなく「どう引っかかったか」を持たせると、支援に直結する。
既存の rhetorical role / operation 語彙に接地させた案:

| `doubt_type` | 意味 | つながる学習機会 |
|---|---|---|
| `definition` | 用語・記号の意味が不明 | SymbolRegistry / 定義ノードの提示 |
| `justification_gap` | なぜ成り立つのか（導出の飛躍） | 該当 DerivationChain の展開 |
| `premise` | 前提そのものへの疑い | REQUIRES を遡った前提トピック提示 |
| `prior_conflict` | 既有知識との衝突 | tension と連続。誤解 or 良い違和感の入口 |
| `scope` | どこまで成り立つのか（適用範囲） | 境界条件・反例の教材 |
| `connection` | 他の概念とどう繋がるのか | グラフ近傍の提示 |
| `unclassified` | 分類不能 | P4: 落とさず保持 |

---

## 5. データモデル（新テーブル不要）

tension の方式に倣い `interest_traces.payload` の拡張で足りる。
**質問原文（`text`）は残したまま追加する**（P4: 情報を落とさない）。

```jsonc
"structure_anchor": {
  "anchor_type": "claim | equation | derivation_step | concept | stage | chunk | segment",
  "anchor_id": "...",
  "anchor_label": "線形化ステップ",
  "doubt_type": "justification_gap",
  "attribution_source": "learner_selected | llm_candidate | confirmed",
  "evidence_quote": "質問中の該当箇所の逐語",
  "confidence": 0.72
}
```

- コースビルダー製トピックのように claim/equation 構造を持たない教材では、
  `claim → concept → chunk → segment` の順で**粗い粒度へ縮退**させ、
  無理に細かく帰属しない。
- LLM 帰属（B）は `attribution_source="llm_candidate"` かつ本人確定前は
  `status="candidate"`。本人が確定/訂正して初めて `confirmed`。

---

## 6. 設計原則（tension の不変条項を継承）

- **P1 違和感/帰属を確定するのは人間** — LLM 出力は常に候補。本人 confirm なしに確定しない。
- **P4 情報を落とさない** — 質問原文を残す。分類不能は `unclassified` で保持。
- **P5 evidence-based** — `evidence_quote` / `confidence` / `reason` を必須にする。
- **P6 チャット応答を遅延させない** — 同期パスは非LLM（A・D）のみ。LLM（B）は非同期バッチ。
- **P7 演技化させない** — バッジ・ランキング化しない。確認プロンプト（C）は頻度をゲートする。
- **domain-independent** — 帰属語彙に特定分野・特定論文の用語をハードコードしない。

---

## 7. 推奨：段階導入

### Stage 1（非LLM・即効）
方法 **A** を配線。選択テキスト・タップ要素・現在セグメントを
`structure_anchor` に**同期記録**する。取れるときだけ取る（`attribution_source="learner_selected"`）。

### Stage 2（LLM・非同期）
方法 **B** を tension worker と同じ枠組みで追加。候補は本人に
「この疑問は◯◯についてでしたか？」と軽く確認（訂正可能）。

### Stage 3（活用）
- 再訪キューを「2日前の問い」ではなく
  **「『消去』ステージの導出根拠に引っかかっていた問い」**へ構造化。
- 教員側は **k-匿名化集約**で「この教材はこの導出に疑問が集中」を可視化
  → 教材改善ループ（評価利用は禁止, P3/P7）。

---

## 8. 検討ポイント（実装前に合意したい点）

1. どの方法の組み合わせで進めるか（推奨: **A + B**、確定手段として **C** を部分導入）。
2. `doubt_type` の語彙をこの7分類で確定してよいか。
3. C（回答末尾の確認プロンプト）を出す**ゲート条件**（毎回は出さない）。
4. 構造を持たない教材での**縮退ルール**の許容範囲。
5. 教員向け集約の粒度（stage 単位 / claim 単位）と k の値。
