import json
import os
import secrets
import time
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, session, render_template_string

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET", secrets.token_hex(32))

APP_ID = os.getenv("FEISHU_APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
REDIRECT_URI = os.getenv("FEISHU_REDIRECT_URI", "http://127.0.0.1:8000/oauth/callback")
AUTH_URL = "https://open.feishu.cn/open-apis/authen/v1/authorize"
TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v1/access_token"
USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"

# 模型侧配置（默认走 DeepSeek 的 OpenAI 兼容接口）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
MODEL_TIMEOUT = 60

SYSTEM_PROMPT = (
    "你是用户的私人飞书工作助理，帮用户整理飞书里的工作信息。"
    "用中文回答，简洁、分点、可执行。"
    "不确定的信息要明确说明不确定，不要编造消息、人名或时间。"
    "涉及发消息、改文档、建日程等写操作时，只输出草稿并请用户确认，不要声称已经执行。"
)


def call_model(messages, temperature=0.3):
    """调用模型接口，返回纯文本回复。"""
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY，请在 .env 中填入模型密钥后重启服务。")
    response = requests.post(
        f"{DEEPSEEK_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        },
        timeout=MODEL_TIMEOUT,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"模型接口返回 {response.status_code}：{response.text[:300]}")
    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"模型返回格式不符合预期：{str(data)[:300]}") from exc

TENANT_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
IM_CHATS_URL = "https://open.feishu.cn/open-apis/im/v1/chats"
IM_MESSAGES_URL = "https://open.feishu.cn/open-apis/im/v1/messages"

_token_cache = {"token": "", "expires_at": 0.0}


def scope_link(scopes):
    """飞书在权限不足时给出的申请地址格式。"""
    return (
        f"https://open.feishu.cn/app/{APP_ID}/auth?q={','.join(scopes)}"
        "&op_from=openapi&token_type=tenant"
    )


def tenant_token():
    """用应用凭证换应用身份 token（只读群消息用这个身份，不需要你登录）。"""
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    if not APP_ID or not APP_SECRET:
        raise RuntimeError("请先在 .env 中配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET。")
    response = requests.post(
        TENANT_TOKEN_URL,
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        timeout=20,
    )
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取应用凭证失败：{data.get('code')} {data.get('msg')}")
    _token_cache["token"] = data["tenant_access_token"]
    _token_cache["expires_at"] = time.time() + int(data.get("expire", 7200))
    return _token_cache["token"]


def feishu_get(url, params=None, scopes=()):
    """只读调用飞书接口，出错时把该开哪个权限一并说清楚。"""
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {tenant_token()}"},
        params=params,
        timeout=30,
    )
    data = response.json()
    if data.get("code") != 0:
        message = f"{data.get('code')} {data.get('msg') or ''}".strip()
        if data.get("code") == 99991672 and scopes:
            message += f"。需要在开放平台开通权限并发布版本：{scope_link(scopes)}"
        if data.get("code") == 11205:
            message += "。这个应用还没有开启“机器人”能力，请到开放平台的应用能力里添加机器人。"
        raise RuntimeError(message)
    return data.get("data") or {}


def format_time(value):
    """飞书的 create_time 是毫秒时间戳，转成本地可读时间；坏值原样返回。"""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(value) / 1000))
    except (TypeError, ValueError, OverflowError, OSError):
        return str(value)


def fetch_recent_messages(chat_id, hours):
    """按时间倒序窗口拉取一个会话里的文本消息。"""
    start = int(time.time()) - max(1, hours) * 3600
    data = feishu_get(
        IM_MESSAGES_URL,
        {
            "container_id_type": "chat",
            "container_id": chat_id,
            "start_time": str(start),
            "sort_type": "ByCreateTimeAsc",
            "page_size": 50,
        },
        scopes=("im:message:readonly",),
    )
    messages = []
    for item in data.get("items", []):
        body = item.get("body") or {}
        try:
            text = (json.loads(body.get("content") or "{}") or {}).get("text", "")
        except (ValueError, AttributeError):
            text = str(body.get("content"))[:200]
        messages.append(
            {
                "time": item.get("create_time"),
                "time_text": format_time(item.get("create_time")),
                "msg_type": item.get("msg_type"),
                "sender": (item.get("sender") or {}).get("id", ""),
                "text": text,
            }
        )
    return messages

