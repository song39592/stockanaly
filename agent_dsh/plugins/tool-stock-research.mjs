// Tool2：fetchStockResearch —— 回连本地 FastAPI 个股调研接口，拉取 K线元信息 + 公告/新闻/调研 markdown。
import { defineTool } from '@deepseek-ai/dsh-tools';

export const name = 'bull-tool-stock-research';
export const inject = ['tools'];

const FASTAPI = 'http://127.0.0.1:8000';

export function apply(ctx) {
  ctx.tools.register(defineTool({
    name: 'fetchStockResearch',
    description: '调用本地 FastAPI 调研服务，获取某只股票的 K线元信息 + 公告/新闻/机构调研 markdown 调研报告。code 为 6 位股票代码，name 为股票名称（可空）。',
    parameters: {
      code: { type: 'string', required: true, description: '6 位股票代码，如 300209' },
      name: { type: 'string', description: '股票名称，可选' },
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    timeoutMs: 200000,
    async execute(args, exec) {
      const controller = new AbortController();
      const onAbort = () => controller.abort();
      exec?.signal?.addEventListener('abort', onAbort, { once: true });
      try {
        const res = await fetch(`${FASTAPI}/api/stock/research`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: args.code, name: args.name || '' }),
          signal: controller.signal,
        });
        const j = await res.json();
        if (j && j.ok && j.markdown) return j.markdown;
        return `个股调研接口返回失败：${(j && j.error) || ('HTTP ' + res.status)}`;
      } catch (e) {
        return `个股调研接口调用失败（请确认 FastAPI 后端已在 ${FASTAPI} 运行）：${e.message}`;
      } finally {
        exec?.signal?.removeEventListener('abort', onAbort);
      }
    },
  }));
}
