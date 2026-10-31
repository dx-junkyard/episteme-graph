---
id: IK-1045
title: "学習者の地図の L2 が優先するのは「直近の凍結で加わった関係」だけで、前の版で確定した関係（ブラックホール — 超大質量ブラックホール・惑星形成 — 星形成）は次の凍結の後に手がかりから外れ、片端が描かれなければ線にも名前にも出ない"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-28
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/field_atlas_overlay_spec.md"
feature_context:
  realizing: "学生が分野の地図の L2（コースの概念マップ）で、教員が確定した関係を実線として読む"
  layers: [field_atlas_s, frontend_learning_ui]
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
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 logic: routes/atlas_view.py の _l2_course_hints は「凍結で加わった関係」を load_frozen_history の末尾 2 版の無向差として取り、
    その両端を course_hints に入れる。版上げが 1 回進むと、前の版で加わった確定済みの関係は差から外れ、両端は配置か 1 hop で拾われない限り
    上限 20 に入らない。接続軸 meaning: 「新しい版で加わった」という近い手がかりで「教員が確定した関係を見せる」を代えており、事実文
    「関係の実線は、ここに描いている項目どうしの間にだけ引いています」は描かなかった確定済みの関係を名前で言わない。構造軸 none（選出を
    サーバ 1 関数に寄せた構造は正しい）、統制軸 none。
generalization:
  level: repo_pattern
  general_form: "人が確定したものを表示で優先する条件を「直近の変更に含まれるか」で代えると、次の変更が来た時点で前の確定が優先から外れ、上限の外に黙って落ちる"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction, trace_walk]
  note: "凍結 2026.4（関係 1 本だけの版上げ）の後、学生 st-03 の L2 は実線 2 本（ブラックホール — 重力波・暗黒物質 — 暗黒成分の物理）で、2026.3 で確定した 2 本は端点（超大質量ブラックホール・惑星形成）が描かれず線にも名前にも出なかった（run uxsim/runs/c-astro-r25-atlas-r7/20261027T172454Z-57525 seq 6）。IK-1017 の是正の _l2_course_hints が末尾 2 版の差だけを読むことと突き合わせた。"
resolution:
  perspective: [carry_through, single_point_fix]
  note: >-
    第 7 波: added_relation_pairs を全版の差に（前の版で確定した関係も手がかりに残る）。第 8 波: それでもトピックの対応・配置で
    上限 20 が埋まると列の最後の関係が押し出された（同じ型の 2 回目）。select_l2_concept_ids に leading（焦点・いまここ）/
    relation_pairs / relation_reserve（_MAX_L2_NODES // 2）を足し、改訂で加わった関係に枠を先に配る（組ごとに両端そろえて・
    片端だけは入れない = 原則①′）。枠に入らなかった組はトピックの対応・配置の後に組ごとに試す。それでも描けなかった確定済みの
    関係は omitted_note の末尾に label_vocab.atlas_l2_undrawn_relations_line で名前を言う（「A — B」・件数なし・挙げきれなければ「ほか」）。
  landed_in:
    - "backend/api/routes/atlas_view.py"
    - "backend/core/label_vocab.py"
    - "docs/features/field_atlas_overlay_spec.md"
    - "docs/manual/student/02-student.md"
    - "backend/tests/test_atlas_review_round25_r7.py"
    - "backend/tests/test_atlas_review_round25_r8.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "L2 の選出は 1 関数に寄っていたので、入力の導出（版の差）と枠の配分だけを直した。固有の差分: 優先の列に足すのでなく、確定済みの関係に枠を予約し、描けない分は名前で言う（次の手がかりが来ても押し出されない）。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場で固定コース（8 トピックの binding）の L2 に、2026.2 以降の確定済みの関係が実線か「描いていないもの」の名前で出ること（次周の再演）"
      - "関係の枠（上限の半分）がトピックの対応・配置を押し出して、配置の手がかりが事実文から消える分野での見え方"
related: [IK-1017, IK-0989]
view_of: []
history: []
---

## 課題

症状: 第 6 周（凍結 2026.3 の直後）は「ブラックホール — 超大質量ブラックホール」が実線で出ていた。第 7 周（2026.4 の後・足跡あり）の L2 は、
いまここ（星間磁場）・コースの論文の配置・2026.4 で加わった関係の両端で 20 概念が埋まり、2026.3 で確定した「ブラックホール — 超大質量ブラック
ホール」「惑星形成 — 星形成」は片端（超大質量ブラックホール・惑星形成）が描かれず、線にも名前にも出なかった。事実文は「関係の実線は、ここに
描いている項目どうしの間にだけ引いています」だけで、描かなかった確定済みの関係は言わない。

原因: IK-1017 の是正が「凍結で加わった関係」を直近の 2 版の差で取るため、版上げのたびに前の確定が手がかりから外れる。

## 発見の観点

前周との対照（reproduction）で実線の本数の変化を読み、選出の入力（trace_walk）を確かめた。

## 解決の観点

選出の入力の導出（第 7 波）と、確定済みの関係への枠の予約 + 描けない分の名前（第 8 波）。

## 一般化

型 extent-decided-by-proxy-signal: 「確定済み」を「最近変わった」で代えている。確かめ方: 関係だけの版上げを 2 回続けた後、1 回目に確定した関係が
L2 の実線か事実文のどちらかに出ること。

## 第 7 波の是正（2026-10-28）

