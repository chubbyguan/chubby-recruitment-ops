# ATS 开源项目研究：Frappe HRMS 与 OpenCATS

> 核验日期：2026-07-24（Asia/Shanghai）
> 研究范围：`frappe/hrms`、`opencats/OpenCATS` 的 GitHub 默认分支、最新 Release、官方文档与关键源代码。
> 目标：判断哪些招聘产品能力值得独立实现到 `chubby-recruitment-ops`，而不是把其中任一系统直接嵌入插件。

## 一、结论先行

两个项目都值得参考，但参考对象不同：

- **Frappe HRMS** 是完整 HR 平台里的招聘模块。最值得借的是：`招聘申请 → 岗位开放 → 多轮面试 → 多面试官结构化反馈 → Offer` 的对象拆分、岗位编制约束、角色权限和标准 REST API。
- **OpenCATS** 是更纯粹的 ATS/招聘 CRM。最值得借的是：独立候选人主档、候选人与岗位的多对多申请关系、每个申请独立阶段、阶段变更历史、活动时间线、来源和岗位漏斗。
- **不建议把任何一个项目作为插件依赖，也不建议复制其代码。** 当前插件是 Gmail + 招聘数据表 + Codex/Claude Code 的轻量业务编排。部署 Frappe/OpenCATS 会引入独立服务器、数据库、账户体系和运维面，偏离“个人或团队安装插件即可运行”的产品方向。
- 当前插件最急需修的不是增加更多 AI，而是**修正招聘数据模型**：现在 `candidates` 表同时承担“候选人身份”和“某个岗位的申请阶段”，`interviews_offers` 又把面试、反馈、Offer 混在一张表。候选人一人多投、多岗匹配、多轮面试后，数据会失真。

建议优先级：

1. **P0：新增 Application 申请实体和阶段历史。**
2. **P0：拆分 Interview、Interview Feedback、Offer。**
3. **P1：加入活动时间线、来源归因和阶段 SLA。**
4. **P1：建立基于阶段历史的招聘漏斗、转化率、停留时间和 Time-to-fill。**
5. **P2：可选的结构化投递表单、消息模板待发送队列和连接器适配接口。**

## 二、项目状态与许可证

