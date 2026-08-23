-- Migration 066: カテゴリギャップ候補（論文の解析から地図カテゴリを育てる層）
-- 正本: docs/features/category_gap_candidates_design.md §5.2（裁定は §4、不変条項の写像は §2）
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う
-- （冪等・毎起動・番号順に再実行）。すべて IF NOT EXISTS で冪等。
--
-- 設計上の要点:
--   1. 2層分離（§4.3 裁定）: 論文単位の**信号**（AI 由来・供給側）は
--      landscape_gap_signals の行として蓄積し、cluster 単位の**候補**は行として持たず
--      読み時に導出する（core/atlas_gaps/store.py::derive_candidates）。行として持つのは
--      教員の**判断**（atlas_gap_decisions）だけ。これにより
--      (a) 次版で概念が入った候補の自然消滅 (b) supersede 連鎖の回避
--      (c) 完了フラグ・掃除バッチの不要化 を同時に満たす（G1 / PN-2）。
--   2. LS3 同型の再解析セマンティクス: 信号の再投入は当該 document の
--      status='active' 行を 'superseded' に遷移させてから挿入する
--      （行削除はしない = P4。正本の実装は core/atlas_gaps/store.py::record_signals）。
--   3. LS5 数値を見せない: confidence は DB のみに置き、API/UI へは段階ラベル
--      （core/atlas_gaps/schema.py::confidence_label）へ変換したものしか載せない。
--      「該当論文 N 件」のような集計数値も出さない（支持論文はタイトル列挙で示す）。
--   4. FK の非対称（§5.2）: signals は documents(id) に CASCADE（論文を消せば信号も消える。
--      landscape_placements と同じ孤児対策で、明示 DELETE の追加が不要）。decisions は
--      コーパス横断の共同財行なので **document への FK を張らない**（1論文が消えても
--      教員の判断は残る）。
--   5. 却下の永続性（§4.2 裁定）: cluster_key は
--      gap|{domain_key}|{parent_region_id}|{normalize_label(proposed_label)} の**版非依存**キー。
--      skeleton_version は signals 側の**刻印列**として持ち、旧版由来は読み時に
--      version_mismatch として示す。版をキーに含めると凍結ごとに却下済み候補が蘇る
--      （ゾンビ候補・G4 違反の運用負荷）。
--   6. 採用と反映の分離（migration 046 の前例）: status='accepted' は「カテゴリとして妥当」の
--      判断のみで骨格 draft は変わらない。draft_node_id は次版下書きへ取り込んだ node の id、
--      applied_version は凍結で実際に反映された版の刻印（'' = 未反映）。
--   7. status に 'candidate' を含めるのは設計書 §5.2 の CHECK 語彙
--      ('accepted','dismissed','merged') からの**意図的な最小逸脱**である。見送り（dismissed）を
--      「見送り済みフィルタから戻す」restore（§5.4）を**行削除なしで**実現するには、
--      判断を取り消した状態を表す語彙が1つ必要になる（AB3 / P4: 行を消さない）。
--      candidate = 「まだ判断していない / 判断を取り消した」であり、候補そのものは
--      引き続き読み時導出（本表に候補行を持つわけではない）。

-- (a) 論文単位の構造化信号（AI 由来・供給側）
CREATE TABLE IF NOT EXISTS landscape_gap_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    run_id UUID,
    domain_key TEXT NOT NULL,
    -- どの版の地図に対する信号か（刻印。cluster_key には含めない = 版非依存）
    skeleton_version TEXT NOT NULL DEFAULT '',
    layer TEXT NOT NULL DEFAULT 'concept'
        CONSTRAINT landscape_gap_signals_layer_check CHECK (layer IN ('region','concept')),
    -- layer='concept' のときの親領域 id（layer='region' では ''）
    parent_region_id TEXT NOT NULL DEFAULT '',
    proposed_label TEXT NOT NULL,
    -- cluster_key と骨格ラベル突合に使う正規化済みラベル（正本は
    -- core/atlas_gaps/schema.py::normalize_label）
    normalized_label TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    -- LS4: 原文逐語（verbatim 検査は agent 側 validator。不一致はその候補のみ drop）
    evidence_quote TEXT NOT NULL DEFAULT '',
    -- LS5: DB のみ。API/UI へは段階ラベルのみ
    confidence DOUBLE PRECISION,
    status TEXT NOT NULL DEFAULT 'active'
        CONSTRAINT landscape_gap_signals_status_check CHECK (status IN ('active','superseded')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_landscape_gap_signals_domain
    ON landscape_gap_signals (domain_key, status);
CREATE INDEX IF NOT EXISTS idx_landscape_gap_signals_document
    ON landscape_gap_signals (document_id);

-- (b) cluster 単位の教員判断（人間由来・弁側）
CREATE TABLE IF NOT EXISTS atlas_gap_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- gap|{domain_key}|{parent_region_id}|{normalize_label(proposed_label)}（版非依存）
    cluster_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'candidate'
        CONSTRAINT atlas_gap_decisions_status_check CHECK (status IN
            ('candidate','accepted','dismissed','merged')),
    -- 見送り（dismissed）は理由必須（空は 422。強制は core/atlas_gaps/store.py）
    review_note TEXT NOT NULL DEFAULT '',
    -- 統合先の cluster_key（status='merged' のとき）
    merged_into TEXT NOT NULL DEFAULT '',
    -- 次版下書きへ取り込んだ node の id（'' = 未取り込み）
    draft_node_id TEXT NOT NULL DEFAULT '',
    -- 凍結で実際に反映された骨格の版（'' = 未反映。採用と反映の分離）
    applied_version TEXT NOT NULL DEFAULT '',
    decided_by UUID REFERENCES users(id) ON DELETE SET NULL,
    decided_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_atlas_gap_decisions_status
    ON atlas_gap_decisions (status);
-- 公開前チェック（採用済みでまだ次版に反映されていない候補）の取得用
CREATE INDEX IF NOT EXISTS idx_atlas_gap_decisions_pending_apply
    ON atlas_gap_decisions (cluster_key)
    WHERE status = 'accepted' AND applied_version = '';
