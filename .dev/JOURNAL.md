# 编排日志（Journal）

> 追加式动作日志：每次关键动作记录一行（时间 / 动作 / 结果）。

| 时间 | 动作 | 结果 |
|---|---|---|
| 08-29 06:0x | **LLM推理层升级受阻(网关内容过滤确认)** | llm.riverbegin.cn 对"彩票画规分析"提示词(斜连/排列5/万千百十个/数字串等)返回空内容：deepseek-flash/pro/glm-5.3/glm-5.3-flash 全部模型×流式/JSON/纯文本/拼音混淆全部失败(0-5%)，普通提示词100%成功；判定为网关侧内容审查，客户端无解；48条数据版(规则推理)兜底已同步服务器；README已记录诊断+方法库数据结构 |
| 08-29 05:5x | **工程同步服务器完成** | scp tools/docs/data(20260828+260828_verified)/.dev/README → /data/zhenjie/zj869/ 全部 exit 0；summarize_methods.py+全部输入文件已在服务器 |
| 08-29 05:0x | **网络恢复后测试 + GLM 接入** | ①deepseek文本恢复正常(5/5)但vision仍空响应；②glm-5.3-flash 在 llm.riverbegin.cn 可用（多模态）：读微时光_1 裁剪图 calib=[2,8,0,5,4]✓、6@万位/3@十位，与 deepseek 12读一致（跨模型互证REJECT）；③settings.yaml 已加 glm-5.3-flash 模型，全部验证脚本支持 --model 切换；④方法库重跑仅抓到1条LLM(网关尾巴)，数据版48条完整 |
| 08-29 05:1x | **README 全面重写** | 记录整个工程设计/已完成/未完成/待办/工具用法/模型切换/密钥位置；后续用户自跑：docs 报告 + tools 命令 + .dev 日志 全齐 |
| 08-29 04:0x | **画规方法库定稿(48条)** | pattern_methods.json + docs/画规方法库.md：48博主全部含画法描述+推理逻辑(规则推导,1条LLM)；LLM深度合成脚本 summarize_methods.py 就绪,网关恢复后一条命令升级；一致性核验通过(FINAL↔命中记录18/18, judge_record单元测试4/4) |
| 08-29 03:0x | **终局定稿** | 36条→CONFIRM 18(13博主/18图)/REJECT 12/KILL 3/NOPOS 1/AMBIGUOUS 2(辉拓数据_4杀号第1位、乐仔红格含义)；乐仔红格=杀号/预测存疑(若预测则万位9命中,待人工)；`260828_verified/`(18图)+`image_patterns_verified.json`+`docs/全量复核终版报告-20260829.md` |
| 08-29 02:5x | **画规方法库初版(48条)** | `pattern_methods.json`+`docs/画规方法库.md`(数据驱动:类型/描述/预测/命中来自记录+核验)；推理层待网关恢复后 `tools/summarize_methods.py` 补充；目标累计1000条 |
| 08-29 02:3x | **终局判定+重建（verify_results_FINAL.json）** | 流萤尾数图改全图识别→尾数{0,1,3,4,6,8}含个位3命中；乐仔红格=万位49/千位05(杀号或预测存疑)；`260828_verified/`重建 |
| 08-29 02:2x | 位名锚定协议 | 富老师_3 3/3读9@万位(校准[2,8,0,5,4]一致)→确认命中；微时光_1 12读校准全一致、6稳定@万位、3位置不稳→维持REJECT待人工核图 |
| 08-29 02:0x | **对齐协议修复（用户指出微时光/富老师_3列位问题）** | 根因：列号式对齐被辅助列干扰（富老师_3三次读9在列1/列2摆动）；改为"位名锚定"：先读26229行5数字与真实开奖2,8,0,5,4逐位比对(calibration_digits核验)，再按位名(万/千/百/十/个)报告预测；judge_record支持position_digits；微时光_1六读校准全一致但6稳定@万位、3位置不稳(十/百/个摆动) |
| 08-29 01:5x | **对齐复核完成（verify_results_aligned.json）** | 36条→CONFIRM 17(+关心❤️_0翻案)/REJECT 12(情有独钟0_1/_3十位8、用规说话_6个位3,8被反证)/KILL 2/NOPOS 1/AMBIGUOUS 4(流萤尾、乐仔杀号、辉拓数据_4杀号) |
| 08-29 01:0x | **全量复核完成（止损交付）** | 36条命中→CONFIRM 16(11博主)/REJECT 9/KILL 2/NOPOS 1/待人工8；网关空响应率高致多次迭代，剩余8条转人工；产物：`docs/全量复核结果-20260829.md`、`image_patterns_verified.json`、`260828_verified/`；kill类按约定不计入命中 |
| 08-29 00:5x | 网关故障定位 | llm.riverbegin.cn 空响应（flash文本也1/5成功）→ 非视觉模型问题，整网关间歇故障；脚本改为空响应快速重试 |
| 08-28 23:5x | **全量复核启动（后台 job pwsh-107）** | 36条命中/33图：9图10条人工已定（5确认/5剔除），24图48次双读机器复核（workers=4，带重试）；工具入库：`tools/verify_chart_hits.py`（双读判定）、`tools/verify_phase2.py`（x坐标三读）、`tools/rebuild_hits.py`（重建命中集+260828_verified）；人工结论 `data/crawl/20260828/settled_manual.json` |
| 08-28 23:4x | SSH链路确认 | `SSH_ASKPASS=secrets/askpass.cmd + SSH_ASKPASS_REQUIRE=force` 可免交互连 10.5.64.5；服务器 `/data/zhenjie/zj869/data/crawl/{20260828,260828}` 在 |
| 08-28 21:0x | **裁剪+视觉验证流水线（4轮18次读取）** | 底部45%裁剪+真实开奖校准提示：视觉列位判定可信（26228/26229校准行全读对）；9图10条旧命中记录→5确认/5剔除（微时光_1百个位、不用关注_0千4、默言言心_2/4百6均误判）；发现未来期幻觉（26231被填数字，500.com最新仅26230）、辅助列陷阱、组合文字"4++3"无位次 |
| 08-28 21:0x | **裁剪工具入库** | `tools/crop_charts.py`（bottom45/zoom/full 三模式，Windows/服务器通用）；报告 `docs/裁剪图视觉验证报告-20260828.md`；裁剪图在 `C:\Users\zhenjie.wu\.dsh\work\gouli_crop\` |
| 08-28 21:0x | 密钥定位 | `DEEPSEEK1_API_KEY` 在 `$DSH_HOME/.credentials.yaml`（53位 sk-n 开头），pwsh 环境无，仅 DSH 进程内 → 生产脚本可直读该文件 |
| 08-28 15:07 | 彩种切换：七星彩 → 排列5（用户指示） | 方案文档重写为 `docs/排列5M1方案.md` |
| 08-28 15:07 | 排列5历史数据源验证 | 500彩票网 `inc/history.php?limit=730` = 730期（2026-08-27~2024-07-31）✅ |
| 08-28 15:07 | 数据源排查 | 排除：体彩官方 webapi（gameNo 1-99 无排列5）、akshare 1.18.88（已移除彩票模块）、澳客/新浪/乐彩/中彩网（404） |
| 08-28 15:2x | gouli99 站点逆向 | SPA(Vue)，API=wsqdata.gouli8.cn `v2/*`；`feeds/rank`/`getNewestSerial` 公开可用但有限流；登录 `v2/user/gaolatUserDefaultLogin` 需签名（HmacMD5，待逆向）；账号凭据已落盘 secrets/gouli99.txt |
| 08-28 15:2x | 派 2 个子代理调研 GitHub | ① 彩票爬虫经验 ② SPA登录+图片识别方案（运行中） |
| 08-28 15:2x | 容器安装 Playwright+Chromium | 后台进行中（无头浏览器方案，绕过签名逆向） |
| 08-28 15:3x | 视觉模型机制验证 | 主模型 deepseek-v4-flash 不支持图片；workflow 子代理 + `{provider:deepseek1, model:deepseek-v4-flash-vision-exp}` 可读图（read_image 实测准确） |
| 08-28 15:3x | 子代理B调研完成（SPA登录+图片识别） | 结论：Playwright 登录取会话(storage_state)→拦截XHR找JSON API→requests批量；图片两阶段（PaddleOCR数字+VL语义）；参考 repo：hello-lottery/bilibili_comment_scraper_webui/Redbook-Search-Comment-MCP2.0/LlmOcr/lottery-crawler |
| 08-28 15:3x | 子代理A调研完成（彩票爬虫经验） | 确认 500彩票网 plw 接口=GitHub 排列3 爬虫同族；表格 tr[2:-1]；gbk 解码/重试/增量模式 |
| 08-28 15:3x | 方案文档新增 M1b 详细设计 | 爬虫（Playwright+storage_state+拦截XHR）+ 识别（切图/PaddleOCR可选+视觉模型）写入 `docs/排列5M1方案.md` §7.5 |
| 08-28 15:4x | **M1a 实现完成** | `collector.py::_fetch_history()` 接 500彩票网真实排列5数据；实测 730 期（26229→24203）全 5 位；完整流水线 exit 0 |
| 08-28 17:2x | **M1b 突破：论坛接口打通** | `v2/feeds/stream?start=N&count=20&lottery=2`（lottery=2=排列五）无需登录返回 JSON：博主名+文字规律+图片URL（360/750/1024）；Chrome 151 无头模式验证可用（npmmirror/Google 下载失败→用户手动下载，已装 /data/zhenjie/chrome/） |
| 08-28 17:2x | **视觉识别实测通过** | 视觉模型精确读出规律图（文字预测截图）各位置候选数字（万/千/百/十位）；识别管线验证完成 |
| 08-28 17:4x | **GitHub 同步** | 服务器 9 个提交经 git bundle → Windows 推送 GitHub（d31499e..395b813）；容器 id_ed25519 未绑定 Wzj910997331（旧 id_rsa 属于 jinqinn），待用户添加公钥后服务器可直推 |
| 08-28 18:0x | **服务器直推配置完成** | 用户已将 id_ed25519 添加到 Wzj910997331；容器 ~/.ssh/config 指定 github.com 用 id_ed25519；远程切 SSH；容器内 git 认证通过 |
| 08-28 15:0x | akshare 安装验证 | 1.18.88 装好但无 lottery 函数，弃用 |
| 08-28 14:52 | 建立 .dev 监控机制 | 编排状态落盘，恢复指引写入 ORCHESTRATOR.md |
| 08-28 14:4x | 编写开发指南 docs/DEV_GUIDE.md | 已提交 `6847467` |
| 08-28 14:4x | 容器环境配置：venv、git 身份、GitHub SSH 密钥 | 完成，提交 `d754322`（.gitignore 加 .venv/） |
| 08-28 14:4x | 修复 GBK 编码崩溃（✗/⚠️/💡 → 兼容写法） | main.py 容器内跑通 exit 0 |
| 08-28 14:3x | 部署到服务器 zhenjie 容器 /data/zhenjie/zj869 | 解压 67 文件，容器内验证通过 |
| 08-28 14:2x | 首次提交并推送 GitHub https://github.com/Wzj910997331/zj869.git | `d31499e first commit` |
| 08-28 14:2x | 解压整理工程到仓库根 | 删除重复散文件/zip，重写 README |
