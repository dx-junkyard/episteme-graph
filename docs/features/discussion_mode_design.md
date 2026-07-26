# 「論文と話す」— 係留付きディスカッションモード（discuss モード）設計書

- 対象: episteme-graph（ura-dev）
- ステータス: 討議確定（2026-07-25）→ **Phase 0〜2 実装済み（2026-07-25、§9 実装記録参照）**。Phase 3（v2）は未着手
- 関連正本: `docs/features/personal_knowledge_network_design.md` / `component_evidence_redesign.md` /
  `reconstruction_loop_design.md` / `assistant_common_infra_design.md` / `manual_help_kb_design.md`（§1-1 が
  本書 Phase 0 と同じ全域検索の事実を独立に確認済み）
- **supersede 注記（学習UI再編, 2026-07-26）**: `docs/features/learning_ui_inspect_hover_design.md`
  §3.5 により、入力欄上の「🗣 もっと自由に話す」常設リンク（`discuss-free-link-row` /
  `discuss-free-link-btn`、本書 §3.2）はサイドバー二枚看板と完全重複するため**削除**された。
  discuss への入口は二枚看板に一本化。本書 §3.2/§6.5/§9 のうち free-link に触れる記述は
  歴史的経緯として残すが、現行実装はリンク非搭載。
- 討議の性格: チームA（好奇心最大化: SDT・情報ギャップ理論・expertise reversal を支柱とする急進案）・
  チームB（構造と足場: 認知負荷理論・ICAP・productive failure を支柱とする条件設計案）・
  チームC（プロダクト現実: 既存アーキテクチャとの整合・実装コスト最小化案）の3チームが独立検討し、
  代表相互批評 → 議長裁定で統合した。単なる折衷ではなく、対立点には §4 のとおり明確な裁定を下した。
  コード参照はすべて本書作成時に実機 grep で裏取り済み。

---

## 0. エグゼクティブサマリー

**結論: 条件付き賛成。しかも条件は重くない。**

「学生が最初から対象論文全体＋周辺知識と全力でディスカッションできるモード」という発案は、
学習科学の知見（§5）に照らして大筋で正しい。決定的なのはコードベース調査の発見:
**「論文全体＋周辺知識との対話」は技術的にはすでに本番で動いている。**
`search_chunks_with_metadata`（`backend/api/services.py:1365`）は最初からコース非依存の
全域ベクトル検索であり、「教材/別の資料」の区別（`content_grounding`、`learning.py:2035-2039`）は
検索制限ではなく事後ラベリングにすぎない。本件は新機能開発ではなく、**既に存在する能力を
「寄り道」という例外の衣から出して正面玄関に置くUX昇格**である。

条件は3つ（いずれも §4 の裁定で確定）:

1. **可視性フィルタを先に塞ぐ**（3チーム唯一の全会一致・先行ブロッカー）。現行の全域検索は
   document の Public/Group/Private を考慮していない。discuss と無関係な既存の潜在バグであり、
   Phase 0 として単独リリースする。
2. **「自由」を「無構造」にしない**。分水嶺は「自由か構造か」ではなく
   **「係留（anchoring）と着地（consolidation）があるか」**。
3. **逐次コースの廃止・格下げはしない**。対等併記（二枚看板）が実証的に正当化できる上限。

規模感: Phase 1 は **migration 0本・新テーブル 0・新エンドポイント 0**（`intent_mode` の4値目と
既存機構のバイパス指定のみ）。Phase 2 で開幕・着地画面（いずれも非LLM）。Phase 3（v2）で初めて
migration（058 想定）と新ルーターに踏み込む。

---

## 1. 問題と背景

現行の学習UIは「トピックを順にたどる」逐次型で、コース教材から外れる質問は
`intent_mode='explore'`（寄り道）として扱われる。フロントは寄り道中であることを
アンバーのモードバー・復帰CTA・「寄り道中：…」ラベルで常時表示する（`app.js:938` 以降）。
機能としては自由探索が可能なのに、**語彙とUIが自由探索を「例外・逸脱」として枠付けている**。

これは自己決定理論が指摘する統制的環境の典型であり（§5）、大学院生という高事前知識の
学習者集団に対しては expertise reversal effect の観点からも正当化しにくい。一方で
「無指導の自由対話」に退行させれば認知負荷理論陣営の批判（§5 歯止め側）がそのまま当たる。
本設計はこの両方の知見を「係留と着地」で満たす。

---

## 2. 不変条項（DM1〜DM8）

既存レイヤーの不変条項（P1〜P7 / PN-1〜7 / W1〜W9）を継承したうえで、discuss 固有に以下を課す。

- **DM1 出所の正直さを弱めない**: content_grounding / tier の判定・表示は無変更で継続。
  `model_generated` には「この論文由来ではありません」を明示。検索スコープの無断フォールバック禁止
  （該当チャンクが無ければ「この範囲には見当たりませんでした」と事実文で返し、勝手に広げない）。
- **DM2 可視性 fail-closed**: 周辺資料への拡張検索は「本人が閲覧可能な document」に限定する
  （Phase 0 のフィルタが前提。`component_context.py:131` の `ANY(:doc_ids)` 先例と同型）。
- **DM3 係留と着地**: 対話は構造要素（claim / equation / figure / component / stage）への係留を
  一級とし、セッションに着地（confirm/dismiss/connect + 再構成プローブ）を用意する。
  「自由＝無構造」にしない（Jang, Reeve & Deci 2010）。
