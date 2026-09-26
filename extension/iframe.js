// GPT Image Studio — iframe UI logic.
//
// Talks to the local aigpt-api server (http://127.0.0.1:8789). Host-permission
// for 127.0.0.1 covers this iframe's fetch calls (no CORS problems).

const API = "http://127.0.0.1:8789";
const MAX_REF_BYTES = 4 * 1024 * 1024; // 4 MiB upload cap (base64 grows ~1.37x)
const MAX_EDIT_BYTES = 2 * 1024 * 1024; // compress target: 4 edit refs at 2 MiB ≈ 11 MiB JSON, under the 16 MiB body cap
const MAX_REF_DIM = 2048;

const $ = (id) => document.getElementById(id);
const state = {
  refImage: null,        // { b64, name } for TEXT TO IMAGE
  editImages: [],        // Array<{ b64, name }> for IMAGE TO IMAGE (max 4)
  editConversationId: null, // conversation to continue via "Edit this" (Phase 3)
  providers: { text: "chatgpt", edit: "chatgpt" },
  accounts: [],
  antigravity: { accounts: [], models: [] },
  serverOn: false,
};

// ------------------------------------------------------------------ server

async function apiFetch(path, opts = {}) {
  const resp = await fetch(API + path, opts);
  const text = await resp.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (e) {
    // ignore parse errors — caller checks status
  }
  return { status: resp.status, data };
}

async function refreshServer() {
  const { status, data } = await apiFetch("/health").catch(() => ({ status: 0, data: {} }));
  state.serverOn = status === 200 && data.service === "aigpt-api";
  const dot = $("dot");
  dot.className = state.serverOn ? "on" : "off";
  $("srv").textContent = state.serverOn ? "server đang chạy" : "server đang tắt";
}

// Auto-start the local API server via the native messaging host. Returns true
// when the server is up (it was already running, or we just started it).
let serverStarting = false; // dedup: only one spawn attempt at a time
async function ensureServer(statusElId = "status") {
  if (state.serverOn) return true;
  if (serverStarting) return false; // another call is already waiting
  serverStarting = true;
  setStatus(statusElId, "Đang khởi động server API local…", "");
  try {
    // Ask the native host (com.aigpt.launcher) to spawn aigpt-api if it is down.
    const resp = await new Promise((resolve) => {
      const port = chrome.runtime.connectNative("com.aigpt.launcher");
      let done = false;
      const finish = (r) => { if (!done) { done = true; resolve(r); } };
      port.onMessage.addListener(finish);
      port.onDisconnect.addListener(() => finish(null));
      port.postMessage({ action: "start" });
      setTimeout(() => { try { port.disconnect(); } catch (e) { } finish(null); }, 5000);
    });
    if (!resp || resp.ok !== true) throw new Error("launcher failed");
    // Poll /health until the freshly spawned server is ready.
    const deadline = Date.now() + 10000;
    for (; ;) {
      const { status, data } = await apiFetch("/health").catch(() => ({ status: 0, data: {} }));
      if (status === 200 && data.service === "aigpt-api") break;
      if (Date.now() > deadline) throw new Error("server did not start");
      await new Promise((r) => setTimeout(r, 500));
    }
    await refreshServer();
    return state.serverOn;
  } catch (e) {
    setStatus(statusElId, "Server API không chạy — khởi động: uv run aigpt-api --port 8789", "err");
    return false;
  } finally {
    serverStarting = false;
  }
}

async function refreshAccounts() {
  const { status, data } = await apiFetch("/accounts").catch(() => ({ status: 0, data: {} }));
  if (status !== 200) {
    state.accounts = [];
  } else {
    state.accounts = data.accounts || [];
  }
  renderAccount();
}

function renderAccount() {
  const container = $("chatgptAccounts");
  const addButton = $("accBtn");
  if (!container || !addButton) return;
  addButton.disabled = false;
  container.textContent = "";
  if (!state.accounts.length) {
    const empty = document.createElement("div");
    empty.className = "provider-account-empty";
    empty.textContent = state.serverOn ? "Chưa có tài khoản ChatGPT." : "Server chưa chạy.";
    container.appendChild(empty);
    return;
  }
  state.accounts.forEach((account) => {
    const row = document.createElement("div");
    row.className = "provider-account-row";
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = (account.email || "?").charAt(0).toUpperCase();
    const info = document.createElement("div");
    info.className = "provider-account-info";
    const name = document.createElement("div");
    name.className = "provider-account-email";
    name.textContent = account.email || "(chưa rõ email)";
    const meta = document.createElement("div");
    meta.className = "provider-account-meta";
    meta.textContent = `${account.type || "free"} · còn ${account.remaining ?? "?"} ảnh`;
    info.append(name, meta);
    const logout = document.createElement("button");
    logout.className = "btn tiny ghost";
    logout.textContent = "Đăng xuất";
    logout.addEventListener("click", () => logoutAccount(account, logout));
    row.append(avatar, info, logout);
    container.appendChild(row);
  });
}

async function logoutAccount(acc, btn) {
  // Prefer the stable user_id; fall back to email. Accounts whose login probe
  // failed may have an empty email but always carry a user_id after first use.
  const key = acc && (acc.user_id || acc.email);
  if (!acc || !key) {
    setStatus("accountStatus", "không đăng xuất được: account chưa có id", "err");
    return;
  }
  const param = acc.user_id ? "user_id" : "email";
  btn.disabled = true;
  try {
    const { status, data } = await apiFetch(
      "/account?" + param + "=" + encodeURIComponent(key), { method: "DELETE" });
    if (status === 200) {
      setStatus("accountStatus", "đã đăng xuất " + (acc.email || key), "ok");
    } else {
      setStatus("accountStatus", data.error || `đăng xuất thất bại (HTTP ${status})`, "err");
    }
  } catch (e) {
    setStatus("accountStatus", "đăng xuất thất bại: " + e.message, "err");
  } finally {
    btn.disabled = false;
    await refreshAccounts();
  }
}

// Login flow: the panel asks the API server (8789) to start a callback
// receiver on 8788 and returns the PKCE authorize URL. The API session
// handles the 8788 receiver lifecycle, so no terminal is needed.
let chatgptLoginPolling = false;

