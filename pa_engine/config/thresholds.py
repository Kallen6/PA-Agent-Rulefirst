"""阈值单一来源（Single Source of Truth）。

规则：
1. 全项目所有决策阈值只允许在此定义一次；
2. 「已代码化」组与 v1 常量一致（pa_agent/ai/kline_features.py、market_features.py、decision_nodes.py）；
3. 「待代码化」组来自 prompt_engineering/二元决策.txt 的量化条款，注释标注节点号；
4. 修改任何数值必须同步 md/02_二元决策节点代码化评估.md 与对应测试。
"""
from __future__ import annotations

from types import MappingProxyType


class Bar:
    """单根 K 线几何判定（对应 v1 kline_features.py）。"""

    DOJI_BODY_RATIO_MAX = 0.25          # 实体比 ≤0.25 → doji
    TREND_BULL_CLOSE_POS_MIN = 0.65     # 阳线收盘位置 ≥0.65 → trend_bull
    TREND_BEAR_CLOSE_POS_MAX = 0.35     # 阴线收盘位置 ≤0.35 → trend_bear
    MICRO_DOUBLE_ATR_TOL = 0.02         # 微双顶/底容差 = 0.02 × ATR
    BREAKOUT_LOOKBACK = 5               # 近 N 根突破判定
    VOLUME_LOOKBACK = 20                # 量比窗口
    VOLUME_HIGH_RATIO = 1.5             # ≥1.5× 均量 → 放量
    VOLUME_LOW_RATIO = 0.7              # ≤0.7× 均量 → 缩量


class Structure:
    """结构级判定（对应 v1 market_features.py）。"""

    LOOKBACK = 40                       # 默认回看根数
    OVERLAP_WINDOW = 10                 # 铁丝网平均重叠窗口
    BARBWIRE_OVERLAP_MIN = 0.65         # §6.4 Barbwire：平均重叠 ≥0.65
    BARBWIRE_WIDTH_ATR_MAX = 3.0        # 区间宽度 ≤3.0 ATR
    BARBWIRE_SCORE_MIN = 0.6            # 综合评分 ≥0.6
    BREAKOUT_RECLAIM_BARS = 5           # §6.5 突破后 N 根内收回 → failed
    BREAKOUT_TEST_TOLERANCE_ATR = 0.15  # 回踩容差
    SURVIVING_MIN_BARS = 5              # 突破存活最少根数
    SPIKE_LOOKBACK = 16                 # 尖峰回看窗口
    SPIKE_MIN_RUN = 3                   # 尖峰最少连续根数
    SWING_MIN_COUNT = 4                 # §4.1 有序波段的最少枢轴数


class Windows:
    """多尺度窗口（对应 v1 decision_nodes.py）。"""

    DIRECTION = 8                       # §2.3 方向投票短窗
    DIRECTION_MED = 20                  # 方向背景参考窗
    ALWAYS_IN_NEAR = 8                  # §2.4 近端主判
    ALWAYS_IN = 20                      # §2.4 背景参考
    MOMENTUM_NEAR = 8


class Prefill:
    """方向 / AlwaysIn 预填（对应 v1 decision_nodes.py 常量）。"""

    BAR_COUNT_MIN = 20
    DIRECTION_WINDOW = 8
    DIRECTION_WINDOW_MED = 20
    DIRECTION_STRONG_SHORT_SCORE = 4
    DIRECTION_BULL_THRESHOLD = 3
    DIRECTION_BEAR_THRESHOLD = -3
    DIRECTION_EMA_SLOPE_ATR = 0.05
    DIRECTION_COG_ATR = 0.10
    TREND_BAR_DOMINANCE_RATIO = 1.5
    OVERLAP_LOW = 0.45
    OVERLAP_HIGH = 0.65
    EMA_SLOPE_LOOKBACK = 10
    ALWAYS_IN_NEAR_WINDOW = 8
    ALWAYS_IN_WINDOW = 20
    ALWAYS_IN_NEAR_SAME_SIDE = 0.65
    ALWAYS_IN_SAME_SIDE = 0.70
    ALWAYS_IN_PULLBACK_ATR = 1.5
    TWENTY_GB_MIN_BARS = 20              # 20GB：连续 ≥20 根整根未触 EMA（v1 文件20）
    SIGNAL_BAR_LONG_ATR = 2.0


