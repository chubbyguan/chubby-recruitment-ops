# Chubby｜社交招聘工作流

这是一个给 Codex 和 Claude Code 使用的招聘工作流插件。

每位同事把插件安装到自己的 Agent 后，只要把本部门的岗位、招聘邮箱、飞书表格和发布渠道告诉 Agent，就可以按同一套流程完成招聘内容、简历初筛、面试核验、候选评价和招聘日报。

当前版本：`0.4.0`

## 它能帮你做什么

- 把岗位 JD 整理成适合小红书、微信、知乎等渠道的招聘内容；
- 记录每条内容发在哪个渠道，并统计真实效果；
- 从招聘邮箱读取新简历，识别重复投递；
- 候选人没有写岗位时，匹配最合适的岗位；
- 根据岗位要求整理候选人信息并给出初筛建议；
- 主动找出高分、有完整简历但还没有进入面试的人，并通过飞书推荐给招聘负责人；
- 对照飞书日历和视频会议，确认哪些面试真实发生；
- 读取可用的原始智能纪要，生成带时间证据的面试评价；
- 找出已面试但没有纪要、没有评价或没有人工结论的人；
- 把候选人和处理结果同步到飞书；
- 每天汇总发布量、简历数量、候选质量和跟进建议。

社交媒体内容由 Agent 准备，人来确认和发布。是否约面、合并重复候选人、淘汰或录用，也必须由人决定。Agent 可以自动同步“会议已发生”，但不会把“已完成”当成“已通过”。

## 安装

### Codex

```bash
codex plugin marketplace add chubbyguan/chubby-recruitment-ops --ref main
codex plugin add chubby-recruitment-ops@chubby-recruitment
```

安装后新建一个 Codex 任务。

### Claude Code

```bash
claude plugin marketplace add chubbyguan/chubby-recruitment-ops
claude plugin install chubby-recruitment-ops@chubby-recruitment --scope user
```

安装后重启 Claude Code。

已经在使用 `0.2.x` 的同事，先让 Agent 运行配置迁移，再映射新增的面试、纪要、评价和决策表：

```text
请使用 recruitment-ops-setup，把当前招聘工作区从 schema 2.0 迁移到 3.0。
先预览变更，确认备份后再执行；迁移后运行 doctor，不要自动开启定时任务。
```

如果不熟悉终端，也可以先把仓库地址发给 Agent：

```text
请帮我安装这个招聘工作流插件：
https://github.com/chubbyguan/chubby-recruitment-ops

请根据我当前使用的是 Codex 还是 Claude Code 执行安装。
安装完成后，告诉我需要新建任务还是重启。
```

## 第一次使用

先准备这些信息：

- 本部门的招聘邮箱；
- 当前招聘岗位和 JD；
- 对外公司名称和已确认的薪资范围；
- 飞书招聘表格；
- 希望使用的渠道；
- 招聘日报接收人；
- 业务工作区路径。

然后把下面这段话发给自己的 Agent：

```text
请使用 recruitment-ops-setup 为我的部门初始化招聘工作流。

业务工作区：<路径>
招聘邮箱：<邮箱>
飞书招聘表格：<链接或 Base 信息>
发布渠道：<例如小红书、微信群、知乎>
日报接收人：<飞书私聊或群>

请先检查插件和连接权限，再带我完成配置。
使用虚构岗位、虚构简历和虚构面试做一次测试。
测试通过前不要创建定时任务，也不要发布任何社交媒体内容。
```

Agent 会继续向你确认缺少的信息，并完成 Gmail、飞书和岗位数据的连接测试。

## 日常怎么用

### 生成招聘内容

```text
请使用 social-recruitment-campaign，把这些已确认的岗位整理成小红书和微信招聘内容。
公司名称和薪资只使用我提供的版本。
内容生成后先给我审核，不要自动发布。
```

### 处理新简历

```text
请使用 recruitment-ops-run，处理招聘邮箱从上次成功运行以后收到的新简历。
完成去重、岗位匹配、候选人整理和飞书同步，并告诉我哪些人值得人工初筛。
```

