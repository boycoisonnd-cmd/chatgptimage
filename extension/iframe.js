// GPT Image Studio — iframe UI logic.
//
// Talks to the local aigpt-api server (http://127.0.0.1:8787). Host-permission
// for 127.0.0.1 covers this iframe's fetch calls (no CORS problems).

const API = "http://127.0.0.1:8787";
const MAX_REF_BYTES = 4 * 1024 * 1024; // 4 MiB upload cap (base64 grows ~1.37x)

const $ = (id) => document.getElementById(id);
const state = {
  refImage: null,        // { b64, name } for the IMAGE tab
  editImages: [],        // Array<{ b64, name }> for the EDIT tab (max 4)
  editConversationId: null, // conversation to continue via "Edit this" (Phase 3)
  accounts: [],
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
  const { status } = await apiFetch("/health").catch(() => ({ status: 0 }));
  state.serverOn = status === 200;
  const dot = $("dot");
  dot.className = status === 200 ? "on" : "off";
  $("srv").textContent = status === 200 ? "server on" : "server off";
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
  const acc = state.accounts[0];
  const name = $("accName"), sub = $("accSub"), btn = $("accBtn"), avatar = $("avatar");
  btn.disabled = false; // re-arm after a login attempt
  if (!acc) {
    name.textContent = "Chưa đăng nhập";
    sub.textContent = state.serverOn ? "no account" : "server offline";
    avatar.textContent = "?";
    btn.textContent = "Add account";
    btn.onclick = startLogin;
    const lo = $("accLogout");
    if (lo) lo.remove();
    return;
  }
  name.textContent = acc.email || "(unknown email)";
  sub.textContent = `${acc.type || "free"} · ${acc.remaining ?? "?"} images left`;
  avatar.textContent = (acc.email || "?").charAt(0).toUpperCase();
  btn.textContent = "Add account";
  btn.onclick = startLogin;
  // Second button: log this account out (removes it from the local pool).
  if (!$("accLogout")) {
    const lo = document.createElement("button");
    lo.className = "btn small ghost";
    lo.id = "accLogout";
    lo.textContent = "Log out";
    lo.addEventListener("click", logoutAccount);
    btn.parentNode.appendChild(lo);
  }
}

async function logoutAccount() {
  const acc = state.accounts[0];
  const btn = $("accLogout");
  if (!btn) return;
  // Prefer the stable user_id; fall back to email. Accounts whose login probe
  // failed may have an empty email but always carry a user_id after first use.
  const key = acc && (acc.user_id || acc.email);
  if (!acc || !key) {
    setStatus("status", "cannot log out: account has no id yet", "err");
    return;
  }
  const param = acc.user_id ? "user_id" : "email";
  btn.disabled = true;
  try {
    const { status, data } = await apiFetch(
      "/account?" + param + "=" + encodeURIComponent(key), { method: "DELETE" });
    if (status === 200) {
      setStatus("status", "logged out " + (acc.email || key), "ok");
    } else {
      setStatus("status", data.error || `logout failed (HTTP ${status})`, "err");
    }
  } catch (e) {
    setStatus("status", "logout failed: " + e.message, "err");
  } finally {
    btn.disabled = false;
    await refreshAccounts();
  }
}

