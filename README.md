# HireMS · 飞书招聘自动化系统

从简历邮件到 Offer 的本地招聘工作台。

```text
飞书企业邮箱 / 本地上传
        ↓
简历附件解析（PDF / DOCX / TXT / MD）
        ↓
AI 结构化提取 + 规则初筛 + 语义评分
        ↓
HR 候选人队列 / 飞书互动卡片
        ↓
面试排期、评价、Offer 跟踪
        ↓
飞书多维表格、日历与提醒
```

## 功能

- 从飞书企业邮箱（IMAP）抓取简历附件，按邮件与附件指纹自动去重。
- 解析简历，提取候选人信息；按岗位要求进行 AI/规则筛选与评分。
- 将候选人同步至飞书多维表格，保留本地待复核队列。
- 发送飞书互动消息卡片，HR 可直接点击“通过 / 淘汰 / 安排面试”。
- 管理一面、二面、终面、面试评价和 Offer 状态。
- 创建飞书日历日程；排期前检查 HireMS 冲突，并可查询已授权面试官日历。
- 每日 09:00 处理汇总、48 小时未处理提醒、面试前 1 小时提醒。

## 快速开始

环境要求：Python 3.10+，以及一个 OpenAI 兼容的 LLM/Embedding 服务。

```powershell
cd HireMS
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`，至少配置 LLM 与 Embedding：

```env
HIREMS_LLM_API_KEY=你的密钥
HIREMS_LLM_BASE_URL=https://你的兼容接口/v1
HIREMS_LLM_MODEL=你的模型名

EMBEDDING_API_KEY=你的密钥
EMBEDDING_BASE_URL=https://你的兼容接口/v1
EMBEDDING_MODEL=你的嵌入模型名
VECTOR_DB=chroma

# 可选：原始简历本地保存目录
RESUME_FILE_DIR=./data/resumes
```

启动本地 Web UI：