| 项目 | 维护状态（截至 2026-07-24） | 最新发布 | 许可证 | 判断 |
|---|---|---|---|---|
| [frappe/hrms](https://github.com/frappe/hrms) | 未归档；默认分支 `develop`；2026-07-23 仍有提交；2026-07-24 默认分支 CI 成功 | [`v16.14.0`](https://github.com/frappe/hrms/releases/tag/v16.14.0)，2026-07-21 | [GPL-3.0](https://github.com/frappe/hrms/blob/develop/license.txt) | 大型、持续活跃、工程成熟，适合作为领域模型参考 |
| [opencats/OpenCATS](https://github.com/opencats/OpenCATS) | 未归档；默认分支 `master`；2026-07-09 新增 `Candidate Declined` 阶段；该提交 CI 成功 | [`v0.10.0`](https://github.com/opencats/OpenCATS/releases/tag/v0.10.0)，2026-06-23 | 顶层 [LICENSE.md](https://github.com/opencats/OpenCATS/blob/master/LICENSE.md) 声明：新 OpenCATS 代码 MPL-2.0，约 2007 年原 CATS 代码使用 CATS Public License 1.1a | 仍在维护，但底层为长期演进的传统 PHP ATS；概念可借，架构不宜照搬 |

维护状态证据：

- Frappe 默认分支最新提交：[`c1e4d5715c9d`](https://github.com/frappe/hrms/commit/c1e4d5715c9d67450893edea7f3683b74ab26d98)，2026-07-23。
- Frappe 默认分支 CI：[`30062100159`](https://github.com/frappe/hrms/actions/runs/30062100159)，2026-07-24，成功。
- OpenCATS 默认分支最新提交：[`b83a23489c5d`](https://github.com/opencats/OpenCATS/commit/b83a23489c5d7b12d81e4e5debfde29b4cd13ef1)，2026-07-09，新增候选人主动放弃状态。
- OpenCATS 对应默认分支 CI：[`29019747739`](https://github.com/opencats/OpenCATS/actions/runs/29019747739)，成功。
- OpenCATS 当前测试矩阵包含 PHP 8.4.1 和 8.5、PHPUnit、Docker 集成测试和 Behat；但 [`ci.yml`](https://github.com/opencats/OpenCATS/blob/master/.github/workflows/ci.yml#L49-L51) 明确让 `composer audit` 以 `|| true` 运行，依赖漏洞不会阻断 CI。这说明“CI 绿”不能等同于“安全债务清零”。

许可证结论：

- 可以研究流程、字段和产品概念，并自行重新实现。
- **不要直接拷贝 Frappe HRMS 的 Python/JSON/前端代码进入当前私有插件**，除非团队明确接受 GPL-3.0 的传播义务。
- OpenCATS 不是一个简单的单许可证仓库；尤其旧文件头仍使用 CATS Public License。若复制具体实现，需要逐文件核验。对当前项目最稳妥的方式仍是 clean-room 独立实现。
- 本节是工程许可证风险判断，不是法律意见。

## 三、Frappe HRMS 深入分析

### 3.1 招聘数据模型

Frappe 的招聘主链路是：

```text
Staffing Plan
  → Job Requisition
  → Job Opening
  → Job Applicant
  → Interview / Interview Feedback
  → Job Offer
```

关键对象：

1. **Job Requisition（招聘申请）**
   - 内部提出招聘需求，不直接等于对外开放岗位。
   - 包含岗位、人数、预计薪酬、申请人、组织、期望到岗日期、完成日期和 `time_to_fill`。
   - 状态为 `Pending / Open & Approved / Rejected / Filled / On Hold / Cancelled`。
   - 源码：[`hrms/hr/doctype/job_requisition/job_requisition.json`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/job_requisition/job_requisition.json)
   - 官方文档：[Job Requisition](https://docs.frappe.io/hr/job-requisition)

2. **Job Opening（岗位开放）**
   - 连接公司、组织、职级、Staffing Plan 和 Job Requisition。
   - 区分 `Open / Closed`，有发布时间、截止时间、工作地点、用工类型和是否发布到网站。
   - 薪资范围和“是否公开薪资”是两个不同字段。这一点与当前“内部可存、对外不公开”的需求高度匹配。
   - 源码：[`hrms/hr/doctype/job_opening/job_opening.json`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/job_opening/job_opening.json)
   - 官方文档：[Job Opening](https://docs.frappe.io/hr/job-opening)

3. **Job Applicant（岗位申请人）**
   - 关联一个 Job Opening、来源、员工推荐、简历附件、简历链接和候选人评分。
   - 阶段为 `Open / Replied / Shortlisted / Rejected / Hold / Accepted`。
   - 通过邮箱创建申请人是正式支持的能力：官方文档说明，Email Account 可以配置 `Append To = Job Applicant`。
   - 源码：[`hrms/hr/doctype/job_applicant/job_applicant.json`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/job_applicant/job_applicant.json)
   - 业务逻辑：[`hrms/hr/doctype/job_applicant/job_applicant.py`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/job_applicant/job_applicant.py)
   - 官方文档：[Job Applicant](https://docs.frappe.io/hr/job-applicant)

需要注意：Frappe 的 `Job Applicant` 更接近“某人对某岗位的一次申请”，并没有像 OpenCATS 那样明确拆出全局 Candidate 主档和 Application 关联表。候选人以相同邮箱再次申请时，代码通过追加编号生成新记录。因此在“同一个人同时匹配多个岗位”的场景，OpenCATS 的模型更适合我们。

### 3.2 候选人管道

Frappe 提供：

- 基于 `Job Applicant.status` 的看板；
- 默认看板列为 `Open / Replied / Shortlisted / Accepted`；
- 从候选人记录直接安排面试；
- 同一种 Interview Type 不允许同一个申请人重复完成；
- 面试结果不会直接静默改变候选人状态，而是弹出人工确认动作。

证据：

- 看板与面试创建：[`job_applicant.py#L93-L186`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/job_applicant/job_applicant.py#L93-L186)
- 面试提交后人工确认更新候选人：[`interview.py#L46-L108`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/interview/interview.py#L46-L108)

可借鉴：

- **面试结论与候选阶段更新分开**。面试官提交 Cleared/Rejected，系统提出建议，但由 HR 确认是否更新申请阶段。这与当前插件“AI 不自动拒绝/约面/Offer”的边界一致。
- 对阶段变更建立显式状态机，禁止任意文本和越级修改。

不足：

- Applicant 的状态粒度较粗；
- 没有直接提供我们需要的 `request_more_info / manual_review / talent_pool` 等 AI 辅助阶段；
- Candidate 与 Application 未彻底分离。

所以应该借其“人工确认与合法转移”思想，不照搬状态集合。

### 3.3 面试与 Offer

这是 Frappe 最值得借鉴的部分。

**Interview Type / Interview Round**

- 定义岗位/职级适用的面试轮次；
- 配置默认面试官；
- 配置预期技能集合与预期平均分；
- 可按岗位复用。

源码：

- [`interview_type.json`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/interview_type/interview_type.json)
- [`expected_skill_set.json`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/expected_skill_set/expected_skill_set.json)
- 官方文档：[Interview Round](https://docs.frappe.io/hr/interview-round)

**Interview**

- 关联申请人、岗位、轮次、面试时间、面试官；
- 状态为 `Pending / Under Review / Cleared / Rejected / Cancelled`；
- 记录平均评分、期望评分、总结；
- 支持改期通知、面试提醒、反馈提醒；
- 是可提交/取消的正式记录，并启用 `track_changes`。

源码：

- [`interview.json`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/interview/interview.json)
- [`interview.py`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/interview/interview.py)

**Interview Feedback**

- 每个面试官独立提交反馈；
- 对预设技能逐项评分；
- 记录平均分、文字反馈和 `Cleared / Rejected`；
- 多个反馈按技能聚合平均分。

源码：

- [`interview_feedback.json`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/interview_feedback/interview_feedback.json)
- [`skill_assessment.json`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/skill_assessment/skill_assessment.json)
- 聚合逻辑：[`interview.py#L179-L220`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/interview/interview.py#L179-L220)
- 官方文档：[Interview Feedback](https://docs.frappe.io/hr/interview-feedback)

**Job Offer**

- 独立于 Interview；
- 关联申请人、Offer 日期、职级、公司；
- 状态为 `Awaiting Response / Accepted / Rejected / Cancelled`；
- Offer 条款是独立子表，支持条款模板和打印格式；
- 是可提交、取消、修订的正式记录。

源码：

- [`job_offer.json`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/job_offer/job_offer.json)
- [`job_offer.py`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/job_offer/job_offer.py)
- 官方文档：[Job Offer](https://docs.frappe.io/hr/job-offer)

对当前插件的直接启发：

- 当前 `interviews_offers` 表不应继续承担所有事实；
- 至少拆成 `interviews`、`interview_feedback`、`offers` 三张表；
- 面试评分应是“每位面试官 × 每项能力”的明细，而不是一个 JSON/长文本总分；
- Offer 应保存版本、人工审批状态、发送状态、候选响应状态和关键日期，但仍禁止插件自动发送。

### 3.4 权限与审计

Frappe 每个 DocType 都声明角色权限：

- `System Manager`、`HR Manager`、`HR User` 对招聘对象有不同程度权限；
- `Interviewer` 可以操作 Interview；
- 对 Interview Feedback，HR 角色偏读取，Interviewer 可以创建、提交和修改。

证据：

- [`job_applicant.json`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/job_applicant/job_applicant.json)
- [`interview.json`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/interview/interview.json)
- [`interview_feedback.json`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/interview_feedback/interview_feedback.json)
- [`job_offer.json`](https://github.com/frappe/hrms/blob/develop/hrms/hr/doctype/job_offer/job_offer.json)

审计方面：

- Interview、Interview Type、Interview Feedback 明确启用了 `track_changes`；
- 并不是所有招聘对象都在其 JSON 中明确启用 `track_changes`，不能把 Frappe 描述成“所有招聘字段都有完整不可变审计”。

适合借鉴：

- 把角色拆成 `招聘管理员 / 招聘负责人 / 面试官 / 只读业务负责人`；
- 面试官只应访问被分配的面试及有限候选信息；
- 所有人工阶段变更记录 `actor / from / to / reason / timestamp`；
- API 使用专用用户，所有请求仍按该用户角色检查。Frappe 官方 REST 文档明确说明 API Token 请求会记在对应用户下并执行角色校验：[REST API](https://docs.frappe.io/framework/user/en/api/rest)。

不宜照搬：

- Frappe 默认 DocType 权限对一些角色仍偏宽，例如 Interviewer 对 Interview 拥有较多操作权；
- 插件本身无法替代招聘数据表的访问控制。必须在数据表侧配置角色/视图权限，并让插件只使用最小权限连接器。

### 3.5 报表

Frappe 的 `Recruitment Analytics` 将 Staffing Plan、Job Opening、Job Applicant 和 Job Offer 串在一个层级报告中，展示申请状态、Offer 日期和 Offer 状态。

源码：

- [`hrms/hr/report/recruitment_analytics/recruitment_analytics.py`](https://github.com/frappe/hrms/blob/develop/hrms/hr/report/recruitment_analytics/recruitment_analytics.py)

可借：

- 从内部招聘计划一直追溯到 Offer；
- 以公司、岗位、申请、Offer 作为可下钻层级；
- `expected_by / completed_on / time_to_fill` 进入招聘运营指标。

不足：

- 标准报告对渠道归因、阶段停留、各阶段转化、候选质量分布和内容表现覆盖不够；
- 当前插件的小红书内容表现和 AI 匹配质量仍需要自己设计。

### 3.6 API 与扩展

Frappe Framework 为每个 DocType 自动生成 CRUD API，并允许调用标记为 whitelisted 的 Python 方法：

- v1：`/api/resource/{doctype}`、`/api/method/{dotted.path}`；
- v2：`/api/v2/document/{doctype}`、`/api/v2/doctype/{doctype}/meta`；
- 支持 API Key/Secret、Session 和 OAuth；
- 请求按 API 用户角色检查。

官方证据：[Frappe REST API](https://docs.frappe.io/framework/user/en/api/rest)

值得借的不是部署 Frappe API，而是给插件连接器定义稳定接口：

```text
list_jobs()
upsert_candidate()
create_application()
append_stage_event()
create_interview()
append_feedback()
create_offer_draft()
write_activity()
query_metrics()
```

这样招聘数据表、未来 Web 应用或其他 ATS 可以替换底层实现，Skill 不需要改业务逻辑。

## 四、OpenCATS 深入分析

OpenCATS 的核心优势是其 ATS 数据模型直接面向招聘 CRM，而不是整个 HRMS。

### 4.1 候选人、岗位与申请关系

OpenCATS 将三者拆开：

- `candidate`：候选人主档；
- `joborder`：岗位；
- `candidate_joborder`：候选人对某岗位的一条管道记录。

`candidate_joborder` 包含：

- `candidate_id`
- `joborder_id`
- 当前 `status`
- `date_submitted`
- `rating_value`
- `added_by`
- 创建/修改时间

数据库证据：

- 候选人、申请关系、阶段和历史：[`db/cats_schema.sql#L143-L292`](https://github.com/opencats/OpenCATS/blob/master/db/cats_schema.sql#L143-L292)
- 岗位：[`db/cats_schema.sql#L715-L759`](https://github.com/opencats/OpenCATS/blob/master/db/cats_schema.sql#L715-L759)

这是当前插件最应该优先借鉴的模型：

```text
Candidate 1 ── N Application N ── 1 Job
```

原因：

- 一个人可能明确投 A 岗，但 AI 同时推荐 B/C 岗；
- 一个人可能半年后重新申请；
- 每个岗位的阶段、评分、Owner、淘汰/放弃原因都不同；
- “候选人阶段”不应该是 Candidate 主档字段，而应该是 Application 字段。

### 4.2 管道状态和历史

OpenCATS 当前默认阶段：

```text
No Status
No Contact
Contacted
Candidate Responded
Qualifying
Submitted
Interviewing
Offered
Not in Consideration
Candidate Declined
Client Declined
Placed
```

证据：[`db/cats_schema.sql#L232-L270`](https://github.com/opencats/OpenCATS/blob/master/db/cats_schema.sql#L232-L270)

每次阶段变化：

- 更新 `candidate_joborder.status`；
- 写入 `candidate_joborder_status_history`；
- 额外写通用 `history` 审计；
- 可选发送状态变更邮件。

证据：

- 阶段变更：[`lib/Pipelines.php#L267-L347`](https://github.com/opencats/OpenCATS/blob/master/lib/Pipelines.php#L267-L347)
- 阶段历史：[`lib/Pipelines.php#L390-L422`](https://github.com/opencats/OpenCATS/blob/master/lib/Pipelines.php#L390-L422)
- 防止重复加入同一岗位管道：[`lib/Pipelines.php#L50-L121`](https://github.com/opencats/OpenCATS/blob/master/lib/Pipelines.php#L50-L121)

适合借鉴：

- 当前状态和历史事件分离；
- 同一候选人不能重复加入同一岗位的同一申请；
- 候选人主动放弃与公司淘汰必须分开；
- 阶段变更既是漏斗事实，也是审计事实。

需要改进后再借：

- OpenCATS 的阶段使用固定数字编码，不适合不同招聘工作区的可配置流程；
- `candidate_joborder_status_history` 本身没有 actor，actor 在另一张通用 history 表，查询和审计会被拆散；
- 我们应把 `actor_id / actor_type / reason_code / note / source_run_id` 直接放进 `application_stage_history`。

### 4.3 活动、面试和 Offer

OpenCATS 有通用 `activity`：

- Email、Meeting、Call、Other、Status Change；
- 关联候选人/岗位、发生时间、创建人和备注。

数据库证据：[`db/cats_schema.sql#L26-L70`](https://github.com/opencats/OpenCATS/blob/master/db/cats_schema.sql#L26-L70)

它还用 `calendar_event` 记录面试类日历事件；`calendar_event_type` 中存在 `Interview`。官方介绍也强调候选人活动、Calendar 和招聘过程：

- [Introduction and Overview](https://documentation.opencats.org/introduction-and-overview)
- [Screens In-Depth](https://documentation.opencats.org/using-the-software/screens-in-depth)

但 OpenCATS 没有像 Frappe 那样独立、结构化的：

- Interview Round；
- Interviewer 多人关联；
- 每位面试官技能评分；
- Offer 条款与正式 Offer 文档。

`Interviewing / Offered / Placed` 主要表现为管道阶段。

结论：

- **活动时间线借 OpenCATS；**
- **结构化面试和 Offer 借 Frappe。**

### 4.4 权限和审计

OpenCATS 有 0～500 的访问等级：

- Disabled
- Read Only
- Add/Edit
- Add/Edit/Delete
- Site Administrator
- Root

数据库证据：[`db/cats_schema.sql#L7-L24`](https://github.com/opencats/OpenCATS/blob/master/db/cats_schema.sql#L7-L24)

它还支持按用户 category 和 secured object 配置 ACL，并向上回退到父级对象：

- 源码：[`lib/ACL.php`](https://github.com/opencats/OpenCATS/blob/master/lib/ACL.php)
- 官方文档：[Access control lists](https://documentation.opencats.org/technical-configuration-options/access-control-lists)

但必须直接指出其限制：

- 源码只取用户的第一个 category；
- 官方 ACL 文档明确列出“并非所有地方都完成访问检查”等待完善项；
- 因此不能把它当作成熟行级/字段级 RBAC 方案。

审计方面：

- `history` 保存字段旧值、新值、说明、时间和 `entered_by`；
- 管道状态另有专门历史；
- `user_login` 保存 IP、User Agent、时间和成功状态。

证据：

- [`db/cats_schema.sql#L642-L674`](https://github.com/opencats/OpenCATS/blob/master/db/cats_schema.sql#L642-L674)
- [`db/cats_schema.sql#L973-L1029`](https://github.com/opencats/OpenCATS/blob/master/db/cats_schema.sql#L973-L1029)
- [`lib/History.php`](https://github.com/opencats/OpenCATS/blob/master/lib/History.php)

适合借鉴：

- append-only 的字段和阶段变更事件；
- 所有事件记录操作人；
- 候选人/岗位/申请统一活动时间线。

不适合：

- 按单一整数访问等级授权；
- 依赖 PHP 配置文件维护 ACL；
- 把“隐藏菜单”误当作后端权限。

### 4.5 报表

OpenCATS 用阶段历史计算：

- 新候选人；
- New Submissions；
- New Placements；
- 周/月/年 `Submitted / Interviewing / Placed`；
- 单岗位 pipeline、submitted、interviewing、placed；
- EEO 报表。

证据：

- 漏斗趋势：[`lib/Dashboard.php#L95-L168`](https://github.com/opencats/OpenCATS/blob/master/lib/Dashboard.php#L95-L168)
- 新增与放置统计：[`lib/Statistics.php#L56-L134`](https://github.com/opencats/OpenCATS/blob/master/lib/Statistics.php#L56-L134)
- 单岗位报告：[`lib/Statistics.php#L555-L635`](https://github.com/opencats/OpenCATS/blob/master/lib/Statistics.php#L555-L635)

官方文档也承认报告功能有限，部分报告入口尚未构建完成：[Screens In-Depth](https://documentation.opencats.org/using-the-software/screens-in-depth)。

适合借鉴：

- 所有漏斗 KPI 从阶段历史计算，不从当前状态反推；
- 按周/月/年比较；
- 按岗位下钻。

不适合：

- 仅统计 Submitted/Interviewing/Placed，不能满足我们的小红书内容 → 投递 → 有效候选 → 面试 → Offer 漏斗；
- 不采纳其 EEO 字段进入候选人评分。OpenCATS 的 Candidate 模型含性别、族裔、退伍军人和残障字段；当前插件已经明确禁止将受保护属性用于提取和评分，这条边界必须保留。

### 4.6 API 与扩展方式

OpenCATS 当前仓库没有 Frappe 那种现代、通用的 REST CRUD API。主要扩展点是：

- PHP 类和模块；
- `Hooks::get()` 拼接并由调用端 `eval()` 执行；
- XML/RSS 岗位导出；
- 老式 SOAP 简历解析接口；
- Career Portal。

证据：

- Hooks：[`lib/Hooks.php`](https://github.com/opencats/OpenCATS/blob/master/lib/Hooks.php)
- XML 岗位导出：[`lib/XmlJobExport.php`](https://github.com/opencats/OpenCATS/blob/master/lib/XmlJobExport.php)
- SOAP 简历解析：[`lib/ParseUtility.php`](https://github.com/opencats/OpenCATS/blob/master/lib/ParseUtility.php)
- WSDL：[`wsdl/parse.wsdl`](https://github.com/opencats/OpenCATS/blob/master/wsdl/parse.wsdl)

不应借鉴：

- `eval` 型 Hook；
- SOAP 解析依赖；
- 直接把 Agent 接到传统 PHP Session 页面；
- 以 XML feed 作为内部领域 API。

## 五、对当前插件的具体改造建议

当前源文件：

- `plugins/chubby-recruitment-ops/assets/recruitment-data-schema.json`
- `plugins/chubby-recruitment-ops/assets/config.example.json`
- `plugins/chubby-recruitment-ops/skills/recruitment-ops-run/SKILL.md`
- `plugins/chubby-recruitment-ops/skills/recruitment-ops-run/references/business-rules.md`

### P0-1：新增 Applications 表

当前：

- `candidates` 表含“候选阶段、明确投递岗位、AI Top1/2/3、评分、建议”；
- 这些字段大部分属于某次岗位申请，不属于候选人永久主档。
- `interviews_offers` 的 `stable_key` 配成“面试记录ID”，但该字段不在其 `required_fields` 中；现有 schema 自身无法保证每条面试记录有稳定主键。拆表时必须一起修正。

建议：

```text
candidates
  candidate_id
  name
  contact_hash
  resume_current
  consent_scope
  retention_status
  created_at
  updated_at

applications
  application_id
  candidate_id
  job_id
  application_type          explicit / ai_suggested / talent_mapping
  source_channel
  source_content_id
  source_message_id
  current_stage
  owner
  match_confidence
  screening_score
  screening_recommendation
  evidence_summary
  missing_materials
  human_review_status
  candidate_decline_reason
  company_close_reason
  created_at
  updated_at
```

约束：

- 至少唯一约束 `(candidate_id, job_id, source_message_id)`，具体需允许候选人未来重新申请；
- AI Top 2/Top 3 不直接创建正式申请，可先放 `application_suggestions` 或标记 `ai_suggested`；
- `候选人主动放弃` 与 `公司不推进` 必须是不同终态；
- 同一 Candidate 可同时拥有多个岗位 Application。

### P0-2：新增 Application Stage History

```text
application_stage_history
  event_id
  application_id
  from_stage
  to_stage
  reason_code
  note
  actor_type                human / automation / import
  actor_id
  source_run_id
  occurred_at
```

规则：

- append-only，不允许覆盖历史；
- AI 只能写“建议事件”，不能写入需人工批准的正式阶段；
- 正式阶段变化必须有 actor；
- 失败的自动化运行不能产生已完成阶段事件；
- 漏斗、阶段停留和转化率全部从该表计算。

### P0-3：拆分面试与 Offer

建议拆成：

```text
interview_templates
  template_id / job_id / round_name / skills / expected_rating / default_interviewers

interviews
  interview_id / application_id / round / scheduled_at / status / owner

interview_feedback
  feedback_id / interview_id / interviewer_id / skill_scores
  evidence / risk / recommendation / submitted_at

offers
  offer_id / application_id / version / approval_status / response_status
  drafted_at / approved_at / sent_at / responded_at
```

保留现有人工边界：

- 插件可以起草面试计划和 Offer 草稿；
- 不自动约面、不自动发 Offer；
- 面试反馈不能直接把申请人改成淘汰/录用；
- 工资等敏感字段不进入公开内容和普通日报。

### P1-1：加入业务活动时间线

当前 `mail_log` 和 `automation_log` 只覆盖技术运行，不足以还原招聘过程。

新增：

```text
recruitment_activity
  activity_id
  candidate_id
  application_id
  type                       email / call / meeting / interview / note / stage_change
  occurred_at
  actor_id
  summary
  external_reference
  visibility                 hr_only / interviewer / hiring_manager
```

这样能回答：

- 候选人最后一次被联系是什么时候；
- 谁做了什么；
- 为什么阶段改变；
- SLA 是否超时。

### P1-2：升级报表

基于内容表、Application 和 Stage History 增加：

- 内容 → 投递转化；
- 投递 → 有效候选；
- 有效候选 → HR Screen；
- HR Screen → 面试；
- 面试 → Offer；
- Offer 接受率；
- 各阶段中位停留时间；
- Time to first review；
- Time to interview；
- Time to fill；
- 按岗位、来源、内容版本、Owner 拆分；
- 候选主动放弃原因与公司不推进原因分开。

无法获得小红书阅读/点击时继续显示“不可得”，不能用点赞量代替。

### P1-3：来源和内容归因升级

Application 应直接保存：

- `source_channel`
- `source_content_id`
- `attribution_code`
- `first_touch`
- `last_touch`

不要只在候选人主档保存一个永久 `来源渠道`，否则候选人二次投递时会覆盖真实来源。

### P1-4：权限与 PII

建议角色：

| 角色 | 能力 |
|---|---|
| recruitment_admin | 配置、连接器、字段映射、所有招聘记录 |
| recruiter | 所负责岗位、候选人、申请和沟通 |
| interviewer | 只读必要候选摘要；写本人被分配面试反馈 |
| hiring_manager | 查看本工作区漏斗、候选摘要和人工决策 |
| auditor | 只读运行日志、阶段历史和授权记录 |

PII 进一步拆分：

- 面试官默认不需要查看完整邮箱、电话、身份证明；
- 日报继续使用候选人代号；
- Offer/薪资信息放在权限更窄的表或受限视图；
- Skill 里的 allowlist 只是软控制，必须由招聘数据表权限承担最终强制控制。

### P2-1：消息模板 + 人工发送队列

借鉴 OpenCATS 的状态邮件模板，但不直接自动发送：

- 收件确认；
- 补材料；
- 面试邀请；
- 面试改期；
- 流程结束；
- Offer 草稿。

每条候选人消息先进入 `pending_communications`，记录模板版本、收件人、申请、审批人、发送状态；人确认后才调用 Gmail/协作工具。

### P2-2：可选投递表单

Frappe Job Portal 和 OpenCATS Career Portal 都证明结构化入口能减少“没写岗位、附件缺失、来源丢失”。

但当前渠道战略是小红书 + 邮箱，所以不应立刻做完整职位门户。更轻的方案：

- 小红书正文仍留招聘邮箱；
- 提供可选的移动端投递表单；
- 表单预填岗位 ID / 内容归因码；
- 明确候选人数据用途、保留期限和撤回方式；
- 表单最终仍进入同一 Application 模型。

### P2-3：适配器接口

将 Skill 里的“自动选择 邮箱/协作工具”升级为明确 adapter contract：

- `GmailAdapter`
- `DataTableAdapter`
- `NotificationAdapter`
- 未来 `ATSAdapter`

所有写操作输入输出使用 JSON Schema，并带：

- idempotency key；
- actor；
- source run id；
- readback result；
- error classification。

## 六、不建议加入的能力

1. **不部署完整 Frappe HRMS**
   - 插件目前不需要薪酬、考勤、请假、员工主数据和 ERPNext 依赖。
   - 只有当公司决定统一替换 HRIS/ATS，才应单独评估 Frappe，而不是让招聘插件偷偷演变成 HR 平台。

2. **不嵌入 OpenCATS**
   - 会新增 PHP、MariaDB、Web 服务器、独立登录体系和升级维护。
   - 当前插件已经以招聘数据表作为工作区事实源，再引入 OpenCATS 会产生双主数据。

3. **不复制代码**
   - Frappe GPL-3.0；
   - OpenCATS 存在 MPL-2.0 与 CATS Public License 1.1a 的逐文件许可证边界。

4. **不采用 OpenCATS 的固定数字阶段**
   - 招聘流程不同，应使用可配置状态机和稳定字符串 ID。

5. **不采用 OpenCATS 的 ACL 实现**
   - 只取第一个 category，官方文档也承认覆盖未完成。

6. **不采用 `eval` Hook、SOAP Resume Parser 和 XML 内部 API**
   - 与当前插件安全边界、可测试性和现代连接器架构不符。

7. **不引入受保护属性评分**
   - OpenCATS 的 EEO 字段可能适用于特定地区独立合规报告，但年龄、性别、民族、国籍、残障等不得进入当前插件的匹配或初筛评分。

8. **不自动发送阶段变更邮件**
   - OpenCATS 支持阶段变化触发邮件；当前插件应只生成待审批消息，避免 AI/自动化误发淘汰、面试或 Offer。

## 七、建议的版本路线

### v0.2：正确的数据模型

- Applications；
- Application Stage History；
- Candidate Declined / Company Closed 分开；
- Interviews / Feedback / Offers 拆分；
- 数据迁移与兼容读取；
- schema、doctor、acceptance 和测试同步升级。

验收标准：

- 同一候选人可关联两个岗位，阶段互不覆盖；
- 同一岗位阶段变更可完整还原；
- AI 建议不会自动成为正式阶段；
- 多面试官反馈互不覆盖；
- Offer 草稿不能自动发送。

### v0.3：招聘运营与审计

- 活动时间线；
- 阶段 SLA；
- 来源/内容归因；
- 招聘漏斗与阶段停留；
- 操作人、原因码和事件审计；
- 协作工具权限配置检查。

### v0.4：候选人体验和连接器

- 待审批消息模板；
- 可选移动投递表单；
- Adapter contract；
- 可接入其他 ATS，但不新增第二份主数据。

## 八、最终判断

如果只选三个能力进入下一版，应选：

1. **OpenCATS 的 Candidate–Application–Job 多对多模型；**
2. **OpenCATS 的阶段历史 + 活动时间线；**
3. **Frappe 的多轮面试、多人结构化反馈和独立 Offer。**

这三项会把当前插件从“能处理简历、给出 AI 建议”升级成“能够长期承载真实招聘事实的轻量 ATS 工作流”。继续增加内容模板或更复杂的 AI 分数，不先修这三个模型，规模一大就会出现阶段覆盖、多人反馈丢失、来源归因失真和无法审计的问题。
