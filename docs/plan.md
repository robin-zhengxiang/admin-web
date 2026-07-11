# 本机管理面板（Token 看板 + Skill 管理 + 定时任务管理）

## Context

用户希望在本机搭一个 HTTP 服务，集中做三件事：
1. Token 消耗看板（图表 + 下钻到具体 session/task）
2. 管理本机 skill 列表（编辑、enable/disable）
3. 管理本机定时任务（改时间、开关、看日志）

第 3 点本机已有 [task-manager](file:///Users/robinzheng/.claude/skills/task-manager) skill（`taskctl.py` + `tasks.json`），管的是 launchd 任务的 list/enable/disable/run/logs，但**不支持改时间**（改 schedule 需要新增能力）。第 1、2 点目前都没有对应工具，需要新建。这个 HTTP 服务本质上是把这三块能力做成一个可视化面板，并复用/扩展已有的 `taskctl.py`。

## 环境勘察结论

- 本机**没有 node/npm**（无 nvm/volta/brew node），但有 `brew`、系统 `python3.9.6`、`pip3`，**没装 flask/fastapi**。
- 所有现有 skill 脚本（task-manager、dingtalk-notify）都是纯 `python3` stdlib + `subprocess`，零第三方依赖。
- Token 用量数据源：`~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl`，每个 assistant 消息行带 `usage`（`input_tokens`/`output_tokens`/`cache_creation_input_tokens`/`cache_read_input_tokens`/`model`）+ `timestamp`/`sessionId`/`cwd`/`uuid`/`parentUuid`。部分 session 还有 `{"type":"ai-title","aiTitle":"...","sessionId":...}` 记录，可作为人类可读的会话标题。当前全量约 133MB / 126 个 session 文件，会持续增长。
- Skill enable/disable **有官方机制**：`~/.claude/settings.json` 的 `skillOverrides` 字段（值 `off` / `user-invocable-only` / `name-only`），不需要靠改文件名之类的 hack。
- 定时任务已有 `taskctl.py`，改时间需要新增：读写 plist 的 `StartCalendarInterval`（用 stdlib `plistlib`），改完 `unload -w` + `load -w` 使其生效。

## 技术选型（默认方案，可在你确认计划时调整）

- **后端**：纯 Python3 stdlib（`http.server.ThreadingHTTPServer` + 自写小路由），不引入 Flask/FastAPI —— 和现有 skill 脚本风格一致，零安装、零依赖失效风险。
- **数据层**：SQLite（stdlib `sqlite3`）做 token 用量的本地索引。原因：jsonl 全量 133MB+ 且会持续增长，每次请求都全量重新解析不利于「下钻」的交互体验；用一个后台线程增量扫描（记录每个 session 文件已读到的行号/偏移，只追加新行），把每条消息的用量落一张表，看板查询走 SQL 聚合，快且可扩展。
- **前端**：单页纯 HTML/CSS/JS + 一份 vendored 的 Chart.js（本地文件，不依赖 CDN，避免本机无网也能用）。不上构建工具（本机没 node，也没必要）。
- **部署方式**：常驻服务（像 `dingtalk-listener` 一样），只监听 `127.0.0.1:8787`（本机回环，不对外暴露），注册进 launchd，并顺手登记进现有 `task-manager` 的 `tasks.json`，这样这个面板自己也能被 `taskctl.py` 管理（复用现有基础设施，不重复造轮子）。
- **鉴权**：暂不做登录鉴权 —— 仅监听 loopback，单用户本机场景。会在 SKILL.md 里明确写这一权衡（本机任何进程/浏览器 tab 都能访问 127.0.0.1:8787）。

## 目录结构

按用户要求，代码和文档都集中放在用户目录下一个专门的项目目录，而不是塞进 `~/.claude/skills/`（参考 `~/SG-Property-Research` 的先例：独立项目用 `~/Title-Case-With-Hyphens` 命名，不混进 Claude 配置目录）：

```
~/admin-web/               # 项目根目录（代码 + 文档都在这）
  README.md                      # 项目说明：做什么、怎么启动、目录说明
  server.py                      # 入口：启动 HTTP 服务
  db.py                          # SQLite schema + 增量索引器（扫 jsonl -> usage_events 表）
  api_tokens.py                  # /api/overview /api/sessions /api/sessions/:id
  api_skills.py                  # /api/skills 相关
  api_tasks.py                   # /api/tasks 相关（包一层 taskctl.py + 新增改 schedule）
  pricing.json                   # 可编辑的模型价格表（成本估算用）
  static/
    index.html / dashboard.js / dashboard.css
    skills.html / skills.js
    tasks.html / tasks.js
    vendor/chart.js
  data/
    usage.db                     # SQLite 文件（运行时生成，不提交/不同步）
  logs/
    local-dashboard.log
```

`api_tasks.py` 会 `sys.path` 导入 `~/.claude/skills/task-manager/taskctl.py` 里的既有函数复用（跨目录 import，不拷贝逻辑）。

同时新增：
- `~/Library/LaunchAgents/com.robinzheng.local-dashboard.plist`（daemon 类型，KeepAlive，`ProgramArguments` 指向 `~/admin-web/server.py`，日志指向 `~/admin-web/logs/`）
- 在 `~/.claude/skills/task-manager/tasks.json` 追加一条登记（type: daemon，log 路径指向 `~/admin-web/logs/local-dashboard.log`）
- 不在 `~/.claude/skills/` 下新建 skill —— 这个服务不是一个「Claude 对话里调用的 skill」，是一个独立跑的网页 App，靠浏览器直接访问，`task-manager` 已经足够覆盖它的启停/日志管理

## 三大模块设计

### 1. Token 看板

**索引器**（`db.py`，后台线程，每 ~60s 跑一次）：
- 遍历 `~/.claude/projects/*/*.jsonl`
- 对每个文件记录已处理的字节偏移（存在 `index_state` 表），增量 `seek` 读新行，避免全量重扫
- 每条带 `usage` 的 assistant 消息 → 写入 `usage_events(session_id, project, ts, model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, uuid, parent_uuid, is_sidechain)`
- 每条 `ai-title` 记录 → 写/更新 `sessions(session_id, title, project, first_ts, last_ts)`

**API**：
- `GET /api/overview?range=7d|30d|all` — 按天聚合的 tokens 时间序列（input/output/cache read/cache creation 分层）、按 model 汇总、按 project 汇总、估算成本（价格表见下）
- `GET /api/sessions?range=&project=&sort=cost|time` — session 列表：标题（ai-title，没有则截取首条用户消息）、project、时长、总 tokens、估算成本
- `GET /api/sessions/:id` — 该 session 的完整时间线：按 `uuid`/`parentUuid` 还原顺序，主链路每轮 assistant 消息展示 tokens + 用到的工具；`is_sidechain=1` 的记录（Agent 子任务）折叠展示为独立分组，各自小计 —— 这就是「下钻到具体 task」的落地方式

**成本估算**：一张可编辑的价格表（json 配置，`model -> {input, output, cache_write, cache_read}` 每百万 token 单价），实现前我会先用当前 Anthropic 官网公开价格填一版默认值，用户可在 skills 目录下的配置文件里改。

**前端**：`/` 首页 —— 顶部汇总卡片（今日/本周/本月 tokens & 估算花费）、按天堆叠图、按模型/按项目占比图、下方 session 表格（点击行进详情页，展示时间线 + 子任务分组）。图表配色/样式做的时候会遵循 `dataviz` skill 的规范（不在计划阶段跑，实现图表代码前再读一次）。

### 2. Skill 管理

- `GET /api/skills` — 扫描 `~/.claude/skills/*/SKILL.md`（以及必要时 `~/.claude/plugins` 下的 skill，先只做个人 skills，plugin skills 只读展示不做 enable/disable，因为那是插件管理的范畴）解析 frontmatter（`name`/`description`），读取 `~/.claude/settings.json` 的 `skillOverrides` 得到当前状态（default/off/user-invocable-only/name-only）
- `POST /api/skills/:name/state {state}` — 原子写回 `~/.claude/settings.json`（写临时文件再 rename，避免写坏配置文件），更新 `skillOverrides[name]`
- `GET/PUT /api/skills/:name/content` — 读/改 SKILL.md 原始 markdown 全文（textarea 编辑，保存即写回文件，同样走临时文件+rename）
- **前端**：`/skills` 页面 —— 表格：名字、description 摘要、状态下拉（default/off/user-invocable-only/name-only）、编辑按钮（弹出/展开 textarea 编辑原文，保存）

### 3. 定时任务管理

复用 `taskctl.py` 的既有逻辑（list 用的 `launchctl list` 状态解析、enable/disable 用的 `launchctl load/unload -w`、logs 用的 `tail`），包一层 HTTP API，而不是重写：

- `GET /api/tasks` — 直接调用 `taskctl.py` 里 `load_tasks()` + `launchctl_rows()` + `status_of()` 这几个既有函数（作为库导入，不是 shell 出去解析文本），拼成 JSON
- `POST /api/tasks/:name/enable`、`/disable`、`/run` — 直接调用 `cmd_enable`/`cmd_disable`/`cmd_run` 里同样的 `subprocess` 调用逻辑
- `GET /api/tasks/:name/logs?n=` — 复用 `cmd_logs` 的 tail 逻辑
- **新增** `PUT /api/tasks/:name/schedule {hour, minute, weekday?}` —— `taskctl.py` 目前没有这个能力，需要新写：用 `plistlib.load/dump` 读改 plist 里的 `StartCalendarInterval`，写回后 `launchctl unload -w` + `load -w` 使其生效（仅对 `type: scheduled` 的任务开放这个操作，daemon 类型没有 schedule 概念）
- **前端**：`/tasks` 页面 —— 表格：名字、状态（●运行中/●已启用空闲/○已禁用）、计划时间（可编辑，改完调用上面新增的 API）、类型、开关按钮、"立即触发"按钮（仅 scheduled）、"看日志"按钮（弹出最近 N 行，可刷新）

为了不重复造轮子，`api_tasks.py` 会 `sys.path` 导入 `task-manager/taskctl.py` 里的函数直接复用，而不是复制一份逻辑。

## 实现顺序

1. 建 `~/admin-web/` 目录结构；后端骨架：`server.py`（路由 + 静态文件服务）+ `db.py`（schema + 增量索引器），先跑通「服务能起、能测到自己」
2. Token 看板：索引器 → `/api/overview` `/api/sessions` `/api/sessions/:id` → 前端图表/表格/详情页
3. Skill 管理：`/api/skills` 读写 + 前端页面
4. 定时任务管理：包一层 `taskctl.py` + 新增改 schedule 能力 + 前端页面
5. 把面板自己注册成 launchd daemon（plist 指向 `~/admin-web/server.py`）+ 登记进 `task-manager/tasks.json`，写 `~/admin-web/README.md`

## 验证方式

- 启动后 `curl http://127.0.0.1:8787/api/overview` 检查能拿到和当前 121+ 个 session 文件量级匹配的合理 token 汇总
- 用 Playwright/浏览器打开三个页面，实际点开一个有多轮对话的 session 看 timeline 是否对得上该 session jsonl 里的真实用量
- 在 skills 页面把某个非关键 skill（比如临时建一个测试 skill）toggle off，确认 `~/.claude/settings.json` 的 `skillOverrides` 真的写入了，且 `/skills` 菜单里确实看不到了；toggle 回来确认能恢复
- 在 tasks 页面改一下购房简报的时间再改回来，确认 plist 内容和 `launchctl list` 状态符合预期，且不会误触发（改完检查 `launchctl list` 里的下次触发逻辑没有立刻乱跑）
- 确认服务只监听 127.0.0.1（`lsof -iTCP -sTCP:LISTEN` 检查 bind 地址），不监听 0.0.0.0
