# FedWatch 跟踪器

把 CME FedWatch 的"各次 FOMC 会议后目标区间概率"每天存下来，看**同一次会议（比如 2027 年 3 月）的概率在不同观察日怎么变**，以及**加息终点**的分布怎么移动。

- 概率是自己用 30 天联邦基金期货（ZQ）收盘价、按 CME 公布的 FedWatch 方法算的，和 CME 页面同一口径
- 也能导入 CME 官网下载的官方历史（每次会议往前一年）
- 本地网页看板：`http://127.0.0.1:8766/`

## 日常使用

| 做什么 | 怎么做 |
|---|---|
| 打开看板 | 双击 `启动看板.bat` |
| 每天自动抓 | 双击一次 `设置每日自动抓取.bat`（工作日 15:40，排在 ETF 监控 15:30 之后） |
| 手动抓最新 | 看板右上角「抓最新数据」 |
| 补一年官方历史 | 见下面「导入 CME 官方历史」 |
| 换台电脑从头装 | 双击 `安装依赖.bat`（建环境 + 回算历史，约 1 分钟） |

每次抓取都会回看 3 个月，电脑关机几天也会自动补上；但 **Yahoo 查不到已经到期的合约**，所以隔太久（超过合约到期）没跑，那段就补不回来了。

## 看板

- **单次会议概率**：选一次会议，横轴是观察日，看每个目标区间的概率怎么变（堆叠 / 折线两种画法），时间轴上标了 FOMC 会议日；下方是期望利率走势和"最新 vs 1天/1周/1月前"对比表（同 CME 的 Current / 1 Day / 1 Week / 1 Month）。
- **加息终点**：沿 FedWatch 的概率树，统计"到某次会议为止，路径上加到的最高区间"的分布随时间怎么变；也可切到降息终点（最低点）。
- **利率路径**：最新、1 周前、1 月前的"每次会议后期望利率"曲线叠在一起，看整条路径怎么平移。
- **全表**：任选一个观察日，会议 × 区间的概率矩阵（同 CME 的 Probabilities 页）。
- **数据与方法**：数据状态、运行日志、导入记录。

网址会记住当前视图，可以收藏，例如 `http://127.0.0.1:8766/#tab=meeting&meeting=2027-03-17`。

## 数据来源和限制

| 数据 | 来源 | 说明 |
|---|---|---|
| ZQ 各月合约日收盘价 | Yahoo Finance（`ZQH27.CBT` 这类代码） | 只列未到期合约，约 18 个月；到期即查不到 |
| EFFR、目标区间 | 纽约联储 Markets API | 官方，晚一天发布 |
| FOMC 日程 | 美联储官网 | 内置 2024–2027，每天尝试从官网补新年份 |
| 官方概率历史 | CME FedWatch 页面手动下载 | 每次会议往前一年 |

CME 自己的结算价接口有反爬，这里不用。

**要开着 Clash**：Yahoo 在国内直连返回 403。程序跟随 Windows 系统代理（和 ETF 监控相反，那边是强制直连），所以抓取时 Clash 的系统代理要开着；没开的话每日任务会失败并弹通知，开了之后下次运行会自动补齐。

**自算历史从 2026-07-29 开始**：更早的日子要用到 7、8 月合约，它们已经到期、Yahoo 查不到。往后每天自动累积。

**会议刚开完的一两天**：纽约联储还没更新区间，程序用当月合约反推决议（例如 9/16 反推为加息 25bp），看板顶部会提示。纽约联储数据一到就自动改用官方区间。万一反推错了，在 `config.yaml` 里填 `target_override: [下限, 上限]`。

## 导入 CME 官方历史

