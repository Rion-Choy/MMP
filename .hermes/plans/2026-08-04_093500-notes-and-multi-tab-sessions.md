# 备注字段与多标签页会话隔离实施方案

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 将管理员端“标签”收敛为每个隐私邮箱一个可编辑的备注文本框，并修复同一浏览器多个隐私邮箱查看页共享公共 session cookie 导致的验证码互相覆盖问题。

**Architecture:** 备注属于 `private_targets` 的单值文本属性，不再需要 `target_tags`、标签颜色、标签筛选和标签管理页面。备注最大 100 个 Unicode 字符，使用增量 SQLite migration 将现有标签文本迁移为备注后移除旧标签结构；管理员端通过独立 JSON/POST 保存备注，前端在输入框失焦时仅更新当前列表行状态，不进行整页刷新。公共查看页改用每个访问 token 一个 Cookie 名称，使同一浏览器的不同邮箱页面拥有互不覆盖的 session；管理员登录 Cookie `mail_portal_admin` 与公共邮箱 Cookie 保持不同名称、独立验证。管理员登录态固定有效 24 小时，不做滑动续期；管理员 CSRF Cookie 同步使用 24 小时浏览器有效期。

**Tech Stack:** FastAPI、Jinja2、SQLAlchemy、Alembic、SQLite、原生 JavaScript、pytest/TestClient。

---

## 已确认的现状与根因

### 问题 1：当前标签实现

当前代码已确认存在以下结构：

- `app/models.py`：`PrivateTarget.tag_id`、`TargetTag` 表以及 ORM relationship。
- `migrations/versions/0003_target_tags.py`：新增 `target_tags`、`private_targets.tag_id`、索引。
- `app/services/tag_service.py`：创建、重命名、删除、分配标签。
- `app/routes/admin.py`：`/admin/tags` 管理页、标签分配、按 `tag_id` 筛选、快捷创建绑定。
- `app/templates/admin/tags.html`：独立标签管理页面。
- `app/templates/admin/targets.html`：目前尚未显示标签控制，但后端仍支持标签筛选和分配。

生产数据库当前：

- `private_targets`：24 条
- `target_tags`：2 条（`free`、`plus`）
- 已分配标签的隐私邮箱：6 条
- `mail_messages`：47 条
- Alembic 当前为 `0004_incremental_sync`，数据库完整性检查为 `ok`

因此不能直接删表或重建数据库；必须先把现有标签信息迁移到备注中，且保留邮箱、邮件和其他运行态数据。

### 问题 2：多标签页 session 冲突的已复现根因

当前公共 session cookie 是固定名称：

```text
mail_portal_session
```

并且路径为 `/`。所以浏览器只能保存一个该名称的 cookie：

1. 打开邮箱 A 页面，服务端设置 A 的 session cookie；
2. 再打开邮箱 B 页面，服务端用相同 cookie 名覆盖成 B 的 session cookie；
3. 回到 A 点击“刷新”，请求携带的是 B 的 session；
4. `find_session(db, raw_id, target_a_id)` 找不到匹配的 A session；
5. refresh 路由创建新的未验证 session，返回 `captcha_required`。

已用独立 TestClient 复现：打开 A、打开 B 后，A 的 refresh 返回 `captcha_required`，证明问题不是验证码有效期，而是同名根路径 cookie 被覆盖。

当前的 `find_session` 已正确绑定 `session_id_hash + target_id`，不能放宽为只按 session ID 查找，否则会造成一个邮箱的 session 访问另一个邮箱的风险。

---

## 方案决策

### A. 标签改备注

推荐采用以下语义：