// Login flow: the panel asks the API server (8787) to start a callback
// receiver on 8788 and returns the PKCE authorize URL. The API session
// handles the 8788 receiver lifecycle, so no terminal is needed.
async function startLogin() {
  const btn = $("accBtn");
  btn.disabled = true;
  if (!state.serverOn) {
    setStatus("status", "API server is not running — start: uv run aigpt-api", "err");
    btn.disabled = false;
    return;
  }
  setStatus("status", "Starting login…", "");
  try {
    const { status, data } = await apiFetch("/login/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (status === 200 && data.authorize_url) {
      await chrome.runtime.sendMessage({ type: "openTab", url: data.authorize_url });
      setStatus("status", "Login page opened. Sign in, then this panel completes automatically.", "ok");
    } else if (status === 409) {
      setStatus("status", data.error || "login already in progress", "err");
      btn.disabled = false;
      return;
    } else {
      setStatus("status", data.error || `POST /login/start HTTP ${status}`, "err");
      btn.disabled = false;
      return;
    }
  } catch (e) {
    setStatus("status", "Cannot reach API server (" + e.message + ")", "err");
    btn.disabled = false;
    return;
  }

  // Poll the login status until the callback arrives or the session times out.
  const deadline = Date.now() + 300000; // 5 min
  for (;;) {
    await new Promise((r) => setTimeout(r, 2500));
    if (Date.now() > deadline) {
      setStatus("status", "Login timed out — try again", "err");
      break;
    }
    try {
      const { status, data } = await apiFetch("/login/status");
      if (status !== 200 || !data) break;
      if (data.state === "done") {
        setStatus("status", "Logged in as " + (data.email || "(unknown)"), "ok");
        await refreshAccounts();
        break;
      }
      if (data.state === "error") {
        setStatus("status", "Login failed: " + (data.error || "unknown error"), "err");
        break;
      }
      if (data.state === "idle") {
        // Session was cleaned up from under us (TTL, etc.).
        setStatus("status", "Login session was closed (try again)", "err");
        break;
      }
      // waiting — keep polling
    } catch (e) {
      // transient fetch error — keep polling
    }
  }
  btn.disabled = false;
}

// ------------------------------------------------------------------ uploads

function readFileAsDataURL(file) {
  return new Promise((resolve, reject) => {
    if (file.size > MAX_REF_BYTES) {
      reject(new Error("image too large (max 4 MiB)"));
      return;
    }
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(new Error("could not read file"));
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
        if (state.editImages.length >= 4) { setStatus("editStatus", "max 4 images", "err"); return; }
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
        label.textContent = `${f.name} — click to replace`;
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

async function generate(prompt, opts) {
  if (!state.serverOn) {
    throw new Error("aigpt-api server is not running — start: uv run aigpt-api");
  }
  if (!state.accounts.length) {
    throw new Error("no account — click Add account, then run: uv run aigpt login --wait");
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
    };
  }
  throw new Error(data.error || `HTTP ${status}`);
}

// Build a result/history card from a {fileUrl, path, prompt} record.
// Text goes through textContent (never innerHTML) so server-returned paths
// and stored prompts cannot inject markup.
function makeCard(rec) {
  const card = document.createElement("div");
  card.className = "hist-card";
  card.addEventListener("click", () => {
    window.open(API + rec.fileUrl, "_blank");
  });
  const img = document.createElement("img");
  img.src = API + rec.fileUrl;
  img.alt = "result";
  img.loading = "lazy";
  img.onerror = () => {
    // File registry is in-memory: ids from a previous server run 404.
    img.remove();
    const gone = document.createElement("div");
    gone.className = "meta";
    gone.textContent = "expired — restart aigpt-api";
    card.appendChild(gone);
  };
  card.appendChild(img);
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = (rec.prompt || rec.path.split(/[\\/]/).pop() || "").slice(0, 40);
  card.appendChild(meta);
  const editBtn = document.createElement("button");
  editBtn.className = "btn tiny";
  editBtn.textContent = "Edit this";
  editBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    // Load the generated image back into the EDIT tab as its source.
    fetch(API + rec.fileUrl)
      .then((r) => r.blob())
      .then((blob) => new Promise((res, rej) => {
        const fr = new FileReader();
        fr.onload = () => res(fr.result);
        fr.onerror = rej;
        fr.readAsDataURL(blob);
      }))
      .then((dataUrl) => {
        state.editImages = [{ b64: dataUrl, name: "last-result.png" }];
        // "Edit this" continues the generation that produced this card (the
        // server silently falls back to a fresh one if it rejects the id).
        state.editConversationId = rec.conversationId || null;
        renderEditThumbs();
        $("tab-image").hidden = true;
        $("tab-edit").hidden = false;
        $("tab-chatgpt").hidden = true;
        $("view-history").hidden = true;
        document.querySelectorAll(".tab").forEach((t) =>
          t.classList.toggle("active", t.dataset.tab === "edit"));
        $("editPrompt").focus();
      })
      .catch(() => {
        setStatus("status", "could not load image for editing", "err");
      });
  });
  card.appendChild(editBtn);
  return card;
}

