import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

// Keep every chart/period/scope, but move repeated ticket rows off the render path.
// Content-addressed detail files let an open page export its exact snapshot even
// while the next refresh is being published.
export function writeModelCacheAssets(cache, outputDir) {
  if (!cache?.generatedAt || !cache.periods) throw new Error('Invalid model cache');
  const detailsDir = path.join(outputDir, 'model_mtm_details');
  fs.mkdirSync(detailsDir, { recursive: true });
  const summary = { ...cache, deliverySchema: 'model-summary-v1', periods: {} };
  for (const [periodKey, period] of Object.entries(cache.periods)) {
    const scopes = {};
    for (const [scope, slice] of Object.entries(period.scopes)) {
      const { detailRows, ...light } = slice;
      if (!Array.isArray(detailRows)) throw new Error(`Missing details: ${periodKey}/${scope}`);
      const json = JSON.stringify({ generatedAt: cache.generatedAt, periodKey, scope, rows: detailRows });
      const detailKey = createHash('sha256').update(json).digest('hex');
      fs.writeFileSync(path.join(detailsDir, `${detailKey}.json`), json);
      scopes[scope] = { ...light, detailKey, detailCount: detailRows.length };
    }
    summary.periods[periodKey] = { ...period, scopes };
  }
  fs.writeFileSync(path.join(outputDir, 'analysis_model_mtm_summary.json'), JSON.stringify(summary));
  return summary;
}