- 每个隐私邮箱最多一个备注；
- 备注是普通文本，不参与唯一性校验、不做颜色、不做标签筛选；
- 数据库字段名为 `note`，类型 `TEXT NULL`，最大长度为 **100 个 Unicode 字符**；
- 空白备注规范化为 `NULL` 或空字符串，推荐统一存 `NULL`；
- 备注只显示在管理后台，不传给公共邮件页面；
- 输入框使用 `<textarea>`，进入/获得焦点即可编辑；失焦（`blur`）时与进入编辑前的值比较，只有发生变化才发请求；
- 保存成功不整页刷新。推荐更新当前备注单元格的状态/值并显示短暂“已保存”标记；如果需求中的“只刷新列表页”是指重新读取列表数据，则可由前端请求成功后重新请求/替换列表区域，但不应使用 `window.location.reload()`。优先选择只更新当前行，避免在保存备注时重新执行整个列表页请求；
- 网络失败时保留用户输入，显示“保存失败，请重试”，不要静默丢失编辑内容。

### B. 多邮箱页 session 隔离

推荐采用“按 token 生成 cookie 名称”的方案，而不是仅依赖 cookie Path：

```text
mail_portal_session_<规范化 token>
```

实现要求：

- 只允许安全字符组成 cookie 名；token 当前是 UUID，可将连字符保留或去除；更稳妥的是使用 token 的安全短 hash 作为 cookie 名后缀，避免 cookie 名过长；
- 所有公共路由必须通过同一 helper 计算当前 token 对应的 cookie 名：入口页、验证码图片、验证码提交、view、AJAX refresh；
- cookie 仍使用 `HttpOnly`、`SameSite=Strict`、`Secure`（生产环境）、`Path=/`、既有 TTL；
- 服务端继续通过 `find_session(db, raw_id, target.id)` 做 target binding，不能因为 cookie 名已隔离而省略；
- 旧版固定名 `mail_portal_session` 可保留一个过渡读取策略，但不能让旧 cookie 影响新 token 会话。建议：新代码优先读取 token-specific cookie；若没有新 cookie，可短期读取旧固定 cookie，仅当它与当前 target 匹配时迁移为 token-specific cookie；若不匹配则创建当前 token 的新 session，并删除/忽略旧固定 cookie。若不需要兼容已有浏览器会话，也可以直接只使用新 cookie 名，旧 cookie 自然失效并要求重新验证一次；
- 不要采用 `Path=/m/<token>` 作为唯一修复，除非同时完整考虑 `/m/<token>/captcha.svg`、`/m/<token>/verify`、`/m/<token>/view`、`/m/<token>/refresh` 的路径匹配及旧 cookie 清理。按 token cookie 名更直观，也避免同一 token 路径和根路径 cookie 的重复发送问题。

---

## 实施步骤（严格 TDD，暂不执行）

### Task 1：先定义备注数据契约并写迁移测试

**Files:**
- Modify: `app/models.py`
- Create: `migrations/versions/0005_target_notes.py`
- Test: `tests/test_target_notes.py`
- Modify: `tests/test_models.py`

步骤：

1. 先写测试，覆盖：
   - `PrivateTarget.note` 可为空；
   - 备注允许普通文本，数据库中可以保留换行；
   - 长度/空白规范化由服务层处理，而不是依赖 SQLite 字段类型；
   - migration 在已有 24 个 target、2 个 tag、6 个 assignments 的 fixture 上，把标签名称复制到 `private_targets.note`；
   - 迁移后 `private_targets.note` 数据正确，其他 target/message 行数不变；
   - 迁移是增量的，不删除 `private_targets` 或 `mail_messages`。
2. 运行新增测试并确认按预期失败（RED）。
3. 在模型中增加 `note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)`。
4. 编写 `0005_target_notes`：
   - 添加 nullable `note` 列；
   - 从 `target_tags` 联结 `private_targets`，把 `TargetTag.name` 复制到对应 `note`；
   - 对无标签 target 保持 NULL；
   - 在确认数据复制完成后删除旧 tag 索引、`tag_id` 列和 `target_tags` 表，或分成两个 migration。推荐在同一个受备份保护的 migration 内完成，避免最终 ORM 与旧结构长期不一致；
   - SQLite 删除列/表若触发 batch table-copy，必须使用 Alembic batch 模式，并在测试中验证数据保留；
   - downgrade 不能假设能无损恢复原来的多个标签元数据；若项目不需要 downgrade，可明确写文档/注释并将回滚边界定义为备份恢复，而不是伪造不可逆的 downgrade。
