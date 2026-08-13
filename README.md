# MailRelay Guard（邮件中继防护）

MailRelay Guard 是为 Alice 一类 AstrBot 助手准备的 SMTP 邮件插件。它允许助手直接投递邮件，同时把收件人权限固定在插件服务端，而不是交给提示词或模型自行判断。

插件标识：`astrbot_plugin_mailrelay_guard`

需要 AstrBot `4.27.2` 或更高版本，且低于 `5`。此版本要求同时覆盖直接 LLM 工具的正确重载，以及 Dashboard 插件 Pages 的邮件中心能力。

## 功能与权限

本插件提供三条彼此独立的 LLM 收件人权限路径。纯文本工具默认可用；开启 `enable_html_mail` 后，每条路径还会获得对应的 HTML 模板工具。每种工具的参数和服务端权限校验都不同，普通用户无法把“发给自己”的请求变成向第三方投递。

| 使用场景 | LLM 工具 | 实际收件人 | 可使用者 |
| --- | --- | --- | --- |
| 通知主人 | `mailrelay_notify_owner`；HTML：`mailrelay_notify_owner_html` | 固定的 `owner_email` | 同时位于 `owner_sender_ids` 且被 AstrBot 识别为管理员的账号 |
| 发给自己 | `mailrelay_send_to_self`；HTML：`mailrelay_send_html_to_self` | 当前发送者已解析或已验证绑定的邮箱 | 拥有可用邮箱的用户，默认仅限私聊 |
| 发给他人 | `mailrelay_send_to_recipient`；HTML：`mailrelay_send_html_to_recipient` | 管理员在参数中指定的邮箱 | 同时位于 `admin_sender_ids` 且被 AstrBot 识别为管理员的账号 |

设计目标如下：

- Alice 可以直接向配置好的主人邮箱发信，不经过草稿确认。
- 普通用户可以让 Alice 发信给自己，但不能指定任何其他收件人。
- 只有已配置的 AstrBot 管理员可以让 Alice 向其他人发信。
- 斜杠命令和 LLM 工具执行相同的服务端权限规则。面板中的命令权限只是额外保护，不是唯一安全边界。

## 图片与文件附件

六个 LLM 邮件工具都支持附件。`include_message_media` 默认是 `true`，因此你可以直接在 QQ 中把图片或文件与请求放在同一条消息里，例如：

```text
[发送一张图片] 爱丽丝，把这张图发到我邮箱，正文写“旅行照片”。
```

也可以回复一条包含图片或文件的旧消息，再让爱丽丝发送。AstrBot/NapCat 会把引用消息解析到 `Reply.chain`，插件从标准 `Image` 和 `File` 组件取媒体，不需要额外填写 NapCat 地址或 Token。

爱丽丝在当前会话工作区生成了报告、表格或图片时，可以通过工具的 `attachment_paths` 附加它们。路径只允许位于当前会话工作区、当前事件已登记的临时文件，或沙箱中的 `/workspace`；不会读取任意服务器路径。普通用户即使要求附加文件，收件人仍然只能是自己的已解析或已验证邮箱；只有管理员代发工具能指定别人。

HTML 邮件可以让爱丽丝在模板中使用图片占位符：

```html
<div style="padding:24px;background-color:#10131f;color:#ffffff">
  <h1 style="color:#00e5ff">Alice 的图片邮件</h1>
  <p>下面是你在 QQ 中发来的第一张图片：</p>
  {{image_1}}
</div>
```

`{{image_1}}` 表示本次附件中的第一张可嵌入图片，依次可用 `{{image_2}}`、`{{image_3}}`。占位符也可写在 `<img src="{{image_1}}">` 中。插件会生成随机 Content-ID 并构造标准 `multipart/related` 邮件；未被引用的图片仍作为普通附件。CID 图片不依赖外部网站，和 `html_allow_remote_images` 的远程图片白名单互不混用。

默认限制为最多 `6` 个附件、单个 `10 MB`、合计 `20 MB`、读取超时 `20` 秒，并阻止常见可执行文件和脚本扩展名。任一附件不符合限制时会拒绝整封邮件，不会静默少发。网易及收件方仍可能有更低的服务商限制。

