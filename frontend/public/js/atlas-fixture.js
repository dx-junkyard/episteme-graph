/* 分野の地図 — モック再現用フィクスチャ (仕様書 §12, Issue B)
 *
 * field_atlas_overlay_interactive.html と同一の描画を再現するデータ。
 * 実データ接続 (E系: 状態導出・Atlas API) が入るまで、オーバーレイUIは
 * このオブジェクトと同じ形の JSON で駆動する。
 *
 * 注意 (設計原則 §1.2):
 * - 踏破率の %・スコア・推薦文言をこのデータに含めない
 * - 霧 (fog) 領域に概念ラベル・誘導文言を持たせない (匿名ドット座標のみ)
 * - pill の文言は backend/core/atlas_state.py::PILL_LABELS を正本とする
 *   (固定は backend/tests/test_atlas_vocab_mirror.py)
 */
window.ATLAS_FIXTURE = {
  "skeleton_version": "2026.1",
  "cartridge": "particle_physics",
  "provenance": "AI生成・教員レビュー済",
  "crumbs": {
    "1": "素粒子物理 › 全体　—　ノードを選ぶと下に詳細",
    "2": "素粒子物理 › 有効場理論・QCD和則（コース）",
    "3": "… › B→c 和則 › 導出チェーン"
  },
  "initial_selection": { "1": "now", "2": "now", "3": "gy" },
  "nodes": {
    "now":   { "label": "B→c 和則", "pill": "いまここ", "status": "now", "verify": "検証: 原文3本に裏付け（式(12)・§3.2 ほか）。あなたの学習の現在地。", "endorse": "承認: 強い支持 — 3名の教員が承認（専門2分野）", "learn": true, "evid": true },
    "ope":   { "label": "演算子積展開", "pill": "原文に裏付け", "status": "verified", "verify": "検証: 短距離展開として複数過程で確認。スコープ: 高エネルギー・摂動領域。", "endorse": "承認: 標準の説明＋A先生の説明が並存", "learn": true, "evid": true },
    "disp":  { "label": "分散関係", "pill": "原文に裏付け", "status": "verified", "verify": "検証: 解析性に基づく厳密な関係。原文2本に導出の記帳あり。", "endorse": "承認: 2名の教員が承認", "learn": true, "evid": true },
    "qft":   { "label": "場の量子論の基礎", "pill": "原文に裏付け・習得済み", "status": "verified", "verify": "検証: 前提知識チェックを通過済み。", "endorse": "承認: 標準の説明", "learn": true, "evid": true },
    "dual":  { "label": "クォーク・ハドロン双対性", "pill": "暗黙の前提", "status": "assumed", "verify": "検証: 記帳された直接検証なし。多数の導出が依存（行動上の合意は強い）。", "endorse": "承認: 承認記録なし — 台帳のスコープ欄は空欄", "learn": true, "evid": true },
    "vcb":   { "label": "Vcb の測定間の不一致", "pill": "解釈が分かれる", "status": "contested", "verify": "検証: 包括的・排他的の2系統の測定が併存し、値が一致しない。", "endorse": "承認: 2つの解釈が帰属つきで並存", "learn": true, "evid": true },
    "spec":  { "label": "スペクトル密度", "pill": "未訪問", "status": "unvisited", "verify": "検証: 原文に記帳あり。まだ訪れていない概念。", "endorse": "承認: 標準の説明", "learn": true, "evid": true },
    "borel": { "label": "ボレル変換", "pill": "未訪問", "status": "unvisited", "glow": true, "verify": "検証: 原文に記帳あり。まだ訪れていない概念。", "endorse": "承認: 標準の説明", "learn": true, "evid": true },
    "fogL":  { "label": "格子QCD（霧の領域）", "pill": "論文未投入", "status": "fog", "verify": "この領域はAIの一般知識による地形。台帳に根拠の記帳がありません。", "endorse": "論文を追加すると、この領域に灯りがつきます。", "learn": false, "evid": false },
    "fogH":  { "label": "ハドロン分光（霧の領域）", "pill": "論文未投入", "status": "fog", "verify": "この領域はAIの一般知識による地形。台帳に根拠の記帳がありません。", "endorse": "論文を追加すると、この領域に灯りがつきます。", "learn": false, "evid": false },
    "d1":    { "label": "相関関数を定義", "pill": "原文に裏付け", "status": "verified", "verify": "検証: 原文 §2 式(3) に対応。", "endorse": "承認: 標準の説明", "learn": true, "evid": true },
    "d2":    { "label": "OPE で展開", "pill": "原文に裏付け", "status": "verified", "verify": "検証: 原文 §2 式(5)–(8) に対応。", "endorse": "承認: 標準の説明", "learn": true, "evid": true },
    "gy":    { "label": "高次項の抑制", "pill": "行間 — AIが補完", "status": "gap", "verify": "原文に対応する記述が見つからず、導出を繋ぐためにAIが補完した行間です。", "endorse": "先生に聞くポイント。確定すると暗黙前提の候補に昇格できます。", "learn": false, "evid": false },
    "d4":    { "label": "和則（整合関係）", "pill": "原文に裏付け", "status": "verified", "verify": "検証: 原文 §3 式(12) に対応。", "endorse": "承認: 3名の教員が承認", "learn": true, "evid": true },
    "d5":    { "label": "Vcb の抽出へ", "pill": "応用", "status": "link", "verify": "次の論文群（フレーバー物理）へ接続します。", "endorse": "承認: —", "learn": true, "evid": true }
  },
  "levels": {
    "1": {
      "viewBox": [680, 370],
      "svgTitle": "分野レベル",
      "svgDesc": "灯りの領域と霧の領域、状態つきノード",
      "regions": [
        { "id": "eft_sumrules", "label": "有効場理論・QCD和則", "kind": "lit", "x": 40, "y": 40, "w": 360, "h": 230 },
        { "id": "flavor_physics", "label": "フレーバー物理", "kind": "lit", "x": 430, "y": 40, "w": 210, "h": 140 },
        { "id": "fogL", "label": "格子QCD", "kind": "fog", "x": 40, "y": 300, "w": 290, "h": 60, "dots": [[160, 338], [215, 330], [262, 341]] },
        { "id": "fogH", "label": "ハドロン分光", "kind": "fog", "x": 350, "y": 300, "w": 290, "h": 60, "dots": [[460, 338], [515, 330], [568, 340]] }
      ],
      "nodes": [
        { "id": "ope", "region": "eft_sumrules", "x": 120, "y": 130 },
        { "id": "disp", "region": "eft_sumrules", "x": 200, "y": 215 },
        { "id": "dual", "region": "eft_sumrules", "x": 300, "y": 225, "label": "双対性の仮定" },
        { "id": "now", "region": "eft_sumrules", "x": 310, "y": 130 },
        { "id": "vcb", "region": "flavor_physics", "x": 535, "y": 110, "label": "Vcb の不一致" }
      ],
      "footprints": ["ope", "disp", "now"],
      "leaders": [["now", "vcb"]]
    },
    "2": {
      "viewBox": [680, 330],
      "svgTitle": "コースレベル",
      "svgDesc": "有効場理論コースの概念マップ",
      "nodes": [
        { "id": "qft", "x": 100, "y": 90 },
        { "id": "ope", "x": 250, "y": 70, "labelPos": "top" },
        { "id": "disp", "x": 240, "y": 190, "labelPos": "left" },
        { "id": "spec", "x": 450, "y": 70 },
        { "id": "borel", "x": 480, "y": 200 },
        { "id": "dual", "x": 330, "y": 270, "label": "双対性の仮定" },
        { "id": "now", "x": 360, "y": 150 }
      ],
      "edges": [
        ["qft", "ope"], ["ope", "spec"], ["ope", "now"],
        ["disp", "now"], ["dual", "disp"], ["now", "borel"]
      ]
    },
    "3": {
      "viewBox": [680, 400],
      "svgTitle": "導出レベル",
      "svgDesc": "理論操作グラフの導出チェーン",
      "chain": ["d1", "d2", "gy", "d4", "d5"]
    }
  }
};