1. 打开 https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html
2. 在工具上方选中会议（标签形如 `17 Mar27`）
3. 左侧「Historical」框 →「Downloads」，下载 Excel
4. 放进 `data\cme_downloads\`（文件名最好带会议日期，如 `FedWatch_20270317.xlsx`）
5. 看板点「导入官方文件」，或等每日任务自动导入；看板上把数据源切到「CME 官方导入」

想看官方数据的「加息终点」，观察期内的每次会议都要下载（中间缺一次会议，那天就算不了）。导入失败的文件会在「数据与方法」里写原因，改好文件后会自动重试。

## 算法

照 CME 的[方法说明](https://www.cmegroup.com/articles/2023/understanding-the-cme-group-fedwatch-tool-methodology.html)：

1. 合约隐含月均 EFFR = 100 − 价格
2. 没会议的整月是"锚月"：均值 = 上月月末利率 = 下月月初利率（当前月不当锚）
3. 会议月：均值 = 月初 × N/D + 月末 × M/D（N = 1 号到决议日的天数），已知一端反解另一端
4. 锚月往前一路倒推、往后只推一个月（后一个月是锚 → 倒推；否则前一个月是锚 → 正推；否则接下个会议月的月初）
5. 每次会议预期变动 ÷ 25bp，整数部分和小数部分拆成相邻两个结果的概率，逐次会议卷积出累积分布

**校验**：拿网上存档的 51 个 CME 官方快照（2026-07-13 ~ 09-10）逐日对比，每次会议各区间最大偏差的中位数 **1.2 个百分点**（75 分位 2.5、90 分位 5.2）。差异主要来自价格时点：这里用日收盘价，CME 页面用盘中实时价。换成"一律倒推"的传播方式，偏差变大（中位数 1.7、75 分位 3.1），用快照里自带的会议月价格对比时差距更明显（90 分位 8.7 对 17.6），说明第 4 步的方向规则和 CME 一致。

**加息终点**：同一棵概率树上，逐条路径记下到达过的最高区间（含现行区间），假设各次会议独立（和 FedWatch 同一假设）。"预期路径高点"是先对每次会议取期望再取最大；前者不会低于后者，两者重合说明高点之前没有定价降息。

远期会议（一年以后）的合约成交清淡，概率日间抖动会比近月大。

## 命令行

```
.venv\Scripts\python.exe -m fedwatch show                      # 终端里看最新概率表
.venv\Scripts\python.exe -m fedwatch show --meeting 2027-03-17 --asof 2026-09-01
.venv\Scripts\python.exe -m fedwatch daily                     # 每日任务（退出码 0 正常 / 2 部分失败 / 1 崩溃）
.venv\Scripts\python.exe -m fedwatch import                    # 只导入 CME 官方文件
.venv\Scripts\python.exe -m fedwatch recompute                 # 用库里的价格全部重算
.venv\Scripts\python.exe -m fedwatch bootstrap                 # 从头回算
.venv\Scripts\python.exe -m fedwatch serve --port 8767         # 换端口开看板
```

## 文件

```
fedwatch/
  calc.py        FedWatch 算法、终点分布、会后决议反推
  sources.py     Yahoo / 纽约联储 / 美联储官网
  pipeline.py    抓取 → 计算 → 导入
  cme_import.py  CME 官方下载文件解析
  analysis.py    看板用的查询
  store.py       SQLite（data/fedwatch.sqlite）
  server.py      本地看板服务
  web/index.html 看板页面
config.yaml      配置
daily_fetch.bat  计划任务入口（失败弹 Windows 通知，日志在 logs/）
```

## GitHub 上的相关项目（参考）

- [ARahimiQuant/pyfedwatch](https://github.com/ARahimiQuant/pyfedwatch)：FedWatch 方法的 Python 实现，要自己准备期货价格
- [tjdwls101010/CME-FedWatch](https://github.com/tjdwls101010/CME-FedWatch)（`pip install cme-fedwatch`）：直接调 CME 结算价接口，但只能拿到最近约 5 个交易日
- [zuowood1234/cme-fedwatch-tracker](https://github.com/zuowood1234/cme-fedwatch-tracker)：用浏览器自动化抓 CME 页面，每日存档（2026-07 起），本项目的校验用了它的存档
- [exclusivezhang/FedWatch](https://github.com/exclusivezhang/FedWatch)：2025 年 7、9、10、12 月四次会议的 CME 官方历史导出，其中 `poly_fed_data/FedWatch_*.csv` 可以直接放进 `data/cme_downloads` 导入（测过 10 月、12 月两份）