## 从 v1.0 升级

v1.1 使用三种直接投递工具取代了旧版的草稿确认流程。旧的草稿命令、草稿令牌和对应配置项已移除，不再需要确认令牌。

升级后，请在启用真实投递前填写 `owner_email`，并分别配置 `owner_sender_ids` 与 `admin_sender_ids`。已有的 SMTP 凭据可以继续使用。旧版的收件人白名单设置改为管理员专用的第三方投递策略。

`v1.1.1` 还修正了测试邮件对示例主人邮箱的处理：没有填写真实 `owner_email` 时，`/mailrelay_send_test` 会拒绝发送，不会把邮件投递给示例地址。

`v1.1.2` 进一步按拒绝优先的原则处理损坏配置：显式清空或写为 `null` 的 `smtp_host` 不会静默恢复为 `smtp.163.com`；无法识别或为 `null` 的敏感布尔开关也不会意外开启投递能力。

`v1.2.0` 增加可配置清洗的 HTML 模板邮件。HTML 功能默认关闭，升级后不会改变现有纯文本工具的行为。

`v1.3.0` 增加 Dashboard 邮件中心和本地投递历史。历史记录默认只保存脱敏元数据；邮件主题和正文仍默认不落盘。升级后请确认 AstrBot 已升级到 `4.27.2` 或更高版本。

`v1.4.0` 新增受策略保护的邮件附件：爱丽丝可以把当前 QQ 消息或引用回复里的图片/文件附在邮件中，也可以附加当前会话工作区中由她生成的文件。HTML 模板支持 `{{image_1}}`、`{{image_2}}` 占位符，图片会作为邮件标准 CID 资源嵌入；没有被模板引用的图片仍会作为普通附件。附件会在服务端执行数量、大小、超时、来源和危险扩展名检查，普通用户的收件人权限不受影响。邮件中心详情会显示附件元数据，但不会保存附件二进制内容。

`v1.3.4` 继续优化邮件中心的选择与阅读：列表标题独占首行，收件人和时间在下一行，SMTP 状态和 HTML 格式不再挤占标题；完整状态仍可在详情中查看。选中邮件后可点击“专注阅读”收起列表，或点击“全屏阅读”进入原生全屏；若 AstrBot 的嵌入页面禁止原生全屏，页面会自动保持可退出的沉浸阅读模式。预览默认阅读尺寸为 120%，可在 90% 至 145% 间本地调节。

## 网易 163 快速配置

默认值已经针对网易 163 邮箱设置：

| 配置项 | 默认值 |
| --- | --- |
| `smtp_host` | `smtp.163.com` |
| `smtp_port` | `465` |
| `smtp_security` | `ssl` |

请在 AstrBot 插件配置中替换以下示例值：

| 配置项 | 应填写的内容 |
| --- | --- |
| `smtp_username` | 真实的网易邮箱完整地址，例如 `alice@163.com` |
| `smtp_password` | 网易邮箱生成的 SMTP 客户端授权码，不是网页登录密码 |
| `sender_address` | 通常与 `smtp_username` 相同的真实邮箱地址 |
| `owner_email` | 接收 Alice 主人通知的邮箱地址 |
| `owner_sender_ids` | 主人身份的 `platform_id:sender_id` 列表 |
| `admin_sender_ids` | 允许向他人代发的管理员身份 `platform_id:sender_id` 列表 |

`SMTP 登录账号` 就是 SMTP 服务器登录使用的真实邮箱地址。对于 163 邮箱，通常是完整地址，例如 `alice@163.com`。`smtp_password` 必须填写网易生成的客户端授权码。

配置步骤：

