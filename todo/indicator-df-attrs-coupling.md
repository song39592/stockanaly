# 后期待办 2：消除指标函数的 `df.attrs` 隐式耦合

## 背景
`indicators/base.py` 里，指标函数的签名是 `def my_indicator(df: pd.DataFrame, **params) -> dict`，
文档也写明「指标函数只收 df」。但实际上**光有 df 并不够**：

```python
# indicators/base.py:139-144
    # 指标函数只收 df，拿不到代码；这里挂在 df.attrs 上供需要按代码再取数的
    # 指标使用（如筹码类指标要经 data.get_chip_frames(code) 拿分布矩阵）。
    df.attrs["code"] = code
    # 周期也挂上：横截面类指标（如 RPS）要按同一周期取「全市场」数据，
    # 否则周线蜡烛会配上日线的 RPS（详见 data.period_of）。
    df.attrs["period"] = period
```

于是实际依赖两个**隐藏属性**：
- **筹码类指标**（`chip_cost.py` / `chip_profit.py` / `chip_scr.py`）靠 `df.attrs["code"]` 反查代码，
  再调 `data.get_chip_frames(code)` 拿筹码分布矩阵；
- **横截面类指标**（`rps.py`）靠 `df.attrs["period"]` 按同一周期取全市场数据。

代码里已经有 `data.code_of(df)` 这个读取器（`indicators/data.py:225` 在用），
说明这个「从 df 里挖 attrs」的做法已经被当成正式约定用了 —— 但它**不是**签名的一部分。

**代价**：
1. 契约是假的 —— 单看函数签名无法得知它还需要 code/period；
2. 单元测试若直接构造 `pd.DataFrame` 调用指标函数，**拿不到 attrs 就会失败或算错**；
3. 新人照文档写指标（文档只说收 df），到运行期才发现还缺东西。

## 为什么现在不做
改动要**动全部 13 个指标的文件**（3 个筹码类 + RPS 至少 4 个真正用到 attrs），
把签名改成显式传参：

```python
def chip_profit(df, code: str = "", **params): ...
```

收益主要是「契约诚实」，**不解决任何实际痛点**（性能、正确性都没有问题）。
参照 `indicators/base.py:246-250` 的教训：项目刚删掉过一段「也许以后要用」的分位计算死代码，
它从没写进响应、还导致过一次 `NameError` 500。**过早的抽象清理属于同一类风险。**

## 什么时候做
出现下面任一情况再做：
- 有人写指标时**真的踩到**（照文档写、运行期才发现缺 code/period）；
- 要给指标写**单元测试**，发现必须伪造 `df.attrs` 才能跑；
- 要把指标计算**移到独立进程/Worker**（attrs 挂载方式需要重做）。

## 怎么做（届时的建议）
优先顺序：
1. **先加读取器、不改签名** —— 现在已有 `data.code_of(df)`，把它文档化、补 `period_of(df)`，
   并在 `base.py` 顶部文档里**明确写出「筹码类/横截面类指标需读 df.attrs」**。
   成本极低，先让契约「诚实」；
2. **再考虑显式传参** —— 把 attrs 逐步换成函数参数（带默认值，逐步迁移），
   最后统一删掉 attrs 挂载。一次只改一个指标，改完编译/测试。

## 验证
- 改动前后：13 个指标的 `/api/indicators/{id}` 输出**逐值一致**；
- 每个筹码类指标在 `code` 显式传入与走 attrs 两种路径下结果一致。
