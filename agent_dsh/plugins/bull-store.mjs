// 红色小牛（bull）共享内存状态 + Skill 规则库读写。
// 这是一个普通 ES 模块（非插件），供多个插件共享进程内状态，避免跨插件服务定义的复杂度。
// 原型定位：内存态随 dsh 进程重启即失；Skill 文件落盘到 bull/skills/*.skill 持久化。
import { readdir, readFile, writeFile, mkdir, unlink } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Skill 规则文件目录：bull/skills/
export const SKILLS_DIR = join(__dirname, '..', 'skills_library');

// 进程内共享状态
export const store = {
  snapshots: new Map(), // sessionId -> 股票池快照对象
  agents: new Map(),    // sessionId -> dsh agent 对象
  skills: [],           // [{ skillId, skillName, description, ruleContent }]
  bindings: new Map(),  // sessionId -> skillId
};

// 内置默认「通用分析」规则
export const DEFAULT_RULES = `你是「通用分析」——一个客观的股票池分析助手。

【职责】基于用户提供的股票池实时快照（今日池列表、近N天入池/出池、板块统计、连续在榜排行），做归纳与解释。

【要求】
1. 只依据快照里真实出现的数据回答，快照未覆盖的信息要明确说明「数据未覆盖」；
2. 严禁编造股票代码、名称、板块、数量、日期等具体事实；
3. 归纳板块热度、新增/出池动向、连续在榜标的时，尽量引用具体「代码 + 名称」；
4. 不预测股价、不荐股、不承诺收益；
5. 用清晰 markdown 输出，结论先行、分点归纳。`;

// 把任意导入/默认对象规范化为统一 skill 结构
export function normalizeSkill(raw) {
  const r = raw || {};
  const id = String(r.skillId || r.id || '').trim()
    || `skill-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  return {
    skillId: id,
    skillName: String(r.skillName || r.name || '').trim() || '未命名',
    description: String(r.description || '').trim(),
    ruleContent: String(r.ruleContent || r.rules || '').trim(),
  };
}

function skillPath(skillId) {
  return join(SKILLS_DIR, `${String(skillId).replace(/[^a-zA-Z0-9_-]/g, '_')}.skill`);
}

export async function saveSkill(skill) {
  await mkdir(SKILLS_DIR, { recursive: true });
  const payload = {
    type: 'stock-pool-skill',
    version: 1,
    skillId: skill.skillId,
    skillName: skill.skillName,
    description: skill.description,
    ruleContent: skill.ruleContent,
  };
  await writeFile(skillPath(skill.skillId), JSON.stringify(payload, null, 2), 'utf8');
}

// 宽松 JSON 解析：先严格解析；失败则把字符串字面量里的裸换行/制表符转义后重试。
// 解决手写 .skill 时 ruleContent 多行文本没写 \n 的常见错误。
export function lenientJson(text) {
  text = String(text || '').replace(/^﻿/, ''); // 去 BOM
  try {
    return JSON.parse(text);
  } catch {
    let out = '';
    let inStr = false;
    let esc = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (inStr) {
        if (esc) { out += ch; esc = false; continue; }
        if (ch === '\\') { out += ch; esc = true; continue; }
        if (ch === '"') { inStr = false; out += ch; continue; }
        if (ch === '\n') { out += '\\n'; continue; }
        if (ch === '\r') { out += '\\r'; continue; }
        if (ch === '\t') { out += '\\t'; continue; }
        out += ch; continue;
      }
      if (ch === '"') inStr = true;
      out += ch;
    }
    return JSON.parse(out);
  }
}

// 从 skills_library/*.skill 加载；空则播种默认「通用分析」
export async function loadSkills() {
  await mkdir(SKILLS_DIR, { recursive: true });
  const skills = [];
  let files = [];
  try {
    files = (await readdir(SKILLS_DIR)).filter(f => f.endsWith('.skill'));
  } catch { /* 目录不可读则忽略 */ }
  for (const f of files) {
    try {
      const raw = lenientJson(await readFile(join(SKILLS_DIR, f), 'utf8'));
      skills.push(normalizeSkill(raw));
    } catch (e) { console.warn(`[bull] 跳过损坏的 skill 文件 ${f}: ${e.message}`); }
  }
  store.skills = skills;
  if (store.skills.length === 0) {
    const def = { skillId: 'general', skillName: '通用分析', description: '内置默认：通用股票池分析', ruleContent: DEFAULT_RULES };
    store.skills.push(def);
    await saveSkill(def);
  }
}

export function listSkills() {
  return store.skills.map(s => ({ skillId: s.skillId, skillName: s.skillName, description: s.description }));
}

export function getSkill(skillId) {
  return store.skills.find(s => s.skillId === skillId) || null;
}

export function bindSkill(sessionId, skillId) {
  store.bindings.set(sessionId, skillId);
}

// 某会话当前应注入的规则全文（未绑定则取第一个 skill）
export function ruleContentFor(sessionId) {
  const bound = sessionId != null ? store.bindings.get(sessionId) : null;
  const skill = (bound && getSkill(bound)) || store.skills[0];
  return skill ? skill.ruleContent : DEFAULT_RULES;
}

export async function addSkill(raw) {
  const s = normalizeSkill(raw);
  store.skills.push(s);
  await saveSkill(s);
  return s;
}

export async function updateSkill(skillId, raw) {
  const i = store.skills.findIndex(s => s.skillId === skillId);
  if (i < 0) return null;
  const s = normalizeSkill({ ...raw, skillId });
  store.skills[i] = s;
  await saveSkill(s);
  return s;
}

export async function deleteSkill(skillId) {
  const i = store.skills.findIndex(s => s.skillId === skillId);
  if (i >= 0) store.skills.splice(i, 1);
  try { await unlink(skillPath(skillId)); } catch { /* 文件不存在则忽略 */ }
}
