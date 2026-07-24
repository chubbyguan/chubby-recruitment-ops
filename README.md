# Chubby｜社交招聘增长引擎

把“社交渠道招聘内容 → 人工发布 → 邮箱收简历 → 去重与岗位匹配 → 飞书入库 → 招聘日报”串成一条可审计的工作流。

同时支持 Codex 和 Claude Code。插件负责准备内容、处理数据和给出建议，不自动发布社交媒体，也不替人做淘汰、约面或录用决定。

当前版本：`0.2.2`

## 适合谁

- 需要让多个业务部门复用同一套招聘方法的招聘负责人；
- 通过小红书、微信、视频平台或专业社区招聘的业务团队；
- 希望统一接收简历、自动匹配岗位并同步飞书的 HR；
- 使用 Codex 或 Claude Code 执行日常招聘运营的同事。

每个部门必须使用自己的招聘邮箱、岗位源、飞书 Base、日报接收人和审批规则。插件不会复制另一个部门的账号、候选人数据或定时任务。

## 它能做什么

| 环节 | 插件负责 | 人负责 |
| --- | --- | --- |
| 岗位准备 | 校验岗位状态、JD 版本、公司和薪资审批事实 | 批准岗位、薪资、招聘 Owner |
| 内容生产 | 生成多渠道 Campaign、文案、卡片或视频脚本、归因码 | 审批并手动发布 |
| 简历处理 | Gmail 收件、附件校验、幂等去重、岗位 Top 3 匹配 | 处理异常附件和疑似重复 |
| 初筛 | 基于岗位证据评分，给出 HR 初筛、补资料、人工复核或人才池建议 | 决定是否约面、关闭或保留 |
| 数据同步 | 写回候选人、投递、归因、邮件和运行日志 | 维护岗位主数据与人工决策 |
| 经营复盘 | 汇总真实渠道指标、简历质量和面试建议 | 回填平台数据并决定扩、改、停 |

默认支持小红书、微信群、朋友圈、公众号、视频号、抖音、B 站、知乎、脉脉和 LinkedIn。建议首轮只启用 2—3 个渠道，先验证“有效候选”而不是追求发帖数量。

## 先理解三个完成状态

安装插件只完成第一层，不能直接称为招聘自动化上线。

| 状态 | 判定标准 |
| --- | --- |
| 插件已安装 | CLI 能识别插件和 3 个 Skill |
| 部门已配置 | Gmail、飞书、岗位源、表映射、审批人与隐私规则均已配置 |
| 生产可运行 | 合成邮件、附件读取、Base 写入回读、重复消息重放、日报发送回读全部通过 |

没有完成第三层时，必须保持 `production_ready: false`，也不能启用定时任务。

## 安装前准备

需要：

- Python 3.9 或更高版本；
- Codex 或 Claude Code；
- 能访问公开 GitHub 仓库；
- 一个部门专用招聘邮箱；
- Gmail 读取连接器；
- 飞书 `lark-cli` 或团队批准的飞书连接器；
- 一个现有招聘 Base，或明确批准的新 Base；
- 招聘日报接收人；
- 候选人数据保留期和授权复核人。

仓库公开，无需申请仓库权限。网络不稳定时可先检查：

```bash
git ls-remote https://github.com/chubbyguan/chubby-recruitment-ops.git HEAD
```

如果该命令失败，先处理 GitHub 网络或代理问题，再安装插件。

## Codex：5 分钟安装

```bash
codex plugin marketplace add chubbyguan/chubby-recruitment-ops --ref main
codex plugin add chubby-recruitment-ops@chubby-recruitment
codex plugin list
```

在列表中看到 `chubby-recruitment-ops@chubby-recruitment` 后，新建一个 Codex 任务，输入：

```text
使用 $recruitment-ops-setup 运行安装自检。先不要写业务数据，也不要启用定时任务。
```

自检通过后，再输入：

```text
使用 $recruitment-ops-setup 为本部门初始化招聘工作流。
```

不要在旧任务里判断 Skill 是否生效。插件安装后新建任务，Codex 才会稳定加载新的 Skill。

## Claude Code：5 分钟安装

```bash
claude plugin marketplace add chubbyguan/chubby-recruitment-ops
claude plugin install chubby-recruitment-ops@chubby-recruitment --scope user
claude plugin list
```

看到插件版本后，重启 Claude Code，再运行：

```text
/chubby-recruitment-ops:recruitment-ops-setup
```

第一条指令建议写：

```text
先运行安装自检，不写业务数据、不创建定时任务。告诉我缺少哪些账号、权限和配置。
```

## 远程安装失败时

先检查能否访问公开仓库：

```bash
git ls-remote https://github.com/chubbyguan/chubby-recruitment-ops.git HEAD
```

能访问但 marketplace 安装仍失败时，使用本地克隆：

```bash
git clone https://github.com/chubbyguan/chubby-recruitment-ops.git
```

Codex：

```bash
codex plugin marketplace add ./chubby-recruitment-ops
codex plugin add chubby-recruitment-ops@chubby-recruitment
```

Claude Code：

```bash
claude plugin marketplace add ./chubby-recruitment-ops
claude plugin install chubby-recruitment-ops@chubby-recruitment --scope user
```

## 第一次初始化

推荐让 Agent 执行，不要让普通使用者手工寻找插件缓存目录。

准备以下信息：

