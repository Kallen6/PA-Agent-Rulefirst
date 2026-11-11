# 输出契约
仅输出一个 JSON 对象，不要任何前后缀：
{
  "cycle_position": "spike|micro_channel|tight_channel|normal_channel|broad_channel|trending_tr|trading_range|extreme_tr|unknown",
  "direction": "bullish|bearish|neutral",
  "always_in": "bullish|bearish|none",
  "signal_bar_quality": "pass|fail|weak",
  "confidence": 0,
  "qualitative_notes": "≤80字",
  "estimated_win_rate": 0,
  "win_rate_reason": "≤40字，可省略",
  "reversal_attempt": false,
  "wedge_type": "none|pullback|reversal|parabolic",
  "entry_quality": "strong|weak|invalid|unknown",
  "second_entry": "first|second|third|unknown",
  "market_legibility": "clear|confused|unknown"
}
规则：不确定用 unknown/weak 并降低 confidence；不要输出价格、止损、目标、仓位、gate_trace（均由代码生成）。
estimated_win_rate 只在能给出判断依据时填写（0–100），代码会用它重算 §10.3；方程不通过则撤销方案。
reversal_attempt / wedge_type 只加禁令，不推翻代码周期与方向。
market_legibility 判断「是否看得懂当前市场」（v1 §0.1）：结构混乱、节点互相矛盾、无法归入任一状态时填 confused，代码会撤销方案等待；clear 表示可读。