async function pollChatgptLogin() {
  if (chatgptLoginPolling) return;
  chatgptLoginPolling = true;
  const deadline = Date.now() + 300000; // 5 min
  try {
    for (; ;) {
      await new Promise((r) => setTimeout(r, 2500));
      if (Date.now() > deadline) {
        setStatus("accountStatus", "Đăng nhập hết hạn — thử lại", "err");
        break;
      }
      try {
        const { status, data } = await apiFetch("/login/status");
        if (status !== 200 || !data) break;
        if (data.state === "done") {
          setStatus("accountStatus", "Đã đăng nhập ChatGPT: " + (data.email || "(chưa rõ)"), "ok");
          await refreshAccounts();
          break;
        }
        if (data.state === "error") {
          setStatus("accountStatus", "Đăng nhập ChatGPT thất bại: " + (data.error || "lỗi không rõ"), "err");
          break;
        }
        if (data.state === "idle") {
          setStatus("accountStatus", "Phiên đăng nhập đã bị đóng (thử lại)", "err");
          break;
        }
        // waiting — keep polling
      } catch (e) {
        // transient fetch error — keep polling
      }
    }
  } finally {
    chatgptLoginPolling = false;
  }
}

async function startLogin(event) {
  const btn = event && event.currentTarget ? event.currentTarget : $("accBtn");
  const copyOnly = btn.id === "copyChatgptLoginLink";
  btn.disabled = true;
  if (!state.serverOn && !(await ensureServer("accountStatus"))) {
    btn.disabled = false;
    return;
  }
  setStatus("accountStatus", "Đang mở đăng nhập ChatGPT…", "");
  try {
    const { status, data } = await apiFetch("/login/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (status === 200 && data.authorize_url) {
      if (copyOnly) {
        await copyTextToClipboard(data.authorize_url);
        setStatus("accountStatus", "Đã copy link ChatGPT. Dán vào profile khác và đăng nhập trong 5 phút…", "ok");
        showToast("Đã copy link đăng nhập ChatGPT");
      } else {
        const opened = await chrome.runtime.sendMessage({ type: "openTab", url: data.authorize_url });
        if (!opened || opened.ok !== true) throw new Error("extension không mở được URL OAuth ChatGPT");
        setStatus("accountStatus", "Đã mở ChatGPT OAuth. Đăng nhập xong, panel sẽ tự hoàn tất.", "ok");
      }
    } else if (status === 409) {
      setStatus("accountStatus", data.error || "đang có phiên đăng nhập chờ", "err");
      btn.disabled = false;
      return;
    } else {
      setStatus("accountStatus", data.error || `POST /login/start HTTP ${status}`, "err");
      btn.disabled = false;
      return;
    }
  } catch (e) {
    setStatus("accountStatus", "Không mở được đăng nhập ChatGPT: " + e.message, "err");
    btn.disabled = false;
    return;
  }

  // Poll independently so copying the link does not keep this button locked.
  void pollChatgptLogin();
  btn.disabled = false;
}

// ------------------------------------------------------------------ uploads

// Compress an oversized reference image on a canvas: downscale to MAX_REF_DIM,
// re-encode (WebP keeps transparency, JPEG otherwise), step quality down until
// the bytes fit the target. Returns a data: URL string (falling back to reading
// the original file when canvas is unavailable - the server re-encodes
// oversized refs anyway).
function compressImageFile(file) {
  const loadImage = () => new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("không đọc được ảnh")); };
    img.src = url;
  });

  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");

  const encode = (blob) => new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(new Error("không đọc được file"));
    r.readAsDataURL(blob);
  });

  if (!ctx) return encode(file); // no canvas → read the original as a data URL

  const draw = (img, scale) => {
    const w = Math.max(1, Math.round(img.naturalWidth * scale));
    const h = Math.max(1, Math.round(img.naturalHeight * scale));
    canvas.width = w;
    canvas.height = h;
    ctx.drawImage(img, 0, 0, w, h);
    return w * h;
  };

  // Encode the canvas to a blob in the chosen format/quality.
  const output = (quality, format) => new Promise((resolve, reject) => {
    canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error("nén ảnh thất bại")),
      format, quality);
  });

  return loadImage()
    .then((img) => {
      const wantsAlpha = file.type === "image/png" || file.type === "image/webp" || file.type === "image/gif";
      let scale = 1;
      if (Math.max(img.naturalWidth, img.naturalHeight) > MAX_REF_DIM) {
        scale = MAX_REF_DIM / Math.max(img.naturalWidth, img.naturalHeight);
      }
      draw(img, scale);
      // First encode doubles as the format probe: toBlob on an unsupported
      // type silently returns a PNG blob, so check blob.type, not the request.
      const firstFormat = wantsAlpha ? "image/webp" : "image/jpeg";
      return output(0.85, firstFormat).then((probe) => {
        const format = wantsAlpha && probe.type === "image/webp" ? "image/webp" : "image/jpeg";
        if (probe.size <= MAX_EDIT_BYTES) return probe;
        // Still too big → step quality down, then downscale further.
        return output(0.6, format).then((b) => b.size <= MAX_EDIT_BYTES ? b
          : output(0.35, format).then((b2) => b2.size <= MAX_EDIT_BYTES ? b2
            : draw(img, Math.min(scale, 1024 / Math.max(img.naturalWidth, img.naturalHeight))) && output(0.6, format)));
      });
    })
    // Always send the compressed result (never the oversized original): the
    // re-encoded blob stays within the per-image budget for multi-ref edits,
    // and the server re-compresses anything still above 2 MiB as a safety net.
    .then((blob) => encode(blob));
}

function readFileAsDataURL(file) {
  return new Promise((resolve, reject) => {
    if (file.size > MAX_REF_BYTES) {
      // Oversized → compress on a canvas instead of rejecting.
      compressImageFile(file)
        .then(resolve)
        .catch(reject);
      return;
    }
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(new Error("không đọc được file"));
    r.readAsDataURL(file);
  });
}