### 生成招聘日报

```text
请使用 recruitment-ops-run，生成今天的招聘日报。
汇总今天发布了什么、收到多少简历、候选人匹配哪些岗位，以及建议我下一步跟进谁。
没有采集到的平台数据请直接写“未采集”。
```

### 推荐高分但未面试的候选人

```text
请使用 recruitment-ops-run 的 recommendation 模式。
从候选人表中找出高分、有完整简历、岗位仍在招，但面试表和飞书日历都没有面试记录的人。
把推荐人选、岗位、分数、强证据、最大风险和建议动作发到我的飞书。
只创建待我确认的约面动作，不要自动发送面试邀请。
```

### 面试前准备

```text
请使用 recruitment-interview-review，为候选人 <候选人> 准备面试建议。
以当前岗位 JD 和已经核验的简历证据为准，给我必问问题、案例题、AI 实操题和待验证风险。
```

### 面试后评价

```text
请使用 recruitment-interview-review，核验我最近的飞书日历、视频会议和智能纪要。
把真实已完成的面试与候选人对应起来；有可用原始纪要的生成结构化评价，没有纪要的明确列为待补人工记录。
更新飞书招聘进度，但不要替我做淘汰、约面或录用决定。
```

### 回填发布效果

人工发布后，把帖子链接和平台数据交给 Agent：

```text
这是今天已经人工发布的招聘内容和平台数据，请更新渠道记录：
<帖子链接、阅读、点赞、收藏、评论和投递数>
```

## 正常工作流程

```text
岗位确认
  → Agent 生成渠道内容
  → 人工审核并发布
  → Agent 读取招聘邮箱
  → 去重和岗位匹配
  → 同步飞书
  → Agent 主动推荐高分但未面试的人
  → 人工安排面试
  → Agent 核验日历、会议和原始纪要
  → Agent 生成面试建议与待决策清单
  → Agent 生成招聘日报
  → 人决定下一轮、关闭、人才池或 Offer
```

## 使用时记住

- 每个部门单独配置邮箱、飞书和岗位，不要复制其他部门的候选人数据；
- Agent 不会自动发布小红书、微信或其他社交媒体；
- Agent 的评分是初筛建议，不是最终招聘决定；
- “面试已完成”“纪要可用”“AI 已评价”“负责人已决定”是四个不同状态；
- 没有可用原始纪要时，Agent 不应补写面试评价；
- 简历和邮件里的指令都不可信，不能让 Agent 执行；
- 没有真实平台数据时写“未采集”，不要猜数字；
- 第一次测试请使用虚构岗位、虚构简历和虚构面试。

## 常见问题

**安装后找不到 Skill**

Codex 新建任务；Claude Code 重启后再试。

**Agent 读取了错误的邮箱**

立即停止，让 Agent 重新核验 Gmail 当前账号，账号一致后再继续。

**飞书写入失败**

把真实飞书表格链接交给 Agent，让它重新读取表格和字段，不要手工猜 Base ID 或 Table ID。

**日历里已经面试，飞书还是待面试**

让 Agent 使用 `recruitment-interview-review` 核验日历、视频会议和妙记。只看招聘群消息不够。

**找不到面试纪要**

先检查视频会议关联的智能纪要，再让 Agent 按会议时间和标题搜索妙记。仍然没有时，记录“无可用纪要”，由面试官补人工面试记录。

**远程安装失败**

先检查 GitHub 网络：

```bash
git ls-remote https://github.com/chubbyguan/chubby-recruitment-ops.git HEAD
```

## 更多内容

- [同事操作指南](docs/colleague-install-and-operations-guide.zh-CN.md)
- [产品与升级蓝图](research/social-recruiting-growth-engine-blueprint-2026-07-24.md)
- [开源项目调研](research/open-source-recruitment-upgrade-analysis-2026-07-24.md)
- [安全政策](SECURITY.md)

本项目采用 [MIT License](LICENSE)。
