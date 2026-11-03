---
id: IK-0964
title: "学習者の分野の地図で、概念の札「未訪問」（本人がまだ訪れていない＝個人の訪問状態）に、台帳の状態の説明「この場所について、検証の記録（台帳）はまだありません…」が添えられ、学生は「未訪問」を「検証の記録が無い」の意味と読む"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/field_atlas_overlay_spec.md"
feature_context:
  realizing: "学習者が分野の地図で概念を選び、その場所が確かめられているか・自分が訪れたかを読み分ける"
  layers: [field_atlas_s, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 representation: routes/atlas_view.py は訪れていない概念の表示状態を "unvisited" にして pill を
    PILL_LABELS["unvisited"]（未訪問）に差し替えるが、pill_note は「表示が未訪問に変わっても意味の説明は台帳の状態に
    付く」として台帳の状態（記帳なし）の説明を付ける。札は個人の訪問状態、注記は認識的状態という直交する 2 軸を
    1 枚の札と 1 行の注記に重ねている。未訪問そのものの説明はどこにも無い。接続軸 meaning: 「未訪問」が台帳の
    意味で読まれる。処理軸 wording。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "直交する 2 つの状態を 1 枚の札に重ね、札は一方の状態を名乗り、添える説明は他方の状態を言う"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, trace_walk]
  note: "懐疑的な学生が宇宙膨張を選び、『未訪問』を自分が見ていない意味と思ったら検証の記録が無い意味らしいと記した。atlas_view の pill と pill_note の組み立てを辿った。run: uxsim/runs/c-astro-r25-atlas-r4/20261024T233738Z-85320 seq 5 / 8。"
resolution:
  perspective: [representation_change]
  note: >-
    札と説明を同じ軸に揃えた。routes/atlas_view.py は札が個人の状態（未訪問・いまここ）のとき説明も ATLAS_PILL_NOTES['unvisited'|'now']（「あなたはまだこの場所を開いていません。確かめられているかどうかは『検証』の行で示します。」）で引き、台帳の状態は検証行だけが言う。
    第 5 波（2026-10-27）: 残り — 「未訪問」の説明を「あなたはまだこの場所から学んでいません（地図で選ぶだけでは数えず、［ここから学ぶ］などで学習を始めると変わります）。…」にし、地図で選ぶことと学んだ記録を言い分けた。
  landed_in:
    - "backend/api/routes/atlas_view.py"
    - "backend/core/label_vocab.py"
    - "backend/tests/test_atlas_review_round25_r4.py"
    - "docs/manual/student/02-student.md"
    - "backend/tests/test_atlas_review_round25_r5.py"
  verification:
    methods: [guardrail]
    unverified:
      - "学習画面での見え方は再演していない"
related: [IK-0888, IK-0412]
view_of: []
history: []
---

## 課題

症状: 地図 L1 で台帳に記帳の無い概念がすべて「未訪問」と出て、その説明は「この場所について、検証の記録（台帳）はまだありません」。
学生は自分の訪問状態と検証の記録を区別できない。

原因: 表示状態は訪問、注記は台帳と、別々の軸を 1 つの札に重ねている。

## 発見の観点

通し受講（reproduction）で学生が語と意味のずれを記し、札と注記の決め方を辿った（trace_walk）。

## 解決の観点

第 4 波（2026-10-25）で解決。札と説明を同じ軸に揃えた。
第 5 波（2026-10-27）で「開く」の言い分けの残りを直した。

## 一般化

札の語と添える説明が同じ状態を指しているかを確かめる。確かめ方: 訪問済み・未訪問 × 台帳あり・なしの 4 通りで
札と注記の組を並べ、語と説明が食い違わないことを見る。

面 B（doubt）第 4 周の観察（2026-10-25・審判 doubt）: 同じ根を 2 人の学生が別の形で見た。st-04 は「未訪問」と台帳の注記を「訪問していないのか・確かめられていないのか」と読み分けられず、
st-07 は同じコースで全領域が霧（「論文未投入」）に見えた（灯りの差は面 C の範囲）。run: uxsim/runs/c-r25-doubt-r4/20261024T233659Z-74988（seq7）/
uxsim/runs/c-r25-doubt-r4/20261024T233257Z-31520（seq12）。

第 5 周の再演（2026-10-27・campaign c-astro-r25-atlas-r5）: 軸の混線は解消。学生（st-09）は札「未訪問」の説明「あなたはまだこの場所を開いていません。確かめられているかどうかは「検証」の行で示します」を訪問の軸で読み、台帳の軸と混同しなかった。残り（軽）: 地図でその概念を選んで右のパネルを開いた直後も札と説明が「まだこの場所を開いていません」のまま — 訪問は学習の痕跡（interest_traces の atlas node_id、［ここから学ぶ］等）で数え、地図で選ぶことは数えないが、説明の「開く」がその区別を言わない。説明を「まだこの場所から学んでいません」の形にするか、訪問に数わる操作を一文で言う。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T071720Z-78305 seq 5 / 8。


## 第 6 周の再演（第 25 周・面 C 第 6 周、2026-10-27・campaign c-astro-r25-atlas-r6）

GONE。札「未訪問」の説明が「あなたはまだこの場所から学んでいません（地図で選ぶだけでは数えず、［ここから学ぶ］などで学習を始めると変わります）。確かめられているかどうかは「検証」の行で示します。」になった。同じパネルの承認の行に内部の欄名「台帳のスコープ欄」が残る件は別課題 IK-1018 に起票。run: uxsim/runs/c-astro-r25-atlas-r6/20261027T123201Z-9456 seq 8。
