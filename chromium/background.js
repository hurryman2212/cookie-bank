const HOST_NAME = "com.cookiebank.adapter";
const api = globalThis.browser || globalThis.chrome;
const promiseApi = Boolean(globalThis.browser);
const DEFAULT_ENABLED = true;

let nativePort = null;
let reconnectTimer = null;
let status = {
  connected: false,
  lastError: null,
};

function runtimeLastError() {
  return globalThis.chrome && chrome.runtime ? chrome.runtime.lastError : null;
}

function withCallback(invoker) {
  return new Promise((resolve, reject) => {
    try {
      invoker((result) => {
        const error = runtimeLastError();
        if (error) {
          reject(new Error(error.message));
          return;
        }
        resolve(result);
      });
    } catch (error) {
      reject(error);
    }
  });
}

function storageGet(defaults) {
  if (promiseApi) {
    return api.storage.local.get(defaults);
  }
  return withCallback((done) => api.storage.local.get(defaults, done));
}

function storageSet(values) {
  if (promiseApi) {
    return api.storage.local.set(values);
  }
  return withCallback((done) => api.storage.local.set(values, done));
}

function storageRemove(keys) {
  if (promiseApi) {
    return api.storage.local.remove(keys);
  }
  return withCallback((done) => api.storage.local.remove(keys, done));
}

function uuid() {
  if (globalThis.crypto && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }

  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10).join(""),
  ].join("-");
}

async function ensureIdentifier() {
  const values = await storageGet({ identifier: null });
  if (values.identifier) {
    return values.identifier;
  }

  const identifier = uuid();
  await storageSet({ identifier });
  return identifier;
}

async function browserInfo() {
  const base = {
    userAgent: navigator.userAgent,
    extensionId: api.runtime.id,
  };

  if (!api.runtime.getBrowserInfo) {
    return base;
  }

  if (promiseApi) {
    try {
      return { ...base, ...(await api.runtime.getBrowserInfo()) };
    } catch (_) {
      return base;
    }
  }

  return base;
}

async function currentState() {
  const values = await storageGet({ enabled: DEFAULT_ENABLED, identifier: null });
  return {
    enabled: Boolean(values.enabled),
    identifier: values.identifier || (await ensureIdentifier()),
    connected: status.connected,
    lastError: status.lastError,
  };
}

async function registerNativePort() {
  if (!nativePort) {
    return;
  }

  const identifier = await ensureIdentifier();
  nativePort.postMessage({
    type: "extension_register",
    identifier,
    extension_id: api.runtime.id,
    browser_info: await browserInfo(),
  });
}

function connectNativePort() {
  if (nativePort) {
    return;
  }

  clearTimeout(reconnectTimer);
  reconnectTimer = null;

  try {
    nativePort = api.runtime.connectNative(HOST_NAME);
  } catch (error) {
    status = { connected: false, lastError: error.message };
    scheduleReconnect();
    return;
  }

  status = { connected: true, lastError: null };

  nativePort.onMessage.addListener((message) => {
    void handleNativeMessage(message);
  });

  nativePort.onDisconnect.addListener(() => {
    const error = runtimeLastError();
    nativePort = null;
    status = {
      connected: false,
      lastError: error ? error.message : null,
    };
    scheduleReconnect();
  });

  void registerNativePort();
}

async function scheduleReconnect() {
  const values = await storageGet({ enabled: DEFAULT_ENABLED });
  if (!values.enabled || reconnectTimer) {
    return;
  }

  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectNativePort();
  }, 3000);
}

function disconnectNativePort() {
  clearTimeout(reconnectTimer);
  reconnectTimer = null;

  if (!nativePort) {
    status.connected = false;
    return;
  }

  try {
    nativePort.postMessage({ type: "extension_disconnect" });
    nativePort.disconnect();
  } finally {
    nativePort = null;
    status.connected = false;
  }
}

async function setEnabled(enabled) {
  await storageSet({ enabled: Boolean(enabled) });
  if (enabled) {
    connectNativePort();
  } else {
    disconnectNativePort();
  }
  return currentState();
}

function cookieUrl(cookie) {
  if (cookie.url) {
    return cookie.url;
  }

  const rawDomain = cookie.domain || cookie.host || cookie.domainName;
  if (!rawDomain) {
    throw new Error(`Cookie ${cookie.name || "(unnamed)"} is missing domain/url`);
  }

  const domain = String(rawDomain).replace(/^\./, "");
  const path = cookie.path
    ? String(cookie.path).startsWith("/")
      ? String(cookie.path)
      : `/${cookie.path}`
    : "/";
  const scheme = cookie.secure ? "https" : "http";
  return `${scheme}://${domain}${path}`;
}

