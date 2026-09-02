// bull-http —— 红色小牛对外 REST 网关（/api/bull/*）+ 会话/Agent 生命周期管理 + 快照存储。
// 数据流：前端 → 本路由 → dsh Agent loop（tool 调用 + Skill 规则注入）→ DeepSeek LLM。
import { randomUUID } from 'node:crypto';
import { installModelSelection } from '@deepseek-ai/dsh-agent';
import { createUserMessage } from '@deepseek-ai/dsh-llm';
import { SessionId } from '@deepseek-ai/dsh-session';
import {
  store, listSkills, getSkill, bindSkill, ruleContentFor,
  addSkill, updateSkill, deleteSkill, lenientJson, ensureSkillsLoaded, normalizeSkill, validateSkill,
} from './bull-store.mjs';

export const name = 'bull-http';
export const inject = ['webServer', 'agents', 'sessions', 'agentDefaultModel'];

const MAX_BODY = 5 * 1024 * 1024; // 5MB，快照足够

function corsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  };
}

function json(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', ...corsHeaders() });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', c => {
      data += c;
      if (data.length > MAX_BODY) { reject(new Error('请求体过大')); req.destroy(); }
    });
    req.on('end', () => resolve(data));
    req.on('error', reject);
  });
}

function safeJson(text) {
  try { return lenientJson(text); } catch { return null; }
}

// 从 assistant/message 事件里抽取最终文本（复刻 dsh-headless 的 summarize 逻辑）
function summarize(events, firstSeq) {
  let started = false;
  let text = '';
  for (const ev of events) {
    if (ev.seq < firstSeq) continue;
    if (ev.type === 'turn/start') { started = true; continue; }
    if (!started) continue;
    if (ev.type === 'assistant/message') {
      const joined = ((ev.data && ev.data.message && ev.data.message.content) || [])
        .filter(b => b.type === 'text').map(b => b.text).join('');
      if (joined) text = joined;
    }
  }
  return text;
}