5. 运行迁移测试并确认 GREEN。

> 备注：如果希望最大限度降低 SQLite 表重建风险，可以保留 `target_tags` 和 `tag_id` 作为废弃兼容列，只新增 `note`；但这会留下旧标签表和旧代码，长期维护成本更高。推荐“先迁移数据，再删除旧功能”的清理方案，不过必须在备份和离线/维护窗口内执行。

### Task 2：移除标签领域代码，添加备注服务

**Files:**
- Modify/Create: `app/services/target_service.py` 或新建 `app/services/note_service.py`
- Modify: `app/routes/admin.py`
- Delete or retire: `app/services/tag_service.py`
- Delete or retire: `app/templates/admin/tags.html`
- Modify: `app/templates/admin/targets.html`
- Test: `tests/test_target_notes.py`, `tests/test_admin_routes.py`

步骤：

1. 先写失败测试：
   - 管理员在 targets 页看到“备注”列和一个 `<textarea>`；
   - 已有迁移备注正确回显；
   - POST/JSON 保存新备注后数据库值改变；
   - 空白输入保存为 NULL/空备注；
   - 超出上限返回明确的 4xx 或可测试的错误结果，且原备注不被覆盖；
   - 未登录、CSRF 缺失/错误不能写备注；
   - public 页面不出现备注内容；
   - `/admin/tags` 不再是用户界面入口（返回 404 或重定向，需在实现前决定）；
   - 标签筛选参数不再参与 targets 列表逻辑。
2. 运行测试确认 RED。
3. 增加独立端点，例如：

```text
POST /admin/targets/{target_id}/note
Content-Type: application/x-www-form-urlencoded 或 application/json
字段：note、csrf_token
响应：JSON {"status":"ok","note":...}
```

推荐 JSON 响应，便于前端根据结果更新当前行状态；服务端仍通过现有 admin session 和 CSRF 校验。
4. 服务层统一做：`strip()`、长度限制、空字符串转 `None`、目标存在/未删除校验。
5. targets 查询移除 `tag_id` 过滤、`TargetTag` 查询和上下文变量；模板删除标签相关列/入口，加入备注 textarea 和保存状态元素。
6. 如果 `/admin/tags` 旧 URL 仍可能被收藏，可暂时 301/303 到 `/admin/targets`，但不要再提供创建/删除标签的写接口。
7. 注意 Jinja 自动转义：备注回显必须使用普通变量，不使用 `|safe`；textarea 中的换行由 HTML 转义处理。
8. 运行相关测试确认 GREEN。

### Task 3：实现备注 blur 保存，不刷新整页

**Files:**
- Modify: `app/templates/admin/targets.html`
- Modify: `app/static/admin-targets.js`（如不存在则新增）
- Modify: `app/static/style.css`
- Test: `tests/test_target_notes.py` 或新增静态契约测试

步骤：

1. 先写静态契约测试，确认：
   - 备注 textarea 有 target ID、保存 URL、CSRF token 或可取得 CSRF token 的方式；
   - JS 监听 `focus`/`focusin` 和 `blur`；
   - 只有新旧值不同才发送请求；
   - 使用 `fetch`，不包含 `window.location.reload()`；
   - 成功时只更新当前列表行/状态，不替换整页；
   - 失败时显示错误且不覆盖 textarea。
