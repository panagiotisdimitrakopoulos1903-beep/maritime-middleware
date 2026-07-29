/**
 * electron/main.js — Electron main process
 *
 * Creates a frameless, always-on-top panel that docks to the right
 * side of the broker's screen. Communicates with the Python backend
 * on localhost:5000 via HTTP and WebSocket.
 */
const { app, BrowserWindow, screen, ipcMain, shell } = require("electron");
const path = require("path");
const fs = require("fs");

const DEFAULT_BACKEND_URL = "http://127.0.0.1:5000";

/**
 * Resolve the backend URL the panel should talk to.
 *
 * Precedence (highest to lowest):
 *   1. process.env.BACKEND_URL — keeps the terminal-based dev workflow
 *      working unchanged (e.g. `BACKEND_URL=http://staging:5000 npm run electron`).
 *   2. config.json in the Electron userData directory — lets a broker/IT
 *      person point a packaged, Finder-launched app at a non-default
 *      backend without any environment variable, which GUI-launched apps
 *      on macOS do not reliably inherit from an interactive shell.
 *   3. DEFAULT_BACKEND_URL — hardcoded fallback, zero-config out of the box.
 *
 * Returns { url, source } where source is one of "env var", "config file",
 * or "default" — used only for the startup log line.
 */
function resolveBackendUrl() {
  if (process.env.BACKEND_URL) {
    return { url: process.env.BACKEND_URL, source: "env var" };
  }

  const configPath = path.join(app.getPath("userData"), "config.json");

  let raw;
  try {
    raw = fs.readFileSync(configPath, "utf8");
  } catch (readErr) {
    if (readErr.code === "ENOENT") {
      // No config file yet — create one with the default so the app
      // works out of the box, and so there's something for a human to
      // find and edit later.
      try {
        fs.writeFileSync(
          configPath,
          JSON.stringify({ backendUrl: DEFAULT_BACKEND_URL }, null, 2) + "\n",
          "utf8"
        );
        console.log(`[maritime-panel] no config.json found; created default at ${configPath}`);
      } catch (writeErr) {
        console.warn(
          `[maritime-panel] could not create config.json at ${configPath}: ${writeErr.message}. Falling back to in-memory default for this session.`
        );
      }
    } else {
      // Filesystem exists but couldn't be read (permissions, read-only FS, etc).
      console.warn(
        `[maritime-panel] could not read config.json at ${configPath}: ${readErr.message}. Falling back to in-memory default for this session.`
      );
    }
    return { url: DEFAULT_BACKEND_URL, source: "default" };
  }

  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (parseErr) {
    console.warn(
      `[maritime-panel] config.json at ${configPath} is not valid JSON (${parseErr.message}). Leaving the file untouched and falling back to in-memory default for this session.`
    );
    return { url: DEFAULT_BACKEND_URL, source: "default" };
  }

  if (typeof parsed.backendUrl !== "string" || parsed.backendUrl.length === 0) {
    console.warn(
      `[maritime-panel] config.json at ${configPath} is missing a valid "backendUrl" string field. Leaving the file untouched and falling back to in-memory default for this session.`
    );
    return { url: DEFAULT_BACKEND_URL, source: "default" };
  }

  return { url: parsed.backendUrl, source: "config file" };
}

const { url: BACKEND_URL, source: backendUrlSource } = resolveBackendUrl();
const isDev = process.env.NODE_ENV === "development" || !app.isPackaged;

console.log(`[maritime-panel] resolved BACKEND_URL=${BACKEND_URL} (source=${backendUrlSource}, isPackaged=${app.isPackaged}, isDev=${isDev})`);

let mainWindow = null;

function createWindow() {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;

  // Panel dimensions — right-side dock
  const PANEL_WIDTH = 420;
  const PANEL_HEIGHT = height;
  const PANEL_X = width - PANEL_WIDTH;
  const PANEL_Y = 0;

  mainWindow = new BrowserWindow({
    x: PANEL_X,
    y: PANEL_Y,
    width: PANEL_WIDTH,
    height: PANEL_HEIGHT,
    frame: false,           // no OS title bar
    alwaysOnTop: true,      // stays above WT3
    resizable: false,
    skipTaskbar: false,
    transparent: false,
    backgroundColor: "#0d1117",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // In development, load from React dev server
  // In production, load built index.html
  if (isDev) {
    mainWindow.loadURL("http://localhost:3000");
    mainWindow.webContents.openDevTools({ mode: "detach" });
  } else {
    mainWindow.loadFile(path.join(__dirname, "../build/index.html"));
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

// ── IPC handlers — called from React via window.electronAPI ──────────────────

// Allow React to open Signal Ocean or WT3 links in the default browser
ipcMain.handle("open-external", async (event, url) => {
  await shell.openExternal(url);
});

// Allow React to read the backend URL
ipcMain.handle("get-backend-url", () => BACKEND_URL);

// Allow panel to be dragged (frameless window needs custom drag)
ipcMain.handle("window-drag", (event, { deltaX, deltaY }) => {
  if (!mainWindow) return;
  const [x, y] = mainWindow.getPosition();
  mainWindow.setPosition(x + deltaX, y + deltaY);
});

// Minimise to taskbar
ipcMain.handle("window-minimize", () => {
  mainWindow?.minimize();
});

// ── App lifecycle ─────────────────────────────────────────────────────────────

app.whenReady().then(() => {
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
