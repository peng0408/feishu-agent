# 飞书只读整理助手

[English](README.en.md) | 简体中文

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)
![Feishu](https://img.shields.io/badge/Feishu-Open%20Platform-3370FF)
![read-only](https://img.shields.io/badge/read--only-yes-success)
![License](https://img.shields.io/badge/license-MIT-green)

把机器人拉进飞书群，它会读取最近的群消息，交给大模型整理成**摘要、待办、需要你回复的条目**，然后在一个本地网页里直接看结果。

全程只读——不发消息、不改文档、不建日程。

## 界面预览

![界面预览](docs/screenshot.png)

## 它能做什么

- 自动列出机器人所在的群
- 选定群 + 时间范围，点一下就能整理
- 结果分三块：要点摘要 / 待办清单 / 需要你跟进的消息
- 可展开「原始消息」逐条核对，确认模型没读错
- 附带一个「直接问 Agent」的问答框
- 支持 `http://127.0.0.1:8000/?auto=1&hours=168` 打开即出结果

## 使用前准备

用的是你自己的飞书自建应用，需要先在[飞书开放平台](https://open.feishu.cn/)准备：

1. 应用能力 → 添加「机器人」
2. 权限管理 → 开通三个应用身份权限：
   - `im:chat:readonly`：读取群组信息，用于列出会话
   - `im:message:readonly`：读取消息
   - `im:message.group_msg`：读取群消息（敏感权限，可能需要填写用途说明或管理员审批）
3. 版本管理与发布 → 创建版本并发布（权限变更必须发版本才生效）
4. 在飞书里把机器人拉进要整理的群

缺哪个权限，接口会把飞书给出的开通链接直接返回给你，点开申请即可。

## 快速开始

```bash
git clone https://github.com/peng0408/feishu-agent.git
cd feishu-agent
python -m pip install -r requirements.txt
cp .env.example .env      # Windows 用 copy
python app.py
```

浏览器打开 <http://127.0.0.1:8000>。

### 配置项

| 变量 | 作用 |
| --- | --- |
| `FEISHU_APP_ID` | 飞书应用的 App ID |
| `FEISHU_APP_SECRET` | 飞书应用的 App Secret |
| `FEISHU_REDIRECT_URI` | OAuth 回调地址，默认 `http://127.0.0.1:8000/oauth/callback` |
| `SESSION_SECRET` | Flask 会话签名密钥，随便一串随机字符 |
| `DEEPSEEK_API_KEY` | 模型密钥 |
| `DEEPSEEK_BASE_URL` | 模型接口地址，默认 `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 模型名，默认 `deepseek-flash` |

回调地址要和飞书开放平台里配置的完全一致。

## 使用方法

1. 打开 <http://127.0.0.1:8000>
2. 页面自动列出机器人所在的群，选一个
3. 填「最近 N 小时」（默认 24，看周报可以填 168）
4. 点「整理」，几秒后出现摘要和待办
5. 想核对内容，展开下方的「查看读到的原始消息」

页面上的「连接 / 重新授权」是可选的用户身份 OAuth；整理消息本身用的是应用身份（`tenant_access_token`），不登录也能用。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/agent/status` | 飞书授权状态、模型名、密钥是否已配置 |
| GET | `/api/feishu/chats` | 列出机器人所在的群 |
| GET | `/api/feishu/messages?chat_id=...&hours=24` | 预览会话最近的消息原文 |
| POST | `/api/agent/summarize` | `{"chat_id": "...", "hours": 24}`，返回摘要与待办 |
| POST | `/api/agent/chat` | `{"message": "..."}`，直接问模型 |

## 工作原理

```
tenant_access_token → GET /im/v1/chats → GET /im/v1/messages
        → 拼成带时间和发送者的 transcript
        → POST /chat/completions（OpenAI 兼容接口）
        → 网页渲染摘要
```

## 权限与隐私

- 只读：代码里没有任何发送消息、修改文档、创建日程的调用
- 只能读到机器人所参与会话的消息，这是飞书平台的限制
- `.env` 已被 `.gitignore` 排除，密钥不会被提交
- OAuth 拿到的 token 只放在会话内存里，不落盘

## 已知限制

- 单次最多读取 50 条消息（`page_size=50`），消息多的群需要按时间窗分段
- 机器人只能读它进群之后的消息，更早的历史消息飞书不提供
- 发送者显示为飞书 `open_id`；要显示姓名需额外开通通讯录权限
- 内置 Flask 开发服务器，适合个人本机使用；长期对外请换 WSGI / 容器部署

## 目录结构

```
feishu-agent/
├── app.py              # Flask 应用：OAuth、飞书只读接口、模型调用、页面
├── docs/
│   └── screenshot.png  # 界面截图
├── requirements.txt
├── .env.example
├── README.md
├── README.en.md
└── LICENSE
```

## 许可证

[MIT](LICENSE)
