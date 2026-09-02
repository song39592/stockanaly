// Skill 角色规则引擎：加载/播种 skill 文件，注入每会话「角色规则」系统提示段。
// 基础「红色小牛」系统提示放在 cordis.patch.yml 的 persona 覆盖里（配置非代码）。
import { ensureSkillsLoaded, ruleContentFor } from './bull-store.mjs';

export const name = 'bull-role-skills';
export const inject = ['systemPrompt'];

export function apply(ctx) {
  // 加载 bull/skills/*.skill 到内存；空则播种默认「通用分析」
  ensureSkillsLoaded().catch(err => console.error(`[bull] Skill 加载失败：${err.message}`));

  // 全局兜底角色规则段（order 50）；bull-http 会为每个 agent 注册同名的会话级段以覆盖它
  ctx.systemPrompt.section({
    name: 'bull-role',
    order: 50,
    text: () => ruleContentFor(null),
  });
}
