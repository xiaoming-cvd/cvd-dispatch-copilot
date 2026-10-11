# 测试说明（阶段 7 交付）

## 运行方式

```bat
.venv\Scripts\python.exe -m pytest           :: 全量（当前 332 条）
.venv\Scripts\python.exe scripts\review_check.py            :: 审查模式（有 COPT）
.venv\Scripts\python.exe scripts\review_check.py --skip-copt  :: 无 COPT 模式
```

## 覆盖范围（tests/）

| 文件 | 覆盖 | 条数 |
|---|---|---|
| test_api_stage5.py | 工作台端到端：上传/预览/baseline/约束草案/确认/求解/导出/队列/版本冲突/快照导出 | 46 |
| test_evaluation6.py | 评测链路：runner 冒烟、物化异常如实记录、39 场景对齐、成本上限布尔语义、固定池完整性、test 口径回归、事后归档声明 | 7 |
| 阶段 0–3 各文件 | 契约校验（重复 ID/负数/单位冲突/矩阵维度/时间窗倒置）、哈希与冻结、独立校验器（含穷举对账）、COPT 目标与大 M 修复、插入/重排/低扰动、扰动与差异量化 | 其余 |

## 关键验收断言（阶段 6 二次复审）

- 39 个 manifest ID = 39 个 raw = 39 个对账 ID；
- 主方法 test 冻结集口径 4/16、5/16、15/16；
- 60 条有数值低扰动记录中 6 条超限均标 FALSE，主 cap=1.10 超限 0 条；
- summary.csv 数值与状态可由 raw 重新导出（测试内逐行重算比对）；
- 报告配对数、版本字段、越界行数由程序自动生成。

## 环境说明

- Python 3.12 + 依赖锁定见 `requirements.txt`；评测证据链含
  `reports/evaluation6/requirements_lock.txt`（pip freeze 全量）。
- COPT 8.0.6 为可选后端（本机全局安装 + coptpy 复制入 venv，属应急处理，
  不可移植）；无 COPT 许可时全部测试与评测可运行（显式 copt 用例除外）。
