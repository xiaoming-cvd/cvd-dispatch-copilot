# 数据授权清单（阶段 7 交付）

## 公开基准数据（评测输入来源）

| 数据集 | 用途 | 许可/条款 | 交付处理 |
|---|---|---|---|
| Solomon VRPTW 100-customers | dev/test 小场景来源 | 学术基准惯用，SINTEF 项目页分发；链接见下 | **仅来源链接 + 处理脚本**（`scripts/download_solomon.py`），原始数据不入库 |
| Kilby 派生算例（Mendeley） | dev/test 大场景来源 | CC BY 4.0（署名 + 许可链接 + 修改说明） | 已入库派生场景含署名与修改说明（`reports/kilby_data_quality.md`、`data/manifests/`）；提交前再核对数据页 |
| Amazon last-mile 数据 | 小范围适配验证 | CC BY-NC 4.0（非商业限制；赛事用途不能仅凭"比赛"推定） | **原始数据不入库**：仅字段映射（`docs/amazon_field_mapping.md`）+ 明确标注的合成样例 |

- Solomon: https://www.sintef.no/projectweb/top/vrptw/100-customers/
- Kilby: https://data.mendeley.com/datasets/3fwc3twwn6/1 （CC BY 4.0）
- LaDe（参考未入库）: https://github.com/wenhaomin/LaDe

## 合成与派生内容（必须向使用者声明）

- **新增订单为合成事件**：按固定种子从基准算例选择已知/新增任务生成的
  插单事件，不是真实动态订单；每场景 `_meta.event_log` 记录种子与选择。
- 成本为模型行驶秒（抽象成本），无真实单价，不报人民币节约。
- 演示 LLM 为 MockProvider，不冒充真实模型。

## 交付包数据边界（红线）

- 不进入交付 Git/zip：任何密钥、`.env`、COPT 许可文件、受限原始数据
  （Amazon 原始数据、未核对页前的 Kilby 原始文件）、虚拟环境、巨型缓存。
- 交付前核对项：Kilby 数据页许可状态（CC BY 4.0）与署名完整性。
