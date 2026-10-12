# 时间窗放宽演示（8 个 tight 场景的 loose 版）——独立成表，非冻结评测

- 生成：`scripts/make_tw_relaxed_demo.py`（幂等可续跑），2026-10-12
- 背景：原 8 个 tight 时间窗场景（dev 7 + test 1）在 60s 主评测中基线物化失败
  （E_PLAN_NOT_FOUND）。300 秒诊断（../baseline300/）证实根因为**初解构造
  失败——时间窗宽度 < 服务时长（5400 模型秒），老订单自身即排不出合法
  原计划**，与预算无关。
- 本实验按队长决策"把数据里的时间窗放宽再试"：生成 8 个**新场景**
  `<原id>-loose`（原草稿一字不动，split=demo，不进入 evaluation6 任何口径），
  放宽规则 `due_s += R`、`return_by_s += R`（R 逐级取 5400/10800/21600，
  即 1/2/4 个服务时长；取"基线可构造且至少一方法可行"的最小级）。

## 结论（全部经独立校验器核验，validation_ok=True）

| 场景 | 放宽 R | 基线成本(模型秒) | insert | rebuild(OR-Tools) | 低扰动 | rebuild(COPT 诊断) |
|---|---|---|---|---|---|---|
| c101-s102-loose | 21600 | 31130 | 可行 32577 | ROUTING_FAIL（快速构造失败） | 可行 32577 | 可行 28688（MIP_OPTIMAL） |
| c102-s102-loose | 10800 | 24387 | 可行 27949 | ROUTING_FAIL | 可行 27949 | 可行 24466（MIP_OPTIMAL） |
| r101-s101-loose | 5400 | 29159 | 可行 34030 | 可行 31821（ROUTING_OPTIMAL） | 可行 30557 | —（未触发） |
| r101-s102-loose | 10800 | 25709 | 可行 25904 | ROUTING_FAIL | 可行 25904 | 可行 20653（MIP_OPTIMAL） |
| r102-s101-loose | 5400 | 23630 | 可行 28535 | 可行 29061（ROUTING_OPTIMAL） | 可行 26401 | —（未触发） |
| r102-s102-loose | 10800 | 27300 | 可行 27810 | ROUTING_FAIL | 可行 27810 | 可行 20509（MIP_OPTIMAL） |
| r201-s102-loose | 5400 | 37157 | 可行 38951 | 可行 37106（ROUTING_OPTIMAL） | 可行 38951 | —（未触发） |
| rc101-s201-loose | 5400 | 33750 | 可行 39879 | 可行 42976（ROUTING_OPTIMAL） | 可行 36728 | —（未触发） |

- **基线 8/8 全部物化成功**（0~16ms）——证实"时间窗过紧"是唯一根因。
- **insert 8/8 可行、低扰动 8/8 可行**（产品主力两方法全绿）。
- **rebuild(OR-Tools) 4/8**：4 个场景毫秒级 ROUTING_FAIL——这是已声明的
  求解器构造能力边界（≠证明不可行）：同一输入换 COPT 后端全部解出
  （MIP_OPTIMAL，独立校验通过），且成本更低（如 r102-s102-loose：
  COPT 20509 vs 低扰动 27810，模型秒）。

## 口径与红线

- 本表为**演示口径**（60s 预算、产品参考链），不并入 60 秒三方法主比较，
  不改冻结评测任何数字；原始 8 个 tight 场景保持原样（页面标"基线暂不可行"）。
- rebuild 的 OR-Tools/COPT 结果分别如实标注后端；COPT 为可选后端
  （开发机有许可；无许可环境脚本记 ERROR，不静默回退）。
- 机器可读明细：results.json（逐场景全字段）/ summary.csv（表格）。
  草稿：`data/processed/scenarios/demo/<sid>.json`
  （_meta.relaxed_from / relax_rule 可溯源）；物化基线在产品数据目录，
  场景列表"原计划参考成本"列已显示。
