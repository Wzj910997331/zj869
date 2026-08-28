# 编排日志（Journal）

> 追加式动作日志：每次关键动作记录一行（时间 / 动作 / 结果）。

| 时间 | 动作 | 结果 |
|---|---|---|
| 08-28 15:07 | 彩种切换：七星彩 → 排列5（用户指示） | 方案文档重写为 `docs/排列5M1方案.md` |
| 08-28 15:07 | 排列5历史数据源验证 | 500彩票网 `inc/history.php?limit=730` = 730期（2026-08-27~2024-07-31）✅ |
| 08-28 15:07 | 数据源排查 | 排除：体彩官方 webapi（gameNo 1-99 无排列5）、akshare 1.18.88（已移除彩票模块）、澳客/新浪/乐彩/中彩网（404） |
| 08-28 15:2x | gouli99 站点逆向 | SPA(Vue)，API=wsqdata.gouli8.cn `v2/*`；`feeds/rank`/`getNewestSerial` 公开可用但有限流；登录 `v2/user/gaolatUserDefaultLogin` 需签名（HmacMD5，待逆向）；账号凭据已落盘 secrets/gouli99.txt |
| 08-28 15:2x | 派 2 个子代理调研 GitHub | ① 彩票爬虫经验 ② SPA登录+图片识别方案（运行中） |
| 08-28 15:2x | 容器安装 Playwright+Chromium | 后台进行中（无头浏览器方案，绕过签名逆向） |
| 08-28 15:3x | 视觉模型机制验证 | 主模型 deepseek-v4-flash 不支持图片；workflow 子代理 + `{provider:deepseek1, model:deepseek-v4-flash-vision-exp}` 可读图（read_image 实测准确） |
| 08-28 15:3x | 子代理B调研完成（SPA登录+图片识别） | 结论：Playwright 登录取会话(storage_state)→拦截XHR找JSON API→requests批量；图片两阶段（PaddleOCR数字+VL语义）；参考 repo：hello-lottery/bilibili_comment_scraper_webui/Redbook-Search-Comment-MCP2.0/LlmOcr/lottery-crawler |
| 08-28 15:3x | 子代理A调研中（彩票爬虫经验） | 运行中 |
| 08-28 15:3x | 方案文档新增 M1b 详细设计 | 爬虫（Playwright+storage_state+拦截XHR）+ 识别（切图/PaddleOCR可选+视觉模型）写入 `docs/排列5M1方案.md` §7.5 |
| 08-28 15:0x | akshare 安装验证 | 1.18.88 装好但无 lottery 函数，弃用 |
| 08-28 14:52 | 建立 .dev 监控机制 | 编排状态落盘，恢复指引写入 ORCHESTRATOR.md |
| 08-28 14:4x | 编写开发指南 docs/DEV_GUIDE.md | 已提交 `6847467` |
| 08-28 14:4x | 容器环境配置：venv、git 身份、GitHub SSH 密钥 | 完成，提交 `d754322`（.gitignore 加 .venv/） |
| 08-28 14:4x | 修复 GBK 编码崩溃（✗/⚠️/💡 → 兼容写法） | main.py 容器内跑通 exit 0 |
| 08-28 14:3x | 部署到服务器 zhenjie 容器 /data/zhenjie/zj869 | 解压 67 文件，容器内验证通过 |
| 08-28 14:2x | 首次提交并推送 GitHub https://github.com/Wzj910997331/zj869.git | `d31499e first commit` |
| 08-28 14:2x | 解压整理工程到仓库根 | 删除重复散文件/zip，重写 README |