function renderEditThumbs() {
  const container = $("editThumbs");
  container.textContent = "";
  state.editImages.forEach((img, i) => {
    const thumb = document.createElement("div");
    thumb.className = "thumb";
    const p = document.createElement("img");
    p.src = img.b64;
    p.alt = img.name;
    thumb.appendChild(p);
    const label = document.createElement("span");
    label.className = "thumb-label";
    label.textContent = img.name;
    thumb.appendChild(label);
    const rm = document.createElement("button");
    rm.className = "btn tiny ghost";
    rm.textContent = "✕";
    rm.addEventListener("click", () => {
      state.editImages.splice(i, 1);
      // Removing a loaded image breaks the "Edit this" continuation context.
      state.editConversationId = null;
      renderEditThumbs();
      if (!state.editImages.length) {
        $("dropzoneEdit").style.display = "block";
      }
    });
    thumb.appendChild(rm);
    container.appendChild(thumb);
  });
  $("dropzoneEdit").style.display = state.editImages.length >= 4 ? "none" : "block";
}

function wireDropzone(dzId, fileId, destKey) {
  const dz = $(dzId), file = $(fileId);
  const isEdit = destKey === "editImages";
  dz.addEventListener("click", () => file.click());
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("drag");
    const files = e.dataTransfer.files;
    for (let i = 0; i < files.length; i++) handleFile(files[i]);
  });
  file.addEventListener("change", () => {
    for (let i = 0; i < file.files.length; i++) handleFile(file.files[i]);
    file.value = ""; // allow re-uploading the same file
  });

  async function handleFile(f) {
    try {
      if (isEdit) {
        if (state.editImages.length >= 4) { setStatus("editStatus", "tối đa 4 ảnh", "err"); return; }
        const dataUrl = await readFileAsDataURL(f);
        state.editImages.push({ b64: dataUrl, name: f.name });
        // A manually-uploaded image starts a fresh edit context, not a
        // continuation of some earlier generation.
        state.editConversationId = null;
        renderEditThumbs();
      } else {
        const dataUrl = await readFileAsDataURL(f);
        state[destKey] = { b64: dataUrl, name: f.name };
        dz.textContent = "";
        const img = document.createElement("img");
        img.className = "preview";
        img.src = dataUrl;
        img.alt = "reference";
        const label = document.createElement("span");
        label.className = "dz-label";
        label.textContent = `${f.name} — bấm để thay`;
        dz.append(img, label);
      }
    } catch (err) {
      setStatus(isEdit ? "editStatus" : "status", err.message, "err");
    }
  }
}

function setStatus(elId, msg, kind) {
  const el = $(elId);
  el.textContent = msg;
  el.className = "status" + (kind ? " " + kind : "");
}

// ------------------------------------------------------------------ generate

