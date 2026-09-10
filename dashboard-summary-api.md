# 三图汇总 API

一次 GET 返回截图中的三部分，不需要启动额外服务。

交付状态：代码、离线汇总和校验已完成。线上发布被自动审批拦截，以下为待发布地址，须用户明确批准具体数据与目标后执行 `--publish`。

```http
GET https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app/ctmTicketStatusMonitorV44/analytics/dashboardSummary.json
```

响应为 JSON 对象，无分页、无业务查询参数。当前版本固定返回 All Claims、最新 Dashboard 快照所在月份，以及现有 2026 completion 数据源中的 Warranty Approval / Parts Preparation 两阶段。

```javascript
const response = await fetch(
  'https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app/ctmTicketStatusMonitorV44/analytics/dashboardSummary.json',
  { cache: 'no-store' }
);
if (!response.ok) throw new Error(`Summary API HTTP ${response.status}`);
const summary = await response.json();
if (!summary) throw new Error('Summary has not been published');
const { ticketTimeline, remainingStatusMix, approvedRepairCostDistribution } = summary;
```

| 字段 | 内容 |
| --- | --- |
| `schemaVersion` | 当前 `1.0` |
| `generatedAt` | 汇总生成时间，UTC；不是工单数据更新时间 |
| `ticketTimeline.year` | 完成年度 |
| `ticketTimeline.sourceGeneratedAt` | 时间线源数据生成时间，保留源时区格式 |
| `ticketTimeline.stages.approval / parts` | 两阶段各自的完成总数、超平均耗时占比、月度曲线和耗时分布 |
| `…completedTickets` | 阶段完成工单数 |
| `…overAveragePercent` | 页面显示的超平均耗时比例，0–100 |
| `…monthlyTrend[]` | `month`、`label`、`averageDays`、`rolling3MonthAverageDays`、`completedTickets` |
| `…durationBuckets[]` | `label`、`count`、`percent`，包含 0–7、8–14、15–21、22–30、31–60、60+ 天 |
| `remainingStatusMix` | `snapshotDate`、`sourceGeneratedAt`、`totalTickets`、`items` |
| `…items[]` | `label`、`count`、`percent`；超过六种状态时保留前五种，其余合并 Others，并在 `details` 中保留拆分 |
| `approvedRepairCostDistribution` | `periodStart`、`periodEnd`、`sourceGeneratedAt`、`currency`、`totalTickets`、`totalAmount`、`buckets` |
| `…buckets[]` | `key`、`label`、`count`、`amount`、`ticketPercent`、`amountPercent` |

费用图扇区按 **amountPercent** 绘制，图例括号内显示 **count**，中间显示 **totalAmount**（AUD）。费用分档直接复用后台 `repairCostDistribution`，包含零费用工单；分界口径与网页一致：[0, 0.01)、[0.01, 100)、[100, 500)、[500, 1000)、[1000, 2500)、[2500, ∞)。

时间线按阶段完成月份统计，工单创建年份可能早于完成年份。平均天数没有数据时为 `null`；原始月均值和三个月滚动平均值直接保留源数据计算结果。时间线的分档占比保留网页的一位小数，其他比例保留两位小数，四舍五入后不保证相加恰好为 100。

该接口是预计算快照，GET 不触发 SAP 查询。Dashboard 分析刷新和时间线资产同步均已接入汇总刷新；每部分保留自己的源更新时间，两套源数据可能不是同一批次。源数据缺失或校验失败会使刷新报错，并保留上一次成功发布的汇总，不写入伪造的零值。

只输出汇总数值，不包含工单编号、客户或员工明细。访问权限继承现有 Firebase 数据库规则，本次不修改规则；如规则要求认证，使用调用方自己的 Firebase 身份，禁止将服务账号私钥放进浏览器。

手动刷新（使用现有 `FIREBASE_DB_URL`、`FIREBASE_SA_PATH`、`MONITOR_ROOT` 环境配置）：

```powershell
python build_dashboard_summary_api.py --publish
```

只生成本地 JSON，不写线上：

```powershell
python build_dashboard_summary_api.py
```

输出到 `generated_exports/dashboard_summary.json`。也可同时传 `--team-json` 和 `--completion-json` 做离线生成；离线文件不可与 `--publish` 混用。

校验：

```powershell
python -m unittest discover -s tests -p test_dashboard_summary_api.py
```
