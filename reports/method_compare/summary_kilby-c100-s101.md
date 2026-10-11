# 三方法同输入对比 · kilby-c100-s101
- input_hash: `474895d851594e8523eabaf1cff7b243952d0dc818c587d8b321b61b97b60fb5`  预算: 60000ms

| 方法 | 状态 | 成本(s) | 扰动(换车/破坏) | 耗时(ms) | 验证 | 终止 |
|---|---|---|---|---|---|---|
| insert | no_feasible_found | None | - | 32 | FAIL | None |
| rebuild | no_feasible_found | None | - | 65452 | FAIL | MIP_TIMEOUT_NO_SOL |
| low_disruption | feasible | 1988 | 0/0 | 60015 | ok | selected |

## 成本口径（第三阶段复审 P0-1）
- pool_best_cost_s = 1942
- reference_best_cost_s = 1942（pool_fallback(未提供跨方法参考，上限仅相对本方法候选池)）
- cost_cap = 1.1 × reference = 2136.2
- within_reference_cap = True （selection_status=selected）