function splitPrompts(raw) {
  return raw
    .replace(/^[=\s]{5,}$/gm, "")
    .split(/\n\s*\n/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function renderPromptPreview() {
  const prompts = splitPrompts($("prompt").value);
  $("genBtn").textContent = `✨ TẠO ${prompts.length} ẢNH`;
  const preview = $("promptPreview");
  const hint = $("previewHint");
  if (prompts.length <= 1) { preview.hidden = true; hint.hidden = true; return; }
  preview.hidden = false;
  hint.hidden = false;
  preview.textContent = "";
  const head = document.createElement("div");
  head.className = "pp-head";
  head.textContent = `Đã phát hiện: ${prompts.length} prompts`;
  preview.appendChild(head);
  prompts.forEach((p, i) => {
    const item = document.createElement("div");
    item.className = "pp-item";
    item.textContent = `${i + 1}. ${p}`;
    preview.appendChild(item);
  });
}

$("prompt").addEventListener("input", renderPromptPreview);
renderPromptPreview();

async function generate(prompt, opts) {
  if (!state.serverOn && !(await ensureServer())) {
    throw new Error("server aigpt-api không chạy — khởi động: uv run aigpt-api");
  }
  if (!state.accounts.length) {
    throw new Error("chưa có account — bấm Thêm account, rồi chạy: uv run aigpt login --wait");
  }
  const body = { prompt, ...opts };
  const { status, data } = await apiFetch("/generate_image", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (status === 200) {
    return {
      paths: data.paths || [],
      files: data.files || [],
      conversationId: data.conversation_id || "",
      provider: "chatgpt",
      model: data.model || "gpt-image-2",
      resolution: data.resolution || "",
      account_email: data.account_email || "",
    };
  }
  throw new Error(data.error || `HTTP ${status}`);
}

// --------------------------------------------------------- Antigravity API

function renderAntigravityAccounts() {
  const container = $("agAccounts");
  const accounts = state.antigravity.accounts;
  if (!container) return;
  container.textContent = "";
  if (!accounts.length) {
    const empty = document.createElement("div");
    empty.className = "provider-account-empty";
    empty.textContent = "Chưa có tài khoản Antigravity.";
    container.appendChild(empty);
    return;
  }
  accounts.forEach((account) => {
    const row = document.createElement("div");
    row.className = "provider-account-row";
    const avatar = document.createElement("div");
    avatar.className = "avatar ag-avatar";
    avatar.textContent = (account.email || "A").charAt(0).toUpperCase();
    const info = document.createElement("div");
    info.className = "provider-account-info";
    const email = document.createElement("div");
    email.className = "provider-account-email";
    email.textContent = account.email || "(chưa rõ email)";
    const quota = document.createElement("div");
    quota.className = "provider-account-meta";
    const credits = account.credits || {};
    const detail = credits.credit_amount != null
      ? `${credits.credit_amount}/${credits.minimum_credit_amount ?? "?"} credits`
      : (credits.status || "quota chưa rõ");
    quota.textContent = `${account.status || "unknown"} · ${detail}`;
    info.append(email, quota);
    const logout = document.createElement("button");
    logout.className = "btn tiny ghost";
    logout.textContent = "Đăng xuất";
    logout.addEventListener("click", async (event) => {
      event.stopPropagation();
      logout.disabled = true;
      const result = await apiFetch("/antigravity/account?email=" + encodeURIComponent(account.email), { method: "DELETE" });
      if (result.status === 200) {
        setStatus("accountStatus", `Đã đăng xuất Antigravity: ${account.email}`, "ok");
      } else {
        setStatus("accountStatus", result.data.error || "đăng xuất thất bại", "err");
      }
      await refreshAntigravityAccounts();
    });
    row.append(avatar, info, logout);
    container.appendChild(row);
  });
}

function maskEmail(email) {
  if (!email || !email.includes("@")) return "";
  const [local, domain] = email.split("@", 2);
  return `${local.slice(0, 2)}${local.length > 2 ? "***" : "*"}@${domain}`;
}

async function refreshAntigravityAccounts() {
  const { status, data } = await apiFetch("/antigravity/accounts").catch(() => ({ status: 0, data: {} }));
  state.antigravity.accounts = status === 200 ? (data.accounts || []) : [];
  renderAntigravityAccounts();
  await refreshAntigravityModels();
}

async function refreshAntigravityModels() {
  const selects = [
    { element: $("textAgModel"), storageKey: "textToImageAntigravityModel" },
    { element: $("editAgModel"), storageKey: "imageToImageAntigravityModel" },
  ].filter((item) => item.element);
  if (!selects.length) return;
  const { status, data } = await apiFetch("/antigravity/models").catch(() => ({ status: 0, data: {} }));
  state.antigravity.models = status === 200 ? (data.models || []) : [];
  const saved = await chrome.storage.local.get([
    "antigravityModel", "textToImageAntigravityModel", "imageToImageAntigravityModel",
  ]).catch(() => ({}));
  const defaultModel = data.default_model || state.antigravity.models[0]?.id || "";
  const validModel = (value) => state.antigravity.models.some((model) => model.id === value);
  const storedModels = {};

  selects.forEach(({ element, storageKey }) => {
    const candidate = saved[storageKey] || saved.antigravityModel || defaultModel;
    const selected = validModel(candidate) ? candidate : defaultModel;
    storedModels[storageKey] = selected;
    element.textContent = "";
    if (!state.antigravity.models.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "Đăng nhập để tải model ảnh";
      element.appendChild(option);
      return;
    }
    state.antigravity.models.forEach((model) => {
      const option = document.createElement("option");
      option.value = model.id;
      option.textContent = model.display_name || model.id;
      option.selected = model.id === selected;
      element.appendChild(option);
    });
  });
  if (state.antigravity.models.length) {
    await chrome.storage.local.set({ ...storedModels, antigravityModel: storedModels.textToImageAntigravityModel }).catch(() => {});
  }
}

async function refreshMcpStatus() {
  const statusEl = $("mcpConnectionStatus");
  if (!statusEl) return;
  if (!state.serverOn && !(await ensureServer("mcpConnectionStatus"))) return;
  statusEl.textContent = "Đang kiểm tra account pool…";
  const [chatgpt, antigravity] = await Promise.all([
    apiFetch("/accounts").catch(() => ({ status: 0, data: {} })),
    apiFetch("/antigravity/accounts").catch(() => ({ status: 0, data: {} })),
  ]);
  const chatRows = chatgpt.status === 200 ? (chatgpt.data.accounts || []) : [];
  const agRows = antigravity.status === 200 ? (antigravity.data.accounts || []) : [];
  const chatReady = chatRows.filter((row) => row.alive !== false).length;
  const agReady = agRows.filter((row) => row.status === "available").length;
  const chatText = chatgpt.status === 200
    ? `ChatGPT: ${chatRows.length} account · ${chatReady} sẵn sàng`
    : "ChatGPT: chưa đọc được account pool";
  const agText = antigravity.status === 200
    ? `Antigravity: ${agRows.length} account · ${agReady} sẵn sàng`
    : "Antigravity: chưa đọc được account pool";
  statusEl.textContent = `${chatText}\n${agText}`;
  await refreshMcpApiKey();
}

async function refreshMcpApiKey() {
  const statusEl = $("mcpApiKeyStatus");
  const createBtn = $("mcpCreateApiKey");
  const revokeBtn = $("mcpRevokeApiKey");
  if (!statusEl || !createBtn || !revokeBtn) return;
  const { status, data } = await apiFetch("/mcp/api-key").catch(() => ({ status: 0, data: {} }));
  if (status !== 200) {
    statusEl.textContent = "Không đọc được trạng thái API key.";
    revokeBtn.hidden = true;
    return;
  }
  const configured = data.configured === true;
  statusEl.textContent = configured
    ? `Đã tạo · prefix ${data.prefix || "aigpt_…"}`
    : "Chưa tạo API key.";
  revokeBtn.hidden = !configured;
  createBtn.textContent = configured ? "Cấp lại API key" : "Tạo API key";
}

async function createMcpApiKey() {
  const statusEl = $("mcpApiKeyStatus");
  const reveal = $("mcpApiKeyReveal");
  const valueEl = $("mcpApiKeyValue");
  const example = $("mcpApiKeyEnvExample");
  if (!statusEl || !reveal || !valueEl) return;
  const { status, data } = await apiFetch("/mcp/api-key", { method: "POST" })
    .catch(() => ({ status: 0, data: {} }));
  if (status !== 200 || !data.api_key) {
    statusEl.textContent = data.error || "Không tạo được API key.";
    return;
  }
  valueEl.value = data.api_key;
  reveal.hidden = false;
  if (example) example.textContent = `AIGPT_MCP_API_KEY=${data.api_key}`;
  statusEl.textContent = "API key mới đã tạo — hãy copy ngay, key cũ đã bị thu hồi.";
  await refreshMcpApiKey();
  showToast("Đã tạo API key — copy và lưu lại");
}

async function revokeMcpApiKey() {
  if (!window.confirm("Thu hồi API key hiện tại? Các MCP process dùng key này sẽ không khởi động lại được.")) return;
  const { status, data } = await apiFetch("/mcp/api-key", { method: "DELETE" })
    .catch(() => ({ status: 0, data: {} }));
  if (status !== 200) {
    $("mcpApiKeyStatus").textContent = data.error || "Không thu hồi được API key.";
    return;
  }
  $("mcpApiKeyReveal").hidden = true;
  $("mcpApiKeyValue").value = "";
  await refreshMcpApiKey();
  showToast("Đã thu hồi API key");
}

async function copyMcpApiKey() {
  const value = $("mcpApiKeyValue")?.value || "";
  if (!value) return;
  try {
    await navigator.clipboard.writeText(value);
    showToast("Đã copy API key");
  } catch (e) {
    $("mcpApiKeyValue").focus();
    $("mcpApiKeyValue").select();
    showToast("Đã chọn API key — nhấn Ctrl+C");
  }
}

const providerStorageKeys = {
  text: "textToImageProvider",
  edit: "imageToImageProvider",
};

function updateEditAspectForProvider(provider) {
  const aspect = $("editAspect");
  if (!aspect) return;
  const source = Array.from(aspect.options).find((option) => option.value === "source");
  if (!source) return;
  source.disabled = provider === "antigravity";
  if (provider === "antigravity" && aspect.value === "source") {
    aspect.value = "16:9";
  }
}

function setProvider(tab, provider, persist = true) {
  const normalized = provider === "antigravity" ? "antigravity" : "chatgpt";
  state.providers[tab] = normalized;
  document.querySelectorAll(`[data-provider-control="${tab}"]`).forEach((button) => {
    const active = button.dataset.provider === normalized;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
  const configs = tab === "text"
    ? { chatgpt: "text-gpt-config", antigravity: "text-antigravity-config" }
    : { chatgpt: "edit-gpt-config", antigravity: "edit-antigravity-config" };
  Object.entries(configs).forEach(([name, id]) => {
    const element = $(id);
    if (element) element.hidden = name !== normalized;
  });
  if (tab === "edit") updateEditAspectForProvider(normalized);
  if (persist) chrome.storage.local.set({ [providerStorageKeys[tab]]: normalized }).catch(() => {});
}

async function loadProviderPreferences() {
  const saved = await chrome.storage.local.get(Object.values(providerStorageKeys)).catch(() => ({}));
  setProvider("text", saved.textToImageProvider, false);
  setProvider("edit", saved.imageToImageProvider, false);
}

let antigravityLoginPolling = false;

async function copyTextToClipboard(value) {
  try {
    await navigator.clipboard.writeText(value);
    return;
  } catch (_) {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.readOnly = true;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    if (!copied) throw new Error("không thể copy link vào clipboard");
  }
}

async function pollAntigravityLogin() {
  if (antigravityLoginPolling) return;
  antigravityLoginPolling = true;
  try {
    const deadline = Date.now() + 300000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      const result = await apiFetch("/antigravity/login/status");
      if (result.data.state === "done") {
        setStatus("accountStatus", `Đã thêm Antigravity: ${result.data.email || "account"}`, "ok");
        await refreshAntigravityAccounts();
        return;
      }
      if (result.data.state === "error") throw new Error(result.data.error || "OAuth thất bại");
    }
    throw new Error("OAuth hết hạn sau 5 phút");
  } catch (e) {
    setStatus("accountStatus", "Đăng nhập Antigravity thất bại: " + e.message, "err");
    await refreshAntigravityAccounts().catch(() => {});
  } finally {
    antigravityLoginPolling = false;
  }
}

async function startAntigravityLogin(event) {
  const button = event && event.currentTarget ? event.currentTarget : $("agAddAccount");
  const copyOnly = button.id === "agCopyLoginLink";
  button.disabled = true;
  if (!state.serverOn && !(await ensureServer("accountStatus"))) { button.disabled = false; return; }
  setStatus("accountStatus", "Đang chuẩn bị Google OAuth…", "");
  try {
    const { status, data } = await apiFetch("/antigravity/login/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    if (status !== 200 || !data.authorize_url) throw new Error(data.error || `HTTP ${status}`);
    if (copyOnly) {
      await copyTextToClipboard(data.authorize_url);
      setStatus("accountStatus", "Đã copy link. Dán vào profile khác và đăng nhập trong 5 phút…", "ok");
      showToast("Đã copy link đăng nhập Antigravity");
    } else {
      const opened = await chrome.runtime.sendMessage({ type: "openTab", url: data.authorize_url });
      if (!opened || opened.ok !== true) throw new Error("extension không mở được URL Google OAuth");
      setStatus("accountStatus", "Đã mở Google OAuth. Hoàn tất đăng nhập trong tab mới…", "");
    }
    void pollAntigravityLogin();
  } catch (e) {
    setStatus("accountStatus", "Đăng nhập Antigravity thất bại: " + e.message, "err");
    await refreshAntigravityAccounts().catch(() => {});
  } finally {
    button.disabled = false;
  }
}

async function generateAntigravity(prompt, opts) {
  if (!state.serverOn && !(await ensureServer())) throw new Error("server aigpt-api không chạy");
  if (!state.antigravity.accounts.length) throw new Error("chưa có account Antigravity — bấm Thêm account");
  const { status, data } = await apiFetch("/antigravity/generate", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, ...opts }),
  });
  if (status === 200) return data;
  throw new Error(data.error || `HTTP ${status}`);
}

// ------------------------------------------------------------------ preview modal & toast

let toastTimer = null;
function showToast(msg) {
  const toast = $("pvToast");
  if (!toast) return;
  toast.textContent = msg;
  toast.hidden = false;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.hidden = true;
  }, 2400);
}

