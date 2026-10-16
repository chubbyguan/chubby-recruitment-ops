# 开源招聘自动化仓库调研与插件升级建议

> 核验日期：2026-07-24（Asia/Shanghai）
> 研究对象：8 个 GitHub 仓库，覆盖 ATS、简历解析/匹配、Agent 工作流、定时批处理和公平性审计。
> 目标：判断哪些能力值得独立实现到 `chubby-recruitment-ops`，而不是寻找一个仓库整体替换现有插件。
> 边界：本报告基于仓库默认分支、许可证、Release、Actions、关键代码与官方文档；未使用真实候选人或招聘账号运行这些项目。Star 和最近推送时间是当日快照，会变化。

## 结论先行

GitHub 上有类似项目，但**没有一个仓库完整覆盖“小红书内容准备 → Gmail 收简历 → 多岗位匹配与证据化评分 → 招聘数据表→ 日报”**。现有插件不应该被其中任何一个整体替换。

最合理的路线是组合借鉴：

1. 用 [OpenCATS](https://github.com/opencats/OpenCATS) 的 `Candidate–Application–Job` 多对多模型和阶段历史，修正现有数据模型；
2. 用 [Frappe HRMS](https://github.com/frappe/hrms) 的多轮面试、多人反馈、独立 Offer 和人工确认机制，补全招聘后半程；
3. 用 [Resume-Matcher](https://github.com/srbhr/Resume-Matcher) 的结构化解析、事实保真不变量、Golden Case 和端到端证据包，建立可验证的 AI 质量体系；
4. 用 [job-scraper](https://github.com/anandanair/job-scraper) 的限额批处理、重试、预算和定时编排模式，增强日常运行可靠性；
5. 只借 [AIBF_API](https://github.com/jbarach2012/AIBF_API) 的决策留痕和人工覆盖审计形状，不采用其演示性偏见算法；
6. 明确拒绝 BOSS/LinkedIn 登录态爬取、反检测、年龄筛选、自动打招呼和自动淘汰。

坦白说，下一版最不该做的是“再加一个更聪明的总分”。当前更急的是三件事：

- **先把真实 Gmail 和协作工具适配器做完并验收。** 现在仓库里主要是 connector contract，不是已经可复制到每个工作区的生产连接器。
- **修正数据模型。** 当前 `candidates` 同时存候选人身份和岗位申请事实，一人多岗时会覆盖；`interviews_offers` 又把面试、反馈和 Offer 混在一起。
- **建立真实评测。** 现有 17 个本地测试验证的是脚本与虚构流程，不等于“AI 匹配准确”或“真实使用场景链路已经跑通”。

## 一、现有插件的真实基线

### 已经做得比多数开源项目更好的部分

- 对岗位设置 `open / conditional / frozen / closed / unknown` 门禁；
- 小红书只准备内容，保留人工终审和发布；
- 邮件按 `message_id`、附件 SHA-256 和弱标识去重；
- SQLite claim 使用 lease/fencing token，能避免并发 worker 互相覆盖；
- Top 3 岗位匹配要求给证据、缺口和置信度；
- 禁止年龄、性别、照片、婚育、民族、籍贯等进入评分；
- 不自动淘汰、约面、背调、发 Offer 或改变岗位状态；
- 日报脱敏，平台指标拿不到时必须显示“不可得”。

这些边界应当保留。部分同类仓库虽然自动化更多，但合规和审计反而明显更弱。

### 当前必须承认的缺口

| 能力 | 当前状态 | 风险 |
|---|---|---|
| Gmail 真实读信/附件 | 定义了 capability contract，依赖外部连接器 | 用户安装后不一定立即可跑；缺少统一实现和真实回归 |
| 招聘数据表写入/回读/通知 | 定义了 adapter contract，依赖 `collaboration-cli` 或外部连接器 | 字段映射、权限、幂等写和回读仍需按工作区验收 |
| 简历解析 | 有处理规则，没有正式版本化 parser/evidence 中间层 | 解析错误可能直接污染匹配和评分 |
| AI 质量评测 | 有 JSON validator 和合成验收，没有匹配准确率基线 | 不能证明 Top 3、硬门槛和建议质量 |
| 候选人与申请关系 | 混在 `candidates` 表 | 一人多岗、重复申请、人才库转正式申请会覆盖 |
| 面试与 Offer | 混在 `interviews_offers` 表 | 多轮、多面试官、多版本 Offer 无法可靠表达 |
| 业务审计 | 有 mail/automation log，缺 application stage history | 难以回答谁在何时因何原因改变了阶段 |
| 定时运行 | 有规则和建议时段，没有统一可恢复 runner | partial、dead-letter、预算和恢复可见性不足 |

另有一个具体 schema 缺陷：`interviews_offers.stable_key` 配置为“面试记录ID”，但该字段不在 `required_fields` 中。下一版拆表时必须一起修正。

## 二、候选仓库总览

| 仓库 | 定位与当日状态 | 许可证 | 最值得借鉴 | 采用判断 |
|---|---|---|---|---|
| [frappe/hrms](https://github.com/frappe/hrms) | 完整 HR/招聘平台；约 8.2k Star；2026-07-23 仍有提交；`v16.14.0` 于 2026-07-21 发布 | [GPL-3.0](https://github.com/frappe/hrms/blob/develop/license.txt) | 招聘申请、岗位开放、面试轮次、多人反馈、Offer、权限、提醒和漏斗 | 高价值领域模型；不嵌入、不复制代码 |
| [opencats/OpenCATS](https://github.com/opencats/OpenCATS) | ATS/招聘 CRM；约 713 Star；2026-07-09 仍有提交；`v0.10.0` 于 2026-06-23 发布 | [MPL-2.0 + 旧 CATS Public License](https://github.com/opencats/OpenCATS/blob/master/LICENSE.md) | Candidate/Application 多对多、阶段历史、活动时间线、去重、来源和漏斗 | 高价值数据模型；clean-room 重写 |
| [srbhr/Resume-Matcher](https://github.com/srbhr/Resume-Matcher) | 求职者侧简历优化器；约 27.8k Star；2026-07-06 推送；多人维护 | [Apache-2.0](https://github.com/srbhr/Resume-Matcher/blob/main/LICENSE) | 混合解析、Schema、事实不变量、Golden/E2E 评测、证据包 | 工程模式最值得采用；不能照搬 ATS 分数 |
| [xitanggg/open-resume](https://github.com/xitanggg/open-resume) | 浏览器端简历构建/英文 PDF 解析；约 8.8k Star；最后推送 2024-10-29 | [AGPL-3.0](https://github.com/xitanggg/open-resume/blob/main/LICENSE) | 本地 PDF 布局/字体/区块解析思路 | 只借思想，从零实现中文版本 |
| [anandanair/job-scraper](https://github.com/anandanair/job-scraper) | 求职者侧抓岗位与岗位匹配；约 38 Star；2026-03-10 推送，公开定时 Actions 仍在运行 | [MIT](https://github.com/anandanair/job-scraper/blob/main/LICENSE) | cron、限额批次、rate limit、退避、预算、Pydantic、状态查询 | 借工程模式；业务方向相反 |
| [andrew-shwetzer/recruiter-plugin](https://github.com/andrew-shwetzer/recruiter-plugin) | Claude Code 招聘插件；约 12 Star；2026-04-28 最后推送；5 个提交、无测试/Release | 无 `LICENSE`；[plugin.json 标注 proprietary](https://github.com/andrew-shwetzer/recruiter-plugin/blob/03b03f82883f176ae2057814fbf0c34e895375a8/.claude-plugin/plugin.json) | 粒度更细的 Skill、连接器分层、pipeline/stale、邮件草稿审批 | 只能借产品思路，禁止复制 |
| [jbarach2012/AIBF_API](https://github.com/jbarach2012/AIBF_API) | ATS 偏见审计原型；约 160 Star；2026-07-07 推送；单一主要贡献者 | [Apache-2.0](https://github.com/jbarach2012/AIBF_API/blob/main/LICENSE) | 决策链、模型版本、归因、人工覆盖、审计 API | 只借审计形状；算法不可生产 |
| [Snseam/boss-zhipin-mcp](https://github.com/Snseam/boss-zhipin-mcp) | BOSS 招聘者端浏览器自动化；约 91 Star；2026-03-31 最后推送；无测试/Release | README 声称 MIT，但仓库无 `LICENSE`、GitHub 未识别许可证 | checkpoint、跨关键词去重、partial 状态、Top N 报告 | 平台自动化整段拒绝 |

“公开仓库”不等于“开源”。没有明确许可证的 `recruiter-plugin` 和 `boss-zhipin-mcp` 不能复制代码、Skill 文本或权重。

## 三、哪些功能真正值得升级

### 1. P0：Candidate 与 Application 分离

最重要的升级来自 OpenCATS，而不是 AI 项目。

建议数据关系：

```text
Candidate 1 ── N Application N ── 1 Job
                      │
                      ├── N Stage Event
                      ├── N Interview
                      ├── N Activity
                      └── 0..N Offer Version
```

`candidates` 只保存候选人的稳定身份、联系方式引用、当前简历、同意范围和保留状态。

新增 `applications`：

```text
application_id
candidate_id
job_id
application_type          explicit / ai_suggested / talent_mapping
source_channel
source_content_id
source_message_id
current_stage
match_confidence
screening_score
screening_recommendation
evidence_summary
missing_materials
human_review_status
candidate_decline_reason
company_close_reason
owner
created_at / updated_at
```

关键规则：

- 同一候选人可同时关联多个岗位，阶段互不覆盖；
- AI Top 2/Top 3 只是 `application_suggestion`，不能静默创建正式申请；
- “候选人主动放弃”和“公司不推进”必须是不同终态；
- 来源归因属于一次 Application，不应作为候选人的永久单值字段。

### 2. P0：阶段历史和 append-only 审计

借鉴 OpenCATS 的阶段历史和 AIBF 的决策留痕，新增：

```text
application_stage_history
  event_id / application_id / from_stage / to_stage
  reason_code / note / actor_type / actor_id
  source_run_id / occurred_at

recruitment_events
  event_id / run_id / object_type / object_id
  action / before / after / reason / source / actor / timestamp
```

规则：

- 历史 append-only，不通过覆盖当前值伪造过去；
- AI 只能写建议事件，不能直接写正式 `rejected / interview_scheduled / offer_sent`；
- 每次人工覆盖 AI 建议都记录原值、新值、理由、操作者和时间；
- 日报必须区分 `succeeded / partial / failed / skipped`，不能把 partial 写成完整成功。

### 3. P0：版本化 `resume-evidence.json`

Resume-Matcher 最值得学的不是简历美化，而是“解析结果必须有结构和事实约束”。

建议中间层：

```json
{
  "schema_version": "2.0",
  "document_hash": "...",
  "parser": {
    "engine": "local-layout+llm",
    "version": "...",
    "model": "...",
    "prompt_version": "..."
  },
  "candidate": {
    "contact_ref": "encrypted-or-external-reference"
  },
  "facts": [
    {
      "fact_id": "fact-001",
      "type": "work_experience",
      "value": "...",
      "source_page": 1,
      "source_text": "...",
      "confidence": "high",
      "verification": "parsed"
    }
  ],
  "conflicts": [],
  "missing_fields": []
}
```

解析顺序：

1. MIME、文件大小、加密和恶意内容检查；
2. 本地 PDF/DOCX 文本与布局预解析；
3. 确定性提取邮箱、电话、链接、日期和标题；
4. LLM 只依据带 ID 的文本块生成结构化 JSON；
5. Schema、日期、经历数量和来源覆盖校验；
6. 扫描件进入 OCR 或 `manual_review`，不能静默给分；
7. 低置信度禁止输出 `hr_screen`。

OpenResume 可提供布局解析思路，但 AGPL 代码不应复制到私有插件；如果实现中文简历解析，应 clean-room 重写。

### 4. P0：要求级匹配，不再迷信总分

每个岗位版本拆成：

- `must_have`
- `preferred`
- `outcome`
- `work_sample`
- `location/work_mode`
- `disqualifier`
- `unknown_or_human_only`

每条要求返回：

- `matched / missing / conflict / unknown`
- `fact_id`
- 解释
- 置信度
- 是否需要补材料

总分继续保留，但只能由已引用证据生成。关键词覆盖率可以单独展示，不能替代质量判断。

`recruiter-plugin` 的“分项评分 + 具体理由”有启发，但以下维度应明确拒绝：

- 学校名气或“名校 prestige”；
- `company_tier`；
- 对空档、降级或职业轨迹的默认负面推断；
- 年龄、性别、照片、婚育等；
- 模型输出 `PASS` 后自动淘汰。

### 5. P0：Golden + 反事实公平性 + E2E 证据包

借鉴 Resume-Matcher 的结构不变量和端到端 monitor：

- 20–30 份合成或去标识化 Golden Resume；
- 每份对应 2–4 个岗位；
- 定义预期 Top 3、硬门槛、证据引用和建议；
- 必须包含已知坏例；
- 提示注入测试；
- PII 泄漏测试；
- 重复邮件和并发测试；
- 文件解析失败和低置信度测试；
- 模型/provider/prompt 升级前后差异；
- 输出完整证据包，而不是只打印“通过”。

公平性 P0 不需要采集或推断真实候选人的敏感属性。先做成对反事实测试：

- 替换姓名、性别化称谓、年龄暗示，业务证据相同，分数和建议应保持一致；
- 去掉照片、婚育、民族、宗教、残疾、籍贯等后，结论不应变化；
- 替换学校名称时，除岗位明确要求合法资质外，不应产生“学校档次”差异；
- 人工批准的 Golden 基线只能显式更新。

AIBF 当前模型基于合成数据、固定技能词表和模拟 ATS，甚至在缺字段时用 `resume_id` 构造演示性人口属性。它不能被当成真实公平性引擎或法律结论。

### 6. P0/P1：可恢复 Batch Runner

借鉴 job-scraper 的批次和预算控制，以及 BOSS MCP 中“每批落盘、可查看 partial”的工程思想，但不引入平台自动化：

```text
run_id
step_id
status                    pending/running/succeeded/partial/failed/skipped
checkpoint
attempt
max_attempts
next_retry_at
error_class
dead_letter_reason
input_count / success_count / skipped_count / failed_count
started_at / finished_at
```

同时增加：

- 每轮处理上限；
- 每分钟请求上限；
- 每日模型预算；
- 指数退避与错误分类；
- checkpoint 恢复；
- dead-letter 人工处理队列；
- 连接器真实 health probe；
- 定时任务只有在 Gmail、协作工具和虚构 E2E 验收后才能启用。

GitHub Actions 可以作为无服务器备用 runner，但不应存放真实简历或候选人 PII；本机/受控运行环境仍是优先方案。

### 7. P1：面试、反馈、Offer 拆分

借鉴 Frappe HRMS：

```text
interview_templates
  template_id / job_id / round_name / skills
  expected_rating / default_interviewers

interviews
  interview_id / application_id / round
  scheduled_at / status / owner

interview_feedback
  feedback_id / interview_id / interviewer_id
  skill_scores / evidence / risk / recommendation / submitted_at

offers
  offer_id / application_id / version
  approval_status / response_status
  drafted_at / approved_at / sent_at / responded_at
```

AI 可以：

- 生成基于 JD 和简历证据的面试提纲；
- 建议需要验证的矛盾或缺失项；
- 汇总多人反馈；
- 起草邀约或 Offer 文案。

AI 不可以：

- 自动邀请或取消面试；
- 直接把面试结果写成正式淘汰/录用；
- 自动发送 Offer；
- 把薪资放进公开内容或普通日报。

### 8. P1：活动时间线、SLA 和真正有用的日报

新增：

```text
recruitment_activity
  activity_id / candidate_id / application_id
  type / occurred_at / actor_id / summary
  external_reference / visibility
```

日报从“今天多少封邮件”升级为：

- 今天必须处理的候选人；
- 超过 SLA 的 Application；
- 收件到首次处理时长；
- 各阶段中位停留时间；
- 内容 → 投递 → 有效候选 → HR Screen → 面试 → Offer 的真实漏斗；
- 按岗位、内容版本、来源和 Owner 拆分；
- AI 建议被人工覆盖的比例及主要原因；
- 解析失败、低置信度和待补材料；
- 真实可得的小红书指标，缺失仍显示“不可得”。

优先 KPI：

- `qualified_candidates / attributed_applications`
- `human_advanced / ai_hr_screen_recommendations`
- `received_at → first_review_at`
- `days_in_stage`
- `human_override_rate`
- `parse_failure_rate`
- `low_confidence_rate`

### 9. P1：Skill 粒度与连接器验收

`recruiter-plugin` 的细粒度命令比一个大 Skill 更容易发现和排障，但它自己的安装链路并不可靠：安装脚本没有正确带上 ATS MCP，部分 CRM 能力只有配置声明，没有实现。

我们的改进方向应是：

- 保留 `recruitment-ops-run` 作为完整轮次入口；
- 增加清晰的窄入口：`prepare-content`、`process-inbox`、`review-candidate`、`pipeline-review`、`daily-digest`；
- setup 内部明确为 `profile → source → destination → policy → acceptance`；
- Gmail、collaboration、Notification 各自有 doctor；
- 在干净 Codex 和 Claude Code 环境分别做安装、Skill 发现、adapter 启动、虚构邮件 E2E；
- “配置完成”与“真实验收完成”必须是不同状态。

### 10. P2：可选能力

验证完 P0/P1 后再考虑：

- 待审批候选人消息队列；
- 可选移动端投递表单，预填岗位 ID 和内容归因码；
- 日历事件草稿与可用时间建议；
- 官方 ATS Adapter；
- 解析器字段级准确率、Top-3 recall、岗位间漂移和 reviewer calibration 看板；
- 有合法基础、最小样本量和独立复核后的群体公平性统计。

不要现在就做完整职位门户、统一 HRIS 或多 ATS 空壳。不同工作区继续使用自己的 Gmail 和招聘数据表，不需要为了“平台感”制造一个新的中央主数据源。

## 四、明确不采用的能力

### BOSS/LinkedIn 浏览器自动化

`boss-zhipin-mcp` 依赖登录态浏览器、反检测、Canvas 截图、二维码解码和自动打招呼；还允许年龄硬筛。除许可证不足外，这些做法同时带来平台条款、封号、隐私和歧视风险。

因此：

- 不做登录态抓取；
- 不做反检测、绕过 Canvas/验证码/访问限制；
- 不做批量自动打招呼、私信或投递；
- sourcing 只接受候选人主动投递、员工推荐、人才活动授权导入和有明确授权/删除机制的官方 API。

### 完整部署 Frappe HRMS 或 OpenCATS

这两套系统都有独立服务器、数据库、账户、权限和升级运维面。当前插件已经选择招聘数据表作为工作区事实源，再部署完整 ATS 会产生双主数据。

只有公司决定统一替换 HRIS/ATS 时，才应单独立项评估；不应让招聘插件偷偷演变成 HR 平台。

### 自动化最终用人决定

不自动：

- 淘汰；
- 约面；
- 背调；
- 发 Offer；
- 改岗位状态；
- 合并疑似重复候选人；
- 发送候选人沟通消息。

这些动作继续走 `preview → human confirm → execute → receipt`。

## 五、建议的版本路线

### v0.2：生产可信度与正确数据模型

目标：先让真实使用场景能稳定跑，且一人多岗不失真。

- Gmail/collaboration/Notification 真实 adapter 与 doctor；
- Candidate / Application 分离；
- Application Stage History；
- `resume-evidence.json`；
- 岗位要求版本化和要求级匹配；
- Golden/E2E/提示注入/PII/反事实公平性测试；
- `run_id + checkpoint + partial + dead-letter`；
- append-only 审计；
- 修复 `interviews_offers.stable_key` 缺失；
- 干净 Codex/Claude Code 安装验收。

验收门槛：

- 同一候选人两个岗位阶段互不覆盖；
- 重放同一邮件不重复创建；
- worker 中断后可恢复，旧 token 不能完成新 lease；
- 低置信度解析不会进入 `hr_screen`；
- 每项匹配都能回到原始事实；
- 模型/Prompt 变化会生成可审阅差异包；
- Gmail 写/协作工具写/日报发送均有 readback receipt。

### v0.3：面试闭环和招聘运营

- Interview Template / Interview / Feedback / Offer 拆分；
- 多面试官结构化评分和证据；
- 活动时间线；
- SLA 和 stale 提醒；
- 来源与内容归因；
- 完整漏斗和阶段停留；
- 候选人沟通草稿/待审批队列；
- 更细粒度 Skill 入口。

### v0.4：可选扩展

- 移动投递表单；
- 日历草稿；
- 首个真实业务需要的 ATS Adapter；
- 模型质量/漂移/人工覆盖看板；
- 在合法治理成熟后再做群体公平性统计。

## 六、优先级决策

如果下一版只能做三项：

1. **真实 Gmail + 协作工具 adapter 与可恢复运行器；**
2. **Candidate–Application–Job + 阶段历史；**
3. **resume-evidence + Golden/E2E 评测。**

如果可以做六项，再加入：

4. 多轮面试、多人反馈和独立 Offer；
5. append-only 决策审计和人工覆盖；
6. SLA、来源归因和真实漏斗。

这六项会把插件从“能生成内容并给 AI 初筛建议”升级为“能承载真实招聘事实、可验证、可恢复、可审计的轻量招聘操作系统”。

## 七、来源与进一步证据

本报告的详细逐文件证据、固定 commit、Actions 状态和许可证判断保存在：

- [ATS 平台专项研究](./intermediate/ats-platforms.md)
- [AI 解析、匹配与公平性专项研究](./intermediate/ai-matching.md)
- [Agent、定时批处理与平台自动化专项研究](./intermediate/agent-workflows.md)

核心一手来源：

- [Frappe HRMS repository](https://github.com/frappe/hrms)
- [Frappe Job Requisition](https://docs.frappe.io/hr/job-requisition)
- [Frappe Job Opening](https://docs.frappe.io/hr/job-opening)
- [Frappe Job Applicant](https://docs.frappe.io/hr/job-applicant)
- [OpenCATS repository](https://github.com/opencats/OpenCATS)
- [Resume-Matcher eval harness](https://github.com/srbhr/Resume-Matcher/tree/main/apps/backend/tests/evals)
- [Resume-Matcher E2E monitor](https://github.com/srbhr/Resume-Matcher/tree/main/apps/backend/e2e_monitor)
- [OpenResume repository](https://github.com/xitanggg/open-resume)
- [job-scraper repository](https://github.com/anandanair/job-scraper)
- [recruiter-plugin fixed snapshot](https://github.com/andrew-shwetzer/recruiter-plugin/tree/03b03f82883f176ae2057814fbf0c34e895375a8)
- [AIBF_API methodology](https://github.com/jbarach2012/AIBF_API/blob/main/docs/01-methodology.md)
- [boss-zhipin-mcp fixed snapshot](https://github.com/Snseam/boss-zhipin-mcp/tree/06a1a7d804aa80131a066bddc1879ac4bc72f841)
