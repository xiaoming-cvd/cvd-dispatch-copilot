# 测试说明（阶段 7 交付）

## 运行方式

```bat
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe scripts\review_check.py
.venv\Scripts\python.exe scripts\review_check.py --skip-copt
```

（依次：全量测试；审查模式（有 COPT，含显式 rebuild copt 冒烟与后端校验）；
无 COPT 模式（rebuild 默认 OR-Tools 冒烟仍会运行）。两种模式退出码 0 才为
全量 pass；任一环节显式跳过（退出码 2）时 overall 显示 partial 而非 pass。
当前基线（2026-10-11 三次复审后）：开发机 `-m "not delivery_sim"` 354 passed；
无 COPT 干净环境 348 passed + 6 skipped；交付模拟 `delivery_sim` 1 passed。）

数据前置条件与两种运行模式（无原件 / 原件齐全）见 README「数据恢复」节：
缺原件时源数据用例显式 skip、review_check 为 partial，均不视为失败。

```bat
.venv\Scripts\python.exe -m pytest -m delivery_sim -q
.venv\Scripts\python.exe scripts\verify_empty_dir.py
```

（交付包空目录无原件模拟回归：白名单打包 → 解压到无 data/raw 目录 →
pytest 收集无错误且源数据用例 skip → review_check 基础通过且 overall=partial。
约 1-3 分钟，日志 reports/verify_empty_dir_log.txt。）

```bat
.venv\Scripts\python.exe scripts\build_delivery_zip.py
```

（重建交付 zip：白名单制 + 敏感字段扫描 + 包内 manifest 自核，自动更新
桌面 校验和清单.txt。）

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