```powershell
python -m uvicorn app.main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000/>。接口文档位于 <http://127.0.0.1:8000/docs>。

### 大规模候选人筛选

筛选会先从全部已入库简历的向量索引中召回最相关的候选池，再执行经验、学历、技能、地点等硬过滤与评分；只有评分靠前的候选人才会调用 LLM 生成评价。默认配置为“召回 50 份、分析前 10 份”，因此不会对所有简历逐份调用 LLM。

```env
SCREENING_RETRIEVAL_LIMIT=50
SCREENING_ANALYSIS_LIMIT=10
CANDIDATE_ANALYSIS_MAX_TOKENS=360
CANDIDATE_ANALYSIS_MAX_CHARS=260
```

候选池较大时可将 `SCREENING_RETRIEVAL_LIMIT` 调至 100～200；若响应时间或成本更重要，则优先下调 `SCREENING_ANALYSIS_LIMIT`。每份 AI 评价会被限制为四条简短要点，并在本地兜底截断，避免队列卡片显示半截报告。

## HR 使用流程

1. 在“收件与入库”中同步邮箱或手动导入简历。
2. 输入岗位要求，例如“3 年以上 Python 后端、熟悉 FastAPI、本科及以上、北京”。
3. 运行筛选，查看 AI 评分和候选人摘要。
4. 在候选人卡片或“招聘流程”看板中查看原始简历，并执行通过、淘汰、安排面试。
5. 完成面试评价后推进到 Offer；跟踪待发、已发、已接受或已拒绝。

没有配置飞书时，以上流程仍可在本地 SQLite 工作流中完整运行；配置完成后自动接入飞书。

## 飞书配置

所有敏感信息仅放在 `.env`，不要提交到 Git 或输入 Web UI。

### 1. 邮箱抓取

```env
MAIL_IMAP_HOST=imap.feishu.cn
MAIL_IMAP_PORT=993
MAIL_IMAP_USER=hr@example.com
MAIL_IMAP_PASSWORD=邮箱专用应用密码
MAIL_IMAP_FOLDER=INBOX
MAIL_SUBJECT_KEYWORDS=简历,应聘,求职
MAIL_LOOKBACK_DAYS=7
```

需要由管理员先开通 IMAP/第三方客户端访问。若企业邮箱不支持 IMAP，可保留手动导入，或实现企业已批准的邮件 API 适配器。

### 2. 多维表格

```env
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_BITABLE_APP_TOKEN=app_xxx
FEISHU_BITABLE_TABLE_ID=tbl_xxx
FEISHU_EXPORT_MIN_SCORE=0.70
```

目标表建议包含：`姓名`、`邮箱`、`电话`、`岗位`、`匹配度`、`技能`、`期望地点`、`AI分析`、`处理状态`。应用必须拥有该多维表格的编辑权限。

### 3. 飞书消息卡片与回调

```env
FEISHU_HR_RECEIVER_IDS=ou_xxx,ou_yyy
FEISHU_CALLBACK_TOKEN=你的回调Token
FEISHU_PUBLIC_BASE_URL=https://hirems.example.com
```

在飞书开放平台为自建应用启用机器人能力，并订阅 `card.action.trigger`。回调地址：

```text
https://你的公网域名/api/v1/feishu/card-actions
```

本机 `localhost` 不可被飞书访问；测试交互卡片需使用飞书长连接或公司批准的公网网关。

### 4. 面试日历与忙闲检查

```env
FEISHU_CALENDAR_ID=feishu.cn_xxx@group.calendar.feishu.cn
FEISHU_INTERVIEWER_CALENDAR_MAP={"ou_xxx":"feishu.cn_xxx@group.calendar.feishu.cn"}
```

`FEISHU_CALENDAR_ID` 是招聘共享日历，应用须有 writer/owner 权限。面试官个人日历不能在未授权时读取；通过 `FEISHU_INTERVIEWER_CALENDAR_MAP` 明确配置可读日历后，系统会在排期前查询对应的忙闲状态。

外部候选人通常没有企业飞书 Open ID，因此目前飞书提醒发送给 HR/面试官；候选人通知建议对接企业邮件或短信服务。

## 自动提醒

服务运行期间，APScheduler 会执行：

| 任务 | 频率 | 行为 |
| --- | --- | --- |
| 昨日处理汇总 | 每天 09:00 | 推送处理数量、状态分布和待复核数量 |
| 超时提醒 | 每 30 分钟 | 提醒超过 `NOTIFY_OVERDUE_HOURS` 未复核的候选人 |
| 面试提醒 | 每 5 分钟检查 | 对一小时内开始的面试提醒面试官 |

提醒未配置飞书时会保留在本地运行记录，不会丢失流程状态。

## 核心接口

所有接口前缀为 `/api/v1`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/resumes` | 手动上传简历 |
| `GET` | `/resumes/{id}/file` | 预览原始简历；追加 `?download=true` 可下载 |
| `POST` | `/operations/mail-sync` | 同步邮箱附件 |
| `POST` | `/queries` | 提交岗位要求 |
| `GET` | `/results/{query_id}` | 执行筛选并返回结果 |
| `GET` | `/workflow/candidates` | 获取招聘流程队列 |
| `POST` | `/workflow/candidates/{id}/action` | 通过、淘汰或安排面试 |
| `POST` | `/workflow/interviews` | 创建面试排期 |
| `POST` | `/workflow/interviews/{id}/feedback` | 回填面试评价 |
| `POST` | `/workflow/candidates/{id}/offer` | 更新 Offer 状态 |
| `POST` | `/workflow/notifications/{kind}` | 手动触发 `daily_summary`、`overdue` 或 `interview_reminder` |
| `POST` | `/feishu/card-actions` | 接收飞书卡片回传 |

## 数据与安全

- `.env`、邮箱密码和飞书 App Secret 不会在页面中保存或回显。
- 本地 `data/hirems_ops.sqlite3` 保存邮件附件指纹、候选人工作流、面试、通知日志和原件相对路径；原始简历默认保存在 `data/resumes/`，不保存邮箱密码。
- 删除候选人时会同步删除其本地原始简历。历史候选人若没有原件，需要重新导入一次后才能在队列中打开。
- AI 评分只用于辅助决策。淘汰、约面与 Offer 应保留人工审核。
- 上线前应设置候选人数据留存周期、访问权限、审计策略和离职人员权限回收流程。

## 验证

```powershell
python -m pytest tests/test_api.py tests/test_document_parsing.py -q
```

查看原始简历功能的专项测试位于 `tests/test_resume_file_access.py`。

## 相关飞书文档

- [批量新增多维表格记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/batch_create?lang=zh-CN)
- [发送消息卡片](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/create)
- [卡片回传交互](https://open.feishu.cn/document/feishu-cards/card-callback-communication?lang=zh-CN)
- [创建日程](https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event/create?lang=zh-CN)
