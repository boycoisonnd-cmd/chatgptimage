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
  const { status, data } = await apiFetch("/health").catch(() => ({ status: 0, data: {} }));
  state.serverOn = status === 200 && data.service === "aigpt-api";
  const dot = $("dot");
  dot.className = state.serverOn ? "on" : "off";
  $("srv").textContent = state.serverOn ? "server đang chạy" : "server đang tắt";
}

// Auto-start the local API server via the native messaging host. Returns true
// when the server is up (it was already running, or we just started it).
let serverStarting = false; // dedup: only one spawn attempt at a time
async function ensureServer() {
  if (state.serverOn) return true;
  if (serverStarting) return false; // another call is already waiting
  serverStarting = true;
  setStatus("status", "Đang khởi động server API local…", "");
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
    setStatus("status", "Server API không chạy — khởi động: uv run aigpt-api --port 8789", "err");
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
  const acc = state.accounts[0];
  const name = $("accName"), sub = $("accSub"), btn = $("accBtn"), avatar = $("avatar");
  btn.disabled = false; // re-arm after a login attempt
  if (!acc) {
    name.textContent = "Chưa đăng nhập";
    sub.textContent = state.serverOn ? "chưa có account" : "server không chạy";
    avatar.textContent = "?";
    btn.textContent = "Thêm account";
    btn.onclick = startLogin;
    const lo = $("accLogout");
    if (lo) lo.remove();
    return;
  }
  name.textContent = acc.email || "(chưa rõ email)";
  sub.textContent = `${acc.type || "free"} · còn ${acc.remaining ?? "?"} ảnh`;
  avatar.textContent = (acc.email || "?").charAt(0).toUpperCase();
  btn.textContent = "Thêm account";
  btn.onclick = startLogin;
  // Second button: log this account out (removes it from the local pool).
  if (!$("accLogout")) {
    const lo = document.createElement("button");
    lo.className = "btn small ghost";
    lo.id = "accLogout";
    lo.textContent = "Đăng xuất";
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
    setStatus("status", "không đăng xuất được: account chưa có id", "err");
    return;
  }
  const param = acc.user_id ? "user_id" : "email";
  btn.disabled = true;
  try {
    const { status, data } = await apiFetch(
      "/account?" + param + "=" + encodeURIComponent(key), { method: "DELETE" });
    if (status === 200) {
      setStatus("status", "đã đăng xuất " + (acc.email || key), "ok");
    } else {
      setStatus("status", data.error || `đăng xuất thất bại (HTTP ${status})`, "err");
    }
  } catch (e) {
    setStatus("status", "đăng xuất thất bại: " + e.message, "err");
  } finally {
    btn.disabled = false;
    await refreshAccounts();
  }
}

