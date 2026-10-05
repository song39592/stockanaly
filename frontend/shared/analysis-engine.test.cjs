const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { analyze, backtest } = require('./analysis-engine.js');

const data = {
  '2026-08-01': [
    { code: '000001', name: '甲', industry: '银行', price: 10, change: 1 },
    { code: '000002', name: '乙', industry: '科技', price: 20, change: -1 },
  ],
  '2026-08-02': [
    { code: '000001', name: '甲', industry: '银行', price: 11, change: 2 },
    { code: '000003', name: '丙', industry: '银行', price: 30, change: 3 },
  ],
  '2026-08-03': [
    { code: '000001', name: '甲', industry: '银行', price: 12, change: 1 },
    { code: '000003', name: '丙', industry: '银行', price: 33, change: 2 },
  ],
};

test('analysis only sees dates through target date', () => {
  const out = analyze(data, '2026-08-02');
  assert.equal(out.ok, true);
  assert.equal(out.dataAsOf, '2026-08-02');
  assert.equal(out.market.poolSize, 2);
  assert.equal(out.candidates.find(x => x.code === '000001').days, 2);
  assert.equal(out.candidates.find(x => x.code === '000003').isNew, true);
});

test('backtest evaluates forward imported prices and reports missing samples honestly', () => {
  const out = backtest(data, { watchScore: 0, maxCandidates: 10, horizons: [1] });
  assert.ok(out.signalCount > 0);
  assert.ok(out.horizons[1].sampleCount > 0);
  assert.equal(typeof out.horizons[1].averageReturnPct, 'number');
});

test('empty data returns explicit error', () => {
  assert.deepEqual(analyze({}, null), { ok: false, error: '没有可分析的数据日' });
});

test('frontend inline application scripts have valid JavaScript syntax', () => {
  for (const file of ['index.html', 'mentor-lab.html']) {
    const html = fs.readFileSync(__dirname + '/' + file, 'utf8');
    const start = html.lastIndexOf('<script>');
    const end = html.indexOf('</script>', start);
    assert.ok(start >= 0 && end > start, file);
    assert.doesNotThrow(() => new vm.Script(html.slice(start + 8, end)), file);
  }
});
