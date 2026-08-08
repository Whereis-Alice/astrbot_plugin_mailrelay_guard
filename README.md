# MailRelay Guard

`MailRelay Guard` 是一个面向 AstrBot 的受策略保护 SMTP 邮件投递插件。它适合让机器人发送少量、可审计的通知邮件，同时把邮件外发从“模型说发就发”改成“策略校验后，由指定管理员明确确认”。

插件标识符为 `astrbot_plugin_mailrelay_guard`，与 `astrbot_plugin_email_tool` 使用不同的插件名、命令名和 LLM 工具名，可同时安装。

## 适用场景

- 给固定地址或固定组织域发送状态通知、测试邮件和人工整理后的摘要。
- 让 LLM 协助起草邮件，但由管理员在原会话中确认后再投递。
- 使用网易 163 邮箱作为 SMTP 发件箱。默认已经预填 `smtp.163.com`、端口 `465` 和 `SSL`。

不适合批量群发、营销邮件、收件箱读取、附件分发或无人值守的任意地址外发。

## 功能

| 能力 | 行为 |
| --- | --- |
| TLS SMTP 投递 | 支持 SSL、STARTTLS 和显式允许的明文 SMTP；网络操作在后台线程执行，不阻塞 AstrBot 事件循环。 |
| 严格收件人策略 | 默认开启白名单，且空白名单表示拒绝全部外发，不会退化为“允许所有地址”。 |
| 双重控制 | 发送、连接测试、状态查看和草稿确认同时要求 AstrBot 管理员身份与 `command_allowed_sender_ids`。 |
| LLM 待确认草稿 | LLM 只能创建内存中的草稿，无法直接投递；草稿必须由原创建者在原会话中确认。 |
| 原子限流 | 全局成功投递上限默认每小时 10 封；只有 SMTP 至少接受一位收件人后才消耗额度。 |
| 最小化审计 | 记录结果、发送者指纹、收件人数和收件域名，不记录授权码、主题或正文。 |
| 安全输入处理 | 校验裸邮箱地址、去重、限制人数和正文长度，并拒绝邮件头换行注入。 |

## 安装

1. 将整个 `astrbot_plugin_mailrelay_guard` 目录压缩为 ZIP，或通过 AstrBot 的插件安装界面导入该目录。
2. 在 AstrBot 中启用插件并打开配置页。
3. 填写下方的“网易 163 邮箱最小配置”。
4. 先执行 `/mailrelay_whoami`，把返回的 `sender_id` 填入 `command_allowed_sender_ids`，然后重载插件。
5. 依次执行 `/mailrelay_status`、`/mailrelay_smtp_test` 和 `/mailrelay_send_test` 验证配置。

本插件不依赖额外 PyPI 包。AstrBot 提供插件 API 和 Pydantic；SMTP、TLS 和 MIME 邮件构造均使用 Python 标准库。

## 网易 163 邮箱最小配置

网易 163 默认参数已填好，账号和授权码必须由你自己填写，插件不会写入示例账号或伪造凭据。

| 配置项 | 默认值 | 需要你做的事 |
| --- | --- | --- |
| `smtp_host` | `smtp.163.com` | 通常无需修改。 |
| `smtp_port` | `465` | 通常无需修改。 |
| `smtp_security` | `ssl` | 通常无需修改。 |
| `smtp_username` | 空 | 填完整的 163 邮箱地址。 |
| `smtp_password` | 空 | 填网易邮箱开启 SMTP 后生成的客户端授权码，不是网页登录密码。 |
| `sender_address` | 空 | 通常与 `smtp_username` 相同。 |
| `sender_name` | `AstrBot MailRelay Guard` | 可改成机器人或服务名称。 |
| `recipient_allowlist` | `[]` | 至少添加一个允许接收邮件的完整地址。 |
| `test_recipient` | 空 | 建议填你的测试收件邮箱。 |
| `command_allowed_sender_ids` | `[]` | 填允许控制插件的 AstrBot 管理员 sender ID。 |

建议的起始配置如下。邮箱地址、授权码和 sender ID 都只是字段说明，必须替换成你自己的值：

```json
{
  "smtp_username": "your_name@163.com",
  "smtp_password": "网易客户端授权码",
  "sender_address": "your_name@163.com",
  "sender_name": "AstrBot 通知",
  "recipient_allowlist": ["your_name@163.com"],
  "test_recipient": "your_name@163.com",
  "command_allowed_sender_ids": ["你的平台用户 ID"]
}
```

