# 阶段 4 复审问题关闭表（v2 · 2026-10-11）

复审依据：《第四阶段复审与跨电脑AI改进建议.docx》（跨电脑 AI 复审，结论：有条件通过
代码骨架，P0 修复后再验收人工确认流程）。执行顺序按复审给定提示词：先写最小反例
（先红 57 项），再修代码，最后语料接入。
验证基线：全量测试 **279 passed, 0 failed, 0 skipped**（`reports/stage4_v2_pytest_log.txt`），
其中新增 `tests/test_llm_review_fixes.py` 63 项（含 25 条语料参数化）。

## P0-1 部分接受会默认接受未列出的条目【已修复】

**问题**：`service.confirm` 的 `decisions.get(idx, True)` 把缺失序号视为接受；
CLI `--accept 0` 实际接受全部。

**修复**（`src/llm/service.py` + `scripts/draft_cli.py`）：
- 语义显式区分：`decisions=None`（未提供）= 全部接受，供程序化调用；
  **显式提供 = 只接受 `decisions[idx] is True` 的条目，未列出的一律拒绝**。
- decisions 校验：`E_DECISION_INVALID` —— 非整数键（含 bool）、非布尔值、
  越界序号（0 ≤ idx < items+preferences 总数）全部拒绝。
- CLI：`--yes`（显式全部接受）与 `--accept <列表>`（只接受所列项）argparse 互斥组；
  确认时二者必须选一，裸 `--confirm` 报 `E_ACCEPT_REQUIRED`（不再隐式全接受）。

**验证**：`TestPartialAccept` 7 项，含复审反例（草案 o1→V1 + o2→V2，传 {0:True}
后只有 o1 进入新场景）；`TestCliValidation` 8 项（非整数/越界/重复/互斥/裸确认/端到端）。

## P0-2 虚假的原文依据能进入草案【已修复】

**问题**：`extractor.map_payload` 只检查 source_text 非空，伪造依据可通过。

**修复**（`src/llm/extractor.py`）：
- `map_payload` 新增 `raw_text` 必填入参；所有结构化条目（items/preferences/
  missing/questions）的 `source_text` 必须是 `raw_text` 的子串。
- 不匹配的处理：items 进 rejected（原有通道，原因注明"原文依据在原始需求中不存在"）；
  preferences/missing/questions 丢弃该条进 rejected（kind=preference/missing/question）
  并计警告，**绝不后台伪造依据**。
- `create_draft` 把原始需求文本传入映射（mock 与真实模型同一防护路径）。

**验证**：`TestSourceTextVerification` 5 项，含服务端到端反例（Provider 返回
"用户从未说过这句话"作依据 → 草案无该硬约束、rejected 有原因）。

## P0-3 无可确认内容仍生成新场景版本【已修复】

**问题**：仅含澄清问题的草案、或全被拒的草案，confirm 仍 version+1，制造
"已确认修改"假象。

**修复**（`service.confirm`）：
- 没有任何被接受的硬约束或偏好时，不产生任何文件、不升版本：
  - 草案含 questions/missing → `E_CONFIRM_NEEDS_CLARIFICATION`；
  - 否则 → `E_CONFIRM_NO_EFFECT`；
  - 两种情形原场景版本与草案 pending 状态均保持不变，可澄清后重新确认。
- 旧场景对象冻结语义不受影响。

**验证**：`TestNoEffectConfirm` 4 项（仅澄清/全拒绝/空草案/无确认记录残留），
均断言版本不变与草案仍 pending。

## P1-1 草案确认写入的原子性【已修复】

**问题**：先 save_confirmed 再 mark_confirmed，第二步失败会留下"确认包已落盘但
草案仍 pending"的半确认态，重复确认可产生第二份确认包。

**修复**（`DraftStore.save_confirmation`，单一状态记录）：
- 确认结果 = 单一文件 `confirmed/<draft_id>.confirmation.json`
  （已确认草案 + 约束包 + 新场景三者同文件），临时文件 + `os.replace` 原子发布；