- **DM4 即答し、必ず返す**: AI は求められた情報を出し惜しみしない（Socratic-first の既定化は不採用）。
  ただし応答末尾の生成プロンプト（予測・自己説明・言い換えの誘い、または問い返し）を
  **構造的必須**とする（確率的付加は不可）。「答えを受け取って終わり」の Passive 退行への最低限の対策。
- **DM5 コースと対等併記**: discuss を上にも下にも置かない。「寄り道」という語を discuss の
  UI 文言から追放する（explore の内部語彙・既存 UI は変更しない）。
- **DM6 数値を見せない・監視しない・煽らない**: スコア・件数・網羅率を学習者に出さない。
  教員向けは k-匿名集約（`core/privacy.py` 正本、k=3）のみ。個別履歴閲覧は作らない。
- **DM7 痕跡は本人確定制のまま**: discuss 由来の tension / structure_anchor / 個人知識ネットワーク
  還流はすべて既存の本人 confirm 制を継承（P1 / PN-3 / PN-6）。AI が確定させる経路を作らない。
- **DM8 同期パスに重い処理を足さない**: 開幕画面・着地画面は非LLM（A層成果の読み出し＋既存 API の
  束ね）。LLM は応答本文のみ（1応答=1コール、`window_history` 経由）。コストは U層タグ
  `learning:chat_discuss` で分離実測し、専用上限の新設は実測後に判断する。

---

## 3. 統合仕様

### 3.1 名称

| 用途 | 名称 | 由来 |
|---|---|---|
| 学習者向け UI 表示名 | **「論文と話す」**（動詞で始まる行為名） | チームA |
| 内部語彙 | `intent_mode='discuss'`（4値目）、U層タグ `learning:chat_discuss` | チームC |
| 設計原則名 | 係留（anchoring）と着地（consolidation） | チームB |

「全力ディスカッション」「自由討論」は正式名称に採らない（命名が実装の規律を緩めるため）。

### 3.2 入口 — 二枚看板

- コース着地画面に同じ視覚的重みで2ボタン: **「順番に学ぶ」**（現行逐次型・無変更）と
  **「この論文と議論する」**。既定選択なし（選択の存在自体を可視化する — 自律性支援）。
- トピック学習中のチャット欄に「もっと自由に話す」リンクを常設。画面遷移なしで
  `intent_mode` だけ `discuss` に切り替える。
- 復帰は督促しない。着地画面（§3.5）と現在地表示の選択肢として本人が選ぶ。

### 3.3 開幕 — 白紙のチャット欄で始めない（Phase 2）

好奇心は空白からは生まれない（Loewenstein 1994 情報ギャップ理論）。開幕画面は3要素、
**すべて非LLM・A層成果の読み出しのみ**:

```
┌ この論文が賭けているもの ────────────────────┐
│ 中心命題（thesis_reconstruction 由来）                  │
│ 支持構造: [claim] [claim] [equation] チップ              │
│ 最も脆い一手: 未検証前提・review_required の事実提示      │
├ 理論のバックボーン ─────────────────────────┤
│ main graph の theory stage 5–8 ノード                   │
│ ノードクリック = そこから対話開始                         │
├ 最初の一手 ───────────────────────────────┤
│ [なぜこの設計?] [前提は何?] [他と矛盾しない?] ＋自由入力   │
└──────────────────────────────────────────┘
```

最脆弱点の提示は D層の「検証状態の事実併記」思想と同一線上。煽り文句ではなく事実文で書く
（禁止語彙は D層ガードレールと同じ）。

### 3.4 対話設計

- **即答＋生成プロンプト必須**（DM4）。discuss 用プロンプトテンプレートの必須要素として記述する。
- **構造要素への係留**: claim / equation / figure / component はインラインチップ
  （component_evidence_redesign の既存レンダラ流用）。チップ起点の質問は structure_anchor
  経路A（明示アンカー・同期非LLM）でそのまま帰属確定する。開幕バックボーン起点の対話は、
  コース内チャットより帰属精度がむしろ上がる。
- **スコープ2段切替**（DM1/DM2）: 入力欄上部に「このコースのソース論文（既定）/
  閲覧できる周辺資料まで」。スコープ選択状態そのものが出所の正直さの UI になる。
- **分岐チップ**: 各応答後に「深掘り」（同一 claim の前提・evidence・導出へ）と「横展開」
  （理論操作グラフ隣接ノード / 関連論文 / 分野の地図へ）。Berlyne の特殊的好奇心と
  拡散的好奇心は別種の欲求であり、別の出口として受け止める。
- **採らないもの**（§4 裁定）: 開アンカー上限（係留集合の統制装置）/ 入室時の事前知識
  診断ラベル / 学習者推定による足場自動調整。v1 は手動切替（「基礎から確認したい」）のみ。

### 3.5 着地（consolidation）— セッションの終わり方を設計する（Phase 2）

「残す」ボタンの自己申告だけでは説明深度の錯覚（Rozenblit & Keil 2002）を検出できない。
Kapur の productive failure が深い学習を生むのは**探索→統合の対構造がある場合**であり、
探索だけを解放して着地を作らないのは引用の半分しか実装していない（チームB の批評を採択）。

討議終了トリガー（明示終了 / トピック切替 / 無活動タイムアウト）で軽量な着地画面を挟む:

1. **「今日話した内容を地図に置く」** — 討議中に立った tension / structure_anchor 候補を
   1画面で confirm / dismiss / connect（既存 API の束ね。本人確定制そのまま）
2. **再構成プローブ、あれば1問** — R層の出題対象は source_backed かつ承認済み claim のみ・
   item は非同期オーサリング依存のため「必ず1問」は構造的に成立しない。「あれば」に留める
3. 対応するコーストピックがあれば「このトピックで続きを学ぶ」を情報的提示（自動遷移しない）