PAGE = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>私人飞书 Agent</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#f7f8fa;color:#1d2129;max-width:900px;margin:0 auto;padding:26px 20px 60px;line-height:1.65}
h1{font-size:24px;margin:0 0 4px}
.sub{color:#86909c;font-size:13px;margin-bottom:18px}
.card{background:#fff;border:1px solid #e5e6eb;border-radius:12px;padding:18px 20px;margin-bottom:14px}
.row{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.row + .row{margin-top:10px}
button{background:#3370ff;color:#fff;border:0;border-radius:8px;padding:9px 16px;font-size:14px;cursor:pointer}
button.ghost{background:#fff;color:#3370ff;border:1px solid #3370ff}
button:disabled{opacity:.55;cursor:default}
select,input,textarea{border:1px solid #e5e6eb;border-radius:8px;padding:8px 10px;font-size:14px;background:#fff;color:#1d2129;font-family:inherit}
select{min-width:190px}
textarea{width:100%;height:70px;resize:vertical;margin:10px 0}
.badge{background:#eef4ff;color:#3370ff;border-radius:6px;padding:3px 9px;font-size:12px}
.status{font-size:13px;color:#86909c}
.sum{font-size:15px}
.sum h2{font-size:17px;margin:20px 0 8px;padding-bottom:6px;border-bottom:1px solid #e5e6eb}
.sum h3{font-size:15px;margin:14px 0 6px}
.sum h4{font-size:14px;margin:12px 0 4px}
.sum ul{margin:6px 0 6px 22px;padding:0}
.sum li{margin:4px 0}
.sum p{margin:8px 0}
.sum code{background:#f2f3f5;padding:1px 5px;border-radius:4px;font-size:13px}
.sum table{border-collapse:collapse;margin:10px 0;font-size:14px}
.sum th,.sum td{border:1px solid #e5e6eb;padding:6px 10px;text-align:left}
.sum th{background:#f7f8fa}
.sum blockquote{margin:8px 0;padding:6px 12px;border-left:3px solid #e5e6eb;background:#fafbfc;color:#4e5969}
.err{color:#d4380d;font-size:14px;white-space:pre-wrap;word-break:break-all}
.err a{color:#d4380d}
details{margin-top:16px}
summary{cursor:pointer;color:#86909c;font-size:13px}
.msg{border-left:2px solid #e5e6eb;padding:2px 0 2px 10px;margin:8px 0;font-size:13px;color:#4e5969;white-space:pre-wrap}
.msg b{color:#1d2129}
.spin{display:inline-block;width:13px;height:13px;border:2px solid #c9cdd4;border-top-color:#3370ff;border-radius:50%;animation:spin .8s linear infinite;vertical-align:-2px;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
</style></head>
<body>
<h1>私人飞书 Agent</h1>
<div class="sub">只读模式：读取机器人所在会话的消息，整理成摘要与待办；不会发送或修改任何内容。</div>

<div class="card">
  <div class="row">
    <span class="status">飞书授权</span>
    <b id="auth">检查中…</b>
    <span id="who" class="status"></span>
    <button class="ghost" onclick="connect()">连接 / 重新授权</button>
  </div>
</div>

<div class="card">
  <div class="row">
    <span class="badge">只读整理</span>
    <select id="chats"></select>
    <span class="status">最近</span>
    <input id="hours" value="24" size="3">
    <span class="status">小时</span>
    <button id="go" onclick="runSummary()">整理</button>
    <button class="ghost" onclick="loadChats()">刷新会话</button>
  </div>
  <div class="row"><span class="status" id="hint">正在加载会话…</span></div>
  <div id="out"></div>
</div>

<div class="card">
  <details>
    <summary>直接问 Agent（可选）</summary>
    <textarea id="q" placeholder="例如：把这段消息整理成待办"></textarea>
    <button onclick="ask()">发送</button>
    <div id="ans" class="sum"></div>
  </details>
</div>

<script>
function $(id){ return document.getElementById(id); }
function esc(s){ return String(s).replace(/[&<>"]/g, function(c){ return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]; }); }
function linkify(s){ return esc(s).replace(/(https?:\/\/[^\s)）]+)/g, function(u){ return '<a href="' + u + '" target="_blank">' + u + '</a>'; }); }
function inlineMd(s){ return esc(s).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>"); }
function cells(row){
  return row.replace(/^\|/, "").replace(/\|$/, "").split("|").map(function(c){ return c.trim(); });
}
function md(text){
  var out = [], list = false;
  var lines = String(text).split("\n");
  for (var i = 0; i < lines.length; i++){
    var line = lines[i].replace(/\s+$/, "");
    if (/^\s*\|/.test(line)){
      if (list){ out.push("</ul>"); list = false; }
      var rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])){ rows.push(lines[i].trim()); i++; }
      i--;
      var head = cells(rows[0]);
      var body = rows.slice(2);
      var html = "<table><thead><tr>";
      head.forEach(function(c){ html += "<th>" + inlineMd(c) + "</th>"; });
      html += "</tr></thead><tbody>";
      body.forEach(function(r){
        html += "<tr>";
        cells(r).forEach(function(c){ html += "<td>" + inlineMd(c) + "</td>"; });
        html += "</tr>";
      });
      out.push(html + "</tbody></table>");
      continue;
    }
    if (/^\s*>\s?/.test(line)){
      if (list){ out.push("</ul>"); list = false; }
      var quoted = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])){
        quoted.push(lines[i].replace(/^\s*>\s?/, "")); i++;
      }
      i--;
      out.push("<blockquote>" + quoted.map(inlineMd).join("<br>") + "</blockquote>");
      continue;
    }
    var li = line.match(/^\s*[-*]\s+(.*)$/);
    if (li){ if(!list){ out.push("<ul>"); list = true; } out.push("<li>" + inlineMd(li[1]) + "</li>"); continue; }
    if (list){ out.push("</ul>"); list = false; }
    var h = line.match(/^\s*(#{2,4})\s+(.*)$/);
    if (h){ var n = h[1].length; out.push("<h" + n + ">" + inlineMd(h[2]) + "</h" + n + ">"); continue; }
    if (!line.trim()){ continue; }
    out.push("<p>" + inlineMd(line) + "</p>");
  }
  if (list){ out.push("</ul>"); }
  return out.join("");
}
function render(text){ try { return '<div class="sum">' + md(text) + '</div>'; } catch (e) { return '<div class="sum">' + esc(text) + '</div>'; } }
function hint(t){ $("hint").textContent = t; }
function fail(msg){ $("out").innerHTML = '<div class="err">' + linkify(msg) + '</div>'; }
function messagesBlock(msgs){
  var texts = msgs.filter(function(m){ return m.text; });
  if (!texts.length){ return ""; }
  var rows = texts.map(function(m){
    return '<div class="msg"><b>' + esc(m.time_text || m.time) + '</b> ' + esc(m.sender || "未知") + '<br>' + esc(m.text) + '</div>';
  }).join("");
  return '<details><summary>查看读到的原始消息（' + texts.length + " 条）</summary>" + rows + "</details>";
}
function connect(){ location.href = "/oauth/start"; }
function refreshAuth(){
  fetch("/api/agent/status").then(function(r){ return r.json(); }).then(function(d){
    $("auth").textContent = d.feishu_authorized ? "已连接" : "未连接";
    $("who").textContent = d.feishu_user ? "（" + d.feishu_user + "）" : "";
    if (!d.model_configured){ fail("模型密钥未配置，请检查 .env 里的 DEEPSEEK_API_KEY。"); }
  }).catch(function(){ $("auth").textContent = "状态未知"; });
}
function loadChats(){
  hint("正在加载会话…");
  $("out").innerHTML = "";
  return fetch("/api/feishu/chats").then(function(r){ return r.json(); }).then(function(d){
    if (d.error){ fail(d.error); hint("加载失败"); return; }
    var sel = $("chats");
    sel.innerHTML = "";
    if (!d.chats || !d.chats.length){
      hint("机器人还不在任何会话里：请在飞书里把「飞书工作助理」拉进一个群。");
      return;
    }
    d.chats.forEach(function(c){
      var o = document.createElement("option");
      o.value = c.chat_id;
      o.textContent = c.name;
      sel.appendChild(o);
    });
    hint("找到 " + d.count + " 个会话，选一个点「整理」。");
  }).catch(function(e){ fail("请求失败：" + e); });
}
function runSummary(){
  var sel = $("chats");
  if (!sel.value){ fail("还没有可选会话，先点「刷新会话」，或把机器人拉进群。"); return; }
  var hours = Number($("hours").value) || 24;
  var name = sel.options[sel.selectedIndex].textContent;
  var btn = $("go");
  btn.disabled = true;
  btn.textContent = "整理中…";
  hint("正在读取「" + name + "」的消息并交给模型整理…");
  $("out").innerHTML = '<div class="status"><span class="spin"></span>整理中，通常几秒到十几秒…</div>';
  var raw = [];
  fetch("/api/feishu/messages?chat_id=" + encodeURIComponent(sel.value) + "&hours=" + hours)
    .then(function(r){ return r.json(); })
    .then(function(m){
      if (m.error){ fail(m.error); hint("读取消息失败"); throw new Error("stop"); }
      raw = m.messages || [];
      return fetch("/api/agent/summarize", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({chat_id: sel.value, hours: hours})
      }).then(function(r){ return r.json(); });
    })
    .then(function(d){
      if (!d){ return; }
      if (d.error){ fail(d.error); hint("整理失败"); return; }
      hint("群「" + name + "」最近 " + hours + " 小时，读到 " + (d.message_count || 0) + " 条文本消息");
      $("out").innerHTML = render(d.reply || "") + messagesBlock(raw);
    })
    .catch(function(e){ if (String(e.message) !== "stop"){ fail("请求失败：" + e); } })
    .then(function(){ btn.disabled = false; btn.textContent = "整理"; });
}
function ask(){
  var message = $("q").value;
  if (!message.trim()){ return; }
  $("ans").innerHTML = '<div class="status"><span class="spin"></span>思考中…</div>';
  fetch("/api/agent/chat", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({message: message})
  }).then(function(r){ return r.json(); }).then(function(d){
    $("ans").innerHTML = render(d.reply || d.error || "");
  }).catch(function(e){ $("ans").innerHTML = '<div class="err">请求失败：' + esc(e) + "</div>"; });
}
window.addEventListener("load", function(){
  var q = new URLSearchParams(location.search);
  if (q.get("hours")){ $("hours").value = q.get("hours"); }
  refreshAuth();
  loadChats().then(function(){
    if (q.get("auto")){ setTimeout(runSummary, 400); }
  });
});
</script>
</body></html>'''

@app.get("/")
def index():
    return PAGE

@app.get("/oauth/start")
def oauth_start():
    if not APP_ID or not APP_SECRET:
        return "请先在 .env 中配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET。", 500
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    params = {"app_id": APP_ID, "redirect_uri": REDIRECT_URI, "state": state}
    return redirect(AUTH_URL + "?" + urlencode(params))

@app.get("/oauth/callback")
def oauth_callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        return "OAuth state 校验失败，请重新开始授权。", 400
    # 飞书不同版本的 OAuth 回调可能使用 code 或 auth_code。
    code = request.args.get("code") or request.args.get("auth_code")
    if not code:
        error = request.args.get("error") or request.args.get("error_code") or "unknown"
        detail = request.args.get("error_description") or request.args.get("error_msg") or "请在飞书授权页点击‘授权’，不要点击‘拒绝’。"
        return f"飞书未返回授权码：{error}<br>{detail}<br><a href='/'>返回 Agent</a>", 400
    response = requests.post(
        TOKEN_URL,
        json={
            "grant_type": "authorization_code",
            "code": code,
            "app_id": APP_ID,
            "app_secret": APP_SECRET,
        },
        timeout=20,
    )
    data = response.json()
    if response.status_code >= 400 or data.get("code") not in (0, None):
        return jsonify(data), 502
    payload = data.get("data", data)
    token = payload.get("access_token")
    if not token:
        return jsonify(data), 502
    session["access_token"] = token
    session["refresh_token"] = payload.get("refresh_token")
    user_resp = requests.get(USER_INFO_URL, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    if user_resp.ok:
        user_data = user_resp.json().get("data", {})
        session["user_name"] = user_data.get("name") or user_data.get("en_name") or "已授权用户"
    return redirect("/")

@app.post("/api/agent/chat")
def agent_chat():
    if not session.get("access_token"):
        return jsonify({"error": "请先完成飞书授权"}), 401
    body = request.get_json(silent=True) or {}
    prompt = (body.get("message") or "").strip()
    if not prompt:
        return jsonify({"error": "请输入要问 Agent 的内容。"}), 400
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if session.get("user_name"):
        messages.append({"role": "system", "content": f"当前授权用户：{session['user_name']}。"})
    messages.append({"role": "user", "content": prompt})
    try:
        reply = call_model(messages)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"reply": reply, "model": DEEPSEEK_MODEL})


@app.get("/api/agent/status")
def agent_status():
    """自查用：确认飞书授权、模型密钥和模型名是否就绪。"""
    return jsonify(
        {
            "feishu_authorized": bool(session.get("access_token")),
            "feishu_user": session.get("user_name"),
            "model_configured": bool(DEEPSEEK_API_KEY),
            "model": DEEPSEEK_MODEL,
            "model_base_url": DEEPSEEK_BASE_URL,
        }
    )


@app.get("/api/feishu/chats")
def feishu_chats():
    """列出机器人所在的会话（只读）。"""
    try:
        data = feishu_get(IM_CHATS_URL, {"page_size": 50}, scopes=("im:chat:readonly",))
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    chats = [
        {"chat_id": item.get("chat_id"), "name": item.get("name") or "（未命名会话）"}
        for item in data.get("items", [])
    ]
    return jsonify({"count": len(chats), "chats": chats})


@app.get("/api/feishu/messages")
def feishu_messages():
    """预览某个会话最近的消息原文（只读）。"""
    chat_id = (request.args.get("chat_id") or "").strip()
    hours = int(request.args.get("hours") or 24)
    if not chat_id:
        return jsonify({"error": "缺少 chat_id，请先调用 /api/feishu/chats。"}), 400
    try:
        messages = fetch_recent_messages(chat_id, hours)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"count": len(messages), "messages": messages})


@app.post("/api/agent/summarize")
def agent_summarize():
    """把一个会话最近的消息交给模型整理成摘要和待办（只读，不发送任何消息）。"""
    body = request.get_json(silent=True) or {}
    chat_id = (body.get("chat_id") or "").strip()
    hours = int(body.get("hours") or 24)
    if not chat_id:
        return jsonify({"error": "请先列出并选择一个会话。"}), 400
    try:
        messages = fetch_recent_messages(chat_id, hours)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    lines = [
        f"[{m['time_text']}] 发送者 {m['sender'] or '未知'}：{m['text']}"
        for m in messages
        if m.get("text")
    ]
    if not lines:
        return jsonify(
            {
                "reply": f"这个会话在最近 {hours} 小时里没有可整理的文本消息。",
                "message_count": 0,
            }
        )
    prompt = (
        f"以下是飞书会话「最近 {hours} 小时」的消息，格式是 [本地时间] 发送者 id：内容。"
        "请输出三部分：1）要点摘要；2）待办清单（谁、做什么、截止时间，缺失的写“待补充”）；"
        "3）需要我本人回复或跟进的消息。只做整理，不要编造没有出现过的内容。\n\n"
        + "\n".join(lines)
    )
    try:
        reply = call_model(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
        )
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"reply": reply, "message_count": len(lines), "model": DEEPSEEK_MODEL})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