- `<draft_id>.lock` 独占锁（O_CREAT|O_EXCL）防止并发重复确认；
  任何一步失败：临时文件清理、锁释放、无半确认态，可安全重试且只产生一份记录；
- 内存模式用 threading.Lock 同等语义；`get_draft` 优先读确认记录
  （确认后草案状态天然为 confirmed）。

**验证**：`TestAtomicConfirm` 4 项——单记录文件结构；**写入失败反例**（模拟磁盘满：
无记录残留、草案仍 pending、重试成功且只有一份记录）；**双 service 并发确认一胜一负**；
残留锁拒绝。

## P1-2 CLI 确认参数校验【已修复】

**修复**：`--accept` int 转换移入 try（非整数干净报错 `E_ACCEPT_INVALID`，
无 traceback）；解析前读取草案条目数，越界（如 999）与重复序号均拒绝；
`--yes`/`--accept` argparse 互斥组；裸确认要求显式选择。

**验证**：`TestCliValidation`（见 P0-1）。

## P1-3 25 条语料与测试脱节【已修复】

**校订**（`data/sample/nl_requirements_tests.json`）：
- 逐条按当前阶段能力重审并写入 `audit` 字段（2026-10-11 校订，重审前不得改 expected 原值）：
  **supported 17 / clarify 1 / deferred 7**；
- deferred（当前契约不支持、绝不声称已实现）：NL-01 新任务加锁、NL-02 时间窗修改、
  NL-04 订单字段、NL-05 新任务顺序锁、NL-06 车辆字段、NL-23 订单字段+注入、
  NL-25 车辆字段+注入——均标注后续阶段能力评估；
- 修正错误预期：NL-08「老客户都尽量别动」expect draft → **clarify**（未点名订单）；
- `counts` 与 `usage` 更新（声明参数化测试的读取方式）。

**接入**：`TestCorpusAudit` —— 25 条参数化，每案构造语料专用场景
（o1–o30，新任务 {o9,o21,o22,o30}，V1–V3，原计划只含旧任务）走 mock 演示链路：
- supported：断言产出的锁/偏好与 expected 一致；
- clarify：断言以问题/缺字段收口且无硬约束；
- deferred：断言**绝不产出硬约束**；
- 全体：每条产出的 source_text 必须是原文子串（P0-2 不变量）。

**附带修复**：mock 规则词表补「之前/之后/先于/晚于」顺序Cue（NL-03 自然句式
"o3 必须在 o5 之前送"此前被误判为单锁，属演示提取器真实缺陷）。

## P2 建议【已修复】

1. **草案 ID 纳入基线哈希**（`service.create_draft`）：digest 加入
   `base_input_hash`，同时间同原文但基线内容不同 → 不同 draft_id；
   `DraftStore.save_draft` 拒绝同 ID 异内容覆盖（`E_DRAFT_ID_COLLISION`，内存与磁盘一致）。
2. **get_draft 路径防护**：ID 仅允许 `[A-Za-z0-9_-]{1,80}`（`E_DRAFT_ID_INVALID`），
   阶段 5 HTTP 化前置。
3. **Provider HTTP 错误脱敏**（`openai_provider._sanitize`）：服务端回显中的
   本机密钥原文与 `sk-*`/`Bearer` 形态在进错误消息前替换为 `***`。

## 边界保持（复审要求）

演示模式与真实模型分离 ✅（mock model=None 全程标注）；硬约束与偏好分离 ✅
（偏好绝不写成锁，进确认包）；未经确认不求解 ✅（本阶段无求解器调用路径）；
注入收口 ✅（语料 NL-19~25 参数化锁定）。

## 证据文件索引

| 证据 | 路径 |
|---|---|
| 全量测试日志（279 passed） | `reports/stage4_v2_pytest_log.txt` |
| 复审修复测试（63 项） | `tests/test_llm_review_fixes.py` |
| 校订后语料（含 audit） | `data/sample/nl_requirements_tests.json` |
| 修复前反例先红（57 failed） | 本轮会话首跑记录（先红后绿） |
