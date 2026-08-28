# 编排状态（Orchestrator State）

> 由编排者（DSH 主代理）维护，用于向人类监控者汇报工作进展与方向。
> **本文件 + JOURNAL.md + git 提交 = 会话断点恢复的唯一可靠依据（上下文超限/新会话接管时，从这里接手）。**

---

## ⚠️ 恢复指引（新会话/新代理接手必读）

1. 读本文件（当前目标/阶段/关键事实）→ `.dev/JOURNAL.md`（历史动作）→ `docs/排列5M1方案.md`（当前方案）→ `README.md` + `docs/DEV_GUIDE.md`（架构）
2. 环境：服务器 `10.5.64.5` root，密码与 SSH 免交互脚本在 Windows 本地 `C:\Users\zhenjie.wu\.dsh\secrets\`（**禁止进仓库**）；GitHub 凭据走系统 GCM
3. 工程位置：服务器 zhenjie 容器 `/data/zhenjie/zj869`（bind 挂载，宿主机同路径）；同步 GitHub `Wzj910997331/zj869`
4. 运行：`docker exec -it zhenjie bash` → `cd /data/zhenjie/zj869` → `.venv/bin/python main.py`
5. 从「下一步」继续；每完成一步，回写本文件 + 日志并 git 提交

---

## 当前目标

**排列5 规律预测系统**（用户 2026-08-28 定，彩种由七星彩改为排列5）：
采集排列5近2年历史 + 博主规律 → 多Agent框架分析/决策/预测。

## 当前阶段

- [x] 工程整理、GitHub 首次提交、部署服务器 zhenjie 容器
- [x] 容器开发环境（venv/git 身份/SSH 密钥）
- [x] 开发指南 `docs/DEV_GUIDE.md`
- [x] **历史数据源验证**：500彩票网 `inc/history.php?limit=730` = 2年730期 ✅（详见方案文档）
- [x] **M1a 实现**：`collector.py::_fetch_history()` 接 500 彩票网真实排列5数据，730期实测跑通，全流水线 exit 0 ✅
- [x] **M1b 接口打通**：gouli99 论坛 API `v2/feeds/stream?lottery=2`（排列五）无需登录返回 JSON；Chrome 151 无头可用；视觉识别实测可读出规律图数字 ✅
- [ ] M1b 爬虫实现：分页抓帖 + 下载图片 + 登录（VIP内容）
- [ ] M1c 视觉识别批量管线 → 结构化入库
- [ ] M2 规律分类+回测
- [ ] M3 决策闭环

## 关键事实（省得重新踩坑）

| 项 | 结论 |
|---|---|
| 排列5历史数据 | 500彩票网 `https://datachart.500.com/plw/history/inc/history.php?limit=N`，UA+Referer，gb2312，`<tr class="t_tr1">`；limit=730 一次拉 2 年 |
| 不可用源 | 体彩官方 webapi（无排列5）、akshare 1.18.88（无彩票模块）、500.com 其他路径、澳客/新浪/乐彩/中彩网 |
| gouli99 论坛 | SPA(Vue)，API=`wsqdata.gouli8.cn/v2/*`（`feeds/rank` 公开但限流；登录 `v2/user/gaolatUserDefaultLogin` 需 HmacMD5 签名；账号 17806707872 在 Windows 本机 `secrets/gouli99.txt`） |
| 容器网络 | 外网通（pypi/500.com/wsqdata）；apt 源 repos.riverbegin.com 不可达；`/root` 未挂载进容器，脚本放 `/home` |
| 容器环境 | zhenjie 容器：Python 3.10.12 + venv `.venv`；Claude Code 2.1.224（`/usr/local/bin/claude`） |
| 编码坑 | 500.com 页面 gb2312；本框架 print 已修复 GBK 兼容 |

## 活跃编排动作（本轮）

- 已完成：数据源探测与验证（排列5 730期打通）
- 待开始：M1a 采集实现（等用户确认节奏）或 M1b（等用户给网站 URL）

## 子代理 / 并行任务

- 暂无

## 最近汇报（最新在上）

- **2026-08-28 15:07**：彩种切排列5；500彩票网数据源验证通过（730期/2年）；akshare 与官方 webapi 排除；方案文档更新为 `docs/排列5M1方案.md`
- **2026-08-28 14:52**：确立「编排状态落盘 + 里程碑汇报」监控机制；人类监控编排者，编排者向人类汇报

## 人类监控方式

- 服务器：`cat /data/zhenjie/zj869/.dev/ORCHESTRATOR.md`
- GitHub：仓库 `.dev/` 目录（已提交）
- 对话：里程碑时主动汇报 + 更新本文件