export function apply(ctx) {
  const { webServer, agents, sessions, agentDefaultModel } = ctx;
  const locks = new Map(); // sessionId -> 串行化 Promise

  function withLock(key, fn) {
    const prev = locks.get(key) || Promise.resolve();
    const p = prev.then(fn, fn);
    locks.set(key, p.catch(() => {}));
    return p;
  }

  async function createAgent(sessionId) {
    await ensureSkillsLoaded();
    const selection = agentDefaultModel.currentSelection();
    const { agent } = await agents.create({
      sessionId: SessionId(sessionId),
      meta: { cwd: process.cwd() },
      agentOptions: { provider: selection.provider, model: selection.model },
      setup: (agentCtx) => {
        installModelSelection(agentCtx, { current: selection, assembled: undefined });
        // 会话级角色规则段（覆盖 role-skills 的全局兜底段）
        agentCtx.systemPrompt.section({
          name: 'bull-role',
          order: 50,
          text: () => ruleContentFor(sessionId),
        });
      },
    });
    await agent.whenIdle();
    store.agents.set(sessionId, agent);
    return agent;
  }

  async function runChat(sessionId, query) {
    let agent = store.agents.get(sessionId);
    if (!agent) agent = await createAgent(sessionId);
    const firstSeq = agent.session.seq;
    agent.followup(createUserMessage({
      content: [{ type: 'text', text: query }],
      source: { kind: 'user' },
    }));
    await agent.whenIdle();
    await sessions.flush(agent.session);
    const answer = summarize(agent.session.events, firstSeq);
    return { answer, traceId: sessionId };
  }

  async function route(seg, req, res, url) {
    // OPTIONS 预检
    if (req.method === 'OPTIONS') {
      res.writeHead(204, corsHeaders());
      return res.end();
    }

    const kind = seg[0];

    if (kind === 'session' && seg[1] === 'create' && req.method === 'POST') {
      const sessionId = `session-${randomUUID()}`;
      try { await createAgent(sessionId); }
      catch (e) { return json(res, 500, { ok: false, error: `创建会话失败：${e.message || e}` }); }
      return json(res, 200, { ok: true, sessionId });
    }

    if (kind === 'session' && seg.length === 3) {
      const sessionId = seg[1];
      const action = seg[2];

      if (action === 'snapshot' && req.method === 'POST') {
        const body = safeJson(await readBody(req));
        if (!body || typeof body !== 'object') return json(res, 400, { ok: false, error: '快照需为 JSON 对象' });
        store.snapshots.set(sessionId, body);
        return json(res, 200, { ok: true });
      }

      if (action === 'chat' && req.method === 'POST') {
        const body = safeJson(await readBody(req));
        const query = String((body && body.query) || '').trim();
        if (!query) return json(res, 400, { ok: false, error: '缺少 query' });
        try {
          const out = await withLock(sessionId, () => runChat(sessionId, query));
          return json(res, 200, { ok: true, ...out });
        } catch (e) {
          return json(res, 500, { ok: false, error: `对话失败：${e.message || e}` });
        }
      }

      if (action === 'bind-skill' && req.method === 'POST') {
        const body = safeJson(await readBody(req));
        const skillId = String((body && body.skillId) || '').trim();
        if (!skillId) return json(res, 400, { ok: false, error: '缺少 skillId' });
        if (!getSkill(skillId)) return json(res, 404, { ok: false, error: 'skill 不存在' });
        bindSkill(sessionId, skillId);
        return json(res, 200, { ok: true });
      }
    }

    if (kind === 'skill') {
      const action = seg[1];

      if (action === 'list' && req.method === 'GET') {
        return json(res, 200, { ok: true, skills: listSkills() });
      }

      if (action === 'detail' && req.method === 'GET') {
        const s = getSkill(url.searchParams.get('skillId'));
        if (!s) return json(res, 404, { ok: false, error: 'skill 不存在' });
        return json(res, 200, { ok: true, skill: s });
      }

      if (action === 'create' && req.method === 'POST') {
        const body = safeJson(await readBody(req));
        let s;
        try { s = await addSkill(body); }
        catch (e) { return json(res, e.code === 'SKILL_EXISTS' ? 409 : 400, { ok: false, error: e.message }); }
        return json(res, 200, { ok: true, skill: s });
      }

      if (action === 'update' && req.method === 'POST') {
        const body = safeJson(await readBody(req));
        const id = String((body && body.skillId) || '').trim();
        let s;
        try { s = await updateSkill(id, body); }
        catch (e) { return json(res, 400, { ok: false, error: e.message }); }
        if (!s) return json(res, 404, { ok: false, error: 'skill 不存在' });
        return json(res, 200, { ok: true, skill: s });
      }

      if (action === 'delete' && req.method === 'POST') {
        const body = safeJson(await readBody(req));
        const id = String((body && body.skillId) || '').trim();
        try { await deleteSkill(id); }
        catch (e) { return json(res, 400, { ok: false, error: e.message }); }
        return json(res, 200, { ok: true });
      }

      if (action === 'import' && req.method === 'POST') {
        const raw = await readBody(req);
        const parsed = safeJson(raw);
        const list = Array.isArray(parsed) ? parsed : (parsed ? [parsed] : []);
        if (!list.length) return json(res, 400, { ok: false, error: '未解析到有效 .skill 内容（文件应为 JSON 格式；ruleContent 若为多行文本，换行需写成 \\n，或用前端 Skill 管理面板编辑保存）' });
        const normalized = list.map(normalizeSkill);
        const ids = normalized.map(s => s.skillId);
        if (new Set(ids).size !== ids.length) return json(res, 409, { ok: false, error: '导入文件内存在重复 skillId' });
        if (normalized.some(s => getSkill(s.skillId))) return json(res, 409, { ok: false, error: '导入内容包含已存在的 skillId，请先修改 ID' });
        const invalid = normalized.map(validateSkill).find(Boolean);
        if (invalid) return json(res, 400, { ok: false, error: invalid });
        const added = [];
        try {
          for (const item of normalized) added.push(await addSkill(item));
        } catch (e) {
          return json(res, e.code === 'SKILL_EXISTS' ? 409 : 400, { ok: false, error: e.message });
        }
        return json(res, 200, { ok: true, skills: added });
      }

      if (action === 'export' && req.method === 'GET') {
        const skillId = url.searchParams.get('skillId');
        const s = getSkill(skillId);
        if (!s) return json(res, 404, { ok: false, error: 'skill 不存在' });
        const payload = JSON.stringify(s, null, 2);
        const fname = `${String(s.skillId).replace(/[^a-zA-Z0-9_-]/g, '_')}.skill`;
        res.writeHead(200, {
          'Content-Type': 'application/octet-stream',
          'Content-Disposition': `attachment; filename="${fname}"`,
          ...corsHeaders(),
        });
        return res.end(payload);
      }
    }

    return json(res, 404, { ok: false, error: '未知接口' });
  }

  // 单条 prefix 路由，内部按 pathname 分发，避免与 dsh 自带 /api 桥冲突
  webServer.register({
    kind: 'prefix',
    path: '/api/bull',
    async handler(req, res) {
      try {
        const url = new URL(req.url, 'http://localhost');
        const seg = url.pathname.split('/').filter(Boolean).slice(2); // 去掉 'api','bull'
        await route(seg, req, res, url);
      } catch (e) {
        if (!res.headersSent) json(res, 500, { ok: false, error: `内部错误：${e.message || e}` });
        else res.end();
      }
    },
  });
}
