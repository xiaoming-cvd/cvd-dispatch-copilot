# 三方法同输入对比 · c101-s101
- input_hash: `1a608faff4bd795f2532e299a4190bbcd9ff28abbe75161b0134edf8107ba668`  预算: 15000ms

| 方法 | 状态 | 成本(s) | 扰动(换车/破坏) | 耗时(ms) | 验证 | 终止 |
|---|---|---|---|---|---|---|
| insert | feasible | 38540 | - | 0 | ok | None |
| rebuild | feasible | 28220 | - | 453 | ok | MIP_OPTIMAL |
| low_disruption | feasible | 32443 | 4/6 | 5640 | ok | no_candidate_within_cost_cap |

## 成本口径（第三阶段复审 P0-1）
- pool_best_cost_s = 32443
- reference_best_cost_s = 28220（reference_results）
- cost_cap = 1.1 × reference = 31042.0
- within_reference_cap = False （selection_status=no_candidate_within_cost_cap）