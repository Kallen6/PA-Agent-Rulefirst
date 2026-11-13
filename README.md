# PA Engine — 价格行为决策引擎

面向加密货币行情（币安 K 线）的价格行为（Price Action）决策引擎。核心思路：**确定性代码优先，LLM 只处理灰区**。

把一整套交易决策拆成 53 个可二分判定的节点（§1–§14），由纯代码给出答案、证据与置信度；只有少数难以量化的感知类问题交给 LLM，且仅在灰区触发。清晰行情毫秒级出决策、零 token；灰区行情单次精简调用（输入上限 10KB）。

## 功能

- **端到端决策**：输入 K 线，输出周期定位、方向、在场方向（AlwaysIn）、诊断置信度、入场价/止损/TP1/TP2、委托方式与完整决策轨迹；
- **双入场路径**：信号棒合格走信号棒路径（突破单/限价单），不合格走背景驱动限价（结构位方案），两条路径都失败才终止；
- **灰区机制**：置信度带宽检测触发感知层，支持自一致性投票（N 次取多数）与增量锚定（读上次记录作参考，不改判代码结论）；
- **下单倾向档位**：保守/均衡/激进/极度激进四档，以代码阈值落地（最低 RR、方程余量、胜率下限、
  信号棒门槛、§9.0P 距离区间、回退限价），不写提示词、不改周期与方向；
- **全链路可追溯**：每个闸门节点记录答案/分支/置信度/证据/来源（代码或模型），可导出为单文件 HTML 报告（内嵌 K 线图，离线可看）；
- **Web 决策台**：本地控制台（合成/实时行情、交易对预设下拉、档位与灰区参数实时调节、决策报告即时渲染）；
- **度量与标定**：合成/真实行情灰区比例、prompt 体积、基线测量、灰区带宽标定、灰区样本标注与审计闭环；
- **零第三方依赖**：只用 Python 3.11+ 标准库（dataclasses/json/pathlib/re/argparse/math/urllib），不装任何包即可运行。

## 运行

### 环境与配置

```bash
copy config\settings.example.json config\settings.json   # Windows；Linux/Mac 用 cp
pip install -r requirements.txt   # 运行时无第三方依赖，此步可跳过；开发依赖见文件内注释
```

必填项只有三个：`data.source`、`data.symbol`、`data.timeframe`。启用 LLM 时再加 `provider.base_url` / `provider.api_key`。公共 K 线接口不需要 API Key。

### 日常命令

```bash
python run.py --check                          # 列出已注册闸门
python run.py --check-config                   # 校验配置必填项并打印数据源/LLM 状态
python run.py --live                           # 真实币安 K 线端到端决策（默认保存记录）
python run.py --live --no-save                 # 只看决策、不落盘
python run.py --live --incremental             # 读取上次同品种记录作为灰区锚点
python run.py --live --vote 3                  # 灰区自一致性投票（3 次取多数）
python run.py --live --stance conservative      # 指定下单倾向档位（默认读 settings.json）
python run.py --regime tight_channel --setup   # 合成行情端到端决策
python run.py --regime broad_channel --llm     # 灰区 + 离线 MockLLM
python run.py --regime broad_channel --llm http  # 灰区 + 真实 API（读 config/settings.json）
```

### Web 决策台

```bash
python run.py --web            # 启动本地控制台并打开浏览器（默认 127.0.0.1:8765）
python run.py --web --live     # 启动服务并预填真实行情参数
python run.py --web --port 9000  # 自定义端口
```

侧栏可切换合成/实时行情、选择交易对（内置 17 个币安 USDT 高流动性预设，
按 24h 成交额选取，支持手输任意交易对）、周期与 K 线根数、感知层 LLM、
下单倾向档位、灰区带宽与自一致性投票；选中即存 `localStorage`，刷新保持。
报告区渲染摘要卡（周期/方向/档位/交易信心）、K 线图与完整闸门追溯表。
默认不写任何文件，勾选「保存记录」后本次决策落盘 `records/`。

### 下单倾向档位

v1 的四档写在 Stage-2 提示词里，数值阈值不变；v2 全部代码化为阈值向量，
周期、方向、Always In 的判定与下单方式路由表完全不受影响。

