# Feishu Private Agent MVP

一个本地运行的飞书 OAuth + Agent 最小版本。当前支持：

- 飞书 OAuth 授权
- 获取当前用户信息
- 授权后展示连接状态
- Agent 对话接口（已接入真实大模型，飞书只读 API 待接入）

## 只读整理飞书消息（需要的权限）

这一版只读：读取机器人所在会话的最近消息，交给模型整理成摘要和待办，不会发送或修改任何内容。

需要在飞书开放平台完成三步：

1. 应用能力 → 添加「机器人」。
2. 权限管理 → 开通两个应用身份权限：`im:chat:readonly`（群组信息只读）、`im:message:readonly`（消息只读）。
   缺权限时接口会直接返回开通链接，点开即可申请。
3. 版本管理与发布 → 创建版本并发布（企业自建应用的权限变更要发版本才生效），然后把机器人拉进要整理的群。

接口：

- `GET /api/feishu/chats` 列出机器人所在会话
- `GET /api/feishu/messages?chat_id=...&hours=24` 预览最近消息原文
- `POST /api/agent/summarize` `{chat_id, hours}` 输出摘要和待办

## 模型

对话由 OpenAI 兼容接口驱动，默认指向 DeepSeek：

```env
DEEPSEEK_API_KEY=你的模型密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
```

换模型或换供应商时只改这三个变量即可，代码不用动。

自查接口：`GET /api/agent/status` 会返回飞书授权状态、模型名和密钥是否已配置。

## 启动

1. 安装 Python 3.10+。
2. 复制 `.env.example` 为 `.env`，填入飞书应用的 App ID、App Secret 和回调地址。
3. 安装依赖：`python -m pip install -r requirements.txt`
4. 启动：`python app.py`
5. 浏览器打开 `http://127.0.0.1:8000`，点击“连接飞书”。

回调地址默认：`http://127.0.0.1:8000/oauth/callback`。在飞书开放平台应用中配置完全相同的重定向 URL。

## 安全

本项目不会保存飞书密码；授权由飞书页面完成。Token 只保存在本地运行内存中，写操作尚未开放。