function sameSiteValue(cookie) {
  const value =
    cookie.sameSite ||
    cookie.samesite ||
    (cookie.rest && (cookie.rest.SameSite || cookie.rest.sameSite));
  if (!value) {
    return undefined;
  }

  const normalized = String(value).toLowerCase().replace(/[-_ ]/g, "");
  if (["none", "no", "norestriction"].includes(normalized)) {
    return "no_restriction";
  }
  if (normalized === "lax") {
    return "lax";
  }
  if (normalized === "strict") {
    return "strict";
  }
  if (normalized === "unspecified") {
    return "unspecified";
  }
  return undefined;
}

function expirationDate(cookie) {
  const value = cookie.expirationDate ?? cookie.expires ?? cookie.expiry;
  if (value === undefined || value === null || value === "") {
    return undefined;
  }

  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return undefined;
  }
  return numeric > 100000000000 ? Math.floor(numeric / 1000) : numeric;
}

function httpOnlyValue(cookie) {
  if (cookie.httpOnly !== undefined) {
    return Boolean(cookie.httpOnly);
  }
  if (cookie.httponly !== undefined) {
    return Boolean(cookie.httponly);
  }
  if (!cookie.rest) {
    return undefined;
  }
  if (cookie.rest.HttpOnly !== undefined || cookie.rest.httponly !== undefined) {
    return true;
  }
  return undefined;
}

function setCookie(cookie) {
  const details = {
    url: cookieUrl(cookie),
    name: String(cookie.name),
    value: String(cookie.value),
  };

  if (cookie.domain) {
    details.domain = String(cookie.domain);
  }
  if (cookie.path) {
    details.path = String(cookie.path);
  }
  if (cookie.secure !== undefined) {
    details.secure = Boolean(cookie.secure);
  }

  const httpOnly = httpOnlyValue(cookie);
  if (httpOnly !== undefined) {
    details.httpOnly = httpOnly;
  }

  const sameSite = sameSiteValue(cookie);
  if (sameSite) {
    details.sameSite = sameSite;
  }

  const expires = expirationDate(cookie);
  if (expires !== undefined && !cookie.discard) {
    details.expirationDate = expires;
  }

  if (cookie.storeId) {
    details.storeId = String(cookie.storeId);
  }

  if (promiseApi) {
    return api.cookies.set(details);
  }
  return withCallback((done) => api.cookies.set(details, done));
}

function removeCookie(cookie) {
  if (!cookie.url) {
    throw new Error(`Cookie ${cookie.name || "(unnamed)"} is missing url`);
  }

  const details = {
    url: String(cookie.url),
    name: String(cookie.name),
  };
  if (promiseApi) {
    return api.cookies.remove(details);
  }
  return withCallback((done) => api.cookies.remove(details, done));
}

async function applyCookie(cookie, index) {
  if (!cookie || typeof cookie !== "object") {
    throw new Error(`Cookie at index ${index} is not an object`);
  }
  if (!cookie.name) {
    throw new Error(`Cookie at index ${index} is missing name`);
  }

  const hasValue = Object.prototype.hasOwnProperty.call(cookie, "value");
  if (!hasValue || cookie.value === null) {
    const removed = await removeCookie(cookie);
    return { index, name: cookie.name, action: "remove", ok: Boolean(removed) };
  }

  const written = await setCookie(cookie);
  return { index, name: cookie.name, action: "set", ok: Boolean(written) };
}

async function applyCookies(request) {
  const cookies = Array.isArray(request.cookies) ? request.cookies : [];
  const results = [];
  const errors = [];

  for (let index = 0; index < cookies.length; index += 1) {
    try {
      results.push(await applyCookie(cookies[index], index));
    } catch (error) {
      errors.push({
        index,
        name: cookies[index] && cookies[index].name,
        message: error.message,
      });
    }
  }

  return {
    type: "apply_result",
    request_id: request.request_id,
    ok: errors.length === 0,
    applied: results.length,
    failed: errors.length,
    results,
    errors,
  };
}

async function handleNativeMessage(message) {
  if (message.type === "apply_cookies") {
    const result = await applyCookies(message);
    if (nativePort) {
      nativePort.postMessage(result);
    }
  }
}

api.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  void (async () => {
    if (message.type === "get_state") {
      sendResponse(await currentState());
    } else if (message.type === "set_enabled") {
      sendResponse(await setEnabled(message.enabled));
    } else if (message.type === "regenerate_identifier") {
      await storageRemove("identifier");
      const identifier = await ensureIdentifier();
      if (nativePort) {
        await registerNativePort();
      }
      sendResponse({ ...(await currentState()), identifier });
    } else {
      sendResponse({ error: "unknown_message" });
    }
  })();
  return true;
});

api.runtime.onStartup.addListener(() => {
  void currentState().then((state) => {
    if (state.enabled) {
      connectNativePort();
    }
  });
});

api.runtime.onInstalled.addListener((details) => {
  void (async () => {
    if (!details || details.reason === "install") {
      await storageSet({ enabled: DEFAULT_ENABLED });
    }
    await ensureIdentifier();
  })();
});

void currentState().then((state) => {
  if (state.enabled) {
    connectNativePort();
  }
});