const previewContext = {
  items: [],
  currentIndex: 0,
};

function openPreview(items, index = 0) {
  if (!items || !items.length) return;
  previewContext.items = items;
  previewContext.currentIndex = Math.max(0, Math.min(index, items.length - 1));
  $("previewModal").hidden = false;
  renderPreviewCurrent();
}

function closePreview() {
  $("previewModal").hidden = true;
  $("pvImg").src = "";
}

function renderPreviewCurrent() {
  const { items, currentIndex } = previewContext;
  const item = items[currentIndex];
  if (!item) return;

  $("pvCounter").textContent = `${currentIndex + 1} / ${items.length}`;
  $("pvPrevBtn").disabled = currentIndex <= 0;
  $("pvNextBtn").disabled = currentIndex >= items.length - 1;

  $("pvPrompt").textContent = item.prompt || (item.path ? item.path.split(/[\\/]/).pop() : "") || "(không có prompt)";
  $("pvMeta").textContent = "Đang tải ảnh…";

  const img = $("pvImg");
  const spinner = $("pvSpinner");
  spinner.hidden = false;
  img.src = API + item.fileUrl;

  img.onload = () => {
    spinner.hidden = true;
    const dim = `${img.naturalWidth} × ${img.naturalHeight} px`;
    const fname = item.path ? item.path.split(/[\\/]/).pop() : "image.png";
    $("pvMeta").textContent = `${dim} · ${fname}`;
  };

  img.onerror = () => {
    spinner.hidden = true;
    $("pvMeta").textContent = "Không tải được ảnh";
  };
}