| 档位 | 配置值 | 最低 RR | 方程余量 | 胜率下限 | 信号棒门槛 | §9.0P 距离 | 回退限价 |
|---|---|---|---|---|---|---|---|
| 保守 | `conservative` | 1.50 | +15% | 55% | ≥4/4 | ×1.25 / ×0.75 | 否 |
| 均衡（默认） | `balanced` | 1.20 | +5% | 50% | ≥3/4 | ×1.0 / ×1.0 | 否 |
| 激进 | `aggressive` | 1.00 | 0% | 40% | ≥2/4 | ×0.75 / ×1.25 | 是 |
| 极度激进 | `extreme_aggressive` | 1.00 | −5% | 30% | ≥2/4（1分入灰区） | ×0.5 / ×1.5 | 是 |

要点：

- `target_rr`（止损外扩目标）必须略高于 `min_rr`，否则外扩会把 RR 压回目标值后被自己的门槛判死；
- 胜率下限是方程之外的独立闸门（§10.3 `low_win_rate` 分支）：方程只要求期望为正，
  该下限控制「接受多低胜率的赌单」，最终生效胜率会以 `trade_confidence` 落入记录与报告；
- 信号棒门槛：激进档把 §9.0 的 2 分边缘信号判为合格（borderline→pass），
  极度激进把 1 分从不合格降级为灰区；
- 回退限价（激进档起）：信号路径被 §9.2/§9.5 否决且信号棒分数 <3 时，自动回退评估 §9.0P
  背景限价（v1「突破单不可行时尝试限价单」条款的最小代码化），两路皆失败才终止；
- 极度激进允许轻微负期望，但仍受 §10.2 止损过滤与 §14 硬禁令约束，不会强制产出订单。

配置入口：`config/settings.json` 的 `engine.decision_stance`；临时覆盖用 `--stance`，
Web 决策台在「引擎参数 → 下单倾向」中可实时切换。

### 测试

```bash
python tests/run_tests.py        # 全部单测（零依赖，自带 runner）
python tests/run_tests.py -k parity   # 按名称过滤
pytest                           # 已安装 pytest 的环境也可直接跑
```

### 度量与审计工具

```bash
python tools/measure_prompt_size.py      # prompt 体积对比
python tools/synthetic_gray_ratio.py     # 8 类合成行情灰区比例
python tools/baseline_measure.py         # 真实行情滑窗基线（纯代码，含 K 线缓存）
python tools/calibrate_band.py           # 灰区带宽扫描；--write 写回 thresholds.py
python tools/quarterly_review.py         # 季度阈值漂移复核（只读）
python tools/label_gray_samples.py       # 灰区样本后续走势标注
python tools/attribute_divergence.py     # 分歧归因
```

### HTML 报告

```bash
python tools/export_html_report.py --regime tight_channel --setup   # 合成行情（含 K 线图）
python tools/export_html_report.py --live --timeframe 15m --open    # 真实行情 + 自动打开
python tools/export_html_report.py --live --offline --timeframe 15m # 读基线缓存，不联网
python tools/export_html_report.py --record records/.../xx.json     # 已存记录（无 K 线，图表降级）
# api.binance.com 超时/被墙时换备用域名或走代理：
python tools/export_html_report.py --live --base-url https://data-api.binance.vision
python tools/export_html_report.py --live --proxy http://127.0.0.1:7890
```

报告为单文件 HTML（内联 CSS + SVG K 线），标注入场/止损/TP 线与信号棒，含卡片区、结论与形态、阶段一/二闸门追溯表、灰区与感知层详情，可直接双击离线打开。

## 架构

### 决策链路