// Login flow: the panel asks the API server (8789) to start a callback
// receiver on 8788 and returns the PKCE authorize URL. The API session
// handles the 8788 receiver lifecycle, so no terminal is needed.
async function startLogin() {
  const btn = $("accBtn");
  btn.disabled = true;
  if (!state.serverOn && !(await ensureServer())) {
    btn.disabled = false;
    return;
  }
  setStatus("status", "Đang đăng nhập…", "");
  try {
    const { status, data } = await apiFetch("/login/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (status === 200 && data.authorize_url) {
      await chrome.runtime.sendMessage({ type: "openTab", url: data.authorize_url });
      setStatus("status", "Đã mở trang đăng nhập. Đăng nhập xong, panel sẽ tự hoàn tất.", "ok");
    } else if (status === 409) {
      setStatus("status", data.error || "đang có phiên đăng nhập chờ", "err");
      btn.disabled = false;
      return;
    } else {
      setStatus("status", data.error || `POST /login/start HTTP ${status}`, "err");
      btn.disabled = false;
      return;
    }
  } catch (e) {
    setStatus("status", "Không kết nối được server API (" + e.message + ")", "err");
    btn.disabled = false;
    return;
  }

  // Poll the login status until the callback arrives or the session times out.
  const deadline = Date.now() + 300000; // 5 min
  for (; ;) {
    await new Promise((r) => setTimeout(r, 2500));
    if (Date.now() > deadline) {
      setStatus("status", "Đăng nhập hết hạn — thử lại", "err");
      break;
    }
    try {
      const { status, data } = await apiFetch("/login/status");
      if (status !== 200 || !data) break;
      if (data.state === "done") {
        setStatus("status", "Đã đăng nhập " + (data.email || "(chưa rõ)"), "ok");
        await refreshAccounts();
        break;
      }
      if (data.state === "error") {
        setStatus("status", "Đăng nhập thất bại: " + (data.error || "lỗi không rõ"), "err");
        break;
      }
      if (data.state === "idle") {
        // Session was cleaned up from under us (TTL, etc.).
        setStatus("status", "Phiên đăng nhập đã bị đóng (thử lại)", "err");
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
    };
  }
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
      state.editImages = [{ b64: dataUrl, name }];
      state.editConversationId = item.conversationId || null;
      renderEditThumbs();
      $("tab-image").hidden = true;
      $("tab-edit").hidden = false;
      $("tab-mcp").hidden = true;
      $("view-history").hidden = true;
      document.querySelectorAll(".tab").forEach((t) =>
        t.classList.toggle("active", t.dataset.tab === "edit"));
      closePreview();
      $("editPrompt").focus();
      showToast("Đã chuyển ảnh sang tab SỬA ẢNH");
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
  meta.textContent = (rec.prompt || (rec.path ? rec.path.split(/[\\/]/).pop() : "") || "").slice(0, 40);
  card.appendChild(meta);
  const editBtn = document.createElement("button");
  editBtn.className = "btn tiny";
  editBtn.textContent = "Sửa tiếp";
  editBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    // Load the generated image back into the EDIT tab as its source.
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
        state.editImages = [{ b64: dataUrl, name: "last-result.png" }];
        // "Edit this" continues the generation that produced this card (the
        // server silently falls back to a fresh one if it rejects the id).
        state.editConversationId = rec.conversationId || null;
        renderEditThumbs();
        $("tab-image").hidden = true;
        $("tab-edit").hidden = false;
        $("tab-mcp").hidden = true;
        $("view-history").hidden = true;
        document.querySelectorAll(".tab").forEach((t) =>
          t.classList.toggle("active", t.dataset.tab === "edit"));
        $("editPrompt").focus();
      })
      .catch(() => {
        setStatus("status", "không tải được ảnh để sửa", "err");
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
  const items = res.paths.map((p, i) => {
    const fileUrl = res.files[i] || "";
    const fileId = fileUrl.includes("=") ? fileUrl.split("=")[1] : "";
    return {
      fileUrl: fileUrl,
      fileId: fileId,
      path: p,
      prompt: $("editPrompt") ? $("editPrompt").value.trim() : "",
      conversationId: res.conversationId || "",
    };
  });
  items.forEach((item, idx) => {
    grid.appendChild(makeCard(item, items, idx));
  });
  container.appendChild(grid);
}

$("genBtn").addEventListener("click", async () => {
  const prompts = splitPrompts($("prompt").value);
  if (!prompts.length) { setStatus("status", "viết mô tả ảnh trước", "err"); return; }
  const btn = $("genBtn");
  btn.disabled = true;
  const opts = {
    aspect: $("aspect").value,
    n: Number($("n").value),
    style: $("style").value,
    thinking: $("thinking").value,
    // A reference on the IMAGE tab is a STYLE reference: match the look,
    // don't copy content. The EDIT tab sends mode="edit" for real edits.
    mode: state.refImage ? "style" : "generate",
    ref_image: state.refImage ? state.refImage.b64 : undefined,
  };
  const grid = document.createElement("div");
  grid.className = "hist-grid";
  const statusEl = $("status");
  statusEl.textContent = "";
  statusEl.className = "status ok";
  statusEl.appendChild(grid);
  const allBatchItems = [];
  for (let i = 0; i < prompts.length; i++) {
    setStatus("status", `đang xử lý ${i + 1}/${prompts.length}: ${prompts[i].slice(0, 40)}`);
    try {
      const res = await generate(prompts[i], opts);
      const currentBatch = res.paths.map((p, idx) => {
        const fileUrl = res.files[idx] || "";
        const fileId = fileUrl.includes("=") ? fileUrl.split("=")[1] : "";
        return {
          fileUrl: fileUrl,
          fileId: fileId,
          path: p,
          prompt: prompts[i],
          conversationId: res.conversationId || "",
        };
      });
      currentBatch.forEach((item) => {
        allBatchItems.push(item);
        const cardIndex = allBatchItems.length - 1;
        grid.appendChild(makeCard(item, allBatchItems, cardIndex));
      });
      saveHistory(res, prompts[i]);
    } catch (err) {
      setStatus("status", `prompt ${i + 1} thất bại: ${err.message}`, "err");
      break;
    }
  }
  if (prompts.length > 1 && !statusEl.className.includes("err")) {
    setStatus("status", `xong ${prompts.length}/${prompts.length} ảnh`, "ok");
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
    saveHistory(res, prompt);
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
    const entries = res.paths.map((p, i) => {
      const fileUrl = res.files[i] || "";
      const fileId = fileUrl.includes("=") ? fileUrl.split("=")[1] : "";
      return {
        path: p,
        fileUrl: fileUrl,
        fileId: fileId,
        prompt: prompt || "",
        conversationId: res.conversationId || "",
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
tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    const name = tab.dataset.tab;
    ["image", "edit", "mcp"].forEach((t) => {
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
  $("tab-mcp").hidden = true;
  $("view-history").hidden = view !== "history";
  tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === "image"));
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
  await refreshAccounts();
  setStatus("status", state.accounts.length
    ? `${state.accounts.length} account — aigpt accounts để xem quota`
    : "chưa có account", "");
});

// ------------------------------------------------------------------ init

wireDropzone("dropzone", "refFile", "refImage");
wireDropzone("dropzoneEdit", "editFile", "editImages");
refreshAccounts();
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