$("pvCloseBtn").addEventListener("click", closePreview);
$("pvBackdrop").addEventListener("click", closePreview);

$("pvPrevBtn").addEventListener("click", () => {
  if (previewContext.currentIndex > 0) {
    previewContext.currentIndex--;
    renderPreviewCurrent();
  }
});

$("pvNextBtn").addEventListener("click", () => {
  if (previewContext.currentIndex < previewContext.items.length - 1) {
    previewContext.currentIndex++;
    renderPreviewCurrent();
  }
});

window.addEventListener("keydown", (e) => {
  const modal = $("previewModal");
  if (!modal || modal.hidden) return;
  if (e.key === "Escape") {
    e.preventDefault();
    closePreview();
  } else if (e.key === "ArrowLeft") {
    e.preventDefault();
    if (previewContext.currentIndex > 0) {
      previewContext.currentIndex--;
      renderPreviewCurrent();
    }
  } else if (e.key === "ArrowRight") {
    e.preventDefault();
    if (previewContext.currentIndex < previewContext.items.length - 1) {
      previewContext.currentIndex++;
      renderPreviewCurrent();
    }
  }
});

$("pvDownloadBtn").addEventListener("click", () => {
  const item = previewContext.items[previewContext.currentIndex];
  if (!item || !item.fileUrl) return;
  const fileName = (item.path ? item.path.split(/[\\/]/).pop() : "") || `gpt-image-${Date.now()}.png`;
  fetch(API + item.fileUrl)
    .then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.blob();
    })
    .then((blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fileName;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      showToast("Đã tải ảnh về máy");
    })
    .catch((e) => showToast("Tải ảnh thất bại: " + e.message));
});

$("pvCopyImgBtn").addEventListener("click", async () => {
  const item = previewContext.items[previewContext.currentIndex];
  if (!item || !item.fileUrl) return;
  showToast("Đang xử lý ảnh…");
  try {
    const res = await fetch(API + item.fileUrl);
    if (!res.ok) throw new Error("không đọc được ảnh");
    const blob = await res.blob();
    let pngBlob = blob;
    if (blob.type !== "image/png") {
      const img = new Image();
      const imgLoaded = new Promise((resolve, reject) => {
        img.onload = resolve;
        img.onerror = () => reject(new Error("Lỗi chuyển đổi ảnh"));
      });
      const objectUrl = URL.createObjectURL(blob);
      img.src = objectUrl;
      await imgLoaded;
      URL.revokeObjectURL(objectUrl);

      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0);
      pngBlob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
    }
    await navigator.clipboard.write([
      new ClipboardItem({ "image/png": pngBlob }),
    ]);
    showToast("Đã sao chép ảnh vào Clipboard");
  } catch (e) {
    showToast("Không thể sao chép ảnh: " + e.message);
  }
});

$("pvCopyPromptBtn").addEventListener("click", () => {
  const item = previewContext.items[previewContext.currentIndex];
  const promptText = item && (item.prompt || (item.path ? item.path.split(/[\\/]/).pop() : ""));
  if (promptText) {
    navigator.clipboard.writeText(promptText)
      .then(() => showToast("Đã sao chép prompt"))
      .catch(() => showToast("Không thể sao chép prompt"));
  } else {
    showToast("Không có prompt để sao chép");
  }
});

$("pvEditBtn").addEventListener("click", () => {
  const item = previewContext.items[previewContext.currentIndex];
  if (!item || !item.fileUrl) return;
  fetch(API + item.fileUrl)
    .then((r) => {
      if (!r.ok) throw new Error("not found");
      return r.blob();
    })
    .then((blob) => new Promise((res, rej) => {
      const fr = new FileReader();
      fr.onload = () => res(fr.result);
      fr.onerror = rej;
      fr.readAsDataURL(blob);
    }))
    .then((dataUrl) => {
      const name = (item.path ? item.path.split(/[\\/]/).pop() : "") || "preview-image.png";
      if (item.provider === "antigravity") {
        state.editImages = [{ b64: dataUrl, name }];
        state.editConversationId = null;
        renderEditThumbs();
        setProvider("edit", "antigravity");
        showTab("image-to-image");
        closePreview();
        $("editPrompt").focus();
        showToast("Đã chuyển ảnh sang IMAGE TO IMAGE · Antigravity");
        return;
      }
      state.editImages = [{ b64: dataUrl, name }];
      state.editConversationId = item.conversationId || null;
      renderEditThumbs();
      setProvider("edit", "chatgpt");
      showTab("image-to-image");
      closePreview();
      $("editPrompt").focus();
      showToast("Đã chuyển ảnh sang IMAGE TO IMAGE · GPT");
    })
    .catch(() => {
      showToast("Không tải được ảnh để sửa");
    });
});

$("pvOpenTabBtn").addEventListener("click", () => {
  const item = previewContext.items[previewContext.currentIndex];
  if (item && item.fileUrl) {
    chrome.runtime.sendMessage({ type: "openTab", url: API + item.fileUrl });
  }
});

