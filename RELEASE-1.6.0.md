# KAYTRADE V1.6.0 · Build 1600

本版本基于已验证的 V1.5.6 Build1561，只调整 5m BOLL 中轨开仓必要条件与外轨首仓倍率。

## 1. 5m BOLL 中轨新增 MACD 必要条件

- `middle_rsi` 做多：必须通过正式模型现有的 `macd_improving`。
- `middle_rsi` 做空：必须通过正式模型现有的 `macd_improving`。
- 直接复用正式模型既有 MACD 定义：最近三根已收盘 5m MACD Histogram，做多为 `c > b > a`，做空为 `c < b < a`。
- 不新增 MACD 金叉/死叉、零轴、Histogram 正负等条件。
- 中轨即使总评分达到 6.0，只要 `macd_improving` 未通过，也禁止开仓。
- 实际订单准备/提交路径会再次检查该条件。
- MACD 暂时不通过时，不因此销毁、消费或改写该 BOLL opportunity；后续仍按原机会生命周期重新评估。
- 中轨被 MACD 拦截时不会自动改判成上下外轨路径。

## 2. 5m BOLL 外轨首仓直接 2× 基础固定仓位

用户当前设置的第一信号/固定仓位继续作为基础仓位 1×：

- `middle_rsi`：1× 基础固定仓位。
- `lower_band`：2× 基础固定仓位。
- `upper_band`：2× 基础固定仓位。

外轨是第一次开仓时直接生成一张 2× 数量的 LIMIT 订单；不是先开 1× 再加仓，也不是连续提交两张订单。外轨本身的触发定义和评分条件不变，不额外要求 MACD improving。

## 保持不变

- 自动开仓最低评分：6.0。
- 5m BOLL 原路径优先级不变。
- LIMIT 开仓。
- 1H ATR × 1 止损。
- 整仓一次性 2R 止盈。
- Path C 关闭。
- 平仓后冷却关闭。
- 连续亏损停开关闭。
- BOLL opportunity 无额外墙钟过期。
- 一根信号去重、故障锁、订单核对与 V1.5.6 51054 执行修复继续保留。

## Build

- Version: `1.6.0`
- Build: `1600`
- Target: Intel Mac / macOS 14+
