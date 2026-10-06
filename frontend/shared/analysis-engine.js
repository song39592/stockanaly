(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.StockAnalysis = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const DEFAULT_CONFIG = Object.freeze({
    minContinuousDays: 3,
    maxCandidates: 10,
    sectorTopN: 3,
    watchScore: 50,
    focusScore: 70,
    horizons: [1, 3, 5, 10],
  });

  function n(value) {
    const x = Number(value);
    return Number.isFinite(x) ? x : null;
  }

  function round(value, digits = 2) {
    const p = 10 ** digits;
    return Math.round((value + Number.EPSILON) * p) / p;
  }

  function datesOf(data, throughDate) {
    return Object.keys(data || {}).filter(d => !throughDate || d <= throughDate).sort();
  }

  function indexByCode(rows) {
    const out = new Map();
    for (const row of rows || []) if (row && row.code) out.set(String(row.code), row);
    return out;
  }

  function streak(data, dates, code, dateIndex) {
    let count = 0;
    for (let i = dateIndex; i >= 0; i--) {
      if (indexByCode(data[dates[i]]).has(code)) count += 1;
      else break;
    }
    return count;
  }

  function sectorCounts(rows) {
    const counts = new Map();
    for (const s of rows || []) {
      const industry = String(s.industry || '未分类');
      counts.set(industry, (counts.get(industry) || 0) + 1);
    }
    return counts;
  }

  function signalLabel(score, cfg) {
    if (score >= cfg.focusScore) return '重点关注';
    if (score >= cfg.watchScore) return '观察';
    return '暂不关注';
  }

  function analyze(data, targetDate, options) {
    const cfg = { ...DEFAULT_CONFIG, ...(options || {}) };
    const dates = datesOf(data, targetDate);
    const date = targetDate && dates.includes(targetDate) ? targetDate : dates[dates.length - 1];
    if (!date) return { ok: false, error: '没有可分析的数据日' };

    const i = dates.indexOf(date);
    const today = data[date] || [];
    const prev = i > 0 ? data[dates[i - 1]] || [] : [];
    const prevMap = indexByCode(prev);
    const todayMap = indexByCode(today);
    const sector = sectorCounts(today);
    const sectorRank = Array.from(sector.entries()).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    const topSectors = new Set(sectorRank.slice(0, cfg.sectorTopN).map(x => x[0]));
    const added = today.filter(s => !prevMap.has(String(s.code)));
    const removed = prev.filter(s => !todayMap.has(String(s.code)));
    const changes = today.map(s => n(s.change)).filter(x => x !== null);
    const positive = changes.filter(x => x > 0).length;
    const avgChange = changes.length ? changes.reduce((a, b) => a + b, 0) / changes.length : null;
    const growth = prev.length ? (today.length - prev.length) / prev.length : 0;
    const retention = prev.length ? (prev.length - removed.length) / prev.length : null;
    const topSectorShare = today.length && sectorRank.length ? sectorRank[0][1] / today.length : 0;

    let regimeScore = 50;
    regimeScore += Math.max(-15, Math.min(15, growth * 100));
    if (avgChange !== null) regimeScore += Math.max(-15, Math.min(15, avgChange * 2));
    if (changes.length) regimeScore += ((positive / changes.length) - 0.5) * 20;
    regimeScore = Math.max(0, Math.min(100, round(regimeScore, 0)));
    const regime = regimeScore >= 65 ? 'risk_on' : regimeScore <= 35 ? 'risk_off' : 'neutral';

    const candidates = today.map(s => {
      const code = String(s.code);
      const days = streak(data, dates, code, i);
      const change = n(s.change);
      const price = n(s.price);
      const ind = String(s.industry || '未分类');
      const indCount = sector.get(ind) || 0;
      const indShare = today.length ? indCount / today.length : 0;
      const isNew = !prevMap.has(code);
      let score = 0;
      score += Math.min(days, 7) / 7 * 35;
      score += Math.min(indCount, 8) / 8 * 20;
      score += topSectors.has(ind) ? 10 : 0;
      score += isNew ? 5 : 10;
      if (change !== null) score += Math.max(0, Math.min(15, 7.5 + change * 1.5));
      else score += 4;
      if (days < cfg.minContinuousDays) score -= 10;
      score = Math.max(0, Math.min(100, round(score, 0)));

      const bullEvidence = [];
      const bearEvidence = [];
      bullEvidence.push(`连续在池 ${days} 个数据日`);
      bullEvidence.push(`${ind} 当日入池 ${indCount} 只，占比 ${round(indShare * 100, 1)}%`);
      if (topSectors.has(ind)) bullEvidence.push('所属行业位于当日股票池数量前列');
      if (change !== null && change > 0) bullEvidence.push(`导入涨幅为 ${round(change, 2)}%`);
      if (isNew) bearEvidence.push('当日首次/重新入池，持续性尚未确认');
      if (days < cfg.minContinuousDays) bearEvidence.push(`连续在池不足 ${cfg.minContinuousDays} 个数据日`);
      if (change === null) bearEvidence.push('缺少当日涨幅字段');
      else if (change < 0) bearEvidence.push(`导入涨幅为 ${round(change, 2)}%`);
      if (price === null) bearEvidence.push('缺少可用于收益复盘的价格字段');

      return {
        code, name: String(s.name || ''), industry: ind, date,
        score, signal: signalLabel(score, cfg), days, isNew,
        price, change, sectorCount: indCount,
        bullEvidence, bearEvidence,
        invalidConditions: ['后续数据日出池', '所属行业热度明显下降', '出现未经当前数据覆盖的重大风险事件'],
      };
    }).sort((a, b) => b.score - a.score || b.days - a.days || a.code.localeCompare(b.code));

    const priced = today.filter(s => n(s.price) !== null).length;
    const changed = changes.length;
    return {
      ok: true,
      version: 1,
      dataAsOf: date,
      generatedAt: new Date().toISOString(),
      methodology: '仅使用目标数据日及之前的股票池、连续在池、行业分布和导入涨幅计算，不使用未来数据。',
      market: {
        regime, score: regimeScore, poolSize: today.length,
        added: added.length, removed: removed.length,
        growthPct: round(growth * 100, 2), retentionPct: retention === null ? null : round(retention * 100, 2),
        averageChangePct: avgChange === null ? null : round(avgChange, 2),
        positiveBreadthPct: changes.length ? round(positive / changes.length * 100, 2) : null,
        topSectorSharePct: round(topSectorShare * 100, 2),
      },
      sectorStats: sectorRank.map(([industry, count]) => ({ industry, count, sharePct: round(count / Math.max(1, today.length) * 100, 2) })),
      candidates: candidates.slice(0, cfg.maxCandidates),
      dataQuality: {
        priceCoveragePct: round(priced / Math.max(1, today.length) * 100, 2),
        changeCoveragePct: round(changed / Math.max(1, today.length) * 100, 2),
        warnings: [
          ...(priced < today.length ? ['部分股票缺少价格，相关回测样本会被跳过'] : []),
          ...(changed < today.length ? ['部分股票缺少涨幅，评分使用中性缺省值'] : []),
          ...(dates.length < 5 ? ['历史数据少于 5 个数据日，持续性与回测统计可信度较低'] : []),
        ],
      },
    };
  }

  function backtest(data, options) {
    const cfg = { ...DEFAULT_CONFIG, ...(options || {}) };
    const dates = datesOf(data);
    const samples = [];
    for (let i = 0; i < dates.length; i++) {
      const analysis = analyze(data, dates[i], cfg);
      if (!analysis.ok) continue;
      for (const candidate of analysis.candidates.filter(x => x.score >= cfg.watchScore)) {
        if (!(candidate.price > 0)) continue;
        const returns = {};
        for (const horizon of cfg.horizons) {
          const futureDate = dates[i + horizon];
          const future = futureDate ? indexByCode(data[futureDate]).get(candidate.code) : null;
          const futurePrice = future ? n(future.price) : null;
          returns[horizon] = futurePrice > 0 ? round((futurePrice / candidate.price - 1) * 100, 2) : null;
        }
        samples.push({ date: dates[i], code: candidate.code, name: candidate.name, score: candidate.score, signal: candidate.signal, entryPrice: candidate.price, returns });
      }
    }

    const horizons = {};
    for (const h of cfg.horizons) {
      const vals = samples.map(s => s.returns[h]).filter(x => x !== null);
      horizons[h] = {
        sampleCount: vals.length,
        missingCount: samples.length - vals.length,
        averageReturnPct: vals.length ? round(vals.reduce((a, b) => a + b, 0) / vals.length, 2) : null,
        winRatePct: vals.length ? round(vals.filter(x => x > 0).length / vals.length * 100, 2) : null,
        bestPct: vals.length ? Math.max(...vals) : null,
        worstPct: vals.length ? Math.min(...vals) : null,
      };
    }
    return {
      version: 1,
      generatedAt: new Date().toISOString(),
      methodology: '每个历史数据日独立生成当时信号；仅用后续第 N 个已导入数据日同一股票的价格评价。缺价或已出池样本不填补。未计交易成本、停牌和复权。',
      signalCount: samples.length,
      horizons,
      recentSamples: samples.slice(-50),
    };
  }

  return { DEFAULT_CONFIG, analyze, backtest };
});
