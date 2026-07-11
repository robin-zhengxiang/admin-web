# admin-web

本机管理面板：Token 消耗看板、Skill 管理、定时任务管理、Bug 反馈。方案文档在 [docs/plan.md](docs/plan.md)（包含设计决策的完整讨论过程，改动较大的功能先读它）。

## 运行

```bash
python3 server.py          # 监听 127.0.0.1:8000
```

当前以调用者的用户身份运行（非 root）。**计划中**会切换成 `/Library/LaunchDaemons` 下的 root 权限 LaunchDaemon（见 docs/plan.md「多用户与鉴权」一节），到那一步之前，跨用户读写会因权限不足而静默跳过或返回错误——这是预期行为，不是 bug。

## 架构

纯 Python3 stdlib，零第三方依赖（和 `~/.claude/skills/task-manager`、`~/.claude/skills/dingtalk-notify` 风格一致）。没有 web 框架，`server.py` 自己实现了一个几十行的路由 + 静态文件服务。

```
server.py       ThreadingHTTPServer 入口；CLEAN_URLS 做 /path -> /path.html 映射；启动时 import 各 api_*.py 模块以触发它们的 @route 注册
routes.py       路由注册表（ROUTES 全局列表）+ ApiError + ResponseHelper（给 handler 一个设 cookie 的钩子）
auth.py         dscl 密码校验（密码走 stdin，不进 argv）+ 内存态 session（重启进程即失效，没有持久化）
users.py        list_local_users() 枚举 /Users/* 下的真实 macOS 账户（uid>=500，排除 Guest 等）；这是"多用户"整个设计的地基，其他模块都靠它知道"本机都有谁"
db.py           SQLite schema + 后台线程增量扫描所有用户的 ~/.claude/projects/*.jsonl
api_tokens.py   /api/overview /api/sessions /api/sessions/:id，pricing.json 驱动成本估算
api_skills.py   /api/skills，跨用户读，写操作靠 session.username == owner_user 收窄
api_tasks.py    /api/tasks，同上，外加 launchctl asuser 跨用户代持执行；计划时间用 crontab.py 校验/转换
crontab.py      标准 5 段 crontab 表达式（分 时 日 月 周）解析 + 校验 + 展开成 launchd 的 StartCalendarInterval（可能是单个 dict 或多个 dict 的列表，取决于表达式里有没有逗号/范围）；也有反向转换（StartCalendarInterval -> cron 字符串）给前端展示当前计划用。全部字段都是 `*` 会被拒绝（等于每分钟触发，不适合日历触发器），展开结果超过 100 条也会被拒绝
api_feedback.py /api/feedback，工单是全屋共享的（看板/skills/tasks 是"读共享写自己"，feedback 是"读写都共享"）
feedback_scanner.py  独立脚本（不被 server.py 引用），定时跑，见下方专节
static/         每个页面一个 html+js；dashboard.css 是全站共用样式表（不只是 dashboard 用）
```

## 前端约定（改 UI 时follow这个）

- **样式**：`static/vendor/pico.min.css`（MIT，vendored，和 Chart.js 一样不走 CDN）打底，靠语义化标签拿默认样式——卡片用 `<article>`，导航用 `<nav><ul>...</ul><ul>...</ul></nav>`（多个 `<ul>` 是 Pico 的标准写法，用来在 nav 里分组左右对齐），表单控件（`input`/`select`/`button`/`textarea`）不用另外套类基本就是好看的。`dashboard.css` 只保留 Pico 没有的东西：`.topbar`、`.stat-tile`、`.badge`、`#detail-overlay`/`#detail-panel` 抽屉、`.pagination`。新加页面元素前先看 Pico 有没有现成的，别重新造。
- **列表只读，编辑一律进抽屉**：`skills.html`/`tasks.html` 的表格行本身不放任何可编辑控件（不放 `<select>`、不放输入框），点一行才在 `#detail-panel` 里展开完整的操作面板（状态切换、内容编辑、计划时间、启停/触发按钮、日志）。这是产品上明确要的模式，新加列表页也照这个来，别在 `<tr>` 里塞 `<input>`/`<select>`。
- **大列表要分页**：session 详情里的时间线（`main_thread`/`sidechain`）用 `dashboard.js` 的 `renderPaginatedTurns()` 分页渲染（20/页），因为单个 session 常有几百轮。以后别的地方渲染可能很长的列表，抄这个模式，别一次性塞进 DOM。

## 已知踩过的坑

**`#detail-overlay` / `#detail-panel` 的 z-index**（session 详情、skill 编辑器、task 日志、feedback 对话线程共用同一套弹层组件）：`#detail-overlay` 设了 `z-index: 10`，但 `#detail-panel` 原来没设 z-index（默认 `auto`）——按 CSS 层叠规则，`auto`/`0` 层叠上下文早于正数 z-index 绘制，所以哪怕 `#detail-panel` 在 DOM 里排在后面，视觉上"看起来在前面"，实际点击/拖选事件全被 `#detail-overlay` 挡在上层截胡了。表现就是：面板里的 textarea 点不进去、选不了文字、复制不了——四个功能模块（看板/skills/tasks/feedback）用的是同一个弹层组件，所以这个 bug 一次性影响了全部详情页。修复：给 `#detail-panel` 显式设 `z-index: 11`（比 overlay 高）+ `user-select: text`。**以后凡是新加一个用到 `#detail-overlay`+`#detail-panel` 的弹层，都要确认面板的 z-index 高于 overlay**，这类"看起来对但点不动"的问题很容易被当成 JS bug 去排查，其实是纯 CSS 层叠问题。

