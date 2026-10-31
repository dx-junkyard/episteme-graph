---
id: IK-1057
title: "観測からの支持線の「観測の根」が、主グラフの段（Diagnostic / application 等）のノードに付いた主張を観測として数えるため、出現順の辺を外した後（IK-1048 の是正後）も Cepheus TeX の式どうしの依存の主張（$\\ddot I$ depends on …）の台帳に「複数の独立した支持線に支えられています」と出て、支持根に序論・観測節の背景文と式由来の合成主張が並ぶ"
status: open
recorded_at: 2026-10-28
resolved_at: null
sources:
  - "docs/features/stakes_ledger_design.md"
feature_context:
  realizing: "主張がどの観測記録に何本の独立した線で支えられているかを、台帳の事実文として教員に見せる"
  layers: [stakes_ledger_sl, doubt_d]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説（読み取りで一部確認）: 第 8 周の審判が api コンテナで support_paths.build_support_context(document_ids=[Cepheus TeX 1771872a]) を
    既定（exclude_claim_sequence_edges=True）と False の両方で読み、主張 42c4e2e8 の段階はどちらも several（辺の除外で変わらない）。
    支持根 5 件はいずれも observation_claim_targets の identified_via = theory_stage（段による同定）で、その主張を参照するノードには
    主グラフの集約ノード「Diagnostic / application」と式の詳細層の手順（67 ノードが参照する式由来の合成主張「The equation system approximates …」）が
    含まれる。対象の sink は同じ主張を参照する式の手順 13 個で、届く辺は式チェーン（eq→eq）の資格付きエッジ。つまり「複数」は出現順の辺ではなく、
    ①段の同定が序論の背景文・式由来の合成主張を観測の主張と数え ②それを参照する集約ノード・式の手順が根になり ③式チェーンの辺で対象へ届く、
    の組み合わせで生じる。IK-1048 の是正はこの主張では効く場所が無かった（是正の確かめは合成グラフだけ — IK-1048 の unverified）。
    処理軸 logic = 「段に付いた主張 = 観測」という同定の判定。接続軸 meaning = 集約ノード・式の手順への参照が「観測から支える経路」と読まれる。
    確定には、observation_targets の段による同定（stage B）を外した / 背景文を除いた読み取りで段階が変わるかを 1 本見る。
generalization:
  level: general
  general_form: "ある種類の対象かどうかを、その対象が属する集約単位の分類から決めると、集約単位に一緒に入った別の種類の項目まで同じ種類として数えられる"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [trace_walk, data_inspection]
  note: "第 7 波の同期キー（sync_keys_wave7.md「審判が読む点: 第 7 周 52350 の主張台帳と同じ主張で、支持根の列から背景文が消えたか」）に従い、te-03 の同じ主張の台帳を第 7 周と並べ（文が逐語同じ）、api コンテナで辺あり / なしの読み取りを 1 本走らせた（書き込みなし）。run: uxsim/runs/c-r25-doubt-r8/20261027T223539Z-94356（seq5・6・8・9・11・12・14・15・16）/ uxsim/runs/c-r25-doubt-r7/20261027T171640Z-52350（seq6〜14）。"
resolution:
  perspective: [pending, single_point_fix]
  note: >-
    部分是正（第 8 波・2026-10-31）。実測（砂場 api コンテナで読み取りのみ・Cepheus TeX 1771872a・台帳の全行）:
    主張 42c4e2e8 の支持根 5 件はすべて段（B）による同定で、種類は prior_work 1・equation_synthesis 1・interpretation 2・
    method_choice 1。根の層は主グラフの集約ノード（main）と式の詳細層の手順の両方。変種の比較（段階語だけ）:
    ①合成主張を観測から外す → この主張 several→single（全体で several→single が 20 行）②段 B を外す → none
    ③根を詳細層に限る → several のまま（合成主張の線）④③+① → 全 several 行が none。
    是正は根拠の確かな範囲だけ: observation_targets で origin=equation_synthesis をどの段でも観測と数えず、段の代理で拾った
    prior_work も採らない（主張自身の分類が段の代理に勝つ）。この主張は「単一の支持線…『診断・応用』が同時に崩れると」
    （集約ノード 1 本）になり、支持根から背景文と合成主張が消える。残り: 集約ノード（main）を根にしてよいかは未決
    （外すと実データでは観測からの支持線が全く無くなる — 段階の意味に触るので判断を持ち越す）。
    第 9 波（2026-10-31）: 段（B）の代理で拾った手法の選択（method_choice —「In the Cep B region, ^13CO is used to trace …」）も
    観測と数えない（_STAGE_PROXY_EXCLUDED_CLAIM_TYPES に追加・DSL / claim 型の段は外さない）。砂場（episteme-uxsim・読み取りのみ）の
    Cepheus TeX 台帳 1925 行で段階語の変化は 0、主張 42c4e2e8 の根から method_choice の 1 件が消える（段階は single のまま）。
    比較（comparison —「While C^18O is better suited …」）と解釈は残る。集約ノードの根は据え置き（未決のまま）。
    実測の方法: probe（build_support_context の obs_targets / graph.claim_refs を差し替えて compute_support_lines_from_context を
    全台帳行に掛け、段階語の変化を数える・書き込みなし）。単体テストは実 artifact の形の fixture（test_doubt_round25_wave8.py）。
  landed_in:
    - "backend/core/doubt/observation_targets.py"
    - "backend/core/doubt/support_paths.py"
    - "backend/tests/test_doubt_round25_wave8.py"
    - "backend/tests/test_doubt_round25_wave9.py"
    - "backend/tests/test_doubt_round25_wave10.py"
