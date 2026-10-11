# 交付包检查清单与私有仓库文件清单（阶段 7）

## 一、交付前检查（已执行项打勾）

- [x] 包内无密钥：`.env` 不入库（.gitignore）；`.env.example` 仅模板。
- [x] 无 COPT 许可文件、无私钥/证书。
- [x] 受限原始数据不入库：Amazon 原始数据不入库（仅映射+合成样例）；
      Solomon 仅链接+脚本；Kilby 派生场景含署名（提交前再核对数据页）。
- [x] 无虚拟环境与巨型缓存：`.venv/`、`__pycache__/`、`.pytest_cache/`
      已入 .gitignore。
- [x] 白名单制打包（2026-10-11 二次复审修正）：交付 zip 由
      `scripts/build_delivery_zip.py` 生成，只打白名单内文件；运行态草稿
      （data/processed/drafts）、临时结果（reports/_tmp_*）、真实模型调用痕迹
      （stage4_real_case*）、历史 pytest 日志（stage*_pytest_log.txt）默认
      排除且开发机原件未删；打包前做敏感字段扫描（sk-/Bearer/私钥头），
      打包后包内 manifest（逐文件 sha256）自动核对一致才产出。
- [x] README 区分已实现 / 模拟 / 尚未实现；Mock 模型、合成新增事件、
      估算成本（模型行驶秒）均已标清。
- [x] 干净环境验证完成并记日志：`reports/stage7_clean_env_log.txt`
      （2026-10-11 三次复审复验：348 passed + 6 skipped；CLI 冒烟退出码 0、
      status=feasible、independent_ok=True；Web `GET /` 与 `/api/scenarios`
      均 200，serve.py 已停止——以上均带命令与时间记在日志内。
      注意"干净环境"=同机新 venv，仍共用开发机 data/raw，非净目录跨机复现；
      跨机开箱行为以 `pytest -m delivery_sim` 空目录回归为准）。
- [ ] 总工验收"冻结评测完成"（阶段 6 二次复审：有条件通过，
      待申请最终验收）。
- [ ] 队长确认后创建私有远端并推送（本清单不自动建仓/邀请协作者）。

## 二、私有仓库文件清单（建议入库范围）

```
README.md  requirements.txt  .env.example  .gitignore
docs/      （architecture/field_dictionary/algorithms/data_licenses/
            testing/known_limitations/demo_script/delivery_checklist/
            handoff/执行清单/data_collection_guide/amazon_field_mapping/
            schema/scenario.schema.json）
src/       （domain/adapters/solver/validation/api/llm/evaluation）
frontend/  scripts/  tests/
data/sample/  data/manifests/  data/processed/scenarios/（冻结草稿+registry）
reports/evaluation6/（frozen_manifest/frozen_inputs/requirements_lock/
            raw/ sensitivity_fixedpool/ summary/reconciliation/
            evaluation_report/ 图表）
reports/stage7_clean_env_log.txt  reports/review_closure*.md
```

不入库：`.env`、`data/raw/` 受限原件、`.venv/`、缓存、
`reports/review_portable_*.zip`（审查流转件，走桌面文件夹）。

## 三、交付物索引

| 物 | 位置 |
|---|---|
| 交付说明 | README.md + docs/（本清单所在目录） |
| 评测报告 | reports/evaluation6/evaluation_report.md（60 秒软预算版） |
| 审查包（桌面） | C:\Users\tianj\Desktop\阶段审查包\（7 个阶段 zip + 校验和清单） |
| 干净环境日志 | reports/stage7_clean_env_log.txt |
| Git 历史核验 | 城配调度副驾.bundle（git bundle，含全部提交；交付包不含 .git 目录，接收方用 `git clone 城配调度副驾.bundle` 还原仓库并核对 frozen_inputs.json 的 git_commit） |
| Kilby 转换质量报告 | reports/kilby_data_quality.md（docs/data_licenses.md 引用的依据，已随包） |
| 基础算例指纹 | reports/base_fingerprint.json（v2.0：布局 Jaccard + 全内容指纹（含客户 ID/时间窗/服务）+ 11 个源文件字节 sha256 对 manifest 核对） |
| 一键审查 | scripts/review_check.py：有 COPT 全量模式与 `--skip-copt` 基础模式；任一环节显式跳过（退出码 2，如缺 Solomon 源文件）时 overall 显示 partial 而非 pass；后端断言为严格相等（实际+请求后端均须匹配，失败保留结果 JSON） |
| 审查证据（单列，2026-10-11 二次复审） | reports/stage7_review_full_summary.json（有 COPT 全量，354 passed，overall=pass，含 rebuild_copt_backend 与 exhaustive）；reports/stage7_review_skip_summary.json（dev skip 模式，354 passed）；reports/stage7_cleanenv_skip_summary.json + stage7_clean_env_log.txt（无许可干净环境，348+6 skipped，overall=pass，退出码 0） |
| 打包器 | scripts/build_delivery_zip.py：白名单制 + 敏感字段扫描 + 包内 manifest 自核；zip 根含 交付包_manifest.json（逐文件 sha256，可独立复验） |

**不在交付包内的引用物**（历史开发机证据，仅存档，原件未删）：reports/stage*_pytest_log.txt、
reports/stage4_demo_*.json、reports/stage4_real_case*.json（真实模型调用痕迹）、
reports/_tmp_*.json（临时结果）、data/processed/drafts/（运行态草稿）、
reports/snapshot_*.zip、reports/review_portable_*.zip、reports/method_compare/。
