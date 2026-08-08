# MailRelay Guard（邮件中继防护）

MailRelay Guard 是为 Alice 一类 AstrBot 助手准备的 SMTP 邮件插件。它允许助手直接投递邮件，同时把收件人权限固定在插件服务端，而不是交给提示词或模型自行判断。

插件标识：`astrbot_plugin_mailrelay_guard`

需要 AstrBot `4.25` 或更高版本，且低于 `5`。提高最低版本是为了避免较旧版本在重载或卸载插件后残留 LLM 工具。

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

## 从 v1.0 升级

v1.1 使用三种直接投递工具取代了旧版的草稿确认流程。旧的草稿命令、草稿令牌和对应配置项已移除，不再需要确认令牌。

升级后，请在启用真实投递前填写 `owner_email`，并分别配置 `owner_sender_ids` 与 `admin_sender_ids`。已有的 SMTP 凭据可以继续使用。旧版的收件人白名单设置改为管理员专用的第三方投递策略。

`v1.1.1` 还修正了测试邮件对示例主人邮箱的处理：没有填写真实 `owner_email` 时，`/mailrelay_send_test` 会拒绝发送，不会把邮件投递给示例地址。

`v1.1.2` 进一步按拒绝优先的原则处理损坏配置：显式清空或写为 `null` 的 `smtp_host` 不会静默恢复为 `smtp.163.com`；无法识别或为 `null` 的敏感布尔开关也不会意外开启投递能力。

`v1.2.0` 增加可配置清洗的 HTML 模板邮件。HTML 功能默认关闭，升级后不会改变现有纯文本工具的行为。

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

默认只启用纯文本邮件。开启 HTML 功能后，插件只增加受清洗的 HTML 模板邮件，不提供附件、抄送或密送。这让 Alice 可以自由设计邮件视觉，同时不会把公共聊天能力变成不受控制的网页或群发渠道。

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
| HTML 模板 | `enable_html_mail`、`sanitize_html_before_send`、`html_allow_links`、`html_allow_remote_images`、`html_remote_image_allowed_domains`、`max_html_body_chars` | HTML 默认关闭且严格清洗。远程图片必须同时开启开关并配置精确域名白名单。 |
| 限制 | `max_messages_per_hour`、`max_successful_messages_per_actor_per_hour`、`max_delivery_attempts_per_actor_per_hour`、`actor_min_send_interval_seconds` | SMTP 失败也会计入单用户尝试上限。 |
| 隐私 | `audit_log_enabled`、`audit_max_file_kb` | 审计不会记录邮件正文、授权码或完整邮箱地址。 |

## 隐私与安全

- 在 SMTP 投递前会再次执行服务端收件人模式校验。
- 普通用户没有能填写第三方收件人地址的工具或命令路径。
- HTML 工具沿用同一套主人、自助和管理员代发权限，清洗发生在最终 SMTP 投递边界，不仅发生在模型工具层。
- HTML 只允许受支持的内联 CSS；脚本、表单、事件属性、危险协议、相对 URL、外链 CSS 和不在白名单内的远程图片会被移除。关闭严格清洗也不会关闭这条底线。
- QQ/NapCat 资料查询只针对当前调用者。好友列表只在内存中用于匹配，不会整体缓存、记录或暴露给 Alice。
- 已验证的自助邮箱保存在 AstrBot 的插件数据目录中。待验证的验证码仅以哈希形式保存在内存，重载插件后失效。
- 审计日志只记录调用者指纹、投递模式、邮件格式、结果、收件人数及收件域名，不记录主题、纯文本正文、HTML 模板、SMTP 密钥或完整邮箱。
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

### NapCat 需要额外配置

不需要。MailRelay Guard 不会新建 NapCat HTTP 连接，也不需要 NapCat 基础 URL。当 AstrBot 已接收 `aiocqhttp` 事件时，插件会检测该事件所使用的适配器连接。QQ 邮箱资料仍可能不存在，因此提供了验证绑定作为回退方式。

### AstrBot 面板显示问号

本仓库的 `_conf_schema.json` 采用严格 UTF-8，无 BOM，符合 AstrBot 的 `encoding="utf-8"` 读取方式。不要把它转成 GBK，也不要为 JSON 添加 BOM。若面板仍显示问号，通常是运行中的旧插件副本或旧配置缓存。请安装本版本的插件包、重载插件，并确认 AstrBot 实际加载的是 `astrbot_plugin_mailrelay_guard` 目录中的最新文件。

## 开发检查

AstrBot 提供插件 API，SMTP、TLS 和邮件 MIME 构造使用 Python 标准库。HTML 清洗依赖 `nh3` 与 `tinycss2`，AstrBot 安装插件时会按 `requirements.txt` 安装它们。

```text
python -m ruff check astrbot_plugin_mailrelay_guard
python -m unittest discover -s astrbot_plugin_mailrelay_guard/tests -t . -v
```

## 上游致谢

MailRelay Guard 在独立开发过程中参考了 [Chris95743/astrbot_plugin_email_tool](https://github.com/Chris95743/astrbot_plugin_email_tool) 探索的 AstrBot SMTP 使用场景。感谢上游作者公开该项目。

本项目不包含上游源码、模板、图片或文档文本。上游项目采用 AGPL-3.0，本项目采用 MIT；本致谢不改变任一项目的许可证条款。

## 许可证

[MIT License](LICENSE)。版本更新请见 [CHANGELOG.md](CHANGELOG.md)。