```
K 线输入（币安 / 合成 / 历史缓存）
        │
        ▼
特征计算 ──► 预填先验（方向 / AlwaysIn / 三窗口）
        │
        ▼
阶段一：结构与状态（§1–§6）          ← 闸门逐节点判定，产出周期定位
        │
        ├── 置信度落入灰区带宽？ ──► 感知层 LLM（单次，≤10KB，--vote N 取多数）
        │
        ▼
阶段二：入场、风险、下单（§9–§11）
    §9.0 信号棒合格?
        ├─ 是 → 信号棒路径（§9.2/9.3/9.5/9.7 → §9.4/9.6 → §11 → §10）
        └─ 否 → §9.0P 背景驱动限价 → 结构位限价方案（§9.4/9.6 → §11 → §10）
        │
        ▼
§14 终审守卫（尖峰追单、区间中部、逆势、第三次尝试、无结构止损等硬禁令）
        │
        ▼
终态（trade / reject / wait）+ 决策记录落盘 + HTML 报告
```

要点：

- 两条入场路径都失败才终止；§14 任一禁令触发即不下单；
- 感知层只出证据与胜率覆写，**不推翻**代码得出的周期、方向与闸门结论；
- 增量锚定（`--incremental`）只进灰区 prompt 与记录，不改变判定；
- 下单倾向档位只改执行门槛（§9 入场容忍度 + §10 RR / 方程 / 胜率），在任何闸门之前注入 `GateContext`，不改变阶段一的周期、方向与 Always In 结论；
- 阈值、档位参数、灰区带宽、prompt 预算的唯一来源是 `pa_engine/config/thresholds.py`，不进 JSON。

### 目录结构

```
config/
├── settings.example.json  # 配置模板（提交仓库）
└── settings.json          # 本地配置（gitignore，含明文 key）
prompts/                   # 精简提示词集（人设/说明/策略卡/输出契约）
pa_engine/
├── main.py                # CLI 入口（--check/--live/--regime/--demo）
├── config/
│   ├── thresholds.py      # 阈值唯一来源（含灰区带宽 BAND）
│   └── settings.py        # config/settings.json 加载
├── data/                  # KlineBar/KlineFrame 数据结构 + 币安 K 线抓取
├── features/              # 指标/单根K线几何/波段结构/区间与突破事件
├── prefill/               # 方向 / AlwaysIn / 三窗口先验
├── gates/                 # §1–§14 二元判定闸门（结构/尖峰/反转/楔形/区间/入场/风险/下单/守卫）
├── engine/                # 流水线 pipeline.decide、下单倾向档位 stance、灰区检测、trace、自一致性投票
├── ai/                    # 感知层客户端（HTTP/Mock）、prompt 组装、输出契约校验
├── records/               # 决策记录落盘、历史检索与增量锚定、灰区样本库、审计
└── adapters/              # 历史记录适配（测试与回放用）
tools/                     # 基线/标定/度量/审计/HTML 报告等工具脚本
tests/                     # 单元测试（run_tests.py 零依赖 runner + conftest.py）
records/                   # 运行产物（自动创建，gitignore）
```

## 配置速查

| 段 / 键 | 作用 |
|---|---|
| `data.source` / `data.symbol` / `data.timeframe` / `data.bar_count` | 数据源、交易对、K 线周期、根数 |
| `data.market` / `data.base_url` | spot/futures（决定默认域名）、代理或镜像地址 |
| `data.api_key` / `data.api_secret` | 仅私有接口需要，公共 K 线留空 |
| `engine.llm_enabled` | 是否允许真实网络调用（默认 false，纯代码决策） |
| `engine.llm_timeout_seconds` / `llm_temperature` | 感知层超时与温度 |
| `engine.decision_stance` | 下单倾向档位（默认 `balanced`；CLI `--stance` / Web 可临时覆盖） |
| `provider.model` / `provider.base_url` / `provider.api_key` | 感知层（OpenAI 兼容接口） |
| `records.dir` | 记录、灰区样本、审计报告根目录（默认 `records`） |
| `notify.*` | PushPlus / 飞书通知（未接线） |

其他说明：

- `data.base_url` 留空时按 `market` 自动选择（spot=`api.binance.com`，futures=`fapi.binance.com`）；
- 未启用 LLM 或调用失败时，流水线记录 `llm_error` 并保留代码决策，不中断；
- `provider.api_key` 为明文落盘，靠 `.gitignore` 保护，仓库只提交模板文件。

## 定位

仅供学习与研究，不构成投资建议；不连接券商、不执行下单。
