# 阶段 5 补充数据集采集指引（2026-10-11）

> 目的：为评测与报告（任务书第 13 章）补充**外部有效性佐证**。
> 主评测集 = 现有 Solomon 派生 17 情景（dev/test 已冻结），新数据集只做补充验证，非必须。
> 原则：**只收原始文件，不做任何清洗/转换**；转换统一走阶段 0 管线（schema 校验 +
> manifest + 布局感知划分 + 测试集隔离），保证可溯源与评测公平。

## 优先级

| 优先级 | 数据集 | 角色 | 工作量 |
|---|---|---|---|
| 1 | Kilby（Mendeley 3fwc3twwn6） | 动态 VRP 补充基准（含 appearing time，天然插单时间） | 一个 zip |
| 2 | LaDe（菜鸟，先 1 小城） | 真实数据案例（阶段 6 展示/规模佐证） | 读条款 + 1 个 CSV |
| 3 | Amazon（1–2 条 route） | 单路线回放演示 | 需 AWS 账号 |

## 每个数据集必交的溯源四件套

1. 原始下载 URL
2. 下载日期
3. 许可条款原文（截图或复制；找不到明确条款就标 **待确认**）
4. 文件 sha256（PowerShell：`Get-FileHash 文件 -Algorithm SHA256`）

## Kilby（优先）

1. 打开 <https://data.mendeley.com/datasets/3fwc3twwn6/1> → Files → Download（整包 zip）
2. 记录 DOI `10.17632/3fwc3twwn6.1` 与页面 License 字段（以页面实际显示为准，截图）
3. zip 原样放 `data/raw/kilby/`，四件套写入 `data/raw/kilby/collection_notes.md`

## LaDe（可选，先小规模）

1. 先到 <https://github.com/wenhaomin/LaDe> 的 terms 链接读研究用途条款，截图存证
2. 只下载 LaDe-D（派送场景）的一个城市：**Jilin 或 Yantai**（文件最小）
3. 放 `data/raw/lade/`，记录条款链接 + 下载日期 + sha256

## Amazon（可选，最小化）

1. 需 AWS 账号从 registry 对应 S3 桶下载；按 `docs/amazon_field_mapping.md`
   只挑 1–2 个 route 的 JSON
2. **CC BY-NC 4.0 非商用**：能否进比赛交付仓库由队长拍板；manifest 标
   `redistributable=待确认`

## 禁止事项

- 不清洗、不转换格式、不改坐标系——原始文件直接入库
- 不把测试集新数据用于调参（布局感知划分后测试集即冻结）
- 不只写"学术基准惯用"代替许可确认（审查红线）

## 统一口径

- 评测成本一律为**行驶时间秒**；只有距离的数据集由转换管线声明换算假设
  （如速度系数）写入 manifest，不静默混用
- 基准时间 ×60 为**模拟尺度转换**，非真实配送分钟数
