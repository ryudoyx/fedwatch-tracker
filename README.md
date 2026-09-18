# FedWatch 跟踪器

把 CME FedWatch 的"各次 FOMC 会议后目标区间概率"每天存下来，看**同一次会议（比如 2027 年 3 月）的概率在不同观察日怎么变**，以及**加息终点**的分布怎么移动。

概率用 30 天联邦基金期货（ZQ）价格、按 CME 公布的 FedWatch 方法自己算，和 CME 页面同一口径。

## 日常使用

| 做什么 | 怎么做 |
|---|---|
| 每天早上 8 点自动抓 + 弹通知 | 双击一次 `设置每日推送.bat`（只需设置一次） |
| 打开看板 | 双击 `启动看板.bat` → http://127.0.0.1:8766 |
| 手动抓最新 | 看板右上角「抓最新数据」 |
| 补一年官方历史 | 见下面「导入 CME 官方历史」 |
| 换台电脑从头装 | 双击 `安装依赖.bat`（建环境 + 回算历史，约 1 分钟） |
| 在线看（手机也能看） | 部署一次到 GitHub，见下面「挂到 GitHub」 |

## 每天早上 8 点的推送

美股那一场北京时间凌晨四五点收盘，8 点数据已经定了。定时任务会抓数、重算，然后弹一条 Windows 通知，例如：

```
FedWatch 9/17：10/28 会议 加息 55%（+7pp），不变 45%
  现行 3.75–4.00%（9/16 决议按期货反推）
  10/28 会议：加息 55%（+7pp），不变 45%
  27年3月：最可能 4.25–4.50% 37%（持平）
  加息终点（到27年12月）：期望 4.49%（-6bp），最可能 4.50–4.75% 33%
  最大变动：26年10月 4.00–4.25% +6.6pp
```

- 括号里都是**和上一个观察日比**的变化。
- 盯哪几次会议、多大的变动才点出来，在 `config.yaml` 的 `digest` 里改（默认重点看 2027-03-17，单日变动 ≥3 个百分点算"最大变动"）。
- 完整的逐日变化表写在 `logs/digest_YYYY-MM-DD.txt`，看板顶部也有同一份「今日摘要」。
- 周末没有新数据就不弹；同一个观察日只弹一次（手动重跑不会重复打扰）。
- 抓取出问题会弹另一条警告，日志在 `logs/daily_YYYYMM.log`。
- 电脑 8 点没开机或在睡眠，任务会在开机后尽快补跑。
- 只想看看通知长什么样：`powershell -ExecutionPolicy Bypass -File notify.ps1 -Kind test`

## 看板

- **今日摘要**：同上，和早上的通知一致。
- **单次会议概率**：选一次会议，横轴是观察日，看每个目标区间的概率怎么变（堆叠 / 折线），时间轴上标了 FOMC 会议日；下方是期望利率走势和"最新 vs 1天/1周/1月前"对比表（同 CME 的 Current / 1 Day / 1 Week / 1 Month）。
- **加息终点**：沿 FedWatch 的概率树，统计"到某次会议为止，路径上加到的最高区间"的分布随时间怎么变；也可切到降息终点（最低点）。
- **利率路径**：最新、1 周前、1 月前的"每次会议后期望利率"曲线叠在一起，看整条路径怎么平移。
- **全表**：任选一个观察日，会议 × 区间的概率矩阵（同 CME 的 Probabilities 页）。
- **数据与方法**：数据状态、导入记录、算法说明。

网址会记住当前视图，可以收藏，例如 `http://127.0.0.1:8766/#tab=meeting&meeting=2027-03-17`。

## 数据来源和限制

| 数据 | 来源 | 说明 |
|---|---|---|
| ZQ 日收盘（历史） | Yahoo Finance（`ZQH27.CBT`） | 未到期合约约 2 年历史；到期即查不到 |
| ZQ 日收盘（最近两天） | TradingView 行情筛选接口 | 只有最近两个完整交易日，但拿到的就是结算价，合约列到约 3 年后 |
| EFFR、目标区间 | 纽约联储 Markets API | 官方，晚一天发布 |
| FOMC 日程 | 美联储官网 | 内置 2024–2027，每次运行尝试补新年份 |
| 官方概率历史 | CME FedWatch 页面手动下载 | 每次会议往前一年 |

- 都是不需要登录的公开接口；CME 自己的结算价接口有反爬，不用，也不做浏览器指纹伪装。
- **为什么两个源一起用**：Yahoo 的日线收盘价要到美国当地半夜才修正成结算价，早上 8 点抓到的还是盘中最后成交价（2026-09-16 那天差 2bp，算出的概率差 1–2 个百分点）。TradingView 收盘后就给结算价，所以最近两天以它为准，更早的历史用 Yahoo。修正完成后两边数值一致。
- Yahoo 在国内直连返回 403，程序跟随 Windows 系统代理，抓取时代理要开着。
- **自算历史从 2026-07-29 开始**：更早的日子要用到已经到期的合约，免费源查不到。往后每天累积。
- **会议刚开完的一两天**：纽约联储还没更新区间，程序用当月合约反推决议（例如 9/16 反推为加息 25bp），看板顶部和摘要里都会注明；官方数据一到自动改用。万一反推错了，在 `config.yaml` 填 `target_override: [下限, 上限]`。
- 原始数据每天顺手导出一份 CSV 到 `data/archive/`，数据库坏了可以从它重建（`python -m fedwatch recompute`）。

## 导入 CME 官方历史

