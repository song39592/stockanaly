# 05 · 前端：二级页（SubTab）机制 3 份 → 抽成 SubTabStrip

> 状态：待办　|　优先级：高　|　收益 ★★★★☆ / 风险 ★★★☆☆

## 问题
个股页、盘面页、策略页各写了一套「顶部一排小按钮 + 一个内容容器，点按钮切换页面」的二级页机制，
三份近乎逐字重复，还各自带一份字段组和一份换肤循环。

## 证据

| | StockPage | MarketPage | StrategyPage |
|---|---|---|---|
| 添加页 | `StockAddSubTab(string, Panel)` :582 | `MktAddSubPage(string, Action<TLP>)` :132 | `StAddSubPage(string, Action<TLP>)` :144 |
| 按钮 Tag | `"stock-subtab"` :588 | `"mkt-subtab"` :138 | `"mkt-subtab"` :150 ⚠️ **用了盘面页的 tag 名** |
| 按钮配置 | :586-597（8 行，含 Width/Height/AutoSize） | :136-147 **逐字相同** | :148-156（同，缺 3 行尺寸） |
| 选中 | `StockSubSelect(int)` :608 | `MktSubSelect(int)` :165 | `StSubSelect(int)` :170 |
| Select 主体 | :610-623 | :167-193 | :172-182 |
| 换肤 | `SkinStockSubTabs()` :626 | `SkinMktSubTabs()` :196 | `StSkinSubTabs()` :184 |
| 字段组 | `_stockSubBtns/Pages/Index/Body/Bar` | `_mktSub*` | `_stSub*` |

`Select` 主体是**3 份逐字相同的 7 步**（连注释都一样）：
越界判断 → 记 index → `Controls.Clear()` → `page.Visible=true` → `Dock=Fill` → `Add` → `Skin(page)` → `Invalidate(true)` → 刷新按钮配色。
换肤循环体也是 3 份逐字相同（`sel ? _cPanel : _cBg` / `sel ? _cText : _cSub` / `Invalidate()`）。

## 建议做法
抽一个小控件 `SubTabStrip`（持有 btns / pages / index / bar / body）：
- `Add(string title, Panel page)` —— 收已建好的 Panel（个股页形态）
- `Add(string title, Action<TableLayoutPanel> build)` —— 收 build 委托（盘面/策略形态）
  → **两个重载都留，不要强行统一入参形态**
- `Select(int idx)` + `Action<int> onSelected` 钩子 —— 承载两处特例

## 注意（踩坑点）
1. **两处特例懒加载必须保住**：
   - 盘面页 AI 子页：首次切到才 `MktLoadMktAi(false)`（MarketPage.cs:186-192，注释明确写了「避免启动瞬间抢跑」）
   - 个股页 K 线子页：index==0 时 `_stockKline.FocusForKeys()`（StockPage.cs:621-622）
   `onSelected` 钩子设计不到位会让 AI 页在启动时就开始生成。
2. 个股页的 7 个子页是**外部先建好再传入**（StockPage.cs:361,412,436,464,481,507,551），不要改成委托式，否则要动 7 个调用点。
3. 按钮 `Tag` 值不同（`"stock-subtab"` vs `"mkt-subtab"`）；`Skin()` 里目前没按这两个 tag 分支，改名风险低，但仍需确认。
4. ⭐ **现成 bug（合并时顺手修）**：`StockPoolLauncher.cs:895-896` 的换肤挂钩**只挂了 Mkt 和 Stock，漏了 St** ——
   策略页的二级页按钮换肤后不会重新上色。这是行为变化，需要留意。

## 验收
- [ ] 三份 Add/Select/Skin 合并为 1 套
- [ ] 个股页 7 个子页切换正常，切到 K 线页仍自动获得键盘焦点
- [ ] 盘面页切到 AI 子页才触发生成（启动时**不**生成）
- [ ] 浅色↔深色切换后，三个页的二级页按钮配色都跟随（含策略页，即修了漏挂 bug）
