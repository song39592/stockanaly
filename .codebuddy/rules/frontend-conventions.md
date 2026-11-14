---
description: 前端约定（原生启动器 / 网页）：控件文本不得当传参通道、隐藏≠清空、顺序纪律、静态约定自检
alwaysApply: true
enabled: true
---

# 前端约定

> 启动器交互细节（Boot 遮罩 / 失败弹窗 / 重试 / 状态灯）见 `docs/launcher-ux.md`
> —— 那是实现层要求，不常驻加载。

## 一、控件文本不是数据通道

- **跨方法传值一律走显式参数**；控件的 `Text` 只当「用户输入的内容」，**不当传参通道**。
  - 反例：`_quickSearch.Text = code;` → `SelectTab(...)` → `StockOpen()` 靠**再读一次** Text 取值
  - 正例：`StockOpen(code);` —— 参数一路显式传下去
- **为什么坏**：
  - 任何「清空 / 隐藏 / 重置」副作用都会改掉 Text，而这些副作用**藏在别处**
    （`SelectTab` 内部就调了 `HideQuickSearch`，后者会清空文本）；
  - 于是读到空串 → 代码**静默走错分支**（6 位代码退化成「指标名」→ 换指标而不是开个股），
    **不报错、只是没反应**，极难定位；
  - 触发条件往往很窄（只在控件**可见**时才会真清空）→ 「以前一直正常」，
    直到新增一条路径（比如接入联想下拉）才炸。
- **隐藏 ≠ 清空**：收起输入框不要顺手清文本；要清就放在「唤起」那一步
  （每次唤出是新输入）。
- **顺序**：先做业务（切页 / 打开对象），最后做 UI 收拾（收起输入框 / 还焦点）。
  - 焦点还原是「锦上添花」，必须 `try/catch` 包住 —— 它触发的 Enter / Leave / Validating
    一旦抛错，异常会冒泡上来把**后面的主流程一起吞掉**
    （表现：框收起了，但界面没跳）。

## 二、自检

- 改完 `launcher/*.cs` 跑：`powershell -File launcher\test-launcher-conventions.ps1`
  （静态扫 `launcher/*.cs`；退出码 0 = 通过）。

## 三、其它指针

- 目录结构 / `build_exe.bat` 清单 / 单一来源清单 → `project-structure.md`
- 发布版 exe 要入库、改后端要重启 → `git-workflow.md`
- 网页与原生窗口**无命令通道**（个股详情只有原生页）→ `project-structure.md` 第二节