1. 在网易邮箱中开启 SMTP 服务，并生成客户端授权码。
2. 在 AstrBot 的插件配置中填写上表内容。
3. 用你的 QQ 账号向机器人发送 `/mailrelay_whoami`。
4. 将返回的完整 `platform_id:sender_id` 放入 `owner_sender_ids`。如果该账号也需要给其他人发信，再放入 `admin_sender_ids`。
5. 在 AstrBot 中把同一账号配置为管理员。
6. 重载插件，执行 `/mailrelay_smtp_test` 测试 TLS 和登录。
7. 执行 `/mailrelay_send_test` 向真实 `owner_email` 发送固定测试邮件。

身份列表默认是空列表，这是刻意的安全默认值。未填写身份时，任何账号都不能触发主人通知或管理员代发。请勿在截图、聊天记录或公开仓库中泄露客户端授权码。

## QQ 与 NapCat 邮箱解析

不需要填写 NapCat 的 URL，也不需要额外部署 HTTP 客户端。MailRelay Guard 会复用 AstrBot 已建立的 OneBot/NapCat 事件连接。

QQ 邮箱不是 OneBot 消息的标准字段，因此插件只会为当前发言者按以下顺序尝试安全、可检测的来源：

1. 管理员在 `self_email_overrides` 中配置的精确身份映射。
2. 用户已验证的邮箱绑定。
3. NapCat 的 `get_stranger_info` 恰好返回 `email` 或 `eMail` 字段。
4. 可选的 NapCat 好友列表回退，只在内存中匹配当前发言者。
5. 可选的 QQ 号邮箱推导。

NapCat 和 QQ 并不保证第 3、4 步能返回邮箱。这取决于 NapCat 版本、好友关系、资料隐私设置以及用户是否开通邮箱。插件默认不会猜测邮箱。

`qq_platform_names` 必须与 `/mailrelay_whoami` 输出中的 `platform_id` 一致。OneBot/NapCat 常见值为 `aiocqhttp`，也是默认值。

`allow_qq_mailbox_derivation` 默认是 `false`。开启后，只有当前 `platform_id` 位于 `qq_platform_names` 且发送者 ID 是纯数字时，插件才会把 QQ 号推导为 `QQ号@qq.com`。这是推导，不是资料读取，也不能证明该邮箱已开通。

例如，确认接受此回退方式后可以使用：

```json
{
  "allow_qq_mailbox_derivation": true,
  "qq_mail_domain": "qq.com",
  "qq_platform_names": ["aiocqhttp"]
}
```

`self_email_overrides` 是列表，每项格式如下：

```text
platform_id:sender_id=email@example.com
```

它优先于用户绑定和 QQ 资料，仅适合管理员已经确认过身份和邮箱的场景。

### 自助绑定备用邮箱

当 Alice 无法解析用户邮箱时，用户可以在私聊中绑定自己的邮箱：

```text
/mailrelay_bind user@example.com
/mailrelay_verify 123456
```

插件会先发送一次性验证码。只有同一平台身份返回正确验证码后，邮箱才会保存。用户随时可以执行：

```text
/mailrelay_unbind
```

默认情况下，绑定、验证和解绑只允许在私聊中执行，避免邮箱地址或验证码出现在群聊。`/mailrelay_identity` 只会说明是否有可用邮箱及其来源，不会在聊天中显示完整邮箱地址。

## LLM 工具

保持 `enable_llm_mail_tools=true` 即可注册以下三个纯文本工具。修改后请重载插件。

### `mailrelay_notify_owner`

```json
{"subject":"任务完成","body":"Alice 已完成计划任务。"}
```

收件人始终是 `owner_email`，工具没有收件人参数。它会直接投递，但只有配置的主人身份且具备 AstrBot 管理员权限时才能触发。

### `mailrelay_send_to_self`

```json
{"subject":"你请求的摘要","body":"Alice 已整理好你请求的摘要。"}
```

工具没有收件人参数。它只能投递给当前事件发送者解析出的邮箱。即使对话中出现其他人的邮箱地址，也不会改变投递目标。默认仅限私聊；只有明确需要群内自助投递时，才应将 `require_private_chat_for_self_delivery` 设为 `false`。

### `mailrelay_send_to_recipient`

```json
{
  "recipients":"person@example.com",
  "subject":"管理员通知",
  "body":"这封邮件由已配置的管理员请求发送。"
}
```

