const api = globalThis.browser || globalThis.chrome;
const promiseApi = Boolean(globalThis.browser);

const powerButton = document.getElementById("power");
const identifierNode = document.getElementById("identifier");
const regenerateButton = document.getElementById("regenerate");
const versionNode = document.getElementById("version");

function runtimeLastError() {
  return globalThis.chrome && chrome.runtime ? chrome.runtime.lastError : null;
}

function sendMessage(message) {
  if (promiseApi) {
    return api.runtime.sendMessage(message);
  }

  return new Promise((resolve, reject) => {
    api.runtime.sendMessage(message, (response) => {
      const error = runtimeLastError();
      if (error) {
        reject(new Error(error.message));
        return;
      }
      resolve(response);
    });
  });
}

function render(state) {
  powerButton.classList.toggle("is-on", Boolean(state.enabled));
  powerButton.classList.toggle("is-connected", Boolean(state.connected));
  powerButton.setAttribute("aria-pressed", state.enabled ? "true" : "false");
  identifierNode.textContent = state.identifier || "-";
}

async function refresh() {
  const manifest = api.runtime.getManifest();
  versionNode.textContent = `v${manifest.version}`;
  render(await sendMessage({ type: "get_state" }));
}

powerButton.addEventListener("click", async () => {
  const current = await sendMessage({ type: "get_state" });
  render(await sendMessage({ type: "set_enabled", enabled: !current.enabled }));
});

regenerateButton.addEventListener("click", async () => {
  render(await sendMessage({ type: "regenerate_identifier" }));
});

void refresh();