2. 运行测试确认 RED。
3. 最小实现：
   - `focus` 时记录初始值；
   - `blur` 时读取当前值，若未变化直接返回；
   - 防止同一 textarea 的并发保存（例如保存中禁用或记录请求序列）；
   - 发送 `POST`，携带 `note` 和 CSRF；
   - 处理 JSON 成功/失败；
   - 只有响应成功后更新“已保存”状态；
   - 若用户在请求期间再次编辑，不能用旧响应覆盖新值，必要时使用 request version/dirty 标记；
   - 不触发整页刷新。
4. CSS 只增加备注 textarea 的宽度、最小高度、状态文案样式，避免大范围视觉改版。
5. 运行静态测试和 `node --check`（若环境有 Node）。

### Task 4：先写多标签页 session 回归测试

**Files:**
- Modify: `tests/test_public_refresh.py`
- Modify: `tests/test_targets.py`
- Modify: `tests/test_security_flows.py`
- Optionally create: `tests/test_public_multi_tab_session.py`

步骤：

1. 写一个最小回归测试，模拟同一浏览器/同一 cookie jar：
   - 创建 token A、token B；
   - 分别使用独立的浏览器 cookie jar 或测试客户端代表两个 tab；
   - A 完成验证码验证；
   - B 完成验证码验证；
   - A 再调用 `/m/A/refresh?page=1`；
   - 预期仍返回 `status=ok`，而不是 `captcha_required`；
   - B refresh 同样返回 `status=ok`；
   - A cookie 与 B cookie 不同名称或不互相覆盖；
   - A 的 cookie 不能访问 B 的 `/view`。
2. 测试过期 session 仍要求验证码；target binding 仍然有效；
3. 运行测试确认当前实现按预期失败（RED）。

### Task 5：实现 token-specific 公共 session cookie

**Files:**
- Modify: `app/routes/public.py`
- Modify: `app/config.py`（如需要增加 cookie 名生成配置）
- Modify: `tests/test_public_refresh.py`
- Modify: `tests/test_security_flows.py`

步骤：

1. 添加单一 helper，例如：

```python
def public_session_cookie_name(token: str) -> str:
    # token 已由 get_active_target 校验为 UUID；仍建议使用安全 hash 后缀
    suffix = hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
    return f"mail_portal_session_{suffix}"
```

2. 将 `get_or_create_public_session`、`captcha_image`、`verify_public_captcha`、`public_messages`、`refresh_public_messages` 全部改为使用该 helper 读取 cookie。
3. `set_public_session_cookie` 接收 `cookie_name` 参数，统一设置 `Path=/`、TTL、HttpOnly、SameSite、Secure。
4. 旧固定 cookie 处理策略二选一并写成测试：
   - 简洁方案：停止读取旧 cookie，第一次访问每个 token 重新生成 session；
   - 兼容方案：若当前 token cookie 不存在，读取旧 cookie 仅在 `find_session` 对当前 target 成功时迁移；不匹配时忽略并创建当前 token session，同时可删除旧 cookie。
   推荐简洁方案，代码和安全边界更清晰；代价是已经打开的旧页面需重新验证一次。
5. 保持 `find_session` 的 target_id 条件，不能以“cookie 名已隔离”为理由移除。
6. 运行回归测试确认 GREEN。

### Task 6：公共 refresh 的交互和安全边界验证

**Files:**
- Modify: `app/static/public-messages.js`（如需提示/处理新 cookie）
- Modify: `app/templates/public/_captcha_content.html`（如需说明）
- Test: `tests/test_public_refresh.py`

步骤：

1. 确认 refresh 成功仍只返回数据库邮件 fragment；不启动 Graph/worker；
2. 缺失/过期 session 仍返回验证码 fragment，并设置当前 token 专用 cookie；
3. 多标签页中 A 的验证码输入不会被 B 的访问覆盖；
4. 不在客户端存储验证码答案、raw session ID 或 token 之外的敏感信息；
5. 保持 `credentials: "same-origin"`；
6. 不使用整页 `window.location.reload()`。

### Task 7：完整验证与部署准备（本阶段仍不执行）

在获得用户明确批准修改和部署后，按以下顺序执行：

