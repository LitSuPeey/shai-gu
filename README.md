# 筛股 Screengu —— A 股多功能选股工作台

> A self-hosted, all-in-one **A-share (China) quantitative stock screener**, built with
> FastAPI + a zero-build frontend. Merges three quant-stock-picking projects into one local web app:
> **Sequoia-X** (6 classic strategies) + **筛票demo** (13-condition screener + 蚂蚁呀欸 four-layer scan) +
> **scrensto** (pattern scoring + similar-stock finder).
>
> Runs entirely on your own machine (`127.0.0.1:8765`). Data is fetched on demand and stored
> locally in SQLite — nothing is uploaded anywhere.

---

## 快速开始 Quick Start

**前提：** 安装 **Python 3.11+**（推荐 3.11/3.12/3.13，Windows 用户在安装时勾选「Add Python to PATH」）。

### 方式 A：一键启动（Windows，推荐）

双击 `start.bat` 即可。脚本会自动寻找可用的 Python 并**自动 `pip install` 全部依赖**，
无需手动预装，随后自动打开浏览器。首次运行需联网同步数据（见下文「数据」）。

### 方式 B：手动启动（任意系统）

```bat
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

浏览器打开 <http://127.0.0.1:8765/>。

> ⚠️ 请务必通过 `start.bat`（或方式 B）启动后访问 <http://127.0.0.1:8765/> ——
> 直接双击 `index.html`（`file://` 协议）会被浏览器拦截全部数据接口，功能不可用。

---

## 功能模块

- **策略选股**：6 个经典策略（海龟 / 均线放量 / 高旗形 / 涨停洗盘 / 上升跌停 / RPS），可单跑或一起跑，可自定义参数
- **条件筛选**：13 条财务/行情/资金面条件，AND 取交集，按 PE-TTM 升序
- **形态打分**：60% 形态相似 + 40% 趋势结构，自带 40 个底部+顶部样本池
- **蚂蚁呀欸**：四层流水线（硬过滤 → 形态 → 打分 → 启动标记），阈值全部可调
- **持有个1000年试试呢？**（超长周期版）：10/20/25/30 年窗口，ZigZag 识别「深蹲（≥35%）+ 翻倍反弹（≥70%）」完整循环，7 项得分；点击结果代码可看长周期月线图（对数轴 + 循环顶底标注 + 上证指数对比）
- **相似股**：a 几乎一致 / b 走势相反 / c 时移相似，对比图每类只画最像的 2 只
- **行情查看**：单只 K 线（MA5/10/20/60），点击结果表股票名称可直接跳转
- **前瞻预警**：日历事件 + 异动侦测 + 冷门板块 + 国家队资金 + 外资重仓 + 拥挤度 + 产业链传导 + 宏观雷达 + 核心信号合成，可导出独立网页报告
- **大周期**：月 K 线 + 回归通道 + 六因子趋势引擎 + 走势虚线外推（1~14 年）
- **快讯轮播**：顶部滚动财经快讯、CCI 市场情绪、伦敦金/布伦特原油、期货异动预警、关键词智能跳转
- **分红时点预测**：从本地分红历史建立「除权习惯画像」，把全市场分红查询收敛到约 1/5
- **GIF 悬浮窗**：右下角动图窗，可拖动、可收起
- **🎓 仅供学习**（B站指定 UP主 内容挖掘）：只读取你保存的 UP主本人发布的视频/动态/字幕/评论，
  提取其中出现的具体股票并保留原文片段，按权重排序。内置串行限速 + 请求硬闸 + 本地增量存档，
  不并发、不自动登录、不读取会员/充电专属内容。可选用免费的**本地离线**语音转写引擎（faster-whisper
  或 funasr）补全视频文稿，音频与文稿只存本机、不上传。

所有模块都支持先选股票范围（全部 / 沪 / 深 / 北），再选板块，再跑筛选。范围与板块随时可改。

---

## 数据

> ⚠️ **关于数据库与 Git：** `data/unified_data.db` 约 1.3GB，**超过 GitHub 单文件 100MB 上限**，
> 因此已被 `.gitignore` 排除，克隆仓库后默认**不含数据库**。请按下方方式之一获取后放入 `data/`。

**方式一（推荐，最快）：下载预构建数据库**
从本项目的 GitHub Release 页面（或作者提供的网盘链接）下载 `unified_data.db`，放入 `data/` 目录。

**方式二：从零同步（约 30 分钟，仅一次）**
启动程序后，按网页引导三步：

1. 点 **「刷新信息」** —— 拉取全市场股票列表与板块映射（约 1 分钟）
2. 点 **「同步数据」** —— 按所选范围拉取日线 / 估值 / 分红（沪深京全部约 30 分钟，可先选小范围）
3. 同步完成后快照表自动后台构建

此后点 **「⬇ 同步数据」** 即可**增量补齐**最新行情（几分钟内完成）。数据留存于本地 `data/unified_data.db`，
重启不丢失。

数据源：AKShare（主）、新浪 / 腾讯 / 东财 / 百度（兜底）。同步需要联网。

---

## 目录结构

```
.
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── api/routes.py        # 所有 REST 接口
│   ├── core/                # db / pool / ranges / sync / snapshot / taskctl / trade_cal
│   ├── modules/             # conditions / strategies / pattern / ant / ant1000 / similar
│   │                        # alert / cycle / divtiming / bili / ticker / futures
│   └── web/                 # index.html + style.css + app.js（前端零构建）
│       ├── memes/           # 表情包（版权见 THIRD_PARTY_NOTICES.md）
│       └── gifs/            # 悬浮窗动图
├── data/                    # 数据库目录（首次运行自动生成 unified_data.db）
├── scripts/
│   ├── import_data.py       # 可选：从旧版 stock_data.db 迁移存量数据
│   └── repair_daily_amount.py  # 可选：日K 成交量/成交额单位自洽修复工具
├── start.bat                # 一键启动（Windows）
├── requirements.txt         # 依赖清单
├── LICENSE                  # MIT
├── THIRD_PARTY_NOTICES.md   # 第三方开源软件声明
└── README.md
```

---

## 许可证 License

本项目代码依据 **MIT License** 发布，详见 [`LICENSE`](./LICENSE)。

本项目使用了多个第三方开源软件，各自的许可证见 [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md)。
其中请特别注意：**`app/web/memes/` 与 `app/web/gifs/` 内的表情包/动图为网络或游戏内素材，
版权归原作者所有，不属于 MIT 许可范围，严禁商用。** 数据源数据仅供学习研究。

---

## 免责声明 Disclaimer

**本工具仅供学习研究使用，不构成任何投资建议。** 投资有风险，入市需谨慎。

表情包来源：明日方舟游戏内表情包以及网络搜索得到，表情包严禁商用，如有侵权请与我联系。