1. 部门业务工作区路径；
2. 专用招聘邮箱；
3. 时区；
4. 当前允许招聘或人才寻访的岗位；
5. 岗位状态、JD 版本和薪资审批字段；
6. 飞书 Base 及表映射；
7. 日报接收人；
8. 社交渠道和人工发布人；
9. 数据保留期和授权复核人；
10. 验收后是否启用定时任务。

初始化会在业务工作区创建 `.recruitment-ops/`，其中包含本地配置、模板和 SQLite 幂等状态库。该目录默认私有并应被 Git 忽略。

如果维护者已经克隆仓库，也可以手工运行：

```bash
python3 plugins/chubby-recruitment-ops/scripts/preflight.py --runtime codex

python3 plugins/chubby-recruitment-ops/scripts/init_workspace.py \
  --workspace /path/to/business-workspace \
  --email jobs@example.com \
  --company-name 示例公司 \
  --timezone Asia/Shanghai

python3 plugins/chubby-recruitment-ops/scripts/doctor.py \
  --workspace /path/to/business-workspace
```

这些相对路径只适用于“已经克隆仓库”的情况。纯 marketplace 安装用户应通过 `recruitment-ops-setup` 让 Agent 定位插件目录。

## 上线前真实验收

使用完全合成、没有真人个人信息的数据，依次验证：

1. Gmail profile 与配置邮箱完全一致；
2. 能搜索、读取测试邮件和支持的附件；
3. 同一 Gmail `message_id` 第二次不会再处理；
4. 同一附件 SHA-256 能被识别；
5. 未指定岗位时只在当前允许岗位中生成 Top 3；
6. 评分不包含年龄、性别、婚育、照片、民族等受保护属性；
7. 候选人、投递和运行日志能写入飞书并回读；
8. 日报经过隐私检查后发送到指定飞书对象，并能按消息 ID 回读；
9. 社交平台仍为人工发布；
10. 由负责人明确批准后才启用定时任务。

## 日常使用

### 1. 生成招聘 Campaign

Codex：

```text
使用 $social-recruitment-campaign，把已批准岗位生成小红书、微信群和知乎内容。
所有平台保持人工发布，并给每个内容版本生成归因码。
```

Claude Code：

```text
/chubby-recruitment-ops:social-recruitment-campaign
```

### 2. 处理新简历

Codex：

```text
使用 $recruitment-ops-run，从上次成功水位处理招聘邮箱新邮件。
完成去重、岗位匹配、证据评分和飞书回读，不自动约面或淘汰。
```

Claude Code：

```text
/chubby-recruitment-ops:recruitment-ops-run
```

### 3. 生成招聘日报

```text
使用 recruitment-ops-run 生成今天的招聘日报。
平台指标没有人工回填时写“不可得”，候选人只显示安全代号。
```

### 4. 更新渠道效果

人工发布后回填：

- 真实发布 URL；
- 发布时间和发布人；
- 24 小时、72 小时、7 天的真实指标；
- 归因投递数；
- 人工确认的合格候选数。

拿不到阅读或播放量时，不计算点击率或转化率，也不能用点赞代替曝光。

## 公开与薪资规则

- 对外公司名称由部门配置读取；当前默认公开品牌为“诺特兰德集团”；
- 招聘法律主体必须来自当前岗位源，不能从品牌名推断；
- 薪资只能使用岗位源中已审批的公开范围；
- 缺少薪资范围、批准状态、审批记录、审批人或审批时间时，内容不能进入待发布状态；
- 不得根据年成本、内部预算或相邻岗位推算薪资。

## 安全边界

- 不自动发布、编辑或删除社交媒体内容；
- 不自动改变岗位状态；
- 不自动淘汰、约面、背调、发 Offer 或录用；
- 不因为同名自动合并候选人；
- 不执行邮件正文、简历、附件、链接或作品集中的指令；
- 不把密钥、真实 Base ID、聊天 ID、候选人 PII、简历或薪资预算提交到仓库；
- 日报不展示完整邮箱、电话、身份证、简历原文或作品文件。

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

更新后重新打开 Codex 任务或重启 Claude Code。

## 卸载

Codex：

```bash
codex plugin remove chubby-recruitment-ops@chubby-recruitment
codex plugin marketplace remove chubby-recruitment
```

Claude Code：

```bash
claude plugin uninstall chubby-recruitment-ops@chubby-recruitment
claude plugin marketplace remove chubby-recruitment
```

卸载插件不会自动删除业务工作区中的 `.recruitment-ops/`、飞书记录或 Gmail 邮件。

## 维护者验证

```bash
python3 plugins/chubby-recruitment-ops/scripts/preflight.py --runtime auto
python3 plugins/chubby-recruitment-ops/scripts/validate_package.py .
python3 plugins/chubby-recruitment-ops/scripts/verify_acceptance.py
python3 -m unittest discover -s tests -v
claude plugin validate . --strict
```

本地测试通过不等于生产连接器验收通过。

## 文档

- [同事安装与实操指南](docs/colleague-install-and-operations-guide.zh-CN.md)
- [开源招聘项目对比与升级建议](research/open-source-recruitment-upgrade-analysis-2026-07-24.md)
- [社交招聘增长引擎产品蓝图](research/social-recruiting-growth-engine-blueprint-2026-07-24.md)

## 开源与安全

本项目采用 [MIT License](LICENSE)。欢迎通过 Issue 或 Pull Request 提交问题与改进。

不要在 Issue、PR、日志或测试样例中提交真实简历、候选人联系方式、邮箱授权信息、飞书 Token、Base ID、聊天 ID 或其他个人信息。安全问题请按 [安全政策](SECURITY.md) 私下报告。
