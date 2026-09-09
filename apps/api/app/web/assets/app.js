const view = document.getElementById("view");
const title = document.getElementById("page-title");
const sub = document.getElementById("page-sub");
const llmPill = document.getElementById("llm-pill");
let state = { overview: null, knowledge: null, integrations: null, chatStatus: null, conversationId: null };

const titles = {
  "/": ["Обзор", "Контур автоматизации продаж и внутренних процессов"],
  "/chat": ["Ассистент сотрудника", "RAG + ReAct: каталог, регламенты, лиды, черновик КП"],
  "/leads": ["Сделки", "Единый контур сайта, TeamChat, PhonePBX и CRM"],
  "/calls": ["Телефония", "Webhook PhonePBX → задача в CRM → уведомление в TeamChat"],
  "/knowledge": ["База знаний", "Продукты и регламенты для RAG-пайплайна"],
  "/workflows": ["Пайплайны", "Те же сценарии, что в n8n: сайт, звонок, бот, синхронизация"],
  "/integrations": ["Интеграции", "CRM, TeamChat, PhonePBX, S3, n8n, LLM"],
};

document.getElementById("refresh-btn").onclick = () => loadAll().then(render);
window.addEventListener("hashchange", render);
document.querySelectorAll("nav a").forEach((a) => {
  a.addEventListener("click", () => setTimeout(render, 0));
});

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function loadAll() {
  try {
    const [overview, knowledge, integrations, chatStatus] = await Promise.all([
      api("/api/overview"),
      api("/api/knowledge"),
      api("/api/integrations"),
      api("/api/chat/status"),
    ]);
    state = { ...state, overview, knowledge, integrations };
    state.chatStatus = chatStatus;
    llmPill.textContent = `LLM: ${chatStatus.provider} · ${chatStatus.mode}`;
  } catch (err) {
    view.innerHTML = `<div class="card">Не удалось загрузить API: ${esc(err.message)}</div>`;
    throw err;
  }
}

function route() {
  const raw = location.hash.replace("#", "") || "/";
  return raw.startsWith("/") ? raw : `/${raw}`;
}

function render() {
  const r = route();
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === r));
  const meta = titles[r] || titles["/"];
  title.textContent = meta[0];
  sub.textContent = meta[1];
  const o = state.overview;
  if (!o) {
    view.innerHTML = `<div class="card">Загрузка контура...</div>`;
    return;
  }
  if (r === "/chat") return renderChat();
  if (r === "/leads") return renderLeads();
  if (r === "/calls") return renderCalls();
  if (r === "/knowledge") return renderKnowledge();
  if (r === "/workflows") return renderWorkflows();
  if (r === "/integrations") return renderIntegrations();
  renderHome();
}

function renderHome() {
  const o = state.overview;
  view.innerHTML = `
    <div class="demo-row">
      <button data-demo="site-lead">Демо: заявка с сайта</button>
      <button class="ghost" data-demo="missed-call">Демо: пропущенный звонок</button>
      <button class="ghost" data-demo="teamchat-question">Демо: вопрос в TeamChat</button>
    </div>
    <div class="grid kpis">
      ${kpi("Сделки", o.leads_total, "CRM + локальный контур")}
      ${kpi("Пропущенные", o.missed_calls, "PhonePBX")}
      ${kpi("Чанки RAG", o.knowledge_chunks, "каталог и регламенты")}
      ${kpi("Запуски пайплайнов", o.workflow_runs, "n8n / API")}
    </div>
    <div class="grid" style="grid-template-columns: 1.2fr .8fr; margin-top:16px">
      <div class="card">
        <h2>Что умеет контур</h2>
        <p class="muted">Учебная платформа проводит путь от заявки до рабочего контура: webhook → квалификация LLM/правил → CRM → мессенджер → база знаний → инструкция для пользователя.</p>
        <ul>
          <li>n8n-воркфлоу лежат в <code>n8n/workflows</code> и дублируются нативным API.</li>
          <li>Агент умеет искать в RAG, создать лид и набросать КП.</li>
          <li>Docker / Nginx / метрики / mock-интеграции готовы к показу без ключей.</li>
        </ul>
      </div>
      <div class="card">
        <h2>Последние пайплайны</h2>
        ${o.workflows.slice(0, 6).map((w) => `<div class="lead"><b>${w.name}</b><span class="muted">${w.trigger} · ${w.status}</span></div>`).join("") || "<p class='muted'>Пока пусто</p>"}
      </div>
    </div>
  `;
  bindDemos();
}

function kpi(label, value, hint) {
  return `<div class="card"><div class="muted">${label}</div><div class="kpi-value">${value}</div><div class="muted">${hint}</div></div>`;
}

function renderLeads() {
  const stages = [
    ["new", "Новые"],
    ["qualified", "Квалификация"],
    ["proposal", "КП"],
    ["callback", "Перезвон"],
  ];
  const items = state.overview.pipeline;
  view.innerHTML = `<div class="grid kanban">${stages
    .map(([key, label]) => {
      const cards = items.filter((l) => l.stage === key);
      return `<div class="card"><h3>${label} · ${cards.length}</h3>${cards.map(leadCard).join("")}</div>`;
    })
    .join("")}</div>`;
}

function leadCard(l) {
  return `<article class="lead"><b>${esc(l.company)}</b><div class="muted">${esc(l.need)}</div><div class="row">${(l.tags || "")
    .split(",")
    .filter(Boolean)
    .map((t) => `<span class="tag ${t}">${t}</span>`)
    .join("")}<span class="tag">${esc(l.source)}</span></div></article>`;
}

