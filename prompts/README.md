# 精简提示词集（Prompt Slimming）

> 原则：**可量化判据下沉到代码，提示词只保留感知判断、状态策略与输出契约。**
> 原项目 `PA_Agent/prompt_engineering/` 保持只读；本目录是可回退的替代实现。
> 每个原章节的去向只有三种：**保留（改写）**、**迁移到代码**、**删除（说明理由）**，不允许“悄悄消失”。

## 一、体积目标

| 项 | v1 | v2 目标 |
|---|---|---|
| 阶段一 system（常驻） | 人设 10KB + 二元决策 44.8KB ≈ 55KB | `00_persona` ≈ 1KB |
| 阶段一 user | 诊断框架 89KB + 文件16 22KB + 数据 ≈ 111KB+ | `10_stage1` + C 片段 + 证据 + K10–K1 ≈ 6–8KB |
| 阶段二增量 | 策略对 26–37KB + 文件16/17/23/检查单 ≈ 35KB | 1 张策略卡 + `20_stage2` + 契约 ≈ 3–4KB |
| **灰区单次总输入** | **≈ 150–180KB** | **≤ 10KB（测试强制断言）；实测 avg 9.1KB / max 9.3KB** |
| 清晰行情 | 同样 150KB+ | **0（纯代码）** |

## 二、逐文件去向

| 原文件（体积） | 去向 | 说明 |
|---|---|---|
| 提示词大纲_人设与思维方式.txt（10KB） | `00_persona.txt`（保留改写） | 人设、三条硬禁令、概率/背景/近期思维、输出语言；删除重复的方法论叙述 |
| 二元决策.txt（44.8KB） | `10/20/30` + 代码 | §0/§2.4/§2.5/§7/§8/§9.0/§9.0P/§9.4/§9.6/§9.7 → `30_c_class_briefs`（代码同时出证据）；§3/§4/§5/§6/§9.2/9.3/9.5/§10/§11/§14 判据 → 代码；§12–13 持仓管理 → 删除（项目不持仓） |
| 市场诊断框架.txt（89KB） | `10_stage1_diagnosis.md`（保留改写） | 8 状态定义、唯一判定树、三窗口、转换规则保留；量化阈值表（与代码重复）删除；检查清单压缩为状态判别顺序 |
| 文件16-K线信号识别.txt（22KB） | 代码 + `30_c_class_briefs` | 单根类型/内包/缺口/跟随等 → `features/kline.py`；信号棒质量 → C 类说明 |
| 文件13-窄通道与宽通道策略.txt（15.6KB） | `strategies/tight_channel.md`、`broad_channel.md` | 保留通道识别与顺势边界规则，删除重复理论 |
| 文件14-楔形形态分析交易.txt（17KB） | `30_c_class_briefs` §8 + `gates/wedge.py` | 三推/收敛/斜率加速 → 代码证据；回撤 vs 反转的定性判断保留 |
| 文件15-二次入场机会.txt（14.3KB） | `30_c_class_briefs` §9.6 + 代码 | H1/H2、L1/L2 计数 → 代码；第三次尝试硬禁止；价格陷阱规则保留 |
| 文件17-止损和止盈与仓位管理.txt（4.1KB） | `20_stage2` + 代码 | 止损/目标/方程规则 → `gates/risk.py`；仓位管理删除（不给仓位建议） |
| 文件18-突破失败与突破测试.txt | 代码 + `30_c_class_briefs` | 突破事件/回收/存活 → `features/structure.py`；失败后的顺主方向原则保留 |
| 文件19-H1H2-L1L2计数.txt | 代码 | 计数算法 → `features/structure.py` |
| 文件20-AlwaysIn与20GB.txt | `30_c_class_briefs` §2.4 + 代码 | 三重判据 → `prefill/always_in.py`；20GB 规则保留在 C 类说明 |
| 文件21-铁丝网与无交易环境.txt | 代码 + 策略卡 | Barbwire 评分/阈值 → `gates/range.py`；不交易原则保留 |
| 文件22-信号失败后的磁力位.txt | `30_c_class_briefs`（并入 §9.6） | 失败后的磁力位作为 watch_points 提示 |
| 文件23-MeasuredMove与结构目标.txt（6KB） | 代码 + `20_stage2` | MM 候选/目标选择 → `features/structure.py`；目标优先级保留一句 |
| 文件24-最终旗形与趋势末端.txt | `strategies/spike.md` | 趋势末端只保留“不追、等回撤”原则 |
| 文件25-主要趋势反转MTR.txt | `30_c_class_briefs` §7 | 仅诊断、禁止逆势三价 |
| 文件27-三角形与收敛形态.txt | `gates/range.py` + `strategies/trading_range.md` | 收敛/扩张边界由代码识别并写入 `patterns`；交易仍按区间规则，扩张不交易 |
| 文件28-双重顶底与微型结构.txt | 代码 + C 类 | 微双/双顶底 → `features/kline.py`；叙事判断保留在 C 类 |
| 极速上涨/下跌 分析识别 + 交易策略（各 19KB/13KB） | `strategies/spike.md` | 合并为一张尖峰卡（SPS/旗形/禁 SCS） |
| 上涨/下跌通道 分析识别 + 交易策略（各 16KB/13KB） | `strategies/tight/normal/broad_channel.md` | 按回撤深度拆成三张卡 |
| 震荡区间 分析识别 + 交易策略（各 17KB） | `strategies/trading_range.md`、`trending_tr.md` | 普通区间与趋势型区间分开 |
| 逐棒分析检查单.txt（2.4KB） | `20_stage2_execution.md` | 压缩为“入场质量四看”与定价顺序 |

