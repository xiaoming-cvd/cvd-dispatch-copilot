# 三方法同输入对比 · kilby-tai75b-s101
- input_hash: `488716e2524471cbad9250085b5a48c2ac583093fc310c469d4ede5ec1615e68`  预算: 60000ms

| 方法 | 状态 | 成本(s) | 扰动(换车/破坏) | 耗时(ms) | 验证 | 终止 |
|---|---|---|---|---|---|---|
| insert | no_feasible_found | None | - | 0 | FAIL | None |
| rebuild | no_feasible_found | None | - | 62687 | FAIL | MIP_TIMEOUT_NO_SOL |
| low_disruption | feasible | 2177 | 0/0 | 60000 | ok | selected |

## 成本口径（第三阶段复审 P0-1）
- pool_best_cost_s = 2158
- reference_best_cost_s = 2158（pool_fallback(未提供跨方法参考，上限仅相对本方法候选池)）
- cost_cap = 1.1 × reference = 2373.8
- within_reference_cap = True （selection_status=selected）