这是唯一带有收件人参数的工具。管理员身份和可选白名单都通过后会直接投递。将 `restrict_admin_other_recipients` 设为 `true` 后，收件人还必须匹配 `admin_other_recipient_allowlist` 或 `admin_other_allowed_domains`。

### HTML 模板工具

将 `enable_html_mail` 设为 `true` 并重载插件后，Alice 可以使用以下工具自由编写 HTML 模板：

| 收件人模式 | HTML 工具 | 收件人规则 |
| --- | --- | --- |
| 通知主人 | `mailrelay_notify_owner_html` | 固定为 `owner_email`，需要主人身份与 AstrBot 管理员权限 |
| 发给自己 | `mailrelay_send_html_to_self` | 只能投递给当前发送者已解析或已验证的邮箱 |
| 发给他人 | `mailrelay_send_html_to_recipient` | 仅配置的 AstrBot 管理员可指定收件人，仍受代发白名单限制 |

HTML 工具需要 `subject` 与 `html_body` 两个参数。`html_body` 是完整 HTML 片段，Alice 应使用内联 CSS。插件会在最终 SMTP 投递边界清洗内容，并从清洗后的 HTML 自动生成纯文本备用正文，因此不需要让模型额外维护两份正文。

```json
{
  "subject": "霓虹任务通知",
  "html_body": "<div style=\"background-color:#0b1020;border:1px solid #ff00aa;border-radius:12px;color:#f4f7ff;padding:24px;text-align:center\"><h1 style=\"color:#00f5ff\">任务完成</h1><p>爱丽丝已完成本次任务。</p></div>"
}
```

HTML 邮件会以 `multipart/alternative` 格式同时发送 HTML 与纯文本版本。QQ、网易、Gmail、Outlook 等客户端会各自过滤部分 CSS，因此视觉效果可能不同。斜杠命令仍保持纯文本，避免在聊天中直接粘贴长 HTML；样式化邮件应由 Alice 的 HTML 工具发送。

### HTML 清洗与外部资源

推荐的最小配置如下：

```json
{
  "enable_html_mail": true,
  "sanitize_html_before_send": true,
  "html_allow_links": false,
  "html_allow_remote_images": false,
  "html_remote_image_allowed_domains": [],
  "max_html_body_chars": 30000
}
```

严格清洗默认开启。它允许常用的邮件排版标签、表格和内联样式，例如颜色、边框、圆角、阴影、字号、间距和文本排版；会移除脚本、表单、`iframe`、SVG、事件属性、`javascript:`、`data:`、相对 URL、`<style>` 标签及 CSS 中的 `url()`、`expression()`、`var()` 等主动或外链内容。

关闭 `sanitize_html_before_send` 只会放宽一部分无主动内容的布局样式，基础安全过滤始终存在，不能作为任意网页直通开关。`html_allow_links` 开启后仅保留绝对 HTTPS 链接。远程图片默认关闭；要保留图片，必须同时开启 `html_allow_remote_images`，并在 `html_remote_image_allowed_domains` 填写精确主机名，例如 `cdn.example.com`。空白名单不会保留任何远程图片。

## Dashboard 邮件中心

安装并重载 `v1.4.0` 后，在 AstrBot Dashboard 的插件页面中打开 **MailRelay Guard - 邮件中心**。该页面由 AstrBot 的已登录 Dashboard 承载，不会额外启动公开的 Web 服务。

邮件中心提供以下工作区：

- **总览**：显示 SMTP 就绪状态、HTML 工具与清洗状态、今日投递数量和本地历史摘要。
- **发件箱**：显示 Alice 的本地发送尝试、投递模式、收件人数、SMTP 接受或拒绝结果及脱敏收件人地址。桌面端列表承担快速定位和切换邮件的导航职责，选中邮件后会在右侧主要工作区阅读。
- **本地投递副本**：按 SMTP 已接受的收件人展示本地镜像，可标记已读、收藏或归档。它不是 IMAP、POP3 或真实邮箱收件箱，也不能说明邮件已经最终送达或被阅读。
- **投递异常**：筛选失败、部分接受和未知结果，便于排查 SMTP 或收件人问题。
- **安全配置**：快速调整非敏感的投递开关、HTML 安全策略、历史保留和限流参数；修改 `enable_html_mail` 后仍需要重载插件，HTML 工具才会重新注册。

