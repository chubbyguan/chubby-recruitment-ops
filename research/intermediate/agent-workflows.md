# GitHub 同类招聘工作流专项研究：Agent、定时批处理与候选人寻访

研究日期：2026-07-24
研究范围：

- [`andrew-shwetzer/recruiter-plugin`](https://github.com/andrew-shwetzer/recruiter-plugin)
- [`anandanair/job-scraper`](https://github.com/anandanair/job-scraper)
- [`Snseam/boss-zhipin-mcp`](https://github.com/Snseam/boss-zhipin-mcp)

方法：只使用上述 GitHub 仓库、GitHub API/Issues 与 LinkedIn、BOSS 直聘官方条款。代码证据固定到本次检查的 commit，避免 `main` 后续变化导致结论漂移。没有运行这些项目，也没有使用真实平台账号做端到端验收，因此“可运行性”仅基于仓库结构、代码与公开 Issue 判断。

## 结论先行

这三个项目都不是可以直接合并进 `chubby-recruitment-ops` 的“现成升级包”：

1. `recruiter-plugin` 最值得借鉴的是产品形态：分层初始化、单命令完整工作流、候选人流水线、邮件只建草稿。但仓库没有开放许可证，插件清单反而写的是 `proprietary`；安装脚本、ATS MCP 与 CRM 宣称之间还存在明显断层。**只能借鉴思路，不能复制代码。**
2. `job-scraper` 是三者中唯一有规范 MIT `LICENSE` 的项目。它面向求职者抓职位，不是企业招聘候选人，但其 **GitHub Actions 定时编排、限额批处理、速率限制、指数退避、模型抽象、Pydantic 数据结构** 值得作为工程模式参考。其评分只输出一个整数，没有证据解释；还会把姓名、邮箱、电话等简历信息一起送入外部 LLM，不能照搬。
3. `boss-zhipin-mcp` 的多关键词批量搜索、跨关键词去重、逐关键词落盘、中断恢复和状态查询很有启发，但实现依赖登录态浏览器、反检测、Canvas 截图、二维码解码和自动打招呼。它还用年龄做硬筛选。BOSS 官方协议公开版本明确把爬虫、拟人程序及规避技术措施的非正常浏览列为“非法获取”方式。**平台自动化部分不应进入我们的插件。**

最值得投入的升级方向不是“再做一个爬虫”，而是把现有邮件招聘闭环增强为：

- 可恢复的批处理运行器；
- 有证据、有置信度、有缺失项的评分卡；
- 有允许迁移规则的候选人状态机；
- 正式的审计事件日志；
- 外部写操作统一审批；
- 只接官方 API、企业授权导出或候选人主动投递的 sourcing 入口。

## 一、仓库健康度与许可证

| 仓库 | 本次固定 commit | 维护信号 | License | 判断 |
|---|---|---|---|---|
| `andrew-shwetzer/recruiter-plugin` | [`03b03f8`](https://github.com/andrew-shwetzer/recruiter-plugin/tree/03b03f82883f176ae2057814fbf0c34e895375a8) | 2026-03-25 创建，最后 push 2026-04-28；5 个 commit；无 tag/release、无测试、无 Issue | GitHub API 未识别许可证，仓库无 `LICENSE`；[`plugin.json`](https://github.com/andrew-shwetzer/recruiter-plugin/blob/03b03f82883f176ae2057814fbf0c34e895375a8/.claude-plugin/plugin.json#L1-L12) 明写 `proprietary` | 不应称为可复用开源代码；只能做产品参考 |
| `anandanair/job-scraper` | [`e08e91d`](https://github.com/anandanair/job-scraper/tree/e08e91d1f55f2dc072b4e9a456ef072f9de6ea3e) | 2025-05-03 创建，最后 push 2026-03-10；约 53 个 commit；无 tag/release、无测试；5 个 Actions 中 1 个因 inactivity disabled | 正式 [MIT LICENSE](https://github.com/anandanair/job-scraper/blob/e08e91d1f55f2dc072b4e9a456ef072f9de6ea3e/LICENSE) | 可在保留版权和许可证的前提下复用代码；但业务方向相反，宜借鉴工程模式 |
| `Snseam/boss-zhipin-mcp` | [`06a1a7d`](https://github.com/Snseam/boss-zhipin-mcp/tree/06a1a7d804aa80131a066bddc1879ac4bc72f841) | 2026-03-25 创建，最后 push 2026-03-31；9 个 commit；无 tag/release、无测试；3 个 Issue 仍开放，包括[无法登录](https://github.com/Snseam/boss-zhipin-mcp/issues/2)和 [MCP 无法启动](https://github.com/Snseam/boss-zhipin-mcp/issues/3) | README 声称 MIT，但仓库树没有 `LICENSE`，GitHub API 也未识别许可证 | 许可证授权不足，不复制代码；运行稳定性也未经证明 |

补充判断：

- “仓库公开可见”不等于“开源”。没有明确许可证时，默认不能复制、修改和分发代码。
- 三个项目都没有测试目录或自动化测试证据，也没有 release/tag。适合作为设计输入，不适合作为生产依赖直接 vendoring。
- `job-scraper` 是唯一看得到持续修复痕迹的项目：2026-03 连续修了 Supabase、模型和工作流问题；但当前公开 Actions 里 `Daily Job Manager` 已因 inactivity 被 GitHub 标为 disabled。

## 二、`andrew-shwetzer/recruiter-plugin`

### 2.1 架构与安装方式

它采用“Claude Code 命令 + 独立 Skill + ATS MCP”三层结构：

- 22 个 Markdown commands；
- 14 个 `skills/rp-*/SKILL.md`；
- 一个 Python MCP Server，目标是 Greenhouse、Lever、Ashby；
- 本地 YAML/Markdown 文件承担 pipeline 与数据状态。

仓库证据：

- 产品能力和本地数据目录：[`README.md`](https://github.com/andrew-shwetzer/recruiter-plugin/blob/03b03f82883f176ae2057814fbf0c34e895375a8/README.md#L1-L135)
- Claude 插件清单：`.claude-plugin/plugin.json`
- ATS MCP 声明：[`.mcp.json`](https://github.com/andrew-shwetzer/recruiter-plugin/blob/03b03f82883f176ae2057814fbf0c34e895375a8/.mcp.json)
- ATS tools：[`mcp-server/recruiter_mcp/server.py`](https://github.com/andrew-shwetzer/recruiter-plugin/blob/03b03f82883f176ae2057814fbf0c34e895375a8/mcp-server/recruiter_mcp/server.py#L103-L207)

安装可信度有两个明显问题：

1. README 推荐 `curl .../install.sh | bash`，但 [`install.sh`](https://github.com/andrew-shwetzer/recruiter-plugin/blob/03b03f82883f176ae2057814fbf0c34e895375a8/install.sh#L39-L78) 假定脚本旁边已经存在 `commands/` 和 `skills/`。单独 pipe 一个脚本时这个前提不成立。
2. `install.sh` 只把 manifest、commands 和 `.mcp.json` 复制进插件缓存，没有复制 `mcp-server/`，但 `.mcp.json` 的工作目录却指向 `${PLUGIN_ROOT}/mcp-server`。另一个 [`remote-install.sh`](https://github.com/andrew-shwetzer/recruiter-plugin/blob/03b03f82883f176ae2057814fbf0c34e895375a8/remote-install.sh#L23-L69) 会 clone 仓库，但只复制 Skills，也没有安装 ATS MCP。

因此它的 README “ATS-integrated”不能当成已验证事实。对我们的启示是：`validate_package` 不能只校验文件存在，还要在干净环境实际安装并启动 MCP/连接器。

### 2.2 可借鉴能力

#### A. 初始化与连接器分层

`setup` 先收集招聘人画像、角色、地点、行业和 ICP，再逐项检查 API；`connect` 单独负责邮箱、日历、ATS、CRM。这个拆分比把所有问题塞进一个初始化 Skill 更容易排障。

证据：

- [`commands/setup.md`](https://github.com/andrew-shwetzer/recruiter-plugin/blob/03b03f82883f176ae2057814fbf0c34e895375a8/commands/setup.md)
- [`commands/connect.md`](https://github.com/andrew-shwetzer/recruiter-plugin/blob/03b03f82883f176ae2057814fbf0c34e895375a8/commands/connect.md)

建议我们吸收：

- 把当前 `recruitment-ops-setup` 内部明确拆成 `profile → source → destination → policy → acceptance` 五段；
- 每个连接器单独有 `doctor` 探针和连接状态；
- 配置完成不等于验收完成，必须保留真实写入—回读状态。

#### B. “一条命令跑完整漏斗”，但逐步降级

[`commands/workflow.md`](https://github.com/andrew-shwetzer/recruiter-plugin/blob/03b03f82883f176ae2057814fbf0c34e895375a8/commands/workflow.md#L52-L105) 给每一步输出状态，某一步没有结果时继续用已有数据。这个交互适合我们的“完整轮次”：

`岗位门禁 → 小红书内容 → 邮箱增量 → 去重 → 匹配评分 → 协作工具回写 → 日报`

建议增加：

- 统一 `run_id`；
- 每步 `pending/running/succeeded/partial/failed/skipped`；
- 失败原因、重试次数和下一步恢复指令；
- `partial` 不能被日报写成“成功完成”。

#### C. 邮件草稿优先和二次确认

[`commands/send.md`](https://github.com/andrew-shwetzer/recruiter-plugin/blob/03b03f82883f176ae2057814fbf0c34e895375a8/commands/send.md#L128-L195) 明确要求先预览、显式确认，并且 Gmail 只创建 draft，不直接发送。这一点与我们的人工审批边界一致。

建议把同一模式扩展到：

- 候选人邀约；
- 面试通知；
- ATS 提交；
- 岗位状态变化；
- 定时任务启用/停用。

#### D. Pipeline + stale 提醒

[`commands/pipeline.md`](https://github.com/andrew-shwetzer/recruiter-plugin/blob/03b03f82883f176ae2057814fbf0c34e895375a8/commands/pipeline.md#L85-L160) 用显式状态漏斗分组候选人，并标记 14 天无进展的对象；[`commands/briefing.md`](https://github.com/andrew-shwetzer/recruiter-plugin/blob/03b03f82883f176ae2057814fbf0c34e895375a8/commands/briefing.md#L107-L178) 会把市场变化、漏斗和今日动作整合成简报。

建议我们升级：

- 招聘数据表增加 `last_stage_changed_at`、`next_action_at`、`stale_reason`；
- 日报增加“今日必须处理”和“超 SLA 候选人”；
- 状态变更必须写操作者、时间、前后状态与原因。

#### E. 多维评分卡

[`skills/rp-score/SKILL.md`](https://github.com/andrew-shwetzer/recruiter-plugin/blob/03b03f82883f176ae2057814fbf0c34e895375a8/skills/rp-score/SKILL.md#L71-L229) 将匹配拆成 9 个维度，要求给具体证据、优势、缺口与定位建议。比单一总分更可审计。

可借鉴的维度：

- 职能/职责语义匹配；
- 必须技能覆盖率；
- 加分技能覆盖率；
- 相关经验深度；
- 行业/业务场景经验；
- 地点与到岗条件；
- 证据缺失与置信度。

不应照搬：

- `company_tier` 和“名校 prestige”容易形成名企、学历偏见；
- `trajectory` 对降级、空档做负面推断，缺乏证据时会误伤；
- 输出 `PASS` 不应触发自动淘汰。

### 2.3 不应引入的能力与风险

1. **许可证风险**：插件清单写 `proprietary`，不能复制其 Skill 文本、权重或 MCP 代码。
2. **第三方 LinkedIn 数据风险**：[`rp-source`](https://github.com/andrew-shwetzer/recruiter-plugin/blob/03b03f82883f176ae2057814fbf0c34e895375a8/skills/rp-source/SKILL.md#L45-L79) 通过第三方 RapidAPI 拉取 LinkedIn 人员资料，或用搜索结果拼接候选人。除非供应商能证明数据授权、目的限制、删除机制和 DPA，否则不进入生产。
3. **明文密钥**：README 明确把 API keys 保存到 `~/.recruiter-skills/config.yaml`；不符合我们的“连接器持有凭证、业务配置不持有秘密”原则。
4. **ATS 外部写缺少确认**：[`commands/submit.md`](https://github.com/andrew-shwetzer/recruiter-plugin/blob/03b03f82883f176ae2057814fbf0c34e895375a8/commands/submit.md#L227-L308) 找到岗位后直接 POST 创建候选人/申请，没有与邮件发送同等级的最后确认。
5. **CRM 宣称大于实现**：`connect.md` 会记录 HubSpot 配置，但仓库中没有 HubSpot tool/MCP；`.mcp.json` 只有 ATS Server。不能把“配置向导显示已连接”当作集成验收。
6. **审计弱**：pipeline 是可覆写的 YAML/Markdown，没有 append-only 业务事件、版本号、操作者和不可逆外部写记录。
7. **无真正定时器**：所谓 daily briefing 是手工命令，没有 cron、hook 或工作流定义。

## 三、`anandanair/job-scraper`

### 3.1 定位与架构

这是求职者侧系统：抓 LinkedIn/CareersFuture 的岗位，将个人简历结构化，与 JD 打分，生成定制简历并跟踪申请状态。它不是企业招聘候选人系统。

主要组件：

- Python 脚本：抓取、解析简历、评分、简历定制、岗位状态管理；
- Supabase：职位、基础简历、定制简历；
- GitHub Actions：定时抓取、评分、简历定制、岗位管理；
- LiteLLM：多模型适配；
- Pydantic：部分结构化数据。

证据：

- 架构和安装：[`README.md`](https://github.com/anandanair/job-scraper/blob/e08e91d1f55f2dc072b4e9a456ef072f9de6ea3e/README.md#L1-L145)
- 数据模型：[`models.py`](https://github.com/anandanair/job-scraper/blob/e08e91d1f55f2dc072b4e9a456ef072f9de6ea3e/models.py)
- 数据库：[`supabase_setup/init.sql`](https://github.com/anandanair/job-scraper/blob/e08e91d1f55f2dc072b4e9a456ef072f9de6ea3e/supabase_setup/init.sql)

### 3.2 可借鉴能力

#### A. 真正的定时编排

- 每日抓职位：[`scrape_jobs.yml`](https://github.com/anandanair/job-scraper/blob/e08e91d1f55f2dc072b4e9a456ef072f9de6ea3e/.github/workflows/scrape_jobs.yml)
- 每 4 小时评分：[`score_jobs.yml`](https://github.com/anandanair/job-scraper/blob/e08e91d1f55f2dc072b4e9a456ef072f9de6ea3e/.github/workflows/score_jobs.yml)
- 每 6 小时生成定制简历：[`hourly_resume_customization.yml`](https://github.com/anandanair/job-scraper/blob/e08e91d1f55f2dc072b4e9a456ef072f9de6ea3e/.github/workflows/hourly_resume_customization.yml)
- 每日岗位管理：[`job_manager.yml`](https://github.com/anandanair/job-scraper/blob/e08e91d1f55f2dc072b4e9a456ef072f9de6ea3e/.github/workflows/job_manager.yml)

建议我们抽象一个 scheduler adapter：

- Codex Automation 作为默认；
- GitHub Actions 作为无人值守备选；
- 两者都调用同一入口，不复制业务逻辑；
- 增加并发锁，防止同一工作区同时启用两个 runner；
- 每次 run 写开始、结束、处理量、失败量和费用。

#### B. 小批量处理、速率限制和重试

[`llm_client.py`](https://github.com/anandanair/job-scraper/blob/e08e91d1f55f2dc072b4e9a456ef072f9de6ea3e/llm_client.py#L38-L69) 有 token-bucket RPM；[`llm_client.py`](https://github.com/anandanair/job-scraper/blob/e08e91d1f55f2dc072b4e9a456ef072f9de6ea3e/llm_client.py#L194-L270) 有指数退避、jitter、每日调用预算和模型池切换；评分任务按 `JOBS_TO_SCORE_PER_RUN` 限量处理。

建议我们新增：

- `max_resumes_per_run`；
- `max_llm_calls_per_run/day`；
- connector-specific backoff；
- 失败队列和最大重试次数；
- 单候选人失败不阻塞整批；
- 明确区分 retryable 与 terminal failure。

#### C. 数据层的状态、索引和查询函数

`jobs` 表包含 `status`、`is_active`、`resume_score`、`last_checked`、`resume_score_stage`，并给评分、状态、抓取时间建索引。这个模式可映射到我们的招聘数据表 + 本地 SQLite：

- 业务状态放协作工具；
- 去重键、run、checkpoint、retry 放 SQLite；
- 日报从稳定聚合字段读取，而不是重新扫描全部附件。

#### D. typed input/output

`models.py` 用 Pydantic 声明 Resume、Experience、Project 等结构。我们的邮件解析与评分也应先进入显式 schema，再进入 Base；未通过验证的字段不能参与评分或对外写。

### 3.3 不应引入的能力与风险

1. **方向相反**：它抓职位、给单个求职者做简历，不提供企业候选人 sourcing、CRM 或 ATS。
2. **LinkedIn 自动抓取风险**：项目 README 自己在[免责声明](https://github.com/anandanair/job-scraper/blob/e08e91d1f55f2dc072b4e9a456ef072f9de6ea3e/README.md#L244-L246)承认抓 LinkedIn 可能违反条款。LinkedIn [User Agreement](https://www.linkedin.com/legal/user-agreement) 和 [Prohibited software and extensions](https://www.linkedin.com/help/linkedin/answer/a1341387/prohibited-software-and-extensions) 都明确禁止未经授权的 scraping/bot 自动化。
3. **评分不可审计**：[`score_jobs.py`](https://github.com/anandanair/job-scraper/blob/e08e91d1f55f2dc072b4e9a456ef072f9de6ea3e/score_jobs.py#L104-L159) 要求模型只返回 0–100 的一个整数，没有引用证据、缺口、置信度或评分版本。不能用于面试建议。
4. **把直接身份信息送入 LLM**：同一文件的 `format_resume_to_text` 包含姓名、邮箱、电话、位置和链接，然后整体进入 LLM prompt。我们的评分前应先做最小化和去标识化，只保留与岗位相关的经历证据。
5. **高权限密钥**：GitHub Actions 使用 `SUPABASE_SERVICE_ROLE_KEY`。一旦 workflow、依赖或日志暴露，影响面大。应使用专用最小权限服务身份，不能让普通 Operator 获取。
6. **模型轮换的目的表述不合适**：README 把 model rotation 描述为“bypass rate limitations”。我们的目标应该是合规容错和预算控制，不是绕开供应商限制。
7. **没有业务审计事件**：Actions 日志可以证明脚本运行，但不能代替候选人状态变化、评分版本、人工覆盖和外部发送的审计记录。
8. **没有自动投递证据**：仓库有“ready to apply”和定制简历流程，但本次代码树未发现直接在平台自动提交申请的实现。不要把准备动作误报成自动投递能力。

## 四、`Snseam/boss-zhipin-mcp`

### 4.1 架构与安装

它通过 FastMCP 暴露工具，用 Playwright CDP 连接已经登录的 Chrome，再操作 BOSS 招聘者页面：

`Claude → MCP stdio → Playwright/CDP → 登录态 Chrome → BOSS 招聘后台`

安装需要 clone、Python 3.12 venv、依赖和 Playwright Chromium，再在 `.mcp.json` 写绝对路径。

证据：

- 工作原理和安装：[`README.md`](https://github.com/Snseam/boss-zhipin-mcp/blob/06a1a7d804aa80131a066bddc1879ac4bc72f841/README.md#L24-L138)
- 浏览器/CDP 管理：[`browser.py`](https://github.com/Snseam/boss-zhipin-mcp/blob/06a1a7d804aa80131a066bddc1879ac4bc72f841/browser.py#L21-L182)
- MCP tools：[`server.py`](https://github.com/Snseam/boss-zhipin-mcp/blob/06a1a7d804aa80131a066bddc1879ac4bc72f841/server.py)

它不是定时系统：没有 GitHub Actions、cron 或队列。它只有“单次调用里的批量执行”和中断恢复。

### 4.2 可借鉴能力

以下只借鉴通用工作流模式，不能复制仓库代码，也不能照搬 BOSS 浏览器操作。

#### A. 多关键词批处理与跨关键词去重

[`boss_multi_search`](https://github.com/Snseam/boss-zhipin-mcp/blob/06a1a7d804aa80131a066bddc1879ac4bc72f841/server.py#L131-L268) 依次执行多个关键词，基于 `expectId` 去重，每个关键词结束就保存；遇到验证码会返回 partial 结果和进度。

可迁移为合规的候选人入口：

- 对员工推荐、招聘会名单、候选人主动投递、官方 ATS 导出做多批次导入；
- `source_record_id + source_system` 去重；
- 每批完成后 checkpoint；
- 中断时返回 `completed_batches` 和恢复位置；
- 不因最后一批失败丢掉前面已处理结果。

#### B. 原子写和中断恢复

[`candidate_db.py`](https://github.com/Snseam/boss-zhipin-mcp/blob/06a1a7d804aa80131a066bddc1879ac4bc72f841/candidate_db.py#L18-L109) 先写 `.tmp` 再 rename，并保存 `first_seen`、`last_updated`、`status`、`source_keyword`。[`boss_pipeline_status`](https://github.com/Snseam/boss-zhipin-mcp/blob/06a1a7d804aa80131a066bddc1879ac4bc72f841/server.py#L519-L546) 会报告状态分布和恢复建议。

我们的 SQLite 已经比 JSON 更适合并发，但仍可补：

- `checkpoint(batch_name, cursor, updated_at)`；
- `run_summary`；
- `dead_letter`；
- 启动时扫描未完成 run 并给恢复建议。

#### C. 可组合查询和 Top N 报告

CandidateDB 可按状态、是否有链接、来源关键词、日期组合过滤，并输出 pipeline stats；这适合映射为协作工具筛选视图和日报明细。

#### D. 安全停止思路

[`check_and_screenshot_verification`](https://github.com/Snseam/boss-zhipin-mcp/blob/06a1a7d804aa80131a066bddc1879ac4bc72f841/browser.py#L218-L250) 遇到验证码时停止并要求人工处理，而不是试图破解。虽然我们不应接入这类浏览器抓取，但“检测到平台风控就停止、保留 partial、等待人处理”的通用原则值得保留。

### 4.3 必须拒绝的能力与风险

#### A. 平台条款与封号风险

README 明确写出“反检测”“随机延迟模拟人类操作”，并通过 Canvas 截图、二维码解析获取永久链接：

- [`README.md`](https://github.com/Snseam/boss-zhipin-mcp/blob/06a1a7d804aa80131a066bddc1879ac4bc72f841/README.md#L234-L240)
- [`scraper.py`](https://github.com/Snseam/boss-zhipin-mcp/blob/06a1a7d804aa80131a066bddc1879ac4bc72f841/scraper.py#L315-L449)

BOSS 直聘官方公开的 [用户协议（2019-09-30 版）](https://www.zhipin.com/web/common/protocol/protocol-2019-09-30.html) 把蜘蛛、爬虫、拟人程序以及避开/破坏技术措施的非正常浏览列入“非法获取”。仓库实现与这条红线高度冲突。协议版本较旧，部署前仍应由业务法务核对账号当前接受的最新版条款；但这已经足以说明我们不应主动引入该方案。

应明确禁止进入插件：

- 自动登录或复用招聘账号 cookie；
- 自动滚动抓候选人；
- 模拟人类随机延迟做反检测；
- 绕过 Canvas/技术措施获取简历；
- 批量提取或分享候选人永久链接；
- 自动打招呼、自动发消息；
- 遇到验证码后继续尝试规避。

#### B. 自动外联没有确认门

[`boss_greet_by_index` 和 `boss_send_greeting`](https://github.com/Snseam/boss-zhipin-mcp/blob/06a1a7d804aa80131a066bddc1879ac4bc72f841/server.py#L382-L443) 直接执行外部沟通，没有 preview、明确确认和发送审计。我们的插件应保持“小红书人工发布、邮件只生成草稿、邀约必须确认”。

#### C. 年龄硬筛选和不当画像

示例配置把 `max_age: 35` 设为硬条件；[`boss_filter_and_score`](https://github.com/Snseam/boss-zhipin-mcp/blob/06a1a7d804aa80131a066bddc1879ac4bc72f841/server.py#L551-L690) 会将超过年龄上限者直接过滤，并把薪资、学历、离职状态用于加减分，Top N 自动改为 `shortlisted`。

这与我们的安全边界直接冲突：

- 年龄不得进入评分；
- 不得因照片、性别、婚育、民族、籍贯等敏感属性筛选；
- 学历只有在岗位确有必要、且有审批依据时才能作为门槛；
- 薪资期望应该用于“预算是否匹配”的单独提示，不应隐性降低能力分；
- AI 不能自动 shortlisted/rejected。

#### D. 本地 PII 与登录态保护不足

- cookies 以明文 JSON 保存：[`browser.py`](https://github.com/Snseam/boss-zhipin-mcp/blob/06a1a7d804aa80131a066bddc1879ac4bc72f841/browser.py#L167-L182)
- 候选人姓名、年龄、学校、薪资、全文等写入明文 `candidates_db.json`：[`candidate_db.py`](https://github.com/Snseam/boss-zhipin-mcp/blob/06a1a7d804aa80131a066bddc1879ac4bc72f841/candidate_db.py#L10-L39)
- `.gitignore` 忽略了 cookies 和 chrome-profile，但没有忽略 `candidates_db.json`、简历截图和验证码截图：[`.gitignore`](https://github.com/Snseam/boss-zhipin-mcp/blob/06a1a7d804aa80131a066bddc1879ac4bc72f841/.gitignore)
- CDP debug port 和 Chrome profile 会放大本机会话被接管的影响面。

#### E. 无审计和危险删除

`boss_clear_dedup()` 参数全空时直接清空全部记录，没有确认，也没有软删除或恢复机制。候选人状态同样只是当前值，没有事件历史。

## 五、能力对照

| 能力 | recruiter-plugin | job-scraper | boss-zhipin-mcp | 对我们的价值 |
|---|---|---|---|---|
| Skill/Plugin 架构 | Claude commands + Skills + ATS MCP | 不是 Agent 插件 | MCP Server | 借鉴 setup/run/connector 分层 |
| 真正定时任务 | 无，daily briefing 仍需手动 | GitHub Actions cron | 无 | 借鉴 Actions 作为备用 runner |
| 批处理与限额 | 单次 3–10 人为主 | 每批限量、速率限制、预算、重试 | 多关键词、逐批落盘、partial | P1：统一 batch runner |
| 候选人 sourcing | RapidAPI LinkedIn/WebSearch | 无，抓的是岗位 | BOSS 登录态抓取 | 只做官方 API/授权导入；不抓平台 |
| 评分 | 9 维、带理由 | 单整数 0–100 | 关键词分 + 年龄硬筛 | 借鉴证据化维度；拒绝敏感属性 |
| CRM/ATS | Greenhouse/Lever/Ashby MCP；HubSpot 仅配置宣称 | 无 | 本地 JSON | 先做 adapter contract，再接官方 API |
| Pipeline | YAML 状态漏斗 + stale | Supabase 职位状态 | JSON 状态 + 恢复建议 | 加允许迁移规则、SLA 和审计事件 |
| 审计 | 弱，可覆写文件 | Actions 技术日志，业务审计弱 | 弱，可覆写 JSON | P0：append-only event log |
| 外部发送审批 | Gmail draft 前确认 | 不发候选人消息 | 自动打招呼，无确认 | 保持人工确认 |
| 隐私安全 | 明文 config/API key | PII 进入 LLM，service-role key | 明文 cookies/PII/截图 | P0：去标识化、最小权限、保留期 |
| 许可证可复用性 | proprietary/无 LICENSE | MIT | README 称 MIT、无 LICENSE | 只可代码复用 job-scraper，且需保留许可 |

## 六、建议的迭代路线

### P0：先补生产可信度

1. **审计事件表**
   - `event_id/run_id/workspace/actor/action/object_type/object_id/before/after/reason/source/timestamp`
   - 评分、人工覆盖、状态迁移、外部草稿/发送、数据表写入都记事件。
2. **外部写统一审批**
   - 邮件、邀约、ATS 提交、岗位开关、定时任务激活全部走 preview → confirm → execute → receipt。
3. **评分数据最小化**
   - 姓名、邮箱、电话、照片、出生日期等不进入评分 prompt；
   - 每项分数必须绑定简历/JD 的证据句和 `confidence`；
   - 输出 `missing_evidence`，不能用推测补齐。
4. **合法来源策略**
   - 在 config 加 `candidate_source_policy`；
   - 只允许 `email_inbound`、`employee_referral`、`career_fair_import`、`authorized_ats_api`、`candidate_consent_upload`；
   - 明确拒绝 `linkedin_scrape`、`boss_browser_automation`、`anti_detection`。
5. **数据保留与删除**
   - 可按候选人执行查找、导出、删除；
   - 日报脱敏；
   - 超过保留期自动进入待删除清单，由人确认。

### P1：吸收最有价值的工程模式

1. **可恢复 Batch Runner**
   - capped batch、checkpoint、重试、dead-letter、并发锁、partial 状态。
2. **状态机**
   - 显式允许 `new → screened → review_requested → interview_recommended → interview_scheduled`；
   - 禁止模型直接进入 rejected/offer；
   - 退回与跳转必须记录理由。
3. **评分卡 v2**
   - 必须项覆盖、加分项、相关成果、经验深度、场景匹配、到岗约束；
   - 总分之外保留证据、缺口、置信度、评分版本和模型版本。
4. **SLA 与 stale**
   - `next_action_at`、`days_in_stage`、超时提醒；
   - 日报优先显示“今天要处理谁”，而不是只报数量。
5. **限额与成本控制**
   - RPM、每日预算、每轮简历上限、指数退避和失败分级。
6. **真实安装验收**
   - 在 Codex 和 Claude Code 干净环境分别验证安装、Skill 发现、连接器启动、虚构简历 E2E；
   - 不能只做静态 package validate。

### P2：在有明确业务需求后再扩展

1. **ATS Adapter**
   - 先定义 `list_jobs/create_candidate/add_note/get_status` contract；
   - 首个真实使用场景确认在用的 ATS 后再实现，避免为了“支持很多 ATS”堆空壳。
2. **官方 sourcing**
   - 仅接供应商有明确授权和删除机制的 API；
   - 做数据来源标签、授权依据、导入时间和删除期限。
3. **日历与邀约**
   - 默认创建草稿/待确认事件，不自动邀请候选人。
4. **市场/漏斗简报**
   - 可借鉴 recruiter-plugin 的 briefing 结构，但数据只来自招聘工作区授权源。

## 七、明确的“不做清单”

- 不复制 `recruiter-plugin` 或 `boss-zhipin-mcp` 的代码、Skill 文本和权重；
- 不做 LinkedIn/BOSS 登录态抓取；
- 不做反检测、绕过 Canvas、验证码或访问限制；
- 不自动批量打招呼、私信或投递；
- 不以年龄、性别、照片、婚育等敏感信息筛选；
- 不把完整简历 PII 原样发送给外部模型；
- 不把 API key、cookie、Base token 或候选人数据放进仓库；
- 不让“模型分数”直接改变候选人最终状态；
- 不把 GitHub Actions 成功或静态校验成功冒充生产招聘链路验收。

## 八、给主研究的推荐表述

一句话结论：

> 可以吸收三个仓库的工作流设计，但不能照搬平台抓取。优先升级“可恢复批处理 + 证据化评分 + 状态机 + 审计 + 审批”；候选人 sourcing 只走官方 API、授权导入和主动投递。

如果只选三项进入最近一个版本：

1. `run_id + checkpoint + partial/dead-letter` 的可恢复批处理；
2. 带证据、置信度、缺失项和版本号的评分卡；
3. 候选人状态迁移与外部写操作的 append-only 审计。