スキップ可・既定オン・スキップしても痕跡は残り後から地図に置ける（情報を落とさない）。
個人知識ネットワークへの還流は本人が確定した分だけ（DM7）。

### 3.6 逐次コースとの関係

対等併記。コースは「体系的な足場が欲しいときに選ぶルート」として再定義し、
worked example の供給路として維持する。「まず論文と格闘し、詰まったらコースへ」という
順序逆転も正規の学び方として案内文に明記する（Kapur; Schwartz & Bransford 1998）。

---

## 4. 討議の裁定記録

| # | 争点 | 対立 | 裁定 | 理由 |
|---|---|---|---|---|
| 1 | 可視性フィルタ | 争点なし（3チーム一致） | **全 Phase の先行ブロッカー** | 権限漏れの制度化を防ぐ。唯一の全会一致 |
| 2 | 探索とコースの序列 | A「対等以上」vs B・C「対等併記」 | **対等併記** | expertise reversal はトピック単位で作用し、大学院生も初見論文では初学者。「対等以上」は根拠から導出できない |
| 3 | 即答 vs 開示遅延 | B「Socratic-first・1テンポ遅らせる」vs A・C「即時充足を殺すな」 | **即答＋生成プロンプト構造的必須** | Loewenstein の即時充足価値と ICAP の Passive 退行対策は分離すれば両立。開示遅延も確率的問い返しも不採用 —「すぐ答え、必ず返す」 |
| 4 | 係留集合UI（開アンカー上限3） | B 提案 vs A「拡散的好奇心の病理化」・C「新状態機械は過大」 | **不採用（v1）**。係留チップ表示のみ | 上限という統制装置より着地画面での回収に賭ける |
| 5 | 事前知識推定・診断ラベル | B 提案 vs A「診断の押し付け」・C「監視原則の外縁」 | **不採用**。手動切替のみ | 監視にしない・数値を見せない文化との整合。B も自己修正済み |
| 6 | migration の要否 | A「course_id NOT NULL を正面突破」vs C「migration ゼロで実測から」 | **段階裁定: v1 はゼロ、document 直付け入口を v2 の確定ゴールに明記** | `interest_traces.course_id NOT NULL`（migration 020）を温存する限り探索は構造的にコースの間借り人。ただし疑似 course_id のような provenance を汚す回避策は禁止 |
| 7 | 開幕体験 | A 提案 vs C 当初案（入口ボタンのみ） | **採用（Phase 2）** | 非LLM・A層読み出しのみでコスト論では削れない |
| 8 | consolidation | B 提案 vs A「軌跡＋残すボタン」・C 当初案（なし） | **採用（Phase 2、軽量版・スキップ可・「あれば1問」）** | 説明深度の錯覚への較正装置は自己申告では代替できない |
| 9 | 専用コスト上限 | A「`DISCUSS_MAX_CALLS_PER_DAY` 新設」vs C「U層タグで実測してから」 | **C 案採用** | 既存 `LEARNING_CHAT_MAX_CALLS_PER_DAY` を適用し、U層タグ分離で実測 → Phase 3 で要否判断。実測なき上限設計は当てずっぽう |

---

## 5. 教育学的根拠（3チーム引用の相互検証済み確定リスト）

引用はモデル内知識に基づき著者名・年・理論名の粒度で相互検証した。効果量・DOI・巻号の
細部数値は主張しない。実装時に UI 文言へ引用を載せることはしない（根拠は設計判断の裏付けであり、
学習者への権威づけ表示は「煽らない」原則に反する）。

**探索・自律性・好奇心の側（本モード新設を支える）**

| 研究 | 支える設計判断 |
|---|---|
| Ryan & Deci (2000) 自己決定理論、Vansteenkiste et al. (2004) | 二枚看板（選択の可視化）。自律性支援的文脈は統制的文脈より深い概念的処理と持続性を生む。「寄り道」語彙の追放 |
| Loewenstein (1994) 情報ギャップ理論 | 開幕画面（ギャップを意図的に開く）と即答の既定化（ギャップ充足の即時性に価値がある） |
| Berlyne (1960) | 深掘り/横展開の2分岐チップ（特殊的好奇心と拡散的好奇心は別の欲求） |
| Kang et al. (2009)、Gruber, Gelman & Ranganath (2014) | 周辺知識への越境許容。好奇心状態は付随情報の記憶も強化するとされる |
| Kalyuga, Ayres, Chandler & Sweller (2003) expertise reversal effect | **本提案の最重要根拠**: 高事前知識層への画一的逐次足場の既定化は正当化しにくい。同時に効果がトピック単位である点が「対等以上」を退けた根拠でもある |
| Hidi & Renninger (2006) 興味発達4段階モデル | 後期段階の学習者には自己駆動的経路を。初期段階では足場の比重を上げる（コース併存の根拠） |
| Kapur (2008)、Kapur & Bielaczyc (2012) productive failure、Schwartz & Bransford (1998) | 「まず格闘、詰まったらコースへ」の順序逆転を正規化。ただし探索→統合の対構造が条件（着地画面の直接根拠） |
| Slamecka & Graf (1978) 生成効果、Rosenshine, Meister & Chapman (1996) | 問いの軌跡の資産化（tension / anchor 還流の思想と整合） |
| Falk & Dierking (2000) free-choice learning | 経路・順序・深さの自己決定環境を一級市民にする構想全体 |

**対話設計の側（「ただのQ&A」への退行を防ぐ）**