桌面端进入邮件中心后，左侧应用导航会切换为窄轨，列表控制默认只显示文件夹、搜索、数量和刷新；筛选与归档说明按需展开。每封邮件的标题独占首行，收件人与时间位于下一行；SMTP 状态和 HTML 格式不再出现在列表中，避免挤占标题，完整投递信息仍可在详情展开。点击“专注阅读”可收起列表；点击“全屏阅读”会优先进入浏览器原生全屏，同时隐藏插件导航和普通操作栏。若 AstrBot 的嵌入页面不允许原生全屏，页面会自动保留可通过“退出全屏”返回的沉浸阅读模式。手机端仍会先显示列表；打开邮件后进入详情阅读，并可返回列表继续切换。

### HTML 安全预览

当邮件内容归档已开启时，邮件详情可在“HTML 安全预览”“纯文本”和“清洗后源码”之间切换。预览使用已清洗的 HTML，并在 Dashboard 中再次按更严格规则处理：不加载远程图片、不保留链接跳转，也不会执行脚本、表单或其他主动内容。预览旁的阅读尺寸控制只调整页面中的显示面积，不会修改已归档内容或重新发送邮件。预览只用于核对本地投递副本，不保证与各邮箱客户端的最终渲染完全一致。

### 本地历史与隐私

`mail_history_enabled` 默认是 `true`。默认历史只保存在 AstrBot 插件数据目录中的本地 SQLite 数据库，包含时间、投递模式、邮件格式、SMTP 结果、收件人数量、收件域名和脱敏邮箱地址；不会保存主题、正文、完整邮箱地址、原始 QQ 身份或 SMTP 授权码。邮箱绑定验证码邮件也不会进入此历史。

若确实需要在邮件中心查看主题、纯文本正文和 HTML 预览，请主动开启 `mail_history_store_content=true`。此时，只有 SMTP 至少接受一位收件人后，插件才会在本机保存主题、正文和**已清洗后的** HTML，用于 Dashboard 查看；不会保存清洗前的原始 HTML。请仅在你信任该 AstrBot 主机及其 Dashboard 管理员的场景下启用，并按需要缩短保留期限或在邮件中心中清空本地历史。

本地历史默认保留 `30` 天，最多 `500` 条，由 `mail_history_retention_days` 与 `mail_history_max_records` 控制。清空历史只删除插件本地副本，不会删除已经交给邮件服务商的邮件，也不会影响收件人真实邮箱。

### WebUI 与 SMTP 凭据

邮件中心不需要 NapCat 专用 URL、Token 或额外 WebUI 配置；它复用 AstrBot 已建立的 Dashboard 和 OneBot/NapCat 连接。NapCat 相关的邮箱资料解析仍由现有 `napcat_*` 配置项控制。

为避免凭据经页面接口暴露，`smtp_host`、`smtp_port`、`smtp_security`、`smtp_username`、`smtp_password`、`sender_address` 和 `sender_name` 必须继续在 AstrBot 原生插件配置面板中填写。尤其是 `smtp_password` 应填写服务商提供的 SMTP 客户端授权码，不能通过邮件中心保存、查看或修改。

## 斜杠命令