// Build a result/history card from a {fileUrl, path, prompt} record.
// Text goes through textContent (never innerHTML) so server-returned paths
// and stored prompts cannot inject markup.
function makeCard(rec, itemList, index = 0) {
  const card = document.createElement("div");
  card.className = "hist-card";
  card.addEventListener("click", () => {
    openPreview(itemList || [rec], index);
  });
  const img = document.createElement("img");
  img.src = API + rec.fileUrl;
  img.alt = "result";
  img.loading = "lazy";
  let revalidated = false;
  img.onerror = async () => {
    if (!revalidated) {
      revalidated = true;
      try {
        const res = await fetch(API + "/images");
        if (res.ok) {
          const data = await res.json();
          const images = data.images || [];
          const baseName = (rec.path || "").split(/[\\/]/).pop();
          const found = images.find((item) => (rec.fileId && item.id === rec.fileId) || (baseName && item.name === baseName));
          if (found && found.id) {
            rec.fileId = found.id;
            rec.fileUrl = `/file?id=${found.id}`;
            img.src = API + rec.fileUrl;
            return;
          }
        }
      } catch (e) {
        // network or parse error
      }
    }
    img.remove();
    const gone = document.createElement("div");
    gone.className = "meta";
    gone.textContent = "đã xóa";
    card.appendChild(gone);
  };
  card.appendChild(img);
  const meta = document.createElement("div");
  meta.className = "meta";
  const provider = rec.provider === "antigravity" ? "Antigravity" : "ChatGPT";
  const details = [provider, rec.model, rec.resolution, rec.accountEmail].filter(Boolean).join(" · ");
  meta.textContent = `${details ? details + " · " : ""}${(rec.prompt || (rec.path ? rec.path.split(/[\\/]/).pop() : "") || "").slice(0, 40)}`;
  card.appendChild(meta);
  const editBtn = document.createElement("button");
  editBtn.className = "btn tiny";
  editBtn.textContent = "Sửa tiếp";
  editBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    // Load the generated image back into the IMAGE TO IMAGE tab as its source.
    fetch(API + rec.fileUrl)
      .then((r) => {
        if (!r.ok) throw new Error("not found");
        return r.blob();
      })
      .then((blob) => new Promise((res, rej) => {
        const fr = new FileReader();
        fr.onload = () => res(fr.result);
        fr.onerror = rej;
        fr.readAsDataURL(blob);
      }))
      .then((dataUrl) => {
        if (rec.provider === "antigravity") {
          state.editImages = [{ b64: dataUrl, name: "last-result.png" }];
          state.editConversationId = null;
          renderEditThumbs();
          setProvider("edit", "antigravity");
          showTab("image-to-image");
          $("editPrompt").focus();
          return;
        }
        state.editImages = [{ b64: dataUrl, name: "last-result.png" }];
        // "Edit this" continues the generation that produced this card (the
        // server silently falls back to a fresh one if it rejects the id).
        state.editConversationId = rec.conversationId || null;
        renderEditThumbs();
        setProvider("edit", "chatgpt");
        showTab("image-to-image");
        $("editPrompt").focus();
      })
      .catch(() => {
        setStatus("status", "không tải được ảnh để sửa", "err");
      });
  });
  card.appendChild(editBtn);
  return card;
}

function showResult(res, statusElId, promptOverride = "") {
  const container = $(statusElId);
  container.textContent = "";
  container.className = "status ok";
  const grid = document.createElement("div");
  grid.className = "hist-grid";
  const items = buildResultItems(res, promptOverride || ($("editPrompt") ? $("editPrompt").value.trim() : ""));
  items.forEach((item, idx) => {
    grid.appendChild(makeCard(item, items, idx));
  });
  container.appendChild(grid);
}

function buildResultItems(res, prompt) {
  return (res.paths || []).map((path, index) => {
    const fileUrl = (res.files || [])[index] || "";
    const fileId = fileUrl.includes("=") ? fileUrl.split("=")[1] : "";
    return {
      fileUrl,
      fileId,
      path,
      prompt,
      conversationId: res.conversationId || "",
      provider: res.provider || "chatgpt",
      model: res.model || "",
      resolution: res.resolution || "",
      accountEmail: maskEmail(res.account_email || ""),
    };
  });
}

function setGenerationStatus(elId, msg, kind = "") {
  const el = $(elId);
  const grid = el.querySelector(".hist-grid");
  el.textContent = msg;
  el.className = "status" + (kind ? " " + kind : "");
  if (grid) el.appendChild(grid);
}

$("genBtn").addEventListener("click", async () => {
  const prompts = splitPrompts($("prompt").value);
  if (!prompts.length) { setStatus("status", "viết mô tả ảnh trước", "err"); return; }
  const provider = state.providers.text;
  const model = provider === "antigravity" ? $("textAgModel").value : "";
  if (provider === "antigravity" && !model) {
    setStatus("status", "chưa có model Antigravity — hãy đăng nhập account trước", "err");
    return;
  }
  const btn = $("genBtn");
  btn.disabled = true;
  const grid = document.createElement("div");
  grid.className = "hist-grid";
  const statusEl = $("status");
  statusEl.textContent = "";
  statusEl.className = "status ok";
  statusEl.appendChild(grid);
  const allBatchItems = [];
  for (let i = 0; i < prompts.length; i++) {
    setGenerationStatus("status", `đang xử lý ${i + 1}/${prompts.length}: ${prompts[i].slice(0, 40)}`);
    try {
      let res;
      if (provider === "antigravity") {
        res = await generateAntigravity(prompts[i], {
          model,
          mode: "generate",
          aspect: $("aspect").value,
          resolution: $("textAgResolution").value,
          n: Number($("n").value),
          ref_images: state.refImage ? [state.refImage.b64] : [],
        });
      } else {
        res = await generate(prompts[i], {
          aspect: $("aspect").value,
          n: Number($("n").value),
          style: $("style").value,
          thinking: $("thinking").value,
          mode: state.refImage ? "style" : "generate",
          ref_image: state.refImage ? state.refImage.b64 : undefined,
        });
      }
      const currentBatch = buildResultItems(res, prompts[i]);
      currentBatch.forEach((item) => {
        allBatchItems.push(item);
        const cardIndex = allBatchItems.length - 1;
        grid.appendChild(makeCard(item, allBatchItems, cardIndex));
      });
      await saveHistory(res, prompts[i]);
    } catch (err) {
      setGenerationStatus("status", `prompt ${i + 1} thất bại: ${err.message}`, "err");
      break;
    }
  }
  if (prompts.length > 1 && !statusEl.className.includes("err")) {
    setGenerationStatus("status", `xong ${prompts.length}/${prompts.length} ảnh`, "ok");
  }
  btn.disabled = false;
});