## 路由约定（写新 API 时follow这个）

```python
@route("GET", r"/api/foo/(?P<id>\d+)", public=False)  # public=True 才不需要登录
def handler(match, query, body, session, resp):
    # match: re.Match，用 match.group("id") 取路径参数
    # query: parse_qs 的结果，值是 list，例如 query.get("range") -> ["30d"] 或 None
    # body: POST/PUT 的 JSON body，dict（GET 请求是 {}）
    # session: 已登录用户的 {"username":..., "uid":..., "expires":...}，public 路由可能是 None
    # resp: ResponseHelper，要设 cookie 才用得到，其余情况忽略
    raise ApiError(404, "not found")  # 抛这个会被自动转成对应状态码的 JSON 错误
    return {"some": "json"}           # 正常返回值会被 json.dumps 序列化
```

新写的模块要在 `server.py` 顶部 `import api_xxx  # noqa: F401` 一下（哪怕不直接用），否则它的 `@route` 装饰器不会执行，路由就注册不上——这是最容易踩的坑。

## 多用户权限模型（贯穿所有模块的核心约束）

这个应用管理的是**整台 Mac**，不是单个用户目录。本机目前有 `robinzheng`（管理者）和 `renyina` 两个真实账户。规则统一是：

- **token 看板**：全屋汇总可见，谁登录都看全部数据，可按 `owner_user` 筛选。
- **skills / tasks**：全屋列表可见（带 `owner_user` 字段），但**写操作**（toggle skill、改 schedule、enable/disable/run task）必须 `session["username"] == owner_user`，否则 403。前端也会把不属于自己的行对应按钮置灰，但**后端的校验才是唯一防线**——写新的写操作 API 时永远先检查这个，别只信前端。
- **feedback**：读写都全屋共享，因为反馈的对象是这个 App 本身，不是个人隐私资源。

对应到写文件时：写别的用户的文件（`settings.json` / `SKILL.md` / plist）之后要 `os.chown` 回那个用户的 uid/gid（`api_skills.py`/`api_tasks.py` 里有例子），否则文件会变成当前进程用户所有，等切到 root 运行后会导致目标用户自己没法再正常读写。当前非 root 运行时，跨用户写会直接 `PermissionError`，各处已经 `try/except` 兜底成合理的 4xx/静默跳过。

## feedback_scanner.py 的安全设计（改这个文件前务必读）

这个脚本会真的调 `claude -p --resume <session_id>` 驱动一个带工具（能读写文件）的 Claude Code session 去修 bug，然后**自动本地 git commit**（不 push、不重启服务，这是产品决策，不是技术限制）。

**曾经的真实 bug**：最早的实现在处理完一个工单后，只看"仓库现在是不是 dirty"就决定要不要 commit——如果仓库里本来就有别的未提交改动（比如我正在写别的功能），会被误判成"这个工单改的"，commit message 却写着这是自动修复。用一个独立的临时仓库副本测出来的（正式仓库当时已经意外多了两个错误归因的 commit，靠 `git reset --soft` 撤销修复的）。

现在的防护（**改动 `_commit_if_changed` / `main()` 时不要绕开这两条**）：
1. `main()` 一开始检查 `_repo_is_clean()`——**如果仓库本来就是脏的，整轮直接跳过**，不处理任何工单。避免把人类正在做的事误算成 AI 的修复。
2. 每个工单处理前记录 `before_head = _current_head()`，处理后同时检查「有没有新的未提交改动」和「HEAD 有没有走（AI 自己 commit 了）」，两者任一为真才算「这个工单改了东西」，diff 范围是 `before_head..after_head`——不是"repo 现在 dirty 不 dirty"这种全局状态。

复现验证方式：`cp -r ~/admin-web /tmp/xxx` 到一个独立目录，在那个副本里跑，不要在真实仓库上做这种实验（see `tests/test_feedback_scanner.py` 已经把这个场景写成自动化测试了）。

工具权限复用自 `~/.claude/skills/dingtalk-notify/headless.py`（`ALLOWED_TOOLS`/`DISALLOWED_TOOLS`，挡住 `git push`/`sudo`/`launchctl`/`rm`/`chmod`/`chown` 等）和 `headless_session.py` 的订阅 OAuth 环境变量模式（不注入 `ANTHROPIC_API_KEY`，走 Claude Code 自己的订阅登录）。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

纯 stdlib `unittest`，没有引入 pytest（保持零依赖）。测试原则：
- 涉及文件系统的模块（`users.py`/`api_skills.py`/`api_tasks.py`）一律用 `tempfile` 造假的 home 目录 + `unittest.mock.patch` 替换 `list_local_users`，**不碰真实的 `~/.claude` 或 `~/Library/LaunchAgents`**。
- 涉及 `launchctl`/`git`/`claude` 这类外部命令的，要么 mock 掉 `subprocess.run`，要么（像 `feedback_scanner` 的 git 归因逻辑）在一个临时 git 仓库里跑真实命令——但绝不在这个真实项目仓库上跑。
- `db.py`/`api_tokens.py`/`api_feedback.py` 的测试用 `mock.patch.object(db, "DB_PATH", tmp_path)` 指向临时 SQLite 文件。

加新功能时照着抄这个隔离方式，别在测试里真的碰生产数据/真实 launchd/真实 git 历史。