| 研究 | 支える設計判断 |
|---|---|
| Chi & Wylie (2014) ICAP、Chi et al. (1989; 1994) 自己説明効果 | 生成プロンプトの構造的必須化。Interactive な相互構築のみが最深の学習を生む。自己説明効果は高事前知識層で効きやすいとされ大学院生対象と整合 |
| Graesser & Person (1994) | 問い返し設計（熟練チューターは why/how/what-if 型の深い問いを多用） |
| VanLehn (2011) | 知的チュータリング対話の効果は人間チューターにかなり近いとされる（実現可能性の傍証） |
| Rozenblit & Keil (2002) 説明深度の錯覚 | 着地画面の再構成プローブ。人は説明させられるまで理解を過大評価する。流暢な LLM 対話でこのリスクは増幅されうる |
| Bjork の desirable difficulties | 生成課題（predict/restate）の価値。ただし「求められた情報の出し惜しみ」の根拠にはならない（開示遅延を退けた理由） |

**歯止めの側（無条件解放を退ける）**

| 研究 | 支える設計判断 |
|---|---|
| Kirschner, Sweller & Clark (2006)、Mayer (2004)、Sweller 認知負荷理論・worked example 効果 | 「無指導の解放」の禁止。逐次コース（worked example 供給路）の維持。これらの批判はすべて事前知識の乏しい局面・無指導条件に向いている |
| Hmelo-Silver, Duncan & Chinn (2007) | Kirschner らへの直接応答: 効果を上げてきた探究学習は濃密に足場かけされている。争点は指導の量ではなく足場の設計 — 本設計の立脚点 |
| Alfieri et al. (2011) メタ分析 | 支援なし発見は直接指導に劣るが、フィードバック付き拡張型発見は上回る。着地画面を「不要なもの」にできない根拠 |
| Jang, Reeve & Deci (2010)、Reeve & Jang (2006) | 自律性支援と構造は対立せず、両方が揃った条件が最良。「自由＝無構造」は誤り（DM3 の直接根拠） |
| Wood, Bruner & Ross (1976) scaffolding、Vygotsky ZPD、Collins らの fading | 対話 UI 自体が実装すべき足場（方向維持=現在地表示、重要点強調=係留チップ）。撤去は漸進 |
| Kirschner vs Hmelo-Silver 論争、Koedinger & Aleven (2007) assistance dilemma | 未決着論争であることの正直な明記。本設計の正当性は「大学院生＝高事前知識・自己調整学習者」という対象特性に依存し、初学者一般への外挿は主張しない |

**総括**: 反対根拠はすべて「無指導・無検証の自由対話」を撃ち、賛成根拠はすべて
「足場と事後検証を備えた探究対話」を支持する。両者は矛盾せず、係留と着地が分水嶺である。

---

## 6. 実装設計

### 6.0 裏取り済みのコードベース事実（2026-07-25 実機確認）

| 事実 | 所在 |
|---|---|
| 全域ベクトル検索に可視性・コースフィルタなし。`material_id` を SELECT（この列は grounding 判定の生命線 — 落とすと判定が壊れる既存制約） | `backend/api/services.py:1365` `search_chunks_with_metadata` |
| `intent_mode` は自由文字列（`"on_path" \| "explore" \| "casual"`） | `backend/api/schemas.py:288` |
| casual バイパス3点: 意図分類スキップ / 前提知識ゲートスキップ / detour 化スキップ（`origin` を返さない → フロントの寄り道バナーが出ない） | `learning.py:1910` / `1975` / `2188-2202` |
| U層 feature タグの分岐先例 | `learning.py:2074`（`learning:chat_casual`） |
| `content_grounding` 3値判定 | `learning.py:2035-2039` |
| 存在しない topic_id は 404 にならず `topic_info=None` として第一級で処理される（`topic_title` は topic_id 文字列へフォールバック） | `learning.py:1825-1826` + `core/course_data.py:353` `find_course_topic` |
| 単一文書スコープ・tier 付き検索の完成品が**未使用**（importer ゼロ） | `backend/core/chat.py:28` `search_chunks(question, material_id, top_k)` |
| `interest_traces.course_id TEXT NOT NULL`・`topic_id` は nullable | `backend/db/020_interest_trace.sql` |
| document 集合への fail-closed フィルタの先例 | `backend/core/component_context.py:131`（`WHERE document_id = ANY(:doc_ids)`） |
| 最新 migration は 057（v2 想定番号は 058） | `backend/db/` |

### 6.1 Phase 0 — 先行ブロッカー（これなしに何も出荷しない）

discuss と無関係な**既存の潜在バグ修正**として単独で先行リリースできる。

- `search_chunks_with_metadata` に `allowed_document_ids` キーワード引数を追加し、SQL 内
  `WHERE d.id = ANY(:doc_ids)` で可視性を強制（`component_context.py` の先例と同型）。
  **`material_id` の SELECT は落とさない**。
- 「本人が閲覧可能な document id 集合」ヘルパーを新設（既存 `user_can_view_document` /
  `resolve_document_access` / group permission ロジックの集合クエリ化。チャンク単位ループでの
  N+1 呼び出しは禁止 — 横断基盤の既存ルール）。
- `test_content_grounding.py`（または新規）に visibility ガードレールテストを追加:
  Private 文書のチャンクが他人の検索結果・cited_sources に出ないこと。

### 6.2 Phase 1 — v1 最小（migration 0・新テーブル 0・新エンドポイント 0）

- `intent_mode='discuss'` を4値目として追加（`schemas.py:288` のコメント更新。explore は
  on_path 中の逸脱の内部語彙として残す — 既存 UI・復帰導線は変更しない）。