$("editBtn").addEventListener("click", async () => {
  if (!state.editImages.length) { setStatus("editStatus", "tải 1 ảnh để sửa trước", "err"); return; }
  const prompt = $("editPrompt").value.trim();
  if (!prompt) { setStatus("editStatus", "viết yêu cầu chỉnh sửa", "err"); return; }
  const btn = $("editBtn");
  btn.disabled = true;
  setStatus("editStatus", "đang sửa… (1–4 phút)");
  try {
    const provider = state.providers.edit;
    let res;
    if (provider === "antigravity") {
      const model = $("editAgModel").value;
      if (!model) throw new Error("chưa có model Antigravity — hãy đăng nhập account trước");
      res = await generateAntigravity(prompt, {
        model,
        mode: "edit",
        ref_images: state.editImages.map((image) => image.b64),
        aspect: $("editAspect").value,
        resolution: $("editAgResolution").value,
        n: Number($("editAgN").value),
      });
    } else {
      res = await generate(prompt, {
        mode: "edit",
        ref_images: state.editImages.map((image) => image.b64),
        aspect: $("editAspect").value,
        n: 1,
        thinking: $("editThinking").value,
        enhance: $("editEnhance").checked,
        // Continue the conversation that "Edit this" loaded (if any). The server
        // falls back to a fresh generation on rejection - invisible here.
        conversation_id: state.editConversationId || undefined,
      });
    }
    showResult(res, "editStatus");
    await saveHistory(res, prompt);
  } catch (err) {
    setStatus("editStatus", err.message, "err");
  } finally {
    btn.disabled = false;
  }
});

$("accBtn").addEventListener("click", startLogin);
$("copyChatgptLoginLink").addEventListener("click", startLogin);
$("agAddAccount").addEventListener("click", startAntigravityLogin);
$("agCopyLoginLink").addEventListener("click", startAntigravityLogin);
document.querySelectorAll(".provider-toggle").forEach((button) => {
  button.addEventListener("click", () => {
    setProvider(button.dataset.providerControl, button.dataset.provider);
  });
});
$("textAgModel").addEventListener("change", () => {
  chrome.storage.local.set({ textToImageAntigravityModel: $("textAgModel").value }).catch(() => {});
});
$("editAgModel").addEventListener("change", () => {
  chrome.storage.local.set({ imageToImageAntigravityModel: $("editAgModel").value }).catch(() => {});
});

// ------------------------------------------------------------------ history

async function saveHistory(res, prompt) {
  try {
    const { history = [] } = await chrome.storage.local.get("history");
    const entries = res.paths.map((p, i) => {
      const fileUrl = res.files[i] || "";
      const fileId = fileUrl.includes("=") ? fileUrl.split("=")[1] : "";
      return {
        path: p,
        fileUrl: fileUrl,
        fileId: fileId,
        prompt: prompt || "",
        conversationId: res.conversationId || "",
        provider: res.provider || "chatgpt",
        model: res.model || "",
        resolution: res.resolution || "",
        accountEmail: maskEmail(res.account_email || ""),
        ts: Date.now(),
      };
    });
    const merged = [...entries, ...history].slice(0, 60);
    await chrome.storage.local.set({ history: merged });
    renderHistory();
  } catch (e) {
    // storage quota exceeded or unavailable — silently skip
  }
}

async function renderHistory() {
  try {
    const { history = [] } = await chrome.storage.local.get("history");
    const grid = $("histGrid");
    grid.textContent = "";
    if (!history.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "chưa có ảnh nào";
      grid.appendChild(empty);
      return;
    }
    const items = history.map((h) => ({
      fileUrl: h.fileUrl || "",
      fileId: h.fileId || (h.fileUrl && h.fileUrl.includes("=") ? h.fileUrl.split("=")[1] : ""),
      path: h.path,
      prompt: h.prompt,
      conversationId: h.conversationId || "",
      provider: h.provider || "chatgpt",
      model: h.model || "",
      resolution: h.resolution || "",
      accountEmail: h.accountEmail || "",
    }));
    items.forEach((item, idx) => {
      grid.appendChild(makeCard(item, items, idx));
    });
  } catch (e) {
    // storage unavailable
  }
}

// ------------------------------------------------------------------ nav

const tabs = document.querySelectorAll(".tab");
function showTab(name) {
  ["text-to-image", "image-to-image", "account", "mcp"].forEach((tabName) => {
    const section = $("tab-" + tabName);
    if (section) section.hidden = tabName !== name;
  });
  $("view-history").hidden = true;
  tabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  if (name === "account") {
    refreshAntigravityAccounts().catch(() => {});
  }
  if (name === "account") refreshAccounts().catch(() => {});
  if (name === "mcp") refreshMcpStatus().catch(() => {});
}
tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    showTab(tab.dataset.tab);
  });
});

function showView(view) {
  // "main" or "history"
  $("tab-text-to-image").hidden = false;
  $("tab-image-to-image").hidden = true;
  $("tab-account").hidden = true;
  $("tab-mcp").hidden = true;
  $("view-history").hidden = view !== "history";
  tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === "text-to-image"));
  if (view === "history") renderHistory();
}

$("fHistory").addEventListener("click", () => showView("history"));
$("histRefreshBtn").addEventListener("click", () => renderHistory());
$("histClearBtn").addEventListener("click", async () => {
  try {
    await chrome.storage.local.set({ history: [] });
    renderHistory();
  } catch (e) { }
});
$("fSettings").addEventListener("click", () => {
  setStatus("status", "Server: http://127.0.0.1:8789 — khởi động bằng: uv run aigpt-api --port 8789", "");
});
$("fAccount").addEventListener("click", async () => {
  showTab("account");
});
$("mcpRefreshStatus").addEventListener("click", () => refreshMcpStatus().catch(() => {}));
$("mcpCreateApiKey").addEventListener("click", () => createMcpApiKey().catch(() => {}));
$("mcpRevokeApiKey").addEventListener("click", () => revokeMcpApiKey().catch(() => {}));
$("mcpCopyApiKey").addEventListener("click", () => copyMcpApiKey().catch(() => {}));

// ------------------------------------------------------------------ init

wireDropzone("dropzone", "refFile", "refImage");
wireDropzone("dropzoneEdit", "editFile", "editImages");
loadProviderPreferences();
refreshAccounts();
refreshAntigravityAccounts();
// Self-rescheduling refresh loop: fast when offline (3 s) so a just-started
// server appears quickly, regular 15 s when online.
async function refreshLoop() {
  await refreshServer();
  setTimeout(refreshLoop, state.serverOn ? 15000 : 3000);
}
refreshLoop();

document.querySelectorAll("pre.mcp-code").forEach((pre) => {
  pre.addEventListener("click", () => {
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(pre);
    sel.removeAllRanges();
    sel.addRange(range);
    navigator.clipboard.writeText(pre.textContent.trim())
      .then(() => showToast("Đã copy command"))
      .catch(() => showToast("Đã chọn — nhấn Ctrl+C"));
  });
});
