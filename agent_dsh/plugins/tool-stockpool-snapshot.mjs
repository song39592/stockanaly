// Tool1：getStockPoolSnapshot —— 按当前 agent 的 session 读取前端上传的股票池快照。
// 无参数：直接从 exec.agent.session 推导 sessionId，避免模型需要先知道会话 id。
import { defineTool } from '@deepseek-ai/dsh-tools';
import { store } from './bull-store.mjs';

export const name = 'bull-tool-stockpool-snapshot';
export const inject = ['tools'];

function sessionIdOf(exec) {
  const s = exec?.agent?.session;
  if (!s) return null;
  return s.id ?? s.sessionId ?? (typeof s === 'string' ? s : null);
}

export function apply(ctx) {
  ctx.tools.register(defineTool({
    name: 'getStockPoolSnapshot',
    description: '读取当前会话的股票池统计快照（今日池列表、近N天入池/出池、板块统计、连续在榜排行）。快照由前端在提问前自动上传并按会话绑定；此工具无参数，直接返回当前会话已绑定的快照 JSON。',
    parameters: {},
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(_args, exec) {
      const sid = sessionIdOf(exec);
      const snap = typeof sid === 'string' ? store.snapshots.get(sid) : null;
      if (!snap) return '当前会话尚未上传股票池快照，请先在前端页面发送一次提问（前端会自动上传快照）。';
      return JSON.stringify(snap, null, 2);
    },
  }));
}