在网易邮箱网页端开启 SMTP/IMAP 服务后，创建“客户端授权码”并填入 `smtp_password`。网易的页面文案可能随版本变化；不要把网页登录密码填写到这里，也不要把授权码贴到聊天记录、截图或公开仓库。

## 命令

除 `/mailrelay_whoami` 外，以下控制命令都要求：

1. 发起者是 AstrBot 管理员。
2. 发起者的 `sender_id` 在 `command_allowed_sender_ids` 中。

| 命令 | 说明 |
| --- | --- |
| `/mailrelay_whoami` | 显示调用者自己的 `sender_id` 与管理员状态，用于首次配置。 |
| `/mailrelay_status` | 显示脱敏后的 SMTP、收件人策略、限流和就绪状态。 |
| `/mailrelay_smtp_test` | 只测试 SMTP TLS 连接和登录，不发送邮件。 |
| `/mailrelay_send_test [邮箱]` | 向传入地址或 `test_recipient` 发送固定测试邮件。 |
| `/mailrelay_send 收件人 | 主题 | 正文` | 发送纯文本邮件；收件人可用英文逗号或分号分隔。 |
| `/mailrelay_confirm 令牌` | 确认当前会话中由 LLM 创建的待确认草稿。 |
| `/mailrelay_cancel 令牌` | 取消当前会话中由 LLM 创建的待确认草稿。 |

手动发送示例：

```text
/mailrelay_send receiver@example.com | 例行通知 | 服务已完成本次任务。
```

命令使用竖线分隔三段，因此主题和正文可以包含空格。V1 只发送纯文本；不提供 HTML、抄送、密送和附件，避免把本插件变成不受控制的数据外发通道。

## 收件人策略

默认 `require_recipient_allowlist=true`。此时一位收件人满足以下任一条件才允许投递：

- 完整邮箱地址在 `recipient_allowlist` 中；
- 收件人域名在 `allowed_recipient_domains` 中。

例如，要允许 `ops@example.com` 与整个 `example.org` 域：

```json
{
  "recipient_allowlist": ["ops@example.com"],
  "allowed_recipient_domains": ["example.org"]
}
```

`allowed_recipient_domains` 留空不会放开限制。若你关闭 `require_recipient_allowlist`，插件会允许任意合法邮箱地址，这会显著扩大提示注入、误操作和数据泄露风险，不建议在公共群聊或多人环境中使用。

## LLM 草稿与确认

LLM 功能默认关闭。若确实需要让模型帮你整理邮件，请完成以下配置并重载插件：

```json
{
  "enable_llm_draft_tool": true,
  "llm_tool_allowed_sender_ids": ["你的平台用户 ID"],
  "draft_ttl_seconds": 600
}
```

启用后，模型只能调用 `mailrelay_prepare_draft` 创建草稿。它会经过同一套收件人、长度和管理员策略校验，并返回一次性确认令牌。邮件内容仅保存在内存中，不能跨插件重载恢复；创建者必须在同一会话使用 `/mailrelay_confirm <令牌>` 才会真正投递。

这种设计刻意不提供“LLM 直接发信”开关。模型输出、网页内容和群聊内容都可能受到提示注入影响；明确的人类确认是邮件外发边界的一部分。

## 配置参考