1. 先在测试数据库跑新 migration，运行专项测试；
2. 跑完整测试：

```bash
cd /var/lib/hermes/mail-portal
.venv/bin/pytest -q
.venv/bin/python -m compileall -q app scripts tests
.venv/bin/alembic check
```

3. 部署前检查运行边界：确认 `mail-portal-web.service` PID、`mail-portal-sync.service` PID、端口、活动同步任务；当前已知 PID 为 Web `656574`、Worker `658749`，部署时必须重新读取，不能直接复用旧值；
4. 备份数据库，至少记录：
   - `alembic current` / `alembic heads`；
   - `PRAGMA integrity_check`；
   - `private_targets`、`target_tags`、`mail_messages`、`mail_recipients`、`sync_runs` 计数；
   - 备份绝对路径。
5. 以服务用户和显式 `MAIL_PORTAL_DATA_DIR=/var/lib/hermes/mail-portal/runtime` 执行 migration；
6. 验证 Alembic head、schema、备注迁移结果、数据计数、integrity；
7. 只在用户授权后重启 Web；验证新备注端点/页面 marker 和本地/public health；
8. Worker 本次不受备注和公共 cookie 逻辑影响，原则上不需要重启；若 ORM migration 改变了 worker 依赖的模型加载，先确认 schema 后再决定是否重启；
9. 通过实际浏览器或等价 Playwright 场景验证：同一浏览器打开 A/B 两个链接、分别验证码、交替刷新，均不再互相要求验证码；
10. 检查 Caddy 实际配置和响应头；本次公共 session 修复通常不需要 Caddy reload，但仍要确认公网访问正常。

---

## 风险、取舍与未决问题

1. **旧标签数据如何转换：** 当前 `free`/`plus` 标签会迁移为对应邮箱备注。若用户不想保留这些值，需要在迁移前明确“标签全部丢弃”；默认不丢数据。
2. **是否保留旧标签表：** 删除旧结构更干净，但 SQLite 可能触发表复制；保留废弃字段迁移风险更低但会留下技术债。推荐先做数据备份和测试 migration 后删除。
3. **备注最大长度：** 已确认按 100 个 Unicode 字符处理；后端和前端都校验，超限不覆盖原值。
4. **“只刷新列表页”的含义：** 本次实现不刷新整个浏览器文档，只异步更新当前备注行的保存状态；如果后续需要重新读取整张列表，也应使用 AJAX 替换列表区域而不是页面 reload。
5. **管理员登录态：** 已确认固定 24 小时有效，不采用滑动续期；签名校验和浏览器 Cookie `Max-Age` 都使用 86400 秒，管理员 CSRF Cookie 同步使用 24 小时。管理员 Cookie `mail_portal_admin` 与公共邮箱的 token-specific Cookie 独立，二者可在同一浏览器共存。
6. **旧 session 兼容：** 简洁方案会让旧打开页面重新验证一次；兼容方案代码稍复杂，但可平滑迁移。建议根据用户是否重视已有页面会话决定。
7. **Cookie 数量：** 按 token 分配 cookie 名会让一个浏览器保存多个邮箱 session；这正是目标，但应设置合理 TTL，并由现有 worker/清理任务删除数据库中过期 session。浏览器端过期 cookie 由 Max-Age 控制。
8. **并发保存备注：** 需要处理 blur 请求和用户快速再次编辑的竞态，不能让旧请求响应覆盖新输入。

## 当前结论

- 问题 1可行，推荐做成 `private_targets.note`，而不是继续保留“单标签”的概念；需要一次增量迁移来保护已有 `free`/`plus` 数据。
- 问题 2已找到并复现确定根因：固定名称、根路径的 `mail_portal_session` cookie 在多个邮箱页面之间互相覆盖；不是验证码本身失效。
- 当前没有修改任何项目代码或数据库；只进行了只读检查，并将方案保存到本计划文件。
