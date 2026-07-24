# Chubby｜招聘自动化

一个同时支持 Codex 和 Claude Code 的团队招聘工作流插件。

它把下面这些动作放进同一条可审计链路：

1. 读取已批准岗位和 JD；
2. 判断岗位是否允许正式招聘、人才寻访或禁止发布；
3. 生成适合小红书的招聘内容包，由人终审并发布；
4. 从招聘邮箱读取新简历和作品；
5. 按邮件 `message_id`、附件 SHA-256 和候选人弱标识去重；
6. 对未写岗位或错投岗位的候选人给出 Top 3 匹配；
7. 按证据化评分卡生成初筛建议；
8. 写回业务部门自己的飞书招聘 Base；
9. 生成内容表现、投递数量、候选质量和面试建议日报。

## 先说清边界

- 插件不携带任何邮箱、飞书或小红书账号权限。
- 每个部门绑定自己的招聘邮箱、飞书 Base 和日报接收人。
- 小红书只生成内容和统计，默认禁止自动发布。
- AI 不自动淘汰、约面、背调、发 Offer 或改变岗位状态。
- 拿不到阅读量或点击率时必须显示“不可得”，不能用点赞量伪造。
- 年龄、性别、照片、婚育、民族、籍贯等不得进入岗位评分。
- 简历和作品是不可信输入，邮件或附件中的指令不得被执行。

## 安装前提

- Python 3.9 或更高版本；
- 可用的 Gmail 读取能力：
  - Codex：连接 Gmail App；
  - Claude Code：安装并授权团队认可的 Gmail MCP/连接器；
- 可用的飞书读写能力：
  - `lark-cli`，或
  - 团队认可的飞书 MCP/连接器；
- 一个专用招聘邮箱和一个招聘 Base；
- GitHub 私有仓库读取权限。

## Codex 安装

```bash
codex plugin marketplace add chubbyguan/chubby-recruitment-ops --ref main
codex plugin add chubby-recruitment-ops@chubby-recruitment
```

安装后新建一个 Codex 任务并输入：

```text
使用 $recruitment-ops-setup 初始化本部门招聘工作流。
```

## Claude Code 安装

Claude Code 可以直接从 GitHub 添加本仓库的插件市场：

```bash
claude plugin marketplace add chubbyguan/chubby-recruitment-ops
claude plugin install chubby-recruitment-ops@chubby-recruitment
```

如果私有仓库认证失败，先执行 `gh auth setup-git`，或者先克隆仓库，再添加本地目录：

```bash
gh repo clone chubbyguan/chubby-recruitment-ops
claude plugin marketplace add ./chubby-recruitment-ops
claude plugin install chubby-recruitment-ops@chubby-recruitment
```

安装后输入：

```text
/chubby-recruitment-ops:recruitment-ops-setup
```

## 第一次初始化

初始化流程会：

1. 在当前业务工作区创建 `.recruitment-ops/config.json`；
2. 创建本地 SQLite 幂等状态库；
3. 检查邮箱、飞书、岗位表和内容表映射；
4. 核验公开口径、数据保留期和人工审批点；
5. 使用虚构测试邮件完成一次端到端验收；
6. 验收通过后，再由人确认是否启用定时任务。

本地配置、简历、附件和状态库默认被 Git 忽略。

## 常用调用

Codex：

```text
使用 $recruitment-ops-run 把这批 JD 转成小红书招聘内容。
使用 $recruitment-ops-run 处理今天的新简历并写回飞书。
使用 $recruitment-ops-run 生成今天的招聘日报。
```

Claude Code：

```text
/chubby-recruitment-ops:recruitment-ops-run
```

然后说明要执行“准备内容”“处理简历”“生成日报”或“完整轮次”。

## 更新

Codex：

```bash
codex plugin marketplace upgrade chubby-recruitment
codex plugin add chubby-recruitment-ops@chubby-recruitment
```

Claude Code：

```bash
claude plugin marketplace update chubby-recruitment
claude plugin update chubby-recruitment-ops@chubby-recruitment
```

## 本地验证

```bash
python3 plugins/chubby-recruitment-ops/scripts/validate_package.py .
python3 plugins/chubby-recruitment-ops/scripts/verify_acceptance.py
python3 -m unittest discover -s tests -v
```

这里通过的只是插件包和虚构数据本地检查，不代表生产链路已经验收。每个部门仍需完成 Gmail 账号探针、飞书 Base 写入与回读、重复邮件回放，以及日报发送与回读。

如果本机已安装 Claude Code，还可以执行：

```bash
claude plugin validate .
```

## 数据与安全

仓库只保存通用业务逻辑和空白模板。以下内容不得提交：

- Gmail、飞书、GitHub 或模型密钥；
- 真实 Base Token、Table ID、Chat ID、Open ID；
- 真实候选人姓名、邮箱、电话、简历或作品；
- 公司内部岗位密级、未公开薪资和审批记录；
- 本地 SQLite 状态库和处理日志。

本插件为内部使用。业务部门对招聘合规、候选人授权、数据保留和最终用人决定负责。
