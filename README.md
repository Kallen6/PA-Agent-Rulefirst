# re_pa_agent — PA Engine v2（轻量化决策引擎）

以 `PA_Agent` 为基础的重构项目。核心变化：**决策流水线从「LLM 为主」翻转为「代码为主、LLM 为辅」**。

- 53 个二元决策节点中，约 20 个纯机械节点由代码直接判定（0 token），约 18 个半机械节点由代码出证据、LLM 只在灰区裁决，仅约 12 个感知节点保留 LLM。
- 目标：清晰行情毫秒级出决策、零 LLM 调用；灰区行情单次精简调用（输入 <10KB）。

## 文档

全部过程文档在 `md/`：

| 文件 | 内容 |
|---|---|
| `md/计划实施.md` | **唯一实施计划入口**（阶段、任务、验收、风险） |
| `md/01_重构建议_轻量化决策引擎.md` | 架构提案与收益估算 |
| `md/02_二元决策节点代码化评估.md` | A/B/C 节点分类与替换理由 |
| `md/03_项目设计问答.md` | 五个设计问题的源码级解答 |
| `md/04_决策流程说明.md` | 端到端决策链路 |
| `md/05_Prompt瘦身对照表.md` | v1→v2 去向、体积与预算 |
| `md/06_离线验证报告.md` | 测试结果、灰区比例、验收结论 |
| `md/07_基线测量.md` | 真实行情灰区占比、调用占比、延迟、prompt 体积 |
| `md/08_带宽标定.md` | BAND 扫描表与工作点选择 |
| `md/09_回放与一致性报告.md` | 阈值对齐、v1 回放、v1/v2 对照、等价回归 |
| `md/10_审计闭环报告.md` | 灰区样本、分歧归因、季度复核 |

## 运行

```bash
python tests/run_tests.py                      # 全部测试（无 pytest 依赖）
python run.py --check                          # 列出已注册闸门
python run.py --check-config                   # 检查 config/settings.json 必填项
python run.py --live                           # 真实币安 K 线端到端决策（默认保存 v2 记录）
python run.py --live --no-save                 # 只看决策、不落盘
python run.py --live --incremental             # 读取上次同品种记录作为灰区锚点
python run.py --live --vote 3                  # 灰区自一致性投票（3 次取多数）
python run.py --regime tight_channel --setup   # 合成行情端到端决策
python run.py --regime broad_channel --llm     # 灰区 + 离线 MockLLM
python run.py --regime broad_channel --llm http  # 灰区 + 真实 API（读 config/settings.json）

python tools/measure_prompt_size.py            # prompt 体积对比
python tools/synthetic_gray_ratio.py           # 8 类行情灰区比例
python tools/baseline_measure.py               # P-1 基线（真实行情滑窗，纯代码）
python tools/calibrate_band.py                 # BAND 扫描；--write 写回 thresholds.py
python tools/quarterly_review.py               # 季度阈值漂移复核（只读）
python tools/replay_v1_records.py --self-test  # v1 回放自测
python tools/compare_v1_v2.py                  # 同快照 v1/v2 字段对照
python tools/pa_validation_regression.py       # test3/5/6 等价回归
python tools/attribute_divergence.py           # 分歧归因 → md/10
python tools/label_gray_samples.py             # 灰区样本后续走势标注
```

## 决策链路（双入场路径）

```
预填/阶段一 → §9.0 信号棒合格?
    ├─ 是 → §9.2/9.3/9.5/9.7 信号棒路径 → §9.4/9.6 → §11 → §10 → §14
    └─ 否 → §9.0P 背景驱动限价
              ├─ 通过 → 结构位限价方案 → §9.4/9.6 → §11 → §10 → §14
              └─ 不通过 → terminal=wait
```

- 两条路径都失败才终止；§14 汇总尖峰追单、区间中部、逆势、第三次尝试、
  无结构止损等禁令，任一触发即不下单；
- 清晰行情 0 次 LLM 调用；灰区单次调用（`--vote N` 时 N 次取多数），
  输入硬上限 10KB；
- 锚定（`--incremental`）只进灰区 prompt 与记录，不改变周期/方向/闸门/价格。

## 配置

配置沿用原项目格式：`config/settings.json`（JSON），模板为 `config/settings.example.json`。
本地文件已在 `.gitignore` 中排除，仓库只提交模板。

```bash
copy config\settings.example.json config\settings.json   # Windows
pip install -r requirements.txt   # 运行时无第三方依赖；开发依赖见文件内注释
```