related: [IK-1048, IK-1030, IK-0902, IK-0858]
view_of: []
history: []
---

## 課題

症状: 第 7 波の是正（IK-1048 = 支持線から出現順の辺を外す）の後の第 8 周でも、te-03 が開いた Cepheus TeX の式どうしの依存の主張
「In an equation of this paper, $\ddot I$ depends on $T$, $T_s$, $M$, $W$.」の台帳は、第 7 周と逐語同じ
「支持線: この対象は複数の独立した支持線に支えられています。」と「観測からの支持根: Dust polarization studies of RCW 57A …、The equation system
approximates to eliminate …、In Cep B, the bending of field segments …、This value is comparable to …、In the Cep B region, ^13CO is used …」を出した。
ノード「理論の土台」の台帳も「単一の支持線…『Diagnostic / application』が同時に崩れると…」と同じ背景文の根。前提の地図の未検証合意リストでも
式の定義・依存の主張の多くが「観測からの支持線: 複数の支持線」（te-01・te-02）。周の前の実測（r8_prereqs.md）では Cepheus TeX の 1924 行中 98 行の段階が
辺の除外で変わっており、除外は効いているが、この主張の「複数」は別の経路から来ている。

原因（仮説）: 上の basis。観測の根の同定と、根になるノードの選び方。

## 発見の観点

是正記録の「確かめていない範囲」（合成グラフでのみ確認）と、同期キーが名指しした主張の台帳を前後の周で並べ、実データの読み取りで経路の正体を見た。

## 解決の観点

未解決（上の resolution.note）。

## 一般化

是正の仮説を合成データだけで確かめると、実データで症状を作っている別の経路を見落とす。確かめ方は、症状を報告した当の対象で、是正の前後の値を読み取りで並べること。

第 8 波（2026-10-31）: 合成主張・段の代理の背景文を観測の根から外す部分是正。集約ノードの根は未決（上の resolution）。

第 9 周の再演（2026-10-31）: 部分是正どおり。te-03 が同じ主張 42c4e2e8（Cepheus TeX・te-03 の入口は教材なので固定コースの替わりの影響なし）を開くと、第 8 周の「複数の独立した支持線」+ 序論の背景文 RCW 57A と合成主張の根 → 「この対象は単一の支持線に立っています。『診断・応用』が同時に崩れると…」になり、背景文と合成主張は根から消えた（段名も日本語 — IK-0901）。残り（STILL）: ①切れ目が主グラフの集約ノード『診断・応用』1 本（集約ノードを根にしてよいかは未決のまま）②根に method_choice / interpretation の文（「^13CO is used to trace…」「C^18O is better suited…」）が段の代理で残る。run: uxsim/runs/c-r25-doubt-r9/20261031T012052Z-34781（seq5・6・11・12・15）。

第 9 波（2026-10-31）: method_choice を段の代理から外した（上の resolution）。残り = 集約ノードの根（未決）と comparison の文。

## 第 10 周の再演（第 25 周・面 B 第 10 周、2026-10-31・judge_doubt_r10 — 同じ主張 42c4e2e8・同じ入口）

部分是正どおり（第 9 波）。主張の台帳の支持根 5 件を第 9 周と並べると、手法の選択（method_choice）「In the Cep B region, ^13CO is used to trace moderately dense gas…」が消え、その枠に解釈（interpretation）「However, the observed spacings also show significant variations…」が入った（段階は「単一の支持線…『診断・応用』」のまま）。STILL: ①切れ目が主グラフの集約ノード『診断・応用』1 本 ②「While C^18O is better suited for probing dense cores, ^13CO more effectively captures…, making it ideal for studying…」は内容がトレーサの選択の理由なのに claim_type が comparison なので根に残る（砂場の読み取り: theory_claims_live の claim_type = comparison）— 主張の型で外す是正は、型の付け方に依存する。新しい食い違い: 同じ主張を未検証合意リストの行は「複数の支持線」と言う（範囲がコースの教材 4 本 — 新規 IK-1120）。run: uxsim/runs/c-r25-doubt-r10/20261031T053250Z-67645（seq5・6・14・16）。

第 10 波（2026-10-31・審判推奨）: 集約ノードを根・切れ目にしてよいかの判断は据え置いたまま、単一の支持線の切れ目が主グラフの集約ノードのとき、事実文に「（『診断・応用』は理論の段をまとめたノードで、この支持線はその段の集約を通ります）」を添える（support_paths.SUPPORT_LINE_AGGREGATE_NOTE_TEMPLATE・graph_layer で判定）。comparison 型の手法の理由文は残る。status は open のまま。

## 第 11 周の再演（第 25 周・面 B 第 11 周、2026-10-31・judge_doubt_r11 — 固定コース 1bad7d9f・3 周連続の同じ対象）

部分是正の維持 + 据え置きの事実文を確認。主張 42c4e2e8 とノード「理論の土台」の台帳の支持線の文に「（『診断・応用』は理論の段をまとめたノードで、この支持線はその段の集約を通ります）」が付いた。STILL: 観測からの支持根に手法の選択の文（「While C^18O is better suited for probing dense cores, ^13CO more effectively captures … making it ideal for studying cloud…」）が入る（第 10 周は method_choice の根が消えていた — 支持根の選ばれ方が周で揺れる）。ノードの台帳の支持根には図番号の抜けた原文「as shown in Figure (also see Figure )」（TeX の参照の残り — IK-0697 の範囲）。run: 99946 seq5・6。
