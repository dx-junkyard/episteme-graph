-- 修正報告を「採用」と「次版で対応済み」に分ける。
-- 公開時に反映済み扱いへ進めるのは incorporated_at が記録された報告だけ。
ALTER TABLE atlas_correction_reports
    ADD COLUMN IF NOT EXISTS incorporated_at TIMESTAMPTZ;
ALTER TABLE atlas_correction_reports
    ADD COLUMN IF NOT EXISTS incorporated_by UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE atlas_correction_reports
    ADD COLUMN IF NOT EXISTS incorporation_note TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_atlas_reports_incorporation
    ON atlas_correction_reports(cartridge_id, status, incorporated_at)
    WHERE status = 'accepted' AND applied_version = '';