1. 打开 https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html
2. 在工具上方选中会议（标签形如 `17 Mar27`）
3. 左侧「Historical」框 →「Downloads」，下载 Excel
4. 文件名最好带会议日期（如 `FedWatch_20270317.xlsx`），放进 `data/cme_downloads/`，点看板上的「导入官方文件」（每天的定时任务也会自动导入）
5. 看板上把数据源切到「CME 官方导入」

想看官方数据的「加息终点」，观察期内每次会议都要下载（中间缺一次会议，那天就算不了）。导入失败的文件会在「数据与方法」里写原因，改好后自动重试。

## 算法

照 CME 的[方法说明](https://www.cmegroup.com/articles/2023/understanding-the-cme-group-fedwatch-tool-methodology.html)：

1. 合约隐含月均 EFFR = 100 − 价格
2. 没会议的整月是"锚月"：均值 = 上月月末利率 = 下月月初利率（当前月不当锚）
3. 会议月：均值 = 月初 × N/D + 月末 × M/D（N = 1 号到决议日的天数），已知一端反解另一端
4. 锚月往前一路倒推、往后只推一个月（后一个月是锚 → 倒推；否则前一个月是锚 → 正推；否则接下个会议月的月初）
5. 每次会议预期变动 ÷ 25bp，整数部分和小数部分拆成相邻两个结果的概率，逐次会议卷积出累积分布

**校验**：拿网上存档的 51 个 CME 官方快照（2026-07-13 ~ 09-10）逐日对比，每次会议各区间最大偏差的中位数 **1.2 个百分点**（75 分位 2.5、90 分位 5.2）。差异主要来自价格时点：这里用日收盘价，CME 页面用盘中实时价。换成"一律倒推"的传播方式，偏差变大（中位数 1.7、75 分位 3.1），用快照里自带的会议月价格对比时差距更明显（90 分位 8.7 对 17.6），说明第 4 步的方向规则和 CME 一致。

**加息终点**：同一棵概率树上，逐条路径记下到达过的最高区间（含现行区间），假设各次会议独立（和 FedWatch 同一假设）。"预期路径高点"是先对每次会议取期望再取最大；前者不会低于后者，两者重合说明高点之前没有定价降息。

一年以后的合约成交清淡，远期会议的概率日间抖动会比近月大。

## 命令行

```
python -m fedwatch show                                  # 终端里看最新概率表
python -m fedwatch show --meeting 2027-03-17 --asof 2026-09-01
python -m fedwatch digest --full                         # 今天的摘要（定时任务用的就是这个）
python -m fedwatch daily                                 # 每日任务（退出码 0 正常 / 2 部分失败 / 1 崩溃）
python -m fedwatch import                                # 只导入 CME 官方文件
python -m fedwatch recompute                             # 用库里的价格全部重算
python -m fedwatch bootstrap                             # 从 Yahoo 回算历史
python -m fedwatch serve --port 8767                     # 换端口开看板
python -m fedwatch export-site _site                     # 导出成静态网页（不需要本地服务也能看）
```

## 文件

```
fedwatch/
  calc.py        FedWatch 算法、终点分布、会后决议反推
  sources.py     Yahoo / TradingView / 纽约联储 / 美联储官网
  pipeline.py    抓取 → 计算 → 导入
  digest.py      每天早上的摘要
  archive.py     CSV 存档读写
  site.py        导出静态网页
  cme_import.py  CME 官方下载文件解析
  analysis.py    看板用的查询
  store.py       SQLite（data/fedwatch.sqlite）
  server.py      本地看板服务
  web/index.html 看板页面
daily_fetch.bat  定时任务入口（抓数 → 摘要通知；失败弹警告，日志在 logs/）
```

## 挂到 GitHub

`.github/workflows/fedwatch.yml` 每个交易日跑一次（23:47 UTC = 北京 07:47）：读 `data/archive/*.csv` → 抓 TradingView 和纽约联储 → 写回 CSV 提交 → 全部重算 → 发布静态网页到 GitHub Pages。仓库里只存 CSV 文本，数据库不进仓库。

部署（只做一次）：

1. 在 GitHub 新建一个 **Public** 空仓库（Pages 免费版要求公开）
2. `git remote add origin https://github.com/<你>/<仓库>.git` 然后 `git push -u origin main`
3. 仓库 Settings → Pages → Build and deployment → Source 选 **GitHub Actions**
4. Actions → fedwatch → Run workflow 跑一次，几分钟后网页在 `https://<你>.github.io/<仓库>/`

之后：

- 上传 CME 官方下载：仓库里进 `data/cme_downloads/` → Add file → Upload files，提交后自动导入并更新网页
- 手动跑一次：Actions → fedwatch → Run workflow
- 抓取出问题时网页照样用已有数据发布，但那一轮会标红，GitHub 会发邮件
- 云端用 TradingView 取价：GitHub 的服务器访问 Yahoo 会被 429 拒掉（本项目不做浏览器指纹伪装那一套绕过）
- 本机的 8 点推送和云端互不影响，各抓各的

## 相关项目

- [ARahimiQuant/pyfedwatch](https://github.com/ARahimiQuant/pyfedwatch)：FedWatch 方法的 Python 实现，要自己准备期货价格
- [tjdwls101010/CME-FedWatch](https://github.com/tjdwls101010/CME-FedWatch)：调 CME 结算价接口，只能拿到最近约 5 个交易日
- [zuowood1234/cme-fedwatch-tracker](https://github.com/zuowood1234/cme-fedwatch-tracker)：每日存档 CME 页面上的官方概率（2026-07 起），本项目的校验用了它的存档
- [exclusivezhang/FedWatch](https://github.com/exclusivezhang/FedWatch)：2025 年几次会议的 CME 官方历史导出，`poly_fed_data/FedWatch_*.csv` 可以直接放进 `data/cme_downloads` 导入