function renderCalls() {
  view.innerHTML = `<div class="card"><table class="table"><thead><tr><th>Кто</th><th>Статус</th><th>Суть</th><th>Когда</th></tr></thead><tbody>
    ${state.overview.calls
      .map(
        (c) => `<tr><td>${esc(c.caller)}</td><td>${esc(c.status)}</td><td>${esc(c.summary)}</td><td>${esc(c.created_at || "")}</td></tr>`
      )
      .join("")}
  </tbody></table></div>`;
}

function renderKnowledge() {
  const docs = state.knowledge.documents || {};
  view.innerHTML = `<div class="card"><p class="muted">${state.knowledge.chunks} чанков проиндексировано гибридным поиском BM25 + hashed embeddings.</p>
    ${Object.entries(docs)
      .map(
        ([source, chunks]) =>
          `<div class="doc"><h3>${esc(chunks[0]?.title || source)}</h3><div class="muted">${esc(source)}</div><p>${esc(chunks[0]?.content || "")}</p></div>`
      )
      .join("")}
  </div>`;
}

function renderWorkflows() {
  view.innerHTML = `
    <div class="demo-row">
      <button data-demo="site-lead">Запустить: заявка с сайта</button>
      <button class="ghost" data-demo="missed-call">Запустить: пропущенный звонок</button>
      <button class="ghost" data-demo="knowledge-sync">Переиндексировать RAG</button>
    </div>
    <div class="card">
      <table class="table"><thead><tr><th>Пайплайн</th><th>Триггер</th><th>Статус</th><th>Время</th></tr></thead>
      <tbody>${state.overview.workflows
        .map((w) => `<tr><td>${esc(w.name)}</td><td>${esc(w.trigger)}</td><td>${esc(w.status)}</td><td>${esc(w.created_at || "")}</td></tr>`)
        .join("")}</tbody></table>
    </div>`;
  bindDemos();
}

function renderIntegrations() {
  const s = state.integrations.systems;
  view.innerHTML = `<div class="grid kpis">
    ${Object.entries(s)
      .map(
        ([name, info]) =>
          `<div class="card"><h3>${name}</h3><div class="${info.mode === "live" ? "status-ok" : "status-mock"}">${info.mode}</div><div class="muted">${info.events ?? info.ui ?? ""}</div></div>`
      )
      .join("")}
  </div>
  <div class="card" style="margin-top:16px">
    <h3>Последние события адаптеров</h3>
    <pre class="trace">${esc(JSON.stringify(state.integrations.recent, null, 2).slice(0, 4000))}</pre>
  </div>`;
}

function renderChat() {
  view.innerHTML = `
    <div class="chat-layout">
      <div class="card">
        <div class="messages" id="messages"></div>
        <div class="composer">
          <input id="chat-input" placeholder="Например: чем заменить СОЖ на пищевом производстве?" />
          <button id="send-btn">Спросить</button>
        </div>
      </div>
      <div class="card trace">
        <h3>Трасса ReAct</h3>
        <p class="muted">Инструменты: ${esc(toolNames())}</p>
        <p class="muted" id="run-stats">Прогонов пока нет.</p>
        <pre id="trace">Пока пусто — задайте вопрос.</pre>
      </div>
    </div>`;
  const input = document.getElementById("chat-input");
  document.getElementById("send-btn").onclick = () => sendChat(input.value);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendChat(input.value);
  });
}

async function sendChat(text) {
  if (!text.trim()) return;
  const box = document.getElementById("messages");
  box.innerHTML += `<div class="bubble user">${esc(text)}</div>`;
  document.getElementById("chat-input").value = "";
  const data = await api("/api/chat", {
    method: "POST",
    body: JSON.stringify({ message: text, conversation_id: state.conversationId }),
  });
  state.conversationId = data.conversation_id;
  box.innerHTML += `<div class="bubble assistant">${esc(data.answer)}</div>`;
  box.scrollTop = box.scrollHeight;
  document.getElementById("run-stats").textContent = runStats(data);
  document.getElementById("trace").textContent = JSON.stringify(
    {
      run_id: data.run_id,
      provider: data.provider,
      model: data.model,
      mode: data.mode,
      citations: data.citations,
      warning: data.warning,
      trace: data.trace,
    },
    null,
    2
  );
}

function toolNames() {
  const tools = state.chatStatus?.tools || [];
  return tools.length ? tools.map((t) => t.name).join(", ") : "загрузка...";
}

function runStats(data) {
  const u = data.usage || {};
  const parts = [
    `режим ${data.mode}`,
    `модель ${data.model || "—"}`,
    `итераций ${data.iterations}`,
    `инструментов ${(data.trace || []).filter((s) => s.type === "tool").length}`,
    `токенов ${u.total_tokens ?? 0}`,
    `$${(u.cost_usd ?? 0).toFixed(6)}`,
    `${u.latency_ms ?? 0} мс`,
  ];
  return parts.join(" · ");
}

function bindDemos() {
  document.querySelectorAll("[data-demo]").forEach((btn) => {
    btn.onclick = async () => {
      btn.disabled = true;
      await api(`/api/demo/${btn.dataset.demo}`, { method: "POST", body: "{}" });
      await loadAll();
      render();
    };
  });
}

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

loadAll().then(render).catch((err) => {
  view.innerHTML = `<div class="card">Не удалось загрузить API: ${esc(err.message)}</div>`;
});
