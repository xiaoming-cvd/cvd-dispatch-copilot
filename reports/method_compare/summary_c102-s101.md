# 三方法同输入对比 · c102-s101
- input_hash: `63998c211883028147ae7d06ed9549faa702b022b6be2ca66e9c32600a33c5b2`  预算: 60000ms

| 方法 | 状态 | 成本(s) | 扰动(换车/破坏) | 耗时(ms) | 验证 | 终止 |
|---|---|---|---|---|---|---|
| insert | feasible | 46106 | - | 15 | ok | None |
| rebuild | feasible | 30819 | - | 41032 | ok | MIP_OPTIMAL |
| low_disruption | feasible | 38196 | 2/5 | 889 | ok | no_candidate_within_cost_cap |

## 成本口径（第三阶段复审 P0-1）
- pool_best_cost_s = 38196
- reference_best_cost_s = 30819（reference_results）
- cost_cap = 1.1 × reference = 33900.9
- within_reference_cap = False （selection_status=no_candidate_within_cost_cap）