| 段 / 键 | 作用 | 状态 |
|---|---|---|
| `_configure` | JSON 的注释位：必填项清单、字段说明、`gaps` 缺口所需配置（加载时忽略，仅作文档/自检） | 文档 |
| `provider.model` / `provider.base_url` / `provider.api_key` | 灰区感知层（OpenAI 兼容） | 已接线 |
| `provider.thinking` / `reasoning_effort` / `context_window` / `fallbacks` / `backup_glm_api_key` | 与 v1 对齐的 provider 字段 | 保留字段（v2 暂只用 model/base_url/key） |
| `provider.api_key_encrypted` | v1 遗留加密字段 | 仅占位，**v2 不解密**，鉴权只认 `api_key` |
| `engine.llm_enabled` | 是否允许真实网络调用（默认 false） | 已接线 |
| `engine.llm_timeout_seconds` / `llm_temperature` | 超时与温度 | 已接线 |
| `records.dir` | v2 记录、灰区样本（`gray_samples/`）、审计报告根目录 | 已接线 |
| `data.source` / `data.symbol` / `data.timeframe` / `data.bar_count` | 币安：数据源、交易对、K线周期、根数 | 已接线（`--live`） |
| `data.market` / `data.base_url` / `data.api_key` / `data.api_secret` | spot/futures、代理地址、私有接口鉴权 | 可选 |
| `notify.*` | PushPlus / 飞书通知 | **缺口，未接线** |

- 必填项：`data.source`、`data.symbol`、`data.timeframe`；启用 LLM 时再加 `provider.base_url` / `provider.api_key`；
- `data.base_url` 留空时按 `market` 自动选择（spot=`api.binance.com`，futures=`fapi.binance.com`）；
- 公共 K 线接口不需要 API Key，`data.api_key` / `api_secret` 仅在私有接口或代理鉴权时填写；
- 阈值/灰区带宽/prompt 预算**不在 JSON**，唯一来源是 `pa_engine/config/thresholds.py`；
- `provider.api_key` 与 v1 一样是明文落盘，靠 `.gitignore` + 文件权限保护；
  若要加密，可在后续接入 Windows DPAPI（见 `md/06` 的已知限制）；
- 未启用或调用失败时，流水线记录 `llm_error` 并保留代码决策，不中断；
- `python run.py --check-config` 会打印当前数据源、base_url、LLM 状态并校验必填项。

## v1 依赖边界

`re_pa_agent` 运行时**不依赖** `PA_Agent`：

- `v1.path` 已从 JSON 配置中移除；`adapters/v1_bridge.py` 只在测试/回放时需要 v1；
- 没有 v1 时引擎照常运行，`tests/support.py` 的 parity 测试会显式跳过；
- v1 仅用于「同一份 K 线，对比 v2 与 v1 的方向/AlwaysIn/结构字段」的只读校验，
  以及后续 T0.6/T1.5/T2.7 的记录回放比对；
- 已补齐的能力：§4.1/4.2、§6.1/6.2/6.6、§7.1–7.4、§8.1–8.3、§9.0P/9.4/9.6/9.7、
  §14 节点；v1 记录级回放与字段一致率；P-1 基线与带宽标定；灰区审计闭环；
  增量锚定；
- 仍未实现：轮询调度（`--live` 每次按 `data.bar_count` 全量抓取）、GUI、通知推送、
  P4 LoRA 蒸馏（本轮按确认跳过）；
- 每个缺口跑起来需要哪些配置键，见 `config/settings.example.json` 的 `_configure.gaps`。

## 目录

```
config/
├── settings.example.json  # 配置模板（提交）
└── settings.json          # 本地配置（gitignore，含明文 key）
prompts/                   # 精简提示词集（人设/说明/8 张策略卡/契约）
pa_engine/
├── config/
│   ├── thresholds.py      # 阈值唯一来源
│   └── settings.py        # JSON 配置加载（对齐 v1 provider 结构）
├── data/                  # KlineBar/KlineFrame 镜像 + 币安 K 线抓取
├── features/              # 指标/单根几何/结构/渲染
├── prefill/               # 方向/AlwaysIn/三窗口
├── gates/                 # 二分判定闸门（含反转/楔形/波段/守卫）
├── engine/                # 灰区检测 + 流水线 + trace + 自一致性投票
├── ai/                    # 感知层（仅灰区调用）
├── records/               # v2 记录 + 历史检索 + 灰区样本库 + 审计报告
└── adapters/              # v1 只读适配（bridge / records）
tools/                     # 基线/标定/回放/对照/回归/归因/标注
```

## 定位

仅供学习与研究，不构成投资建议；不连接券商、不执行下单。
