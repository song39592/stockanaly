import test from 'node:test';
import assert from 'node:assert/strict';
import { lenientJson, normalizeSkill, validateSkill } from './bull-store.mjs';

test('legacy skill is normalized to the v2-compatible schema', () => {
  const s = normalizeSkill({ id: 'legacy', name: '旧规则', rules: '只做测试' });
  assert.equal(s.skillId, 'legacy');
  assert.equal(s.version, 2);
  assert.equal(s.strategyType, 'general');
  assert.deepEqual(s.parameters, {});
});

test('lenientJson accepts a raw newline inside ruleContent', () => {
  const parsed = lenientJson('{"skillId":"x","ruleContent":"第一行\n第二行"}');
  assert.equal(parsed.ruleContent, '第一行\n第二行');
});

test('skill ids cannot collide through filename sanitization', () => {
  const s = normalizeSkill({ skillId: '../bad', skillName: '坏规则', ruleContent: 'x' });
  assert.match(validateSkill(s), /skillId/);
});