| 命令 | 权限 | 说明 |
| --- | --- | --- |
| `/mailrelay_whoami` | 所有人 | 显示用于配置的、带平台范围的身份。 |
| `/mailrelay_identity` | 所有人 | 显示调用者是否有可用自助邮箱，不显示完整地址。 |
| `/mailrelay_bind email@example.com` | 所有人，默认仅私聊 | 发送邮箱绑定验证码。 |
| `/mailrelay_verify 123456` | 所有人，默认仅私聊 | 保存已验证的自助邮箱。 |
| `/mailrelay_unbind` | 所有人，默认仅私聊 | 删除调用者保存的邮箱绑定。 |
| `/mailrelay_self 主题 \| 正文` | 有已解析邮箱的用户，默认仅私聊 | 只发送给调用者自己的邮箱。 |
| `/mailrelay_owner 主题 \| 正文` | 已配置的主人身份加 AstrBot 管理员 | 只发送给 `owner_email`。 |
| `/mailrelay_send 收件人 \| 主题 \| 正文` | 已配置的管理员身份加 AstrBot 管理员 | 向明确指定的收件人发送。 |
| `/mailrelay_status` | 已配置的管理员身份加 AstrBot 管理员 | 显示脱敏后的配置和限制状态。 |
| `/mailrelay_smtp_test` | 已配置的管理员身份加 AstrBot 管理员 | 测试 TLS 与 SMTP 登录，不投递邮件。 |
| `/mailrelay_send_test` | 同时具备已配置主人身份、已配置管理员身份和 AstrBot 管理员权限 | 向 `owner_email` 发送固定测试邮件。 |

示例：

```text
/mailrelay_self 你的摘要 | Alice 已整理好你请求的内容。
/mailrelay_owner 服务提醒 | 定时备份已经完成。
/mailrelay_send colleague@example.com | 审阅请求 | 请查看聊天中提到的内容。
```

默认只启用纯文本邮件。附件功能默认开启但受大小、数量、来源和类型限制；开启 HTML 功能后，插件增加受清洗的 HTML 模板邮件，可使用 CID 内嵌当前消息图片，不提供抄送或密送。这让 Alice 可以自由设计邮件视觉，同时不会把公共聊天能力变成不受控制的网页或群发渠道。

## 重要配置项

`_conf_schema.json` 中每个运行时配置项都有默认值。以下是最需要关注的分组：

| 分组 | 配置项 | 说明 |
| --- | --- | --- |
| SMTP | `smtp_host`、`smtp_port`、`smtp_security`、`smtp_username`、`smtp_password`、`sender_address` | 网易 163 的 SSL 参数已预填。账号、授权码和发件地址仍必须替换为真实值。 |
| 主人与管理员 | `owner_email`、`owner_sender_ids`、`admin_sender_ids` | 从 `/mailrelay_whoami` 复制完整 `platform_id:sender_id`，避免不同平台的相同 ID 发生混淆。两个身份列表默认为空。 |
| 直接工具 | `enable_llm_mail_tools`、`enable_owner_delivery`、`enable_self_delivery`、`enable_admin_other_delivery`、`require_private_chat_for_self_delivery` | 每个工具都会在代码中独立校验模式。自助发送默认仅限私聊。 |
| QQ 与 NapCat | `napcat_email_lookup_enabled`、`napcat_friend_list_fallback_enabled`、`qq_platform_names` | 不需要 URL。好友列表回退默认关闭。 |
| 自助邮箱 | `self_email_overrides`、`self_binding_enabled`、`verification_code_ttl_seconds`、`verification_resend_seconds`、`verification_max_attempts` | 映射格式为 `platform_id:sender_id=email@example.com`。绑定验证码有次数限制。 |
| QQ 号推导 | `allow_qq_mailbox_derivation`、`qq_mail_domain` | 默认关闭，因为 QQ 号推导不是已验证的资料邮箱。 |
| 管理员收件人策略 | `restrict_admin_other_recipients`、`admin_other_recipient_allowlist`、`admin_other_allowed_domains` | 对管理员第三方投递增加可选白名单限制。 |
| HTML 模板 | `enable_html_mail`、`sanitize_html_before_send`、`html_allow_links`、`html_allow_remote_images`、`html_remote_image_allowed_domains`、`enable_inline_images`、`max_html_body_chars` | HTML 默认关闭且严格清洗。远程图片必须同时开启开关并配置精确域名白名单；QQ/工作区图片可用 CID 内嵌，不需要远程图片开关。 |
| 附件 | `enable_attachments`、`allow_message_images`、`allow_message_files`、`allow_workspace_attachments`、`max_attachments_per_message`、`max_attachment_size_mb`、`max_total_attachment_size_mb`、`attachment_fetch_timeout_seconds`、`blocked_attachment_extensions` | 默认允许受限附件。当前消息/引用回复媒体和工作区文件均在服务端读取、去重、限大小和拦截危险类型。 |
| 邮件中心历史 | `mail_history_enabled`、`mail_history_store_content`、`mail_history_retention_days`、`mail_history_max_records` | 默认记录脱敏投递元数据，不保存主题和正文。开启内容归档后，已接受邮件的主题、纯文本和清洗后 HTML 会保存在本机，供 Dashboard 查看。 |
| 限制 | `max_messages_per_hour`、`max_successful_messages_per_actor_per_hour`、`max_delivery_attempts_per_actor_per_hour`、`actor_min_send_interval_seconds` | SMTP 失败也会计入单用户尝试上限。 |
| 隐私 | `audit_log_enabled`、`audit_max_file_kb` | 审计不会记录邮件正文、授权码或完整邮箱地址。 |