class Spike:
    """§3 尖峰 / 高潮（来自二元决策.txt §3.1、§3.6）。"""

    CANDIDATE_MAX_RUN = 1               # 1 根 → 仅 spike_candidate，不出 spike
    ROUTABLE_MIN_RUN = 2                # 2 根起可路由
    OVERLAP_MAX_CANDIDATE = 0.30        # 2 根：实体重叠 <30%
    OVERLAP_MAX_STANDARD = 0.20         # 3–5 根：实体重叠 <20%
    STANDARD_MAX_RUN = 5
    CLIMAX_MIN_RUN = 6                  # 6 根起高潮预警
    CLIMAX_WICK_TO_BODY_MIN = 0.50      # 衰竭：尾线 > 实体 50%
    CLIMAX_BODY_TO_MEAN_MAX = 0.30      # 衰竭：实体 < 近期均值 30%
    SPIKE_DISPLACEMENT_RANGE_MULT = 1.0  # 尖峰高度须 ≥1.0× 先前盘整宽度（区别于回撤）
    FIRST_PAUSE_MIN_BARS = 2            # §3.4 首次暂停/回撤窗口下限（SPS 前提）
    FIRST_PAUSE_MAX_BARS = 4            # §3.4 首次暂停/回撤窗口上限


class Channel:
    """§5 通道宽度四级（二元决策.txt §5.1–5.4，prompt 自注「量化」）。"""

    MICRO_PULLBACK_MAX = 0.25           # 微型通道：几乎无回撤（≤25%）
    TIGHT_PULLBACK_MAX = 0.30           # 紧凑通道：回撤 <30%
    TIGHT_OVERLAP_MAX = 0.20            # 平均重叠 <20%
    TIGHT_TREND_BAR_RATIO_MIN = 0.65    # 趋势棒比例 >65%
    NORMAL_PULLBACK_MIN = 0.30          # 常规通道：30%–50%
    NORMAL_PULLBACK_MAX = 0.50
    BROAD_PULLBACK_MIN = 0.50           # 宽通道：50%–78.6%
    BROAD_PULLBACK_MAX = 0.786
    BROAD_OVERLAP_MIN = 0.40            # 重叠 >40%
    BROAD_LEG_BARS_MIN = 10             # 每波段 >10 根
    BROAD_MIN_HHHL_GROUPS = 3           # ≥3 组 HH+HL → broad_channel
    TRENDING_TR_MAX_HHHL_GROUPS = 2     # ≤2 组 → trending_tr


class Entry:
    """§9 入场信号（二元决策.txt §9.2/9.3/9.5 + §9.0 三分诊）。"""

    SIGNAL_BAR_LONG_ATR = 1.5           # 信号棒振幅 ≥1.5×ATR → 过长
    SIGNAL_BAR_BODY_MIN = 0.50          # 实体占全幅 ≥50%
    SIGNAL_BAR_CLOSE_POS_BULL = 0.65    # 阳线收盘位置 ≥0.65
    SIGNAL_BAR_CLOSE_POS_BEAR = 0.35    # 阴线收盘位置 ≤0.35
    QUALITY_PASS_SCORE = 3              # 4 项中 ≥3 → 合格
    QUALITY_FAIL_SCORE = 1              # ≤1 → 不合格；=2 → 灰区
    # §9.0P 背景驱动限价：结构位/EMA/边界距现价的区间
    BACKGROUND_LIMIT_MIN_ATR = 0.05
    BACKGROUND_LIMIT_MAX_ATR = 1.0
    # §9.4/§9.6 入场次数（H1/H2、L1/L2 计数）
    FIRST_ENTRY_MAX_HL = 1              # 0–1 次顺势尝试 → 第一次入场
    SECOND_ENTRY_MIN_HL = 2             # ≥2 次 → 二次入场
    THIRD_ENTRY_MIN_HL = 3              # ≥3 次 → 禁止第三次尝试
    # §9.6 二次入场价格明显优于第一次 → 陷阱警告（×ATR）
    SECOND_ENTRY_TRAP_ATR = 0.20
    # §9.7 入场棒强度（信号棒路径的软门槛）
    ENTRY_BAR_BODY_MIN = 0.45
    ENTRY_BAR_CLOSE_POS_BULL = 0.60
    ENTRY_BAR_CLOSE_POS_BEAR = 0.40