| 分组 | 配置项 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 基础 | `enabled` | `true` | 插件总开关。 |
| SMTP | `smtp_host` | `smtp.163.com` | SMTP 主机。 |
| SMTP | `smtp_port` | `465` | SMTP 端口。 |
| SMTP | `smtp_security` | `ssl` | `ssl`、`starttls` 或 `plain`。 |
| SMTP | `allow_plain_smtp` | `false` | 仅明确开启后才允许 `plain`。 |
| SMTP | `smtp_username` | 空 | SMTP 登录账号。 |
| SMTP | `smtp_password` | 空 | SMTP 授权码。 |
| SMTP | `sender_address` | 空 | 邮件 From 地址。 |
| SMTP | `sender_name` | `AstrBot MailRelay Guard` | From 显示名称。 |
| SMTP | `smtp_timeout_seconds` | `20` | SMTP 超时秒数，范围 5-120。 |
| 收件人 | `require_recipient_allowlist` | `true` | 是否强制精确地址/域名白名单。 |
| 收件人 | `recipient_allowlist` | `[]` | 完整允许地址列表。 |
| 收件人 | `allowed_recipient_domains` | `[]` | 允许域名列表。 |
| 限制 | `max_recipients_per_message` | `3` | 单封邮件的收件人数上限。 |
| 限制 | `max_subject_chars` | `120` | 邮件主题字符上限。 |
| 限制 | `max_body_chars` | `5000` | 纯文本正文字符上限。 |
| 限制 | `max_messages_per_hour` | `10` | 每小时成功投递上限。 |
| 测试 | `test_recipient` | 空 | 默认测试信收件人。 |
| 权限 | `command_allowed_sender_ids` | `[]` | 被允许控制插件的管理员 sender ID。 |
| 审计 | `audit_log_enabled` | `true` | 是否写入最小化 JSONL 审计。 |
| 审计 | `audit_max_file_kb` | `512` | 当前审计文件大小上限，达到后轮换一份 previous 文件。 |
| LLM | `enable_llm_draft_tool` | `false` | 是否注册仅起草、不发送的 LLM 工具。 |
| LLM | `llm_tool_allowed_sender_ids` | `[]` | 允许 LLM 起草的管理员 sender ID。 |
| LLM | `draft_ttl_seconds` | `600` | 内存草稿有效期，范围 60-3600 秒。 |
| LLM | `max_pending_drafts_per_actor` | `3` | 每用户、每会话的待确认草稿上限。 |

所有配置项在 `_conf_schema.json` 中都有默认值。更新配置后请重载插件，特别是 `enable_llm_draft_tool` 与允许 ID 列表，因为 LLM 工具会在初始化阶段注册。

## 审计与隐私

审计文件位于 AstrBot 的插件数据目录，通常类似：

```text
data/plugin_data/astrbot_plugin_mailrelay_guard/mailrelay_guard_audit.jsonl
```

它只记录投递结果、操作类别、发送者短指纹、收件人数、收件人域名和简短技术状态。它不会记录 SMTP 授权码、完整收件人地址、主题或正文。请继续将 AstrBot 数据目录视为敏感数据，并按照自己的备份与访问控制规则管理它。

## 常见问题

**`SMTP 登录失败`**

确认 163 邮箱已开启 SMTP 服务，`smtp_username` 是完整邮箱地址，`smtp_password` 是客户端授权码而非网页登录密码。

**`该收件人不在允许范围内`**

将完整地址加入 `recipient_allowlist`，或谨慎将其域名加入 `allowed_recipient_domains`，然后重载插件。

**`当前发送者不在 command_allowed_sender_ids 中`**

先执行 `/mailrelay_whoami`，将该命令返回的 `sender_id` 添加到配置，同时确认它在 AstrBot 中具有管理员身份。

**启用了 LLM 草稿工具但模型看不到它**

检查 `enable_llm_draft_tool=true`、`llm_tool_allowed_sender_ids` 非空并包含当前管理员 ID，然后重载插件。该工具不会直接发送邮件，仍须执行确认命令。

## 设计与致谢

MailRelay Guard 的产品方向受 [Chris95743/astrbot_plugin_email_tool](https://github.com/Chris95743/astrbot_plugin_email_tool) 启发，感谢上游作者对 AstrBot SMTP 邮件能力的探索。

本项目从零独立实现，没有复制上游的源代码、模板、图片或 README 表达；上游项目采用 AGPL-3.0，本项目采用 MIT。这里的致谢不改变上游项目的许可证，也不替代上游仓库的许可声明。

相较于直接把 SMTP 发送暴露给 LLM，本项目将默认行为收紧为：空白名单拒绝、管理员二次鉴权、明确确认草稿、成功后计数的原子限流、SMTP 超时和不含邮件内容的审计。它不包含上游的 NapCat/内存监控功能，以保持邮件外发边界清晰。

## 发布说明

项目仓库：[Whereis-Alice/astrbot_plugin_mailrelay_guard](https://github.com/Whereis-Alice/astrbot_plugin_mailrelay_guard)。发布新版本时，请同步更新 `metadata.yaml`、`CHANGELOG.md` 与本 README 中的版本说明。

## 许可证

[MIT License](LICENSE)。变更记录见 [CHANGELOG.md](CHANGELOG.md)。
