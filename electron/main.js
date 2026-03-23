const { app, BrowserWindow, dialog } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const http = require("http");
const net = require("net");

const API_HOST = "127.0.0.1";
const API_PORT = Number(process.env.ELECTRON_API_PORT || 8100);
const API_HEALTH_URL = `http://${API_HOST}:${API_PORT}/api/health`;
const RENDERER_URL = process.env.ELECTRON_RENDERER_URL || "http://127.0.0.1:5173";
const API_BASE = process.env.ELECTRON_API_BASE || `http://${API_HOST}:${API_PORT}`;
const PYTHON_CANDIDATES = Array.from(
  new Set([
    process.env.PYTHON || "",
    process.platform === "win32" ? "python" : "python3",
    process.platform === "win32" ? "python3" : "python",
  ].filter(Boolean)),
);

let mainWindow = null;
let backendProcess = null;
let ownsBackendProcess = false;
let quitting = false;
let backendReady = false;
let bootPromise = null;
/** 串行化 ensureBackendReady，避免并发下二次 spawn 覆盖 backendProcess */
let ensureBackendPromise = null;
let backendStderrBuf = "";

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isPortOpen(host, port) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    socket.setTimeout(800);
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("timeout", () => {
      socket.destroy();
      resolve(false);
    });
    socket.once("error", () => {
      socket.destroy();
      resolve(false);
    });
    socket.connect(port, host);
  });
}

function httpReady(url) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => {
      res.resume();
      resolve(res.statusCode && res.statusCode >= 200 && res.statusCode < 500);
    });
    req.on("error", () => resolve(false));
    req.setTimeout(1200, () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function waitUntil(url, timeoutMs = 60000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await httpReady(url)) return true;
    await wait(600);
  }
  return false;
}

/** 在极短时间内多次探测，降低「端口刚被占用但尚未 listen」的竞态 */
async function detectExistingBackend(maxWaitMs = 2500) {
  const start = Date.now();
  while (Date.now() - start < maxWaitMs) {
    if (await httpReady(API_HEALTH_URL)) return true;
    if (await isPortOpen(API_HOST, API_PORT)) {
      if (await httpReady(API_HEALTH_URL)) return true;
    }
    await wait(200);
  }
  return false;
}

function spawnBackend() {
  const cwd = path.resolve(__dirname, "..");
  backendStderrBuf = "";
  const args = ["-m", "uvicorn", "api.server:app", "--host", API_HOST, "--port", String(API_PORT)];
  const spawnOpts = {
    cwd,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  };

  const supersededProcesses = new WeakSet();

  const spawnWithCandidate = (index) => {
    const pythonBin = PYTHON_CANDIDATES[index];
    if (!pythonBin) {
      throw new Error("Unable to locate Python interpreter. Set PYTHON environment variable.");
    }
    const proc = spawn(pythonBin, args, spawnOpts);
    backendProcess = proc;
    ownsBackendProcess = true;

    proc.once("error", (err) => {
      if (err && err.code === "ENOENT" && index + 1 < PYTHON_CANDIDATES.length) {
        supersededProcesses.add(proc);
        spawnWithCandidate(index + 1);
        return;
      }
      console.error(`[api] failed to start backend with ${pythonBin}:`, err);
    });

    proc.stdout.on("data", (chunk) => {
      process.stdout.write(`[api] ${chunk}`);
    });
    proc.stderr.on("data", (chunk) => {
      const s = chunk.toString();
      backendStderrBuf = (backendStderrBuf + s).slice(-8000);
      process.stderr.write(`[api] ${chunk}`);
    });
    proc.on("exit", (code, signal) => {
      if (!quitting && !supersededProcesses.has(proc)) {
        console.error(`[api] exited unexpectedly, code=${code}, signal=${signal || "none"}`);
      }
    });
  };

  spawnWithCandidate(0);
}

function stderrLooksLikeAddrInUse(buf) {
  const t = (buf || "").toLowerCase();
  return (
    t.includes("errno 48") ||
    t.includes("eaddrinuse") ||
    t.includes("address already in use")
  );
}

async function ensureBackendReady() {
  if (backendReady) {
    if (await httpReady(API_HEALTH_URL)) return;
    backendReady = false;
  }

  if (ensureBackendPromise) {
    await ensureBackendPromise;
    return;
  }

  ensureBackendPromise = (async () => {
    try {
      if (await httpReady(API_HEALTH_URL)) {
        ownsBackendProcess = false;
        backendReady = true;
        return;
      }

      const existing = await detectExistingBackend();
      if (existing) {
        ownsBackendProcess = false;
        const ok = await waitUntil(API_HEALTH_URL, 90000);
        if (!ok) {
          throw new Error(`Backend health check timeout: ${API_HEALTH_URL}`);
        }
        backendReady = true;
        return;
      }

      if (backendProcess && backendProcess.exitCode !== null) {
        backendProcess = null;
        ownsBackendProcess = false;
      }

      if (!backendProcess) {
        spawnBackend();
      }

      const ok = await waitUntil(API_HEALTH_URL, 90000);
      if (ok) {
        backendReady = true;
        return;
      }

      if (
        backendProcess &&
        ownsBackendProcess &&
        backendProcess.exitCode !== null &&
        stderrLooksLikeAddrInUse(backendStderrBuf)
      ) {
        backendProcess = null;
        ownsBackendProcess = false;
        if (await waitUntil(API_HEALTH_URL, 15000)) {
          backendReady = true;
          return;
        }
      }

      throw new Error(`Backend health check timeout: ${API_HEALTH_URL}`);
    } finally {
      ensureBackendPromise = null;
    }
  })();

  await ensureBackendPromise;
}

function stopBackend() {
  if (!backendProcess || !ownsBackendProcess) {
    backendProcess = null;
    ownsBackendProcess = false;
    return;
  }
  try {
    backendProcess.kill("SIGTERM");
  } catch (err) {
    console.warn("[api] failed to stop backend:", err);
  }
  backendProcess = null;
  ownsBackendProcess = false;
  backendReady = false;
}

function createWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.focus();
    return;
  }
  mainWindow = new BrowserWindow({
    width: 1500,
    height: 980,
    minWidth: 1180,
    minHeight: 760,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: true,
      sandbox: false,
      additionalArguments: [`--api-base=${API_BASE}`],
    },
  });
  mainWindow.loadURL(RENDERER_URL);
}

async function boot() {
  if (bootPromise) return bootPromise;
  bootPromise = (async () => {
    try {
      await ensureBackendReady();
      createWindow();
    } catch (err) {
      dialog.showErrorBox("Electron 启动失败", String(err instanceof Error ? err.message : err));
      app.quit();
    } finally {
      bootPromise = null;
    }
  })();
  try {
    await bootPromise;
  } catch {
    /* handled above */
  }
}

app.whenReady().then(boot);

app.on("before-quit", () => {
  quitting = true;
  stopBackend();
});

app.on("window-all-closed", () => {
  stopBackend();
  if (process.platform !== "darwin") app.quit();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    void boot();
  }
});