- `_is_discuss` フラグで casual と同型の3点バイパス: 意図分類スキップ / 前提知識ゲートスキップ /
  detour 化スキップ（`origin=None` により既存フロントの寄り道バナーは自動的に出ない）。
  casual と違い**応答スタイルは会話調ではなく学術ディスカッション調**（discuss 専用プロンプト
  テンプレート。生成プロンプト必須要素を含む）。
- topic_id は予約キー `_discussion` を送る（`find_course_topic` が None を返し既存コードは
  そのまま動く。ただし `topic_title` のフォールバックが生文字列 `_discussion` になるため、
  表示・プロンプト・痕跡 payload 用に `_discussion` → 「論文との議論」のラベル変換を1箇所置く）。
- スコープ2段切替: 既定=コースの `sources[].material_id` 由来 document のみ、
  拡張=Phase 0 ヘルパーの「閲覧可能な document」全部。リクエストにスコープを持たせ、
  無断フォールバック禁止（DM1）。
- 二枚看板の入口ボタン＋「もっと自由に話す」リンク（`app.js`。送信時の intent_mode 文字列を
  変えるだけ）。discuss 中のモードバーは「寄り道中」ではなく「論文と議論中」。
- U層タグ `learning:chat_discuss`（`learning.py:2074` の分岐に1値追加）。コスト上限は
  既存 `LEARNING_CHAT_MAX_CALLS_PER_DAY` を適用（専用上限は Phase 3 で実測判断）。
- 痕跡: interest_traces は course 文脈のまま書けるので migration 不要。payload に
  `entry_mode: 'discuss'` を追記（後から U層・k-匿名集計・personal_graph が区別できる）。
  tension prefilter / structure_anchor worker は topic_id nullable 対応済みのため無変更で機能。
  discuss 由来の確定痕跡はそのまま「わたしの地図」のノードになる。

**再利用（無変更）**: 全域 RAG 検索・content_grounding 3値判定・tier 集約・CostGate・
`window_history`（学習チャット 20/2000 のまま）・チャットパネル・根拠チップレンダラ・
casual 音声パイプライン・個人知識ネットワーク導出。

### 6.3 Phase 2 — 開幕と着地

- **開幕画面**（§3.3）: `GET` 系の読み出しのみで構成（thesis_reconstruction ＋ main graph
  バックボーン ＋ review_required / 未検証前提の事実提示）。非LLM・A層成果の投影のみで
  同期パス原則（DM8）に完全整合。データソースは `theory_component_graphs.graph_json`（main layer）と
  `stage_outputs` の thesis_reconstruction。W層 `core/deliberation/` の読み出しロジック
  （FastAPI 非 import・course_id 不使用）を可能な範囲で共有する。
- **着地画面**（§3.5）: tension / anchor 候補の confirm / dismiss / connect 一括提示
  （既存 API の束ね）＋再構成プローブ「あれば」1問（`reconstruction/next` 流用＋セッション終了
  トリガー追加）。既定オン・スキップ可・痕跡保持。
- 着地導線「このトピックで続きを学ぶ」（既存 `inline_actions` 機構）・Field Atlas 現在地チップ
  （読み取り専用）。
- 深掘り / 横展開の分岐チップ。

### 6.4 Phase 3（v2）— コース非依存の正面突破

Phase 1/2 の U層実測でモードの価値を確認してから、専用の設計文書を切って着手する
（interest_traces の読み手全域 — worker・digest・k-匿名集計・personal_graph derive — に及ぶ
準破壊的変更のため）。

- **migration 058 想定**: `interest_traces.course_id` の nullable 化＋ `document_id` 列追加、
  `learning_chat_history` の document スコープ対応。**疑似 course_id のような provenance を
  汚す回避策は禁止**（裁定 #6）。
- **新ルーター `backend/api/routes/discuss.py`**: `POST /api/learning/documents/{document_id}/discuss`
  — 未受講・コース外から論文と話す入口。認可は `resolve_document_access`、fail-closed。
  未使用の完成品 `core/chat.py::search_chunks`（単一文書スコープ・tier 付き）を主検索に転用。
- 教員向け k-匿名集約: anchor-insights と同型（discuss でどの構造要素に問いが集まるか）。
  個別監視は作らない（DM6）。
- 専用 `DISCUSS_MAX_CALLS_PER_DAY` の要否を U層実測で判断。
- 音声版（casual 基盤流用）、W層読み出しロジックの学習者向け簡易パネル
  （route 層 `_require_teacher` の権限モデル再設計が必要なため楽観視しない）。

### 6.5 ガードレールテスト（`backend/tests/test_discuss_guardrails.py` 想定）

- Phase 0: 閲覧不可 document のチャンクが検索結果・cited_sources に漏れない（fail-closed）
- discuss 応答に数値スコア・件数・網羅率が含まれない（禁止語彙は既存カタログ流用）
- discuss プロンプトテンプレートに生成プロンプト必須要素が存在する（構造検査）
- スコープ「ソース論文のみ」で該当なしのとき、他スコープへ無断フォールバックしない
- `_discussion` 予約 topic_id で interest_traces / tension prefilter が正常動作する
- U層タグ `learning:chat_discuss` が casual / 通常チャットと分離記録される
- 教員向け集計が `core/privacy.py` の k=3 を通る（リテラル再定義禁止）

---

## 7. 残るリスクと運用上の観察ポイント

