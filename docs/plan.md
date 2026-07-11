# 本机管理面板（Token 看板 + Skill 管理 + 定时任务管理 + Bug 反馈）

## Context

用户希望在本机搭一个 HTTP 服务，集中做四件事：
1. Token 消耗看板（图表 + 下钻到具体 session/task）
2. 管理本机 skill 列表（编辑、enable/disable）
3. 管理本机定时任务（改时间、开关、看日志）
4. Bug 反馈：用户提交网站 bug，定时扫描——能修的直接修代码，不能修的给出反馈并支持多轮交互

第 3 点本机已有 [task-manager](file:///Users/robinzheng/.claude/skills/task-manager) skill（`taskctl.py` + `tasks.json`），管的是 launchd 任务的 list/enable/disable/run/logs，但**不支持改时间**（改 schedule 需要新增能力）。第 4 点发现本机 [dingtalk-notify](file:///Users/robinzheng/.claude/skills/dingtalk-notify) 已有 `headless.py`——用 `claude -p --resume <session_id>` 驱动真正带工具的 Claude Code（`acceptEdits` 权限 + 命令黑白名单），可以直接复用，不用重新设计"怎么让 AI 安全地改代码"这一层。第 1、2 点目前都没有对应工具，需要新建。这个 HTTP 服务本质上是把这四块能力做成一个可视化面板，并复用/扩展已有的 `taskctl.py` 和 `headless.py`。

## 环境勘察结论

- 本机**没有 node/npm**（无 nvm/volta/brew node），但有 `brew`、系统 `python3.9.6`、`pip3`，**没装 flask/fastapi**。
- 所有现有 skill 脚本（task-manager、dingtalk-notify）都是纯 `python3` stdlib + `subprocess`，零第三方依赖。
- Token 用量数据源：`~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl`，每个 assistant 消息行带 `usage`（`input_tokens`/`output_tokens`/`cache_creation_input_tokens`/`cache_read_input_tokens`/`model`）+ `timestamp`/`sessionId`/`cwd`/`uuid`/`parentUuid`。部分 session 还有 `{"type":"ai-title","aiTitle":"...","sessionId":...}` 记录，可作为人类可读的会话标题。当前全量约 133MB / 126 个 session 文件，会持续增长。
- Skill enable/disable **有官方机制**：`~/.claude/settings.json` 的 `skillOverrides` 字段（值 `off` / `user-invocable-only` / `name-only`），不需要靠改文件名之类的 hack。
- 定时任务已有 `taskctl.py`，改时间需要新增：读写 plist 的 `StartCalendarInterval`（用 stdlib `plistlib`），改完 `unload -w` + `load -w` 使其生效。
- 本机除了 `robinzheng` 还有另一个 macOS 账户 `renyina`（`dscl . -list /Users` 确认）。用户要求这个后台统一覆盖所有本机用户，这就带出一个真实的权限问题——我尝试看一眼 `/Users/renyina/.claude` 时被系统权限分类器直接拦下（"未经明确请求不应探查其他用户的私有配置"），这正是下面"多用户与鉴权"一节要解决的。

## 多用户与鉴权（用户明确要求，架构关键决定）

- **数据可见范围**：token 看板是**全屋汇总视图**——所有登录用户看到的是本机全部账户（目前 `robinzheng` + `renyina`）汇总的 token 用量，可按"用户"维度筛选/下钻，和按 model/project 一样。这是用户明确选择的方案（"统一后台，读取所有用户数据"）。
- **写权限收紧到"自己"**：skill 管理、定时任务管理会列出**所有用户**名下的 skill / launchd 任务（带"所属用户"列），但 enable/disable/编辑/改时间**只对当前登录身份对应的那个用户开放**——登录的是 `robinzheng` 就只能改 `robinzheng` 名下的东西，要改 `renyina` 的必须用她自己的密码登录。看得到全部，但只能改自己的。
- **鉴权机制**：登录页要求输入"本机某个 macOS 账户的用户名 + 密码"，后端用 `dscl . -authonly <username>` 校验——**密码通过 stdin 传给 dscl，不放进命令行参数**（`dscl . -authonly <user>`，不带密码位置参数时会从 stdin 读取密码），避免密码短暂出现在 `ps` 输出里。校验通过后签发一个 session cookie（记录 username + uid），后续每个写操作都要核对"cookie 里的 uid == 目标资源所属的 uid"。
- **为什么必须变成 LaunchDaemon（而不是 LaunchAgent）**：要读取其他用户的 `~/.claude/projects`、`~/.claude/skills`、`~/.claude/settings.json`、`~/Library/LaunchAgents`，普通用户权限的进程读不到别人主目录下 700/750 权限的文件，只有 root 能绕过这些文件权限检查。所以这个服务要从"像 dingtalk-listener 一样的 LaunchAgent（`~/Library/LaunchAgents`，当前用户身份）"改成 **LaunchDaemon**（`/Library/LaunchDaemons`，root 身份，系统启动时常驻，不依赖任何人登录）。
  - 安装/加载这个 LaunchDaemon 需要 `sudo`，这是会影响系统的操作——**实现阶段真正执行 `sudo launchctl bootstrap system ...` 前，我会再单独跟你确认一次，不会自动执行**。
- **写文件的所有权处理**：以 root 身份写另一个用户的 `settings.json` / `SKILL.md` / plist 后，必须把文件 owner/group `chown` 回该用户（`os.chown` + `pwd.getpwnam` 拿 uid/gid），否则文件会变成 root 所有，影响那个用户之后自己用 Claude Code 正常读写这些文件。
- **定时任务跨用户操作的技术细节**：`launchctl` 管理的是每个登录用户各自的 GUI session domain，root 不能直接对别人的 LaunchAgent `launchctl load/unload`，需要用 `launchctl asuser <目标uid> launchctl load/unload -w <plist>` 代持执行——这是本次改动里技术上最微妙的一块，实现时要单独测试验证。
- **已知风险**：读取其他用户主目录理论上可能撞到 macOS 隐私权限（TCC / 完全磁盘访问权限）——`~/.claude` 不在 Documents/Desktop 等受保护目录下，大概率没问题，但要在实现后实测；如果被拦，需要去"系统设置 → 隐私与安全性 → 完全磁盘访问权限"给这个 daemon 授权。

## 技术选型（默认方案，可在你确认计划时调整）

- **后端**：纯 Python3 stdlib（`http.server.ThreadingHTTPServer` + 自写小路由），不引入 Flask/FastAPI —— 和现有 skill 脚本风格一致，零安装、零依赖失效风险。
- **数据层**：SQLite（stdlib `sqlite3`）做 token 用量的本地索引。原因：jsonl 全量 133MB+ 且会持续增长，每次请求都全量重新解析不利于「下钻」的交互体验；用一个后台线程增量扫描（记录每个 session 文件已读到的行号/偏移，只追加新行），把每条消息的用量落一张表，看板查询走 SQL 聚合，快且可扩展。
- **前端**：单页纯 HTML/CSS/JS + 一份 vendored 的 Chart.js（本地文件，不依赖 CDN，避免本机无网也能用）。不上构建工具（本机没 node，也没必要）。
- **部署方式**：常驻服务，但因为要读所有用户数据，改成 root 权限的 **LaunchDaemon**（`/Library/LaunchDaemons`，不是 `~/Library/LaunchAgents`），只监听 `127.0.0.1:8000`（本机回环，不对外暴露，端口改成好记的 8000）。`task-manager` 现有的 `taskctl.py` 假设都是当前用户身份的 LaunchAgent，登记这个 LaunchDaemon 时要在 `tasks.json` 里标注类型和路径的差异，`taskctl.py` 的 enable/disable 对 LaunchDaemon 需要 `sudo launchctl`，这点会在实现时处理。
- **鉴权**：见上面新增的"多用户与鉴权"一节——用 `dscl . -authonly` 校验 macOS 本机账户密码，不是自己另建一套密码系统。

## 目录结构

按用户要求，代码和文档都集中放在用户目录下一个专门的项目目录，而不是塞进 `~/.claude/skills/`（参考 `~/SG-Property-Research` 的先例：独立项目用 `~/Title-Case-With-Hyphens` 命名，不混进 Claude 配置目录）：

```
~/admin-web/               # 项目根目录（代码 + 文档都在这）
  README.md                      # 项目说明：做什么、怎么启动、目录说明
  server.py                      # 入口：启动 HTTP 服务（root 运行）
  db.py                          # SQLite schema + 增量索引器（扫所有用户的 jsonl -> usage_events 表）
  users.py                       # 枚举本机真实用户（uid/gid/home），供 db/api_skills/api_tasks 复用
  auth.py                        # dscl 密码校验 + session cookie 签发/校验
  api_tokens.py                  # /api/overview /api/sessions /api/sessions/:id
  api_skills.py                  # /api/skills 相关（跨用户读，按登录身份收窄写）
  api_tasks.py                   # /api/tasks 相关（跨用户读，按登录身份收窄写；包一层 taskctl.py + 新增改 schedule）
  api_feedback.py                # /api/feedback 相关（工单 CRUD + 追加回复）
  feedback_scanner.py            # 定时脚本（独立于 server.py，由 launchd 定时触发）：扫工单 -> 调 headless -> 视情况本地 commit
  pricing.json                   # 可编辑的模型价格表（成本估算用）
  static/
    login.html / login.js        # 登录页（用户名 + 密码）
    index.html / dashboard.js / dashboard.css
    skills.html / skills.js
    tasks.html / tasks.js
    feedback.html / feedback.js   # 反馈按钮 + 工单列表 + 对话线程
    vendor/chart.js
  data/
    usage.db                     # SQLite 文件（运行时生成，不提交/不同步）
  logs/
    local-dashboard.log
    feedback-scanner.log
```

`users.py` 提供一个 `list_local_users()`：用 `pwd` 模块枚举 `/Users/*` 下真实用户（排除系统账户/Guest），返回每个用户的 `username`/`uid`/`gid`/`home`，供索引器、skills API、tasks API 统一复用，避免各处重复写"怎么找到所有用户"这段逻辑。

`api_tasks.py` 会 `sys.path` 导入 `~/.claude/skills/task-manager/taskctl.py` 里的既有函数复用（跨目录 import，不拷贝逻辑），但因为要跨用户操作 LaunchAgent，会在其基础上包一层"代持执行"（`launchctl asuser <uid> ...`），见下面第 3 节。

`feedback_scanner.py` 同样 `sys.path` 导入 `~/.claude/skills/dingtalk-notify/headless.py` 里的 `run_headless`/`start_headless_session`（连同它已经定义好的 `ALLOWED_TOOLS`/`DISALLOWED_TOOLS` 黑白名单），不重新发明"怎么安全地让 AI 改代码"这一层，见下面第 4 节。

同时新增：
- `/Library/LaunchDaemons/com.robinzheng.local-dashboard.plist`（**LaunchDaemon**，root 身份，KeepAlive，`ProgramArguments` 指向 `python3 ~/admin-web/server.py`，日志指向 `~/admin-web/logs/`）—— 注意路径是 `/Library/LaunchDaemons`（系统级，需要 root 所有 + `sudo` 加载），不是 `~/Library/LaunchAgents`
- 在 `~/.claude/skills/task-manager/tasks.json` 追加一条登记，`type` 标注为 `daemon-root`（和现有 `daemon`/`scheduled` 区分，因为它的 enable/disable 需要 `sudo launchctl` 而不是普通 `launchctl`——这一点会体现在 `taskctl.py` 的判断逻辑里）
- 不在 `~/.claude/skills/` 下新建 skill —— 这个服务不是一个「Claude 对话里调用的 skill」，是一个独立跑的网页 App，靠浏览器直接访问

## 四大模块设计

### 0. 登录（前置于以上三块）

- `GET /login` — 登录页（用户名 + 密码）
- `POST /api/login {username, password}` — 调用 `auth.py`：`subprocess.run(["dscl", ".", "-authonly", username], input=password, ...)`，返回码 0 即通过；用 `pwd.getpwnam(username)` 校验该用户名确实是本机真实账户（排除 Guest/系统账户）。通过后签发 session（服务端内存 dict：`session_id -> {username, uid, expires}`），种一个 `HttpOnly` cookie。
- 所有 `/api/*`（除 `/api/login`）都要求带有效 session cookie，未带则 401 → 前端跳转 `/login`。
- 每个写操作（skill toggle/edit、task enable/disable/run/schedule）在处理前都要核对 `session.uid == 目标资源所属的 uid`，不符直接 403。

### 1. Token 看板（全用户汇总视图）

**索引器**（`db.py`，后台线程，每 ~60s 跑一次）：
- 用 `users.py` 的 `list_local_users()` 拿到本机所有真实用户，对每个用户遍历 `<home>/.claude/projects/*/*.jsonl`（不再只扫当前用户）
- 对每个文件记录已处理的字节偏移（存在 `index_state` 表，key 带上 username 前缀避免路径冲突），增量 `seek` 读新行，避免全量重扫
- 每条带 `usage` 的 assistant 消息 → 写入 `usage_events(session_id, **owner_user**, project, ts, model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, uuid, parent_uuid, is_sidechain)` —— 新增 `owner_user` 列区分数据属于哪个 macOS 账户
- 每条 `ai-title` 记录 → 写/更新 `sessions(session_id, owner_user, title, project, first_ts, last_ts)`

**API**：
- `GET /api/overview?range=7d|30d|all&user=` — 按天聚合的 tokens 时间序列（input/output/cache read/cache creation 分层，**返回最原始的 token 数量，不只是估算成本**）、按 model 汇总、按 project 汇总、**按 owner_user 汇总**（默认全屋汇总，`user=` 可筛选单个账户）、估算成本（价格表见下）
- `GET /api/sessions?range=&project=&user=&sort=cost|time` — session 列表：标题（ai-title，没有则截取首条用户消息）、**所属用户**、project、时长、总 tokens、估算成本
- `GET /api/sessions/:id` — 该 session 的完整时间线：按 `uuid`/`parentUuid` 还原顺序，主链路每轮 assistant 消息展示 tokens + 用到的工具；`is_sidechain=1` 的记录（Agent 子任务）折叠展示为独立分组，各自小计 —— 这就是「下钻到具体 task」的落地方式。session 详情是只读的，不受"只能改自己"的写权限限制。

**成本估算**：一张可编辑的价格表（json 配置，`model -> {input, output, cache_write, cache_read}` 每百万 token 单价），实现前我会先用当前 Anthropic 官网公开价格填一版默认值，用户可在项目目录下的配置文件里改。

**前端**：`/` 首页 —— 顶部汇总卡片（今日/本周/本月 tokens & 估算花费，默认全屋，可切换单用户）、按天堆叠图、按模型/按项目/**按用户**占比图、下方 session 表格（点击行进详情页，展示时间线 + 子任务分组）。图表配色/样式做的时候会遵循 `dataviz` skill 的规范（不在计划阶段跑，实现图表代码前再读一次）。

### 2. Skill 管理（跨用户读，写收窄到登录身份）

- `GET /api/skills` — 对每个本机用户扫描 `<home>/.claude/skills/*/SKILL.md`（以及必要时 `<home>/.claude/plugins` 下的 skill，先只做个人 skills，plugin skills 只读展示不做 enable/disable，因为那是插件管理的范畴），解析 frontmatter（`name`/`description`），读取该用户 `<home>/.claude/settings.json` 的 `skillOverrides` 得到当前状态（default/off/user-invocable-only/name-only）。返回列表带 `owner_user` 字段。
- `POST /api/skills/:owner_user/:name/state {state}` — **先核对 `session.uid` 对应的 username == `:owner_user`，不符 403**；通过后原子写回该用户的 `settings.json`（写临时文件再 rename），并 `os.chown` 回该用户的 uid/gid，更新 `skillOverrides[name]`
- `GET/PUT /api/skills/:owner_user/:name/content` — 读/改 SKILL.md 原始 markdown 全文（写入同样先做 uid 核对 + 写后 chown）
- **前端**：`/skills` 页面 —— 表格：名字、**所属用户**、description 摘要、状态下拉（default/off/user-invocable-only/name-only）、编辑按钮。不是当前登录身份拥有的行，状态下拉和编辑按钮置灰，hover 提示"需要以 xxx 身份登录才能修改"。

### 3. 定时任务管理（跨用户读，写收窄到登录身份）

复用 `taskctl.py` 的既有逻辑（list 用的 `launchctl list` 状态解析、enable/disable 用的 `launchctl load/unload -w`、logs 用的 `tail`），包一层 HTTP API：

- `GET /api/tasks` — 对每个本机用户扫描其 `<home>/Library/LaunchAgents/*.plist`（以及 robinzheng 名下 `task-manager/tasks.json` 里登记的中文名/描述，其他用户的任务没有登记表就用 plist 里的信息展示），拼成 JSON，带 `owner_user` 字段。这一步直接调用 `taskctl.py` 里的既有函数（`launchctl_rows`/`status_of` 等），不 shell 出去解析文本。
- `POST /api/tasks/:owner_user/:name/enable`、`/disable`、`/run` — **先核对 `session.uid` == `:owner_user`**，通过后执行；**关键差异**：不能像 `taskctl.py` 现在那样直接 `launchctl load/unload`（那是"以当前用户身份操作当前用户的 LaunchAgent"），因为服务本身以 root 运行，要操作目标用户 GUI session domain 下的 LaunchAgent，必须用 `launchctl asuser <目标uid> launchctl load/unload -w <plist>` 代持执行 —— 这是本次改动技术上最微妙的一块，实现时要单独测试验证（不同 macOS 版本 `asuser`/`bootstrap` 语义可能有差异）。
- `GET /api/tasks/:owner_user/:name/logs?n=` — 复用 `cmd_logs` 的 tail 逻辑（日志文件路径通常在 `/tmp` 或用户目录下，root 可直接读）
- **新增** `PUT /api/tasks/:owner_user/:name/schedule {hour, minute, weekday?}` —— 用 `plistlib.load/dump` 读改 plist 里的 `StartCalendarInterval`，写回后同样通过 `asuser` 代持 `unload -w` + `load -w` 使其生效（仅对 `type: scheduled` 的任务开放这个操作）
- **前端**：`/tasks` 页面 —— 表格：名字、**所属用户**、状态（●运行中/●已启用空闲/○已禁用）、计划时间（可编辑，改完调用上面新增的 API）、类型、开关按钮、"立即触发"按钮（仅 scheduled）、"看日志"按钮。不属于当前登录身份的行，操作按钮置灰。

为了不重复造轮子，`api_tasks.py` 会 `sys.path` 导入 `task-manager/taskctl.py` 里的函数直接复用，而不是复制一份逻辑，只在"代持执行"这一层做包装。

### 4. Bug 反馈（全屋共享工单 + 定时自动修复，本地 commit 不 push 不重启）

**数据模型**（`db.py` 新增两张表）：
- `feedback_tickets(id, owner_user, title, created_at, updated_at, status, claude_session_id)` —— `status` ∈ `open`（待处理/用户刚回复）/ `in_progress`（扫描器正在处理）/ `needs_input`（AI 需要澄清，等用户回复）/ `resolved`（AI 判断已修复）/ `wontfix`（人工关闭）
- `feedback_messages(id, ticket_id, role, content, created_at)` —— `role` ∈ `user` / `agent`，构成每张工单的多轮对话线程

**可见范围**：和 token 看板一样**全屋共享**——任何登录用户都能看到所有工单列表和对话内容（这是用户明确选择的方案）。回复自己没提的工单在前端也不做限制（都是本机自己人，不像 skill/task 那样有"改坏了会影响别人"的顾虑）。

**前端**：
- 每个页面右下角一个悬浮"反馈"按钮 → 展开一个小表单（标题 + 描述，自动带上当前页面路径），提交后创建工单
- `/feedback` 页面：工单列表（状态角标 + 标题 + 所属用户 + 最后更新时间），点开进入对话线程视图；`needs_input` 状态下可以直接在线程里追加回复

**API**：
- `POST /api/feedback {title, description, page}` — 建工单（`status=open`），第一条 message 就是这条描述
- `GET /api/feedback` — 列出全部工单（全屋共享，见上）
- `GET /api/feedback/:id` — 工单详情 + 完整消息列表
- `POST /api/feedback/:id/reply {content}` — 追加一条 `role=user` 的消息；工单状态如果是 `needs_input`/`resolved`，重置回 `open`，等下一轮扫描

**`feedback_scanner.py`（独立脚本，由 launchd 定时触发，比如每 15-30 分钟一次，注册进 `task-manager/tasks.json` 为一个新的 `scheduled` 任务）**：
1. 查 `status = 'open'` 的工单
2. 对每张工单：`cwd` 固定在 `~/admin-web`。若 `claude_session_id` 为空 → `start_headless_session()`（首次建会话）；否则 `run_headless(新回复内容, claude_session_id)`（在同一个 headless 会话里续聊，保留之前讨论过的上下文）——直接复用 `dingtalk-notify/headless.py` 现成的函数，不重新写一套"怎么调用带工具的 Claude"
3. Prompt 模板大致是："这是 admin-web 项目（当前工作目录）收到的用户反馈：<标题 + 描述 + 历史对话>。如果你能确认问题所在并修复，直接编辑代码修复，修复后说明改了什么、为什么这样改；如果信息不足或涉及产品设计取舍，不要改代码，在回复里提出你需要用户说明的具体问题。"
4. `headless.py` 已有的黑白名单直接生效：`acceptEdits` 允许自动读写编辑文件，但 `DISALLOWED_TOOLS` 挡掉 `git push`/`sudo`/`launchctl`/`rm`/`chmod`/`chown` 等——即使反馈内容里被诱导"帮我删了这个文件/push 到远程"，也执行不了
5. 扫描器（**不是** AI 自己，是这段确定性的 Python 代码）调用后检查 `~/admin-web` 是否有未提交改动（`git status --porcelain`）：
   - 有改动 → 扫描器自己跑 `git add -A && git commit -m "..."` **落一个本地 commit**（**不 push**，用户选择的方案），工单状态置为 `resolved`，把 headless 的说明文字 + `git diff --stat` 摘要写成一条 `role=agent` 消息
   - 没有改动（说明 AI 只是在提问/解释，没触碰代码）→ 工单状态置为 `needs_input`，把 headless 返回的文本写成 `role=agent` 消息
6. **不自动 push、不自动重启 daemon**——代码修复的效果要等你自己 review 这些本地 commit 之后手动 push，并且（如果改到了 `server.py` 等运行时会加载的文件）手动重启服务才会生效。这是刻意的边界：AI 可以落地一个可回滚的 commit，但"同步到远程"和"让改动生效"这两步留给人。

## 实现顺序

1. 建 `~/admin-web/` 目录结构；`users.py`（枚举本机用户）+ 后端骨架：`server.py`（路由 + 静态文件服务，端口 8000）+ `db.py`（schema + 跨用户增量索引器），先跑通「服务能起、能测到自己」—— **这一步先以当前用户身份跑，暂不需要 root，方便快速迭代**
2. `auth.py` + 登录页：`dscl` 密码校验 + session cookie，先只保护路由框架，验证"登录 robinzheng / 登录 renyina 分别拿到不同 session"这个核心机制没问题
3. Token 看板：跨用户索引器 → `/api/overview` `/api/sessions` `/api/sessions/:id` → 前端图表/表格/详情页（这一步涉及读 renyina 的数据，**在本机只有当前用户权限时会读不到，属预期**，先用 robinzheng 自己的数据验证逻辑对不对，等第 6 步切到 root LaunchDaemon 后再验证全量）
4. Skill 管理：`/api/skills` 读写 + 写权限收窄（uid 核对 + chown）+ 前端页面
5. 定时任务管理：包一层 `taskctl.py` + `asuser` 代持执行 + 新增改 schedule 能力 + 前端页面
6. Bug 反馈：`api_feedback.py`（工单 CRUD）+ 前端反馈按钮/`/feedback` 页面 → 再写 `feedback_scanner.py`（复用 `headless.py`，先手动跑一次脚本验证"扫到工单 → 调 headless → 判断改没改代码 → 状态流转对不对"），验证通过后再登记进 `task-manager/tasks.json` 做定时触发
7. **把面板切换成 root LaunchDaemon**：写 `/Library/LaunchDaemons/com.robinzheng.local-dashboard.plist`，**执行 `sudo launchctl bootstrap system ...` 前会单独跟你确认**；成功后重新验证"能读到 renyina 的数据""能以 renyina 身份登录并管理她自己的 skill/task"；登记进 `task-manager/tasks.json`，写 `~/admin-web/README.md`

## 验证方式

- 启动后 `curl http://127.0.0.1:8000/api/overview`（未登录应 401；带合法 session cookie 后）检查能拿到和当前 121+ 个 session 文件量级匹配的合理 token 汇总
- 用 Playwright/浏览器打开三个页面，实际点开一个有多轮对话的 session 看 timeline 是否对得上该 session jsonl 里的真实用量
- **分别用 `robinzheng` 和 `renyina` 的密码登录**，确认：token 看板两边看到的都是全屋汇总（一致）；skills/tasks 页面能看到对方的行但操作按钮置灰；试图绕过前端直接 `curl` 一个不属于自己的 `/api/skills/:owner_user/...` 写接口，确认后端真的返回 403（不能只靠前端置灰）
- 在 skills 页面把某个非关键 skill（比如临时建一个测试 skill）toggle off，确认对应用户的 `settings.json` 的 `skillOverrides` 真的写入了、文件 owner 还是那个用户（没有变成 root）；toggle 回来确认能恢复
- 在 tasks 页面改一下购房简报的时间再改回来，确认 plist 内容和 `launchctl list` 状态符合预期，且不会误触发
- 确认服务只监听 127.0.0.1（`lsof -iTCP -sTCP:LISTEN` 检查 bind 地址），不监听 0.0.0.0
- 确认 LaunchDaemon 切换后，读取 `renyina` 的 `~/.claude` 没有被 TCC 拦截；如被拦，去"系统设置 → 隐私与安全性 → 完全磁盘访问权限"授权后重测
- 提一个真实的小 bug 工单（比如"tasks 页面某个按钮文字打错了"），手动跑一次 `feedback_scanner.py`，确认：headless 会话真的改了对应文件、`~/admin-web` 出现了一个新的本地 git commit（**没有 push**，`git log` 只在本地领先 origin）、工单状态变成 `resolved` 且消息里能看到修复说明；再提一个刻意含糊的工单（比如"网站不太好用"），确认 AI 没有乱改代码，工单状态变成 `needs_input` 并追问，之后在 `/feedback` 里回复补充信息，工单状态回到 `open`，下一轮扫描能续上同一个 headless 会话（而不是失忆重新开始）
- 确认 `feedback_scanner.py` 全程没有触发 `git push` / `launchctl` / `sudo` / `rm`（`headless.py` 现成的黑名单应该已经挡住，但要实测确认没有绕过）
