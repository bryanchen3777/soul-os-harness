"""
harness/__init__.py — Soul OS Time-lapse Harness (TL-1)

TL-1 (2026-09, IMPLEMENTATION): Time-lapse Harness 最小实现 + 第一个实验
「TL-1 Same-Stimulus Longitudinal Growth Test」。

规格: docs/TIME-LAPSE-HARNESS.md (TL-0, v1)

定位:
  - 实验框架, 不是 feature (D0)。
  - 只活在 harness/ 与 tests/, 不碰 src/soul/scheduler.py, 不改 frozen contract。
  - SimulationClock 是 harness-local 的 (D2), 禁止加速 production scheduler。
  - GrowthProbe 复用现有 capability → motive → decision (src/soul/motive.py +
    src/soul/decision.py), 禁止另写 classifier。
  - Observer 的 derived 解析写独立 analysis/ 流, 永不回写 canonical。
  - 隔离 data_root: data/time_lapse/<experiment_id>/<run_id>/, 0 production mutation。

模块:
  - clock:    SimulationClock (harness-local 模拟时钟)
  - fixture:  TL-1 fixture (SEED=42 + 30 天事件剧本 + seeded Ruka baseline)
  - probe:    GrowthProbe (same stimulus @ T0/T15/T30)
  - observer: Observer (structured observation + derived 解析)
  - records:  GrowthProbeRecord schema + run header + JSONL 写入
  - runner:   TL-1 run 编排 (隔离 data_root + 执行序列 + determinism 验证)
"""