## 三、判断链路完整性清单

| 链路环节 | 保障位置 |
|---|---|
| 数据不足/看不懂 → wait | `10_stage1` §0 + `gates/base` preflight + `prefill` 置信度 |
| 周期定位（8 状态 + 唯一判定树） | `10_stage1` + `gates/spike|channel|range` |
| 方向 / Always In | `prefill/direction.py`、`prefill/always_in.py`（parity 对照 v1） |
| 策略路由（状态 → 入场方式） | `gates/order.py` + `strategies/*.md` |
| 入场质量 / 信号棒 | `gates/entry.py` §9.0 + §9.0P 双路径 + `30_c_class_briefs`（灰区时 LLM 复核） |
| 入场次数 | `gates/entry.py` §9.4/§9.6（H1/H2、L1/L2），第三次尝试硬禁止 |
| 反转 / 楔形 | `gates/reversal.py`、`gates/wedge.py` 出证据；感知层可加禁令，不改写周期 |
| 止损 / 目标 / 交易者方程 | `gates/risk.py`（纯代码，支持信号棒极点与结构失效位两种止损依据） |
| 禁止行为（逆势/追高潮/区间中部…） | `gates/guard.py` §14 最终闸门 + `20_stage2` 硬禁令 |
| 决策 trace 节点编号 | `engine/trace.py`，与 v1 `二元决策.txt` 节点号一致 |
| 增量参考 | `records/history.py` + `ai/prompt.py` 锚定块（仅参考，超预算最先裁剪） |

## 四、预算与加载规则

- 灰区调用只加载：`00_persona` + 相关策略卡 1 张（状态明确时）+ `30_c_class_briefs`
  （限长 1,600 字符，超出截断）+ `40_output_contract` + 程序证据 + K10–K1；
  `--incremental` 时额外加载「上次结论」锚定块。
- 降级顺序（`ai/prompt.py` 的 6 档尝试）：锚定块 → K 线表（10→8→6→4 根）→
  C 类速查 → 策略卡；人设、争议节点、输出契约永不裁剪。
- 任何新增提示词必须更新本表，且 `tests/unit/test_prompt_budget.py` 必须保持绿色。
