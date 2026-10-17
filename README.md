# 飞书只读整理助手

一个跑在自己电脑上的飞书消息整理工具：把机器人拉进群，它会读取最近的群消息，交给大模型整理成**摘要、待办、需要你回复的条目**，在一个本地网页里直接看结果。

全程只读——不会发消息、不会改文档、不会建日程。

## 它能做什么

- 自动列出机器人所在的群
- 选定一个群、设定「最近 N 小时」，点一下就能整理
- 结果分三块呈现：要点摘要 / 待办清单 / 需要你跟进的消息
- 可以展开「原始消息」，逐条核对模型有没有读错
- 附带一个「直接问 Agent」的问答框

## 使用前要准备什么

它用的是**你自己的**飞书自建应用，所以需要先在[飞书开放平台](https://open.feishu.cn/)准备一个应用：

1. 应用能力 → 添加「机器人」
2. 权限管理 → 开通三个应用身份权限：
   - `im:chat:readonly`（读取群组信息，用来列出会话）
   - `im:message:readonly`（读取消息）
   - `im:message.group_msg`（读取群消息，敏感权限，可能需要填写用途说明或管理员审批）
3. 版本管理与发布 → 创建版本并发布（权限变更必须发版本才生效）
4. 在飞书里把机器人拉进你要整理的群

缺哪个权限，接口会直接把飞书给出的开通链接返回给你，点开申请即可。

## 快速开始

```bash
git clone https://github.com/peng0408/feishu-agent.git
cd feishu-agent
python -m pip install -r requirements.txt
copy .env.example .env     # macOS/Linux 用 cp
python app.py
```

然后浏览器打开 <http://127.0.0.1:8000>。

`.env` 里需要填：

| 变量 | 作用 |
| --- | --- |
| `FEISHU_APP_ID` | 飞书应用的 App ID |
| `FEISHU_APP_SECRET` | 飞书应用的 App Secret |
| `FEISHU_REDIRECT_URI` | OAuth 回调地址，默认 `http://127.0.0.1:8000/oauth/callback` |
| `SESSION_SECRET` | Flask 会话签名密钥，随便一串随机字符 |
| `DEEPSEEK_API_KEY` | 模型密钥 |
| `DEEPSEEK_BASE_URL` | 模型接口地址，默认 `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 模型名，默认 `deepseek-flash` |

回调地址要和飞书开放平台里配置的**完全一致**。

## 怎么用

1. 启动后打开 <http://127.0.0.1:8000>
2. 页面会自动列出机器人所在的群，选一个
3. 填「最近 N 小时」（默认 24，看周报可以填 168）
4. 点「整理」，几秒后出现排版好的摘要和待办
5. 想核对内容，展开下方的「查看读到的原始消息」

页面上方的「连接 / 重新授权」是可选的用户身份 OAuth；整理消息本身用的是应用身份（`tenant_access_token`），不登录也能用。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/agent/status` | 飞书授权状态、模型名、密钥是否已配置 |
| GET | `/api/feishu/chats` | 列出机器人所在的群 |
| GET | `/api/feishu/messages?chat_id=...&hours=24` | 预览会话最近的消息原文 |
| POST | `/api/agent/summarize` | `{"chat_id": "...", "hours": 24}`，返回摘要与待办 |
| POST | `/api/agent/chat` | `{"message": "..."}`，直接问模型 |

## 它是怎么跑通的

```
tenant_access_token → GET /im/v1/chats → GET /im/v1/messages
        → 拼成带时间和发送者的 transcript
        → POST /chat/completions（OpenAI 兼容）
        → 网页渲染摘要
```

## 权限与隐私

- 只读：代码里没有任何发送消息、修改文档、创建日程的调用
- 只能读到**机器人所参与会话**里的消息，这是飞书平台的限制
- `.env` 已被 `.gitignore` 排除，密钥不会被提交；请在本地保管好
- OAuth 拿到的 token 只放在会话内存里，不落盘

## 已知限制

- 单次最多读取 50 条消息（`page_size=50`），消息很多的群需要按时间窗分段
- 机器人只能读它进群之后的消息，更早的历史消息飞书不提供
- 内置的是 Flask 开发服务器，适合个人本机使用；要长期对外提供服务请换成 WSGI/容器部署
- 发送者目前显示为飞书 `open_id`，要显示姓名还需要额外开通通讯录权限

## 目录结构

```
feishu-agent/
├── app.py           # Flask 应用：OAuth、飞书只读接口、模型调用、页面
├── requirements.txt
├── .env.example
└── README.md
```