## 隐私与安全

- 在 SMTP 投递前会再次执行服务端收件人模式校验。
- 普通用户没有能填写第三方收件人地址的工具或命令路径。
- HTML 工具沿用同一套主人、自助和管理员代发权限，清洗发生在最终 SMTP 投递边界，不仅发生在模型工具层。
- HTML 只允许受支持的内联 CSS；脚本、表单、事件属性、危险协议、相对 URL、外链 CSS 和不在白名单内的远程图片会被移除。关闭严格清洗也不会关闭这条底线。
- QQ/NapCat 资料查询只针对当前调用者。好友列表只在内存中用于匹配，不会整体缓存、记录或暴露给 Alice。
- 已验证的自助邮箱保存在 AstrBot 的插件数据目录中。待验证的验证码仅以哈希形式保存在内存，重载插件后失效。
- 审计日志只记录调用者指纹、投递模式、邮件格式、结果、收件人数、收件域名和附件数量/大小，不记录主题、纯文本正文、HTML 模板、附件二进制、文件名、SMTP 密钥或完整邮箱。
- 附件历史只保存数量、CID 图片数量和字节数；只有明确开启 `mail_history_store_content` 且 SMTP 接受至少一位收件人时，邮件中心详情才会额外保存清洗后的附件文件名。附件原始内容不会写入 SQLite。
- 邮件中心历史与审计日志相互独立。历史默认只保存脱敏投递元数据；只有明确开启 `mail_history_store_content` 后才会保存已接受邮件的主题、正文和清洗后 HTML。Dashboard 预览会再次禁用链接和远程图片。
- 默认通过 TLS 投递。除非明确开启 `allow_plain_smtp`，否则明文 SMTP 会被阻止。
- 全局成功投递上限、单用户成功上限、包含失败的单用户尝试上限和单用户冷却时间，可降低误发和滥用风险。

## 故障排查

### SMTP 登录失败

对于网易邮箱，请确认已经开启 SMTP，且 `smtp_password` 填写的是客户端授权码。`smtp_username` 与 `sender_address` 通常应是同一个真实邮箱地址。

### 用户无法发信给自己

让用户执行 `/mailrelay_identity`。若 NapCat 没有提供可用资料邮箱，请让用户私聊执行 `/mailrelay_bind` 和 `/mailrelay_verify`。只有在部署明确接受这种回退时，才开启 QQ 号邮箱推导。

### 管理员无法向他人发信

账号必须同时通过两项检查：AstrBot 将其识别为管理员，且它的完整 `platform_id:sender_id` 位于 `admin_sender_ids`。若开启了 `restrict_admin_other_recipients`，目标地址还必须命中配置的收件人白名单。

### 主人通知工具被拒绝

调用者必须是 AstrBot 管理员，且必须位于 `owner_sender_ids`。仅放入 `admin_sender_ids` 不会获得主人通知权限。请从 `/mailrelay_whoami` 复制完整值，修改配置后重载插件。

### HTML 样式没有显示或内容被删除