added_relation_pairs（routes/atlas_view.py）を末尾 2 版の差から全版の差へ: load_frozen_history を遡って現行凍結版の各関係が最後に加わった版を決め、 直近の改訂 → 古い改訂の順に端点の組を返す（系列の最初の版から在る関係は含めない・同じ版の中は現行版の並び順・数値なし）。select_l2_concept_ids の course_hints は配置の後にこれを受けるので、前の版で確定した関係も上限内で描かれる。手がかりの語 ATLAS_L2_HINT_ADDED_RELATIONS は 「地図の版の改訂で加わった関係」（選出と一致）。描かなかった確定済みの関係を名前で挙げる行は足していない。

## 第 8 周の再演（2026-10-28・judge_atlas_r8）

**STILL（是正は前提を満たさない）**。凍結 2026.5（関係だけの版上げの 2 回目）の後、固定コース 1bad7d9f の L2 は足跡あり（st-03・run uxsim/runs/c-astro-r25-atlas-r8/20261027T224229Z-98423 seq 6）でも足跡なし（st-01・20261027T224409Z-99225 seq 6）でも実線が「ブラックホール — 重力波」の 1 本だけ。2026.5 で加わった「暗黒エネルギー — 暗黒成分の物理」すら線にならない（暗黒成分の物理が 20 概念に入らない）。2026.3・2026.4 の確定済み 3 本も線にも名前にも出ない（第 7 周は 2 本）。原因（読んで確かめた）: `select_l2_concept_ids` は priority（焦点・いまここ・**コースの全トピックの atlas_node_id**・足跡）→ 配置 → 改訂で加わった関係の両端 の順で上限 20 を埋める。H43 で固定コース（8 トピックの binding あり）を開いたことで、トピックの対応と配置だけで上限が埋まり、第 7 波の「全版の差」に直した関係の両端は列の最後で上限の外に出る。第 7 周は固定外のコース（binding なし）だったので余地があった。事実文は「地図の版の改訂で加わった関係…を優先しています」と言う（偶々両端が別の理由で描かれた古い関係 1 本が効いたと数えられる）。描かなかった確定済みの関係を名前で言う案は未実施のまま — 上限を持つ限りこちらが要る。判定の補足: 下書きのプレビューに概念どうしの関係が無いという頭脳の疑い（te-01 2 回目 seq 2）は読み落としで、プレビューの末尾 4 行に 2026.2〜2026.4 の概念どうしの関係が並び、凍結の影響も `removed_edges: []`（複製で落ちていない）。

## 第 8 波の是正（2026-10-28）

`select_l2_concept_ids` に `leading` / `relation_pairs` / `relation_reserve` を足し、焦点・いまここの次に、改訂で加わった関係へ上限の
半分を**組ごとに**先に配る（片端だけを入れない）。トピックの対応・足跡・配置はその後。描けなかった確定済みの関係は
`omitted_note` の末尾で名前を言う（`label_vocab.atlas_l2_undrawn_relations_line`・件数なし）。優先の列に手がかりを足していく
直し方をやめ、確定済みには枠と名前の 2 つの出口を持たせた。

## 第 9 周の再演（2026-10-31・judge_atlas_r9）

**GONE**。凍結 2026.6（関係だけの版上げの 3 回目）の後、同じ固定コース 1bad7d9f（8 トピックの binding）の L2 に、確定済みの関係が全部実線で出た — 足跡あり（st-03・run uxsim/runs/c-astro-r25-atlas-r9/20261031T015252Z-38248 seq 6）も足跡なし（st-01・20261031T015432Z-39319 seq 6）も同じ 6 本（ブラックホール — 重力波 / ブラックホール — 超大質量ブラックホール / 惑星形成 — 星形成 / 暗黒物質 — 暗黒成分の物理 / 暗黒エネルギー — 暗黒成分の物理 / 宇宙膨張 — 一般相対論）。前後比較: 第 8 周（同コース・同学生）は「ブラックホール — 重力波」1 本だけで、2026.5 で加わった関係すら線にならなかった。描く 20 概念に暗黒成分の物理・惑星形成・一般相対論が入った（`relation_reserve` が効いた）。全部が描けたので「ここには描いていないもの:」の名前の行は出ていない（この経路は未観測 — 確定済みの関係が上限の半分を超える状態で確かめる）。残り（別エントリ）: 実線に向き・種別が無い（IK-1072）。

## 第 10 周の再演（2026-10-31・judge_atlas_r10）

**GONE（名前の行の経路を初めて観測）**。凍結 2026.7 で確定済みの関係が 7 本（端点 12）になり、上限の半分（10）の枠を超えた。同じ固定コース 1bad7d9f の L2（st-03 run uxsim/runs/c-astro-r25-atlas-r10/20261031T053524Z-69161 seq 6・st-01 uxsim/runs/c-astro-r25-atlas-r10/20261031T053658Z-69836 seq 6、2 人とも同じ組）は実線 6 本（今回の初期宇宙・インフレーション → 初期宇宙の物理を含む）+ 事実文末尾「地図の版の改訂で加わった関係のうち、ここには描いていないもの: 惑星形成 — 星形成。」。前後比較: 第 9 周は 6 本全部が描け名前の行は未観測 → 第 10 周は直近 → 古い順に枠が配られ、最も古い 2026.3 の 1 本が名前の行に回った（20 概念から惑星形成が外れ、初期宇宙・インフレーションと初期宇宙の物理が入った）。確定済みの関係は線か名前のどちらかに必ず出た。残り: その名前の行に向きが無い（IK-1118）・「改訂で加わった」が全改訂を指すと読めない（IK-1119）。

## 第 11 周の再演（2026-10-31・judge_atlas_r11）

**GONE（維持）**。確定済みの関係 8 本のうち 7 本が実線、上限で描けなかった 1 本「「ブラックホール」と「超大質量ブラックホール」（関連）」が名前の行（run uxsim/runs/c-astro-r25-atlas-r11/20261031T102103Z-2572 seq 6）。線か名前のどちらかに必ず出た。