class Reversal:
    """§7 反转识别（B 类：代码出证据，仅诊断不下单）。"""

    LOOKBACK = 40
    SINGLE_BAR_WINDOW = 3               # §7.1 观察最近 3 根中的反转棒数量
    REVERSAL_BODY_MIN = 0.45            # 反转棒实体占全幅下限
    REVERSAL_CLOSE_POS = 0.35           # 反转棒收盘须落在反向极点 35% 内
    FAILED_TEST_TOL_ATR = 0.10          # 前极点测试失败的容差（×ATR）
    TRENDLINE_MIN_SWINGS = 2            # 趋势线至少需要 2 个同类摆动点
    SECOND_ENTRY_MIN_HL = 2             # §7.3 二次入场的最小顺势尝试次数


class Wedge:
    """§8 楔形 / 三推（B 类：结构证据 + 禁令）。"""

    LOOKBACK = 40
    PUSH_COUNT = 3                      # 三推需要 3 个同向推进
    # 注：decay 判据为「逐推幅度不增」（wedge.py 实现），不设系数阈值
    CONTRACTION_RATIO = 0.75            # 末段振幅 / 首段振幅 ≤0.75 → 收敛
    PARABOLIC_SLOPE_RATIO = 1.60        # 末推斜率 ≥ 首推 ×1.6 → 抛物线


class Triangle:
    """§6.6 三角形（仅证据，不改变周期分类）。"""

    LOOKBACK = 40
    MIN_SWINGS = 4                      # 至少 2 高 + 2 低
    CONTRACTION_RATIO = 0.75            # 末段区间宽度 / 首段 ≤0.75 → 收敛
    FLAT_SIDE_ATR = 0.25                # 一侧边界走平容差（×ATR）


class Range:
    """§6.1/§6.2 区间边界与类型。"""

    BOUNDARY_TOUCH_MIN = 2              # 上下边界各至少触碰 2 次
    BOUNDARY_TOUCH_TOL_ATR = 0.25       # 触碰容差（×ATR）
    CLEAR_WIDTH_MIN_ATR = 1.0           # 边界清晰的区间宽度下限（太窄属铁丝网）
    CLEAR_WIDTH_MAX_ATR = 6.0           # 边界清晰的区间宽度上限
    TRENDING_GROUPS_MIN = 2             # ≥2 组 HH+HL/LL+LH → 趋势型区间


class Guard:
    """§14 禁止行为守卫。"""

    STRUCTURE_FLIP_COOLDOWN_BARS = 0    # 结构翻转冷却（v1 默认 0；>0 且 --incremental 锚定时生效）


class Risk:
    """§10 风险收益（二元决策.txt §10.1–10.3）。"""

    STOP_TICKS_OUTSIDE = 1              # 止损 = 信号棒极点外 1 跳
    STOP_FILTER_CHANNEL_WIDTH = 0.40    # > 通道宽度 40% → 放弃
    STOP_FILTER_RANGE_WIDTH = 0.10      # > 区间宽度 10% → 放弃
    STOP_FILTER_ATR_MULT = 2.0          # > 2×ATR → 放弃
    MIN_RR = 1.0                        # 兜底下限：未注入档位时使用
    MAX_TP1_RISK_REWARD_RATIO = 1.0     # 兜底外扩目标：未注入档位时使用
    WIDEN_STOP_MAX_SPIKE_FRACTION = 0.5  # 外扩止损上限 = 尖峰高度 50%
    # 纯代码路径的保守胜率先验（C 类判断的缺省值；灰区由 LLM 覆盖）
    WIN_RATE_PRIOR: dict[str, float] = {
        "spike": 0.60,
        "micro_channel": 0.60,
        "tight_channel": 0.58,
        "normal_channel": 0.55,
        "broad_channel": 0.52,
        "trending_tr": 0.55,
        "trading_range": 0.52,
        "extreme_tr": 0.50,
        "unknown": 0.50,
    }