function showResult(res, statusElId) {
  const container = $(statusElId);
  container.textContent = "";
  container.className = "status ok";
  const grid = document.createElement("div");
  grid.className = "hist-grid";
  res.paths.forEach((p, i) => {
    grid.appendChild(makeCard({
      fileUrl: res.files[i] || "",
      path: p,
      conversationId: res.conversationId || "",
    }));
  });
  container.appendChild(grid);
}

$("genBtn").addEventListener("click", async () => {
  const prompt = $("prompt").value.trim();
  if (!prompt) { setStatus("status", "describe an image first", "err"); return; }
  const btn = $("genBtn");
  btn.disabled = true;
  setStatus("status", "generating… (1–4 min)");
  try {
    const res = await generate(prompt, {
      aspect: $("aspect").value,
      n: Number($("n").value),
      style: $("style").value,
      thinking: $("thinking").value,
      // A reference on the IMAGE tab is a STYLE reference: match the look,
      // don't copy content. The EDIT tab sends mode="edit" for real edits.
      mode: state.refImage ? "style" : "generate",
      ref_image: state.refImage ? state.refImage.b64 : undefined,
    });
    showResult(res, "status");
    saveHistory(res);
  } catch (err) {
    setStatus("status", err.message, "err");
  } finally {
    btn.disabled = false;
  }
});

$("editBtn").addEventListener("click", async () => {
  if (!state.editImages.length) { setStatus("editStatus", "upload an image to edit first", "err"); return; }
  const prompt = $("editPrompt").value.trim();
  if (!prompt) { setStatus("editStatus", "write an edit instruction", "err"); return; }
  const btn = $("editBtn");
  btn.disabled = true;
  setStatus("editStatus", "editing… (1–4 min)");
  try {
    const res = await generate(prompt, {
      mode: "edit",
      ref_images: state.editImages.map((i) => i.b64),
      aspect: $("editAspect").value,
      n: 1,
      thinking: $("editThinking").value,
      enhance: $("editEnhance").checked,
      // Continue the conversation that "Edit this" loaded (if any). The server
      // falls back to a fresh generation on rejection - invisible here.
      conversation_id: state.editConversationId || undefined,
    });
    showResult(res, "editStatus");
    saveHistory(res);
  } catch (err) {
    setStatus("editStatus", err.message, "err");
  } finally {
    btn.disabled = false;
  }
});

// ------------------------------------------------------------------ history

async function saveHistory(res, prompt) {
  try {
    const { history = [] } = await chrome.storage.local.get("history");
    const entries = res.paths.map((p, i) => ({
      path: p,
      fileUrl: res.files[i] || "",
      prompt: prompt || "",
      conversationId: res.conversationId || "",
      ts: Date.now(),
    }));
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
      empty.textContent = "no images yet";
      grid.appendChild(empty);
      return;
    }
    history.forEach((h) => {
      grid.appendChild(makeCard({
        fileUrl: h.fileUrl || "",
        path: h.path,
        prompt: h.prompt,
        conversationId: h.conversationId || "",
      }));
    });
  } catch (e) {
    // storage unavailable
  }
}

// ------------------------------------------------------------------ nav

const tabs = document.querySelectorAll(".tab");
tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    const name = tab.dataset.tab;
    ["image", "edit", "chatgpt"].forEach((t) => {
      $("tab-" + t).hidden = t !== name;
    });
    // Hide the history view when switching tabs
    $("view-history").hidden = true;
  });
});

function showView(view) {
  // "main" or "history"
  $("tab-image").hidden = false;
  $("tab-edit").hidden = true;
  $("tab-chatgpt").hidden = true;
  $("view-history").hidden = view !== "history";
  tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === "image"));
  if (view === "history") renderHistory();
}

$("fHistory").addEventListener("click", () => showView("history"));
$("fSettings").addEventListener("click", () => {
  setStatus("status", "Server: http://127.0.0.1:8787 — start it with: uv run aigpt-api", "");
});
$("fAccount").addEventListener("click", async () => {
  await refreshAccounts();
  setStatus("status", state.accounts.length
    ? `${state.accounts.length} account(s) — aigpt accounts for live quota`
    : "no account yet", "");
});

// ------------------------------------------------------------------ init

wireDropzone("dropzone", "refFile", "refImage");
wireDropzone("dropzoneEdit", "editFile", "editImages");
refreshServer();
refreshAccounts();
setInterval(() => { refreshServer(); }, 15000);