| # | リスク | 観察点と対応 |
|---|---|---|
| 1 | 説明深度の錯覚の残存（プローブは「あれば1問」のため item の無い claim では較正が効かない） | 再構成 item のカバレッジ（discuss で触れられた claim のうち出題可能な割合）。低ければオーサリング worker のトリガー拡充を検討 |
| 2 | ICAP Passive への退行（生成プロンプトを学習者が無視すれば Active 止まり） | 生成プロンプトへの応答率（内部計測のみ・学習者に見せない） |
| 3 | `model_generated` 依存の増加（スコープ拡大で根拠なし応答比率が上がりうる） | U層タグ別の content_grounding 分布。連続時は source_backed claim への回帰を促す一言（事実文）を Phase 2 で検討 |
| 4 | コスト（自由対話は往復が伸びる） | `learning:chat_discuss` タグで実測してから専用上限を判断。429 は既存規約どおり数値非表示の事実文 |
| 5 | 初学者局面での迷子（大学院生でも初見論文では初学者） | v1 は手動切替と着地導線のみで受け、学習者推定は作らない。discuss からコーストピックへの着地率が著しく低い場合も「兆候ベースの控えめカード」までとし、強制遷移・診断ラベルには進まない |
| 6 | course_id migration の波及（Phase 3） | interest_traces の読み手全域に及ぶ。Phase 1/2 の実測後に専用設計文書を切る |
| 7 | 教員の受け止め（「学生が教材を無視して進む」懸念） | k-匿名集約を教材改善の資源として返す。個別監視は作らない — ここは譲らない（DM6） |

---

## 8. 非スコープ（v1〜v2 で意図的にやらないこと）

- 開アンカー上限・係留集合の統制 UI（裁定 #4）
- 事前知識の自動推定・診断ラベル・足場の自動調整（裁定 #5）
- Socratic-first / 開示遅延の既定化（裁定 #3）
- 逐次コースの廃止・格下げ・「探索が上」の序列（裁定 #2）
- 学習者への研究引用の権威づけ表示・スコア表示・ゲーミフィケーション
- 教員による discuss 個別履歴の閲覧（k-匿名集約のみ）
- explore（寄り道）語彙・UI の既存動作変更（discuss とは独立に維持）

---

## 9. 実装記録（2026-07-25、Phase 0〜2 完了）

Fable 指揮 + sonnet 並列サブエージェント体制で実装。バックエンドフルスイート
**5,790+ passed / 0 failed**（backend/.venv、docker 不要）。docker 実機 E2E は未実施。

### 9.1 仕様作成後のコード変更に伴う軌道修正（着手前に実機確認して裁定）