class Stance:
    """下单倾向档位（v2 新增：把 v1 的 prompt 文案档位改为阈值向量）。

    v1 的四档写在 Stage-2 提示词里（40–60 行文案），数值阈值一律不变；
    v2 全部代码化，档位不再向 prompt 注入任何文本：

    - ``min_rr``：§10.2 / §10.3 的最低盈亏比；
    - ``min_equation_gap``：§10.3 交易者方程的相对余量下限，
      定义 ``(胜率×回报 − 败率×风险) / (败率×风险)``，负数表示允许轻微负期望；
    - ``target_rr``：``prepare_plan()`` 外扩止损的目标 RR；
    - ``min_win_rate``：§10.3 的独立胜率下限（百分数）。与方程不重复：
      方程只要求期望为正（低胜率高赔付可通过），该下限控制
      「接受多低胜率的赌单」，是 v1 trade_confidence 门槛的代码化；
    - ``signal_pass_score`` / ``signal_fail_score``：§9.0 信号棒打分的
      合格/不合格门槛（0–4 分）。激进档把 borderline 判为合格，
      相当于 v1 的「接受更早、更不完美的入场触发」；
    - ``bg_limit_min_mult`` / ``bg_limit_max_mult``：§9.0P 背景限价的
      距离区间乘数（×``Entry.BACKGROUND_LIMIT_MIN/MAX_ATR``）。
      保守档收窄（结构位须更标准），激进档放宽（接受更远/更近的结构位）；
    - ``fallback_to_bg_limit``：§9.0 信号路径被 §9.2/§9.5 等阻断时，
      是否回退评估 §9.0P 背景限价（v1 激进条款「突破单不可行时尝试限价单」
      的最小代码化）。保守/均衡不回退，保持原有路径顺序语义。

    为什么 ``target_rr`` 必须与 ``min_rr`` 成对：外扩止损会把 RR 压到目标值，
    若目标仍固定为 1.0，保守档的 ``min_rr=1.5`` 会让所有方案被 §10.2 判死。
    取 ``min_rr + 0.05`` 是为了留出浮点余量。

    档位只改执行门槛，不改变周期、方向、Always In 的判定与下单方式路由表
    （§9 路径选择与 §9.0P 硬禁令仍由结构决定，档位只放宽打分与距离边界）。
    """

    DEFAULT = "balanced"
    ORDER: tuple[str, ...] = (
        "conservative",
        "balanced",
        "aggressive",
        "extreme_aggressive",
    )
    LABELS_ZH = MappingProxyType(
        {
            "conservative": "保守",
            "balanced": "均衡",
            "aggressive": "激进",
            "extreme_aggressive": "极度激进",
        }
    )
    PARAMS = MappingProxyType(
        {
            "conservative": MappingProxyType(
                {
                    "min_rr": 1.50,
                    "min_equation_gap": 0.15,
                    "target_rr": 1.55,
                    "min_win_rate": 55.0,
                    "signal_pass_score": 4,
                    "signal_fail_score": 2,
                    "bg_limit_min_mult": 1.25,
                    "bg_limit_max_mult": 0.75,
                    "fallback_to_bg_limit": False,
                }
            ),
            "balanced": MappingProxyType(
                {
                    "min_rr": 1.20,
                    "min_equation_gap": 0.05,
                    "target_rr": 1.25,
                    "min_win_rate": 50.0,
                    "signal_pass_score": 3,
                    "signal_fail_score": 1,
                    "bg_limit_min_mult": 1.0,
                    "bg_limit_max_mult": 1.0,
                    "fallback_to_bg_limit": False,
                }
            ),
            "aggressive": MappingProxyType(
                {
                    "min_rr": 1.00,
                    "min_equation_gap": 0.00,
                    "target_rr": 1.05,
                    "min_win_rate": 40.0,
                    "signal_pass_score": 2,
                    "signal_fail_score": 1,
                    "bg_limit_min_mult": 0.75,
                    "bg_limit_max_mult": 1.25,
                    "fallback_to_bg_limit": True,
                }
            ),
            "extreme_aggressive": MappingProxyType(
                {
                    "min_rr": 1.00,
                    "min_equation_gap": -0.05,
                    "target_rr": 1.05,
                    "min_win_rate": 30.0,
                    "signal_pass_score": 2,
                    "signal_fail_score": 0,
                    "bg_limit_min_mult": 0.50,
                    "bg_limit_max_mult": 1.50,
                    "fallback_to_bg_limit": True,
                }
            ),
        }
    )


class GrayZone:
    """灰区检测参数（v2 新增，P1 用历史样本标定）。"""

    BAND = 0.05                         # 测量值距阈值 ≤5% 相对带宽 → 灰区（T1.7 真实行情标定）
    MIN_CONFIDENCE = 0.80               # 节点置信度低于此值 → 灰区
    CRITICAL_NODES = (                  # 缺一即需 LLM 的感知关键节点
        "2.4",                          # Always In
        "3.1",                          # 尖峰分级
        "3.6",                          # 高潮判定
        "5.2",                          # 紧凑通道
        "5.4",                          # 宽通道 / 台阶
        "6.5",                          # 区间突破
        "9.0",                          # 信号棒质量
    )
    MAX_PROMPT_BYTES = 10 * 1024        # 灰区单次输入硬上限
    CHARS_PER_TOKEN = 1.6               # 中文场景的保守估算
