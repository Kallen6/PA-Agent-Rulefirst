# 阶段一：周期定位（只诊断，不下单）

## 三窗口（同一份K线，硬切分）
- 长程背景 K{n}–K41：主要结构、关键高低点、磁力位。
- 近期结构 K40–K1：定 cycle_position / direction / market_phase。
- 即时信号 K10–K1：信号棒、入场棒、二次入场、失败突破。
冲突时近期为主，背景作风险提示。

## 8状态（按唯一判定树顺序）
1) 有序波段？HH+HL 或 LL+LH ≥2组 → 通道候选；否则 → 区间态。
2) 通道：≥3组且可画平行线 → 按回撤定宽度；仅2组或线不稳 → trending_tr。
   - 回撤<30% 且重叠<20% → tight_channel
   - 回撤30%–50% → normal_channel
   - 回撤50%–78.6% → broad_channel（须≥3组HH+HL；仅2组则trending_tr）
   - 2–10根连续同向、几乎无回撤 → micro_channel（须填 spike_stage）
3) 区间：边界各≥2次测试且EMA近水平 → trading_range；方向完全丧失、EMA纠缠、任意入场期望≤0 → extreme_tr。
4) 尖峰：≥2根连续趋势棒、实体重叠<30% → spike；3–5根且<20% → 标准尖峰；≥6根或出现衰竭 → 高潮风险。
   1根超大棒仅为 spike_candidate，不得输出 spike。spike 优先于 micro_channel（2–10根重叠区）。

## 关键裁决
- 宽通道 vs trending_tr：≥3组且线稳 → broad_channel；仅2组 → trending_tr。
- 趋势型区间与宽通道共用「仅顺主方向」框架。
- 状态转换需收盘确认（突破+跟随/回踩测试成功），转换期降频。
- 边界模糊时给出 alternative_cycle_position。

## 你要输出的（其余由代码产出）
cycle_position、alternative_cycle_position、direction、always_in、market_phase、
diagnosis_confidence、key_signals、bar_by_bar_summary(K5–K1)、qualitative_notes。
gate_trace、gate_result、三价、止损、目标全部由代码计算，不要输出数值。