| # | 設計書の前提 | 実機の現状（2026-07-25） | 裁定 |
|---|---|---|---|
| 1 | 最新 migration 057・v2 想定 058 | help_kb（manual KB）が **058/059 を消費済み**（さらに観測基盤 `discuss_observation_design.md` が 060 を消費） | Phase 0〜2 は migration 0 本のため影響なし。**Phase 3 の migration は 061〜** |
| 2 | casual バイパスは3点（learning.py:1910/1975/2188） | help_kb の **usage_help pre-route** が casual 判定の手前に追加され行番号が全面シフト。U層タグ分岐を含め実質**4点** | discuss 判定は pre-route より後ろ・casual 直後に配置（usage_help が discuss ユーザーにも届く位置関係を維持）。バイパスは4点として実装 |
| 3 | §6.1「可視 document 集合 = user_can_view_document の集合クエリ化」 | `user_can_view_document` は document 単体判定で**コース経由の開示を含まない**。文字通り実装すると公開コース受講生が教員 private の sources を RAG できなくなる退行 | `list_visible_document_ids` は **document 直接可視 ∪ アクセス可能コース（所有/公開テンプレート/グループ/受講中）の sources 由来 document** の和集合とした。コースへのアクセス自体が sources の開示を意味するため |
| 4 | §3.2「コース着地画面に2ボタン」 | 現アプリに独立したコース着地画面は存在しない（コース選択で先頭/in_progress トピックへ自動遷移） | 二枚看板は**サイドバー最上部の常設等重ブロック**として実装。既存の自動トピック選択は不変（逐次 UX を壊さない）。真の着地画面化は将来の UX 改修候補として残す |
| 5 | §3.5 着地画面で confirm/dismiss/**connect** | tension connect は component/edge ピッカーを要する（着地モーダル内実装は過大） | 着地モーダルは confirm/dismiss + 「接続は『わたしの地図』の既存導線で行える」旨の事実文注記。connect API 自体は既存のまま利用可能 |
| 6 | §6.3 Field Atlas 現在地チップ | 現在地は atlas-minimap.js のクロージャ内 private 状態のみで、安価に読める公開 getter が無い | **見送り**（fail-closed・偽装しない）。discuss.js にコメントで理由を記録 |
| 7 | §6.0「positioning.py:194-237 が claim ラベル解決の先例」 | 当該行は thesis 位置ヘルパーで、claim→ラベル解決器ではなかった | `context_lens.py::_claim_id_lookup_from_rows` + `_artifact_claim_text_index` の2先例を合成して解決（DB id / legacy_ids / span_id / claim_object_builder text の順。未解決 id は id 文字列を label に — 情報を落とさない） |

設計書 §6.2 の想定どおり確認できたもの: `find_course_topic` の未知 topic None 返し /
tension prefilter・structure_anchor worker の `_discussion` 耐性 / **reconstruction `next` の
未知 topic → コース全体フォールバック**（改修ゼロで「あれば1問」が成立）。

### 9.2 実装ファイル一覧

- **Phase 0**: `services.py`（`list_visible_document_ids` / `search_chunks_with_metadata` の
  必須キーワード引数 `allowed_document_ids`・空集合 fail-closed・`material_id` SELECT 維持）、
  呼び出し3箇所配線（learning.py / lecture.py / core/graphs/student_graph.py=None 明示）
- **Phase 1 backend**: `schemas.py`（`discuss_scope`）、`services.py`
  （`list_course_source_document_ids`、lecture.py 旧ヘルパーは委譲化）、`learning.py`
  （`DISCUSSION_TOPIC_ID/LABEL`・バイパス4点・スコープ解決と 422・無断フォールバック禁止の
  事実文 context_block・`_get_discuss_system_prompt`（即答 + 生成プロンプト構造的必須）・
  out_of_source_notice 維持・payload `entry_mode:'discuss'`）、
  `core/llm_usage/schema.py`（`learning:chat_discuss` 登録）
- **Phase 1 frontend**: `app.js`（二枚看板 = サイドバー等重ブロック・`enterDiscussMode` =
  `selectTopic("_discussion")` 流用・スコープトグル・モードバー「論文と議論中」・
  「もっと自由に話す」リンク）、index.html、styles.css（`.discuss-*`）
- **Phase 2 backend**: `backend/core/discuss/opening.py`（FastAPI 非 import・非LLM・
  純粋投影と DB 読み分離・数値キー再帰除去）、`learning.py` に
  `GET /api/learning/courses/{course_id}/discuss/opening`
- **Phase 2 frontend**: `discuss.js`（`window.Discuss`: renderOpening / maybeShowLanding /
  notifyActivity / renderBranchChips / reset。着地トリガー = 明示終了・トピック切替・
  無活動15分、10分抑制 + 往復0ガード）、app.js への7フック（全て追加のみ）
- **テスト**: `test_search_visibility.py` / `test_discuss_mode.py` / `test_discuss_opening.py` /
  `test_discuss_ui_static.py` / `test_discuss_phase2_ui_static.py` / `test_discuss_guardrails.py`
  （§6.5 対応表は同ファイル docstring）
- **ドキュメント**: CLAUDE.md「『論文と話す』ディスカッションモード」節、
  `.claude/skills/episteme-graph-dev/SKILL.md`（learning.py 行 + `core/discuss/` 行）

### 9.3 残作業

- docker compose での実機 E2E（開幕画面の実データ表示・着地モーダル・スコープ切替の目視）
- §7 の観察ポイント（U層 `learning:chat_discuss` の実測 → 専用上限の要否判断 — 裁定 #9）
- Phase 3（v2）: 専用設計文書を切ってから（migration 061〜）。着手判断の実測ゲートは
  観測基盤（`docs/features/discuss_observation_design.md`、migration 060）が担う

### 9.4 実装レビューと修正（2026-07-25、Fable + sonnet 並列体制）

コミット後の全面レビュー（7観点並列）で確定した問題を同日修正した。

| # | 指摘 | 修正 |
|---|---|---|
| 1 | 🔴 `GET .../source-chunk/{chunk_id}` に可視性ゲートが無く、認証済みユーザーが任意 chunk_id で他人の Private 文書本文を直読みできた（ff4cc2b 由来の既存穴。Phase 0 は検索経路のみ塞いでいた — DM2 の塞ぎ漏れ） | `get_chunk_passage` に `search_chunks_with_metadata` と同意味論の必須キーワード引数 `allowed_document_ids` を追加し、ルートが `list_visible_document_ids` を渡す。`test_source_chunk_visibility.py` 新設 |
| 2 | 🟠 コース切替で discuss の15分無活動タイマー・turnCount が残存し、別コース画面で旧コースの着地モーダルが誤発火（`Discuss.reset()` が未配線） | `switchCourse`/logout/401 失効で `Discuss.reset()`、`init({getActiveCourseId})` DI によるコース一致ガードを `maybeShowLanding` に追加（不一致時は `landing_shown` も送らない） |
| 3 | claim-refs がコース sources 判定のみで、`all_visible` スコープの引用チャンクが常に 404 | 「コース sources ∪ 本人可視 document」の複合判定へ拡張（fail-closed 維持） |
| 4 | `replace_message_id` の履歴 truncate が `discuss_scope` 不正値 422 より先に commit され、不正リクエストで履歴だけ消えた | scope 値検証を truncate より前へ前倒し |
| 5 | `origin_for_topic` に生 `_discussion` が渡り `LearningSupportOrigin.topic_title` に露出しうる | 変換済みラベル入りの topic_info を渡す（変換1箇所の原則は維持） |
| 6 | 軌道修正 #5 の「接続は『わたしの地図』で行える」事実文注記が着地モーダルに未実装 | 注記を追加 |
| 7 | 開幕画面で `document_run_artifacts` が document あたり2回 SELECT | `refs.equation_records(..., artifacts=)` 後方互換引数で1回化 + 回帰検知テスト |

観測基盤側の修正（ダッシュボードキー名不一致・payload 値ホワイトリスト・manifest 期間）は
`discuss_observation_design.md` §2-2 の追記を参照。修正後フルスイート 5,943 passed / 0 failed。

### 9.5 開幕画面の可読性改修（2026-07-26）

「左上の二枚看板が大きすぎる／中央の開幕画面が学習者にわかりにくい」というオーナー指摘を
受けた改修。§3.2 / §3.3 の**内容**（3区画・非LLM・DM1〜DM8）は変えず、**見せ方**だけを直す。

診断（実画面）: 表示が読めない原因は装飾ではなく、A層の内部表現がそのまま出ていたこと。

| 症状 | 原因 |
|---|---|
| 二枚看板が2行折り返しでサイドバー上部を占有 | 絵文字＋長ラベル（`📘 順番に学ぶ` / `🗣 この論文と議論する`）を2枚のカードに並べていた |
| 中心命題が英語の長文ボタン | `discussChip()` が claim の `label`（論文原文）をボタン文字列にしていた（行数制限なし） |
| バックボーンが `Theory basis` `Equation system` … の英語羅列 | main node の `label` は仕様上 stage label そのもの（CLAUDE.md #308）で、`_STAGE_LABELS` が英語。かつ `stage_label` と `label` が同値のため同じ文字を二重描画していた |
| `▶前提` `▶導出の核` `▶訂正の源` … が意味不明 | 支持構造を区画ごとに `<details>` で並べ、閉じた状態では A層の分類語の縦積みになっていた |
| 何をすればよいか分からない | 唯一の行動導線「最初の一手」が最下部（スクロール外）にあった |

確定した修正:

1. **二枚看板 → セグメントコントロール**（等重・既定選択なしの趣旨は維持）。1つのピルに
   2セグメント、高さ28px、ラベルは「順番に学ぶ」「論文と議論」。アイコンのみにはしない
   （初見で意味が取れなくなる）。選択状態は `aria-pressed` でも公開する。
2. **開幕画面の順序を反転** — 行動の起点（最初の一手）を最上部へ。
3. **各区画に平易な1行**（`.discuss-section-sub`）を付け、A層語彙を橋渡しする。
4. **中心命題は引用ブロック**（既定3行クランプ + 「全文を見る」）＋ 行動は
   「この主張について聞く」ボタンに分離。**原文の要約・和訳はしない**（DM8: 開幕画面は
   非LLM・既存成果の投影のみ。日本語要約が要るならパイプライン側で事前生成する別件）。
5. **バックボーンは矢印つなぎの流れ**（番号 + 日本語 stage 名）。`stage_label` を優先し
   二重描画を解消。点線（`source_backed` でない段階）の意味を凡例1行で明示。
6. **支持構造は1つの `<details>`（「支持構造をくわしく見る」）に集約**。「最も脆い一手」も
   `<details>` 化（見出し文言は維持）。
7. **stage 表示名の日本語化は `core/discuss/opening.py::_STAGE_LABELS` のみ**。stage コードは
   domain-neutral のまま、A層の `THEORY_STAGE_LABELS`（英語）と教員向け UI は無変更。
   未知 stage は従来どおりコードの整形表示へ縮退する。

未着手（別件）: 中心命題の日本語要約（要パイプライン変更）。現状は原文のまま3行クランプ。

### 9.6 着地画面の実効性改修（2026-07-26）

「『今日の議論を振り返る』に出るカードが、いずれも残す意味のない内容になっている」という
オーナー指摘を受けた改修。§3.5 の**構成**（候補 → 理解の確認 → 続きを学ぶ、スキップ可）は
変えず、カードの中身と「残せるもの」の供給源を直す。

診断（実画面 + コード）:

| 症状 | 原因 |
|---|---|
| 3枚とも自分が打った質問文の echo で、ボタンが「この理解で残す」 | `anchorCardHtml` が anchors/digest の `anchor_label` / `anchor_type_label` / `doubt_type_label` を捨て `question_text` だけを描画していた。app.js の `renderAnchorDigestCard` は同じ API から「この疑問は『◯◯』の**定義がわからない**についてでしたか？」を出しており、着地版だけが意味を落としていた。confirm の実体は帰属の確定であって「理解を残す」ではない |
| 残す価値のある「理解」が1つも並ばない | tension 候補は非LLM prefilter のヘッジ・逆接・再訪マーカーでしか立たず（`core/tension/prefilter.py`）、質問→回答だけの往復では発生しない。DM4 の生成プロンプト（末尾の言い換え・問い返し）が守られても、学習者の言い直しを受け止める先が無く `kind='question'` の痕跡になるだけだった |
| 「理解の確認」区画が出ない | 再構成 item は `teacher_approved` 等の承認済み claim にしか自動生成されない（`core/reconstruction/worker.py`）。claim 未レビューのコースでは永久に出ない（既知の限界。本改修の対象外） |

確定した修正:

1. **帰属カードを帰属の問いに戻す**（app.js と同じ形）。見出し「この疑問は『◯◯』の**◯◯**
   についてでしたか?」＋ 質問文は引用として残す ＋ 様相（doubt_type）の訂正チップ。
   `unclassified` / ラベル欠落時は断定せず「この疑問はどこへの引っかかりでしたか?」へ縮退し、
   様相はチップから本人が選ぶ。訂正確定も `POST /api/learning/anchors/{id}/confirm` の
   `doubt_type` で行い、確定するのは常に本人（P1）。
2. **「今日の理解を自分の言葉で」を着地画面の先頭に常設**（候補の有無に関わらず出す）。
   `POST /api/learning/courses/{course_id}/discuss/reflection`（新規・非LLM・migration 不要）
   が本人の記述を `kind='tension'` / `status='articulated'` の痕跡として1行記録する
   （`services.record_learner_articulated_tension`。候補 `candidate` を経由しないので
   LLM は一切関与しない）。`articulated` は `TENSION_OWNED_STATUSES` に含まれるため、
   この行はそのまま「わたしの地図」のノードになる。保存失敗時は入力を消さず事実文を出す。
   監査は既存 `entity_type='tension'`（old_status は空文字）。
3. **観測**: `landing_reflection_saved` を `METRIC_EVENT_VOCAB` に追加（14語彙目）。候補の
   `landing_confirmed` とは別導線なので合算しない。payload は空（DO1: 本文を積まない）。

非スコープ（このラウンドではやらない）: DM4 の遵守自体の計測（応答末尾に誘い・問い返しが
あったかの判定）、prefilter への言い換え宣言マーカー追加、再構成 item のオーサリング条件緩和。
