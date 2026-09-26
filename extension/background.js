// GPT Image Studio — MV3 service worker.
//
// - Opens the side panel when the toolbar icon is clicked (Chrome's native
//   side panel: works on every page, instantly, no page reload needed).
// - Watches tab navigations and catches the platform.openai.com OAuth callback
//   URL that `aigpt login --wait` is listening for. The callback navigation
//   happens even when the page renders "Oops!" — the address-bar URL is what
//   matters. We post ONLY that URL to the local receiver (127.0.0.1:8788);
//   we never read or send credentials.
// - Authenticates the callback POST with a per-login token obtained from
//   /handshake, so a rogue local process cannot spoof a callback.

const RECEIVER = "http://127.0.0.1:8788";

// Toolbar icon click => open the side panel. Registered at the top level so
// Chrome counts the click as a genuine user gesture (required for
// setPanelBehavior to take effect).
if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
}

async function getToken() {
  try {
    // Custom header: web pages cannot send it (CORS), and it proves the call
    // comes from extension code, not a rogue page spoofing an Origin.
    const resp = await fetch(`${RECEIVER}/handshake`, {
      headers: { "X-AIGPT-Nonce": "1" },
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    return typeof data.token === "string" && data.token.length >= 16
      ? data.token
      : null;
  } catch {
    return null;
  }
}

function setBadge(text, color) {
  chrome.action.setBadgeBackgroundColor({ color });
  chrome.action.setBadgeText({ text });
}

function isOAuthCallback(url) {
  try {
    const u = new URL(url);
    return (
      u.origin === "https://platform.openai.com" &&
      u.pathname === "/auth/callback" &&
      u.searchParams.has("code") &&
      u.searchParams.has("state")
    );
  } catch {
    return false;
  }
}

async function postCallback(url) {
  const token = await getToken();
  if (!token) {
    setBadge("!", "#ca8a04"); // receiver not running / no pending login
    return;
  }
  try {
    const resp = await fetch(`${RECEIVER}/callback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, token }),
    });
    const data = await resp.json();
    if (resp.ok) {
      setBadge("OK", "#16a34a");
    } else {
      setBadge("ERR", "#dc2626");
    }
  } catch {
    setBadge("!", "#ca8a04");
  }
  // Clear after 30 s so a later login can show a fresh state.
  setTimeout(() => chrome.action.setBadgeText({ text: "" }), 30000);
}

// The panel asks us to open the login tab. Only ever open the pending login's
// authorize URL on auth.openai.com — never arbitrary http(s), chrome:// or
// file:// URLs, so a compromised panel page cannot navigate Chrome itself.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "openTab" && typeof msg.url === "string") {
    try {
      const u = new URL(msg.url);
      const isAllowedAuth = u.origin === "https://auth.openai.com";
      const isAllowedGoogleAuth =
        u.origin === "https://accounts.google.com" && u.pathname === "/o/oauth2/v2/auth";
      const isAllowedLocalImage =
        u.origin === "http://127.0.0.1:8789" &&
        (u.pathname === "/file" || u.pathname.startsWith("/file/"));
      if (isAllowedAuth || isAllowedGoogleAuth || isAllowedLocalImage) {
        chrome.tabs.create({ url: msg.url });
        sendResponse({ ok: true });
        return;
      }
    } catch (e) {
      // fall through
    }
    sendResponse({ ok: false });
  }
});

const posted = new Set(); // dedup: onUpdated + onCommitted can both fire

function catchCallback(url) {
  if (!url || !isOAuthCallback(url) || posted.has(url)) return;
  posted.add(url);
  postCallback(url);
  setTimeout(() => posted.delete(url), 60000);
}

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  catchCallback(changeInfo.url || tab.url);
});

if (chrome.webNavigation && chrome.webNavigation.onCommitted) {
  chrome.webNavigation.onCommitted.addListener((details) => {
    if (details.frameId === 0) catchCallback(details.url);
  });
}