先确认 `enable_html_mail` 已开启并已重载插件。默认严格清洗会移除脚本、`<style>` 标签、外链资源和不支持的 CSS，请让 Alice 使用内联 CSS。不同邮箱客户端也会自行过滤 CSS，因此应优先使用颜色、边框、圆角、阴影、字号、间距和表格布局。远程图片必须同时开启 `html_allow_remote_images` 并配置精确域名白名单。

### 图片没有显示在 HTML 邮件中

请确认模板使用的是 `{{image_1}}` 这类占位符，并且 `enable_inline_images=true`。编号按本次邮件中的图片顺序计算，不按文件顺序计算；如果当前消息没有图片，插件会拒绝带有占位符的邮件。CID 图片不需要开启远程图片，也不需要给图片配置域名白名单。部分极老旧的邮件客户端可能不支持 CID，此时仍可从普通附件区域打开未引用的图片。

### QQ 文件或图片读取失败

确认消息确实包含 AstrBot 标准 `Image`/`File` 组件，或回复了包含媒体的消息。NapCat 只负责把媒体交给 AstrBot，插件不会自行调用一个固定的 NapCat HTTP 地址。检查 `allow_message_images`、`allow_message_files`、附件大小上限和读取超时；超过任一限制时会拒绝整封邮件。若是爱丽丝刚生成的文件，请把工具返回的工作区路径传给 `attachment_paths`；运行时关闭时，只有当前事件已登记的临时文件路径可用。

### NapCat 需要额外配置

不需要。MailRelay Guard 不会新建 NapCat HTTP 连接，也不需要 NapCat 基础 URL。当 AstrBot 已接收 `aiocqhttp` 事件时，插件会检测该事件所使用的适配器连接。QQ 邮箱资料仍可能不存在，因此提供了验证绑定作为回退方式。

### 邮件中心没有显示正文或 HTML 预览

默认情况下，`mail_history_store_content` 为 `false`，因此历史只显示脱敏投递信息，不保存主题和正文。请在邮件中心“安全配置”或 AstrBot 插件配置中明确开启该项；只建议在信任本机和 Dashboard 管理员的环境中开启。该设置只影响之后至少被 SMTP 接受的邮件，已经仅保存元数据的旧记录不会补回正文。

### 邮件中心是否是真实收件箱

不是。当前版本不连接 IMAP 或 POP3，无法读取真实邮箱中的来信。页面中的“本地投递副本”只是 Alice 已提交给 SMTP 服务端、且该服务端已接受的本地镜像；它不代表最终投递成功，更不代表收件人已阅读。

### AstrBot 面板显示问号

本仓库的 `_conf_schema.json` 采用严格 UTF-8，无 BOM，符合 AstrBot 的 `encoding="utf-8"` 读取方式。不要把它转成 GBK，也不要为 JSON 添加 BOM。若面板仍显示问号，通常是运行中的旧插件副本或旧配置缓存。请安装本版本的插件包、重载插件，并确认 AstrBot 实际加载的是 `astrbot_plugin_mailrelay_guard` 目录中的最新文件。

## 开发检查

AstrBot 提供插件 Pages 与 API，SMTP、TLS、邮件 MIME 构造和本地邮件历史 SQLite 存储使用 Python 标准库。HTML 清洗依赖 `nh3` 与 `tinycss2`，AstrBot 安装插件时会按 `requirements.txt` 安装它们。

```text
python -m ruff check astrbot_plugin_mailrelay_guard
python -m unittest discover -s astrbot_plugin_mailrelay_guard/tests -t . -v
```

## 上游致谢

MailRelay Guard 在独立开发过程中参考了 [Chris95743/astrbot_plugin_email_tool](https://github.com/Chris95743/astrbot_plugin_email_tool) 探索的 AstrBot SMTP 使用场景。感谢上游作者公开该项目。

本项目不包含上游源码、模板、图片或文档文本。上游项目采用 AGPL-3.0，本项目采用 MIT；本致谢不改变任一项目的许可证条款。

## 许可证

[MIT License](LICENSE)。版本更新请见 [CHANGELOG.md](CHANGELOG.md)。
