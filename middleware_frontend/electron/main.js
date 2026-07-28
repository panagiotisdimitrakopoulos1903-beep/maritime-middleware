/**
 * electron/main.js — Electron main process
 *
 * Creates a frameless, always-on-top panel that docks to the right
 * side of the broker's screen. Communicates with the Python backend
 * on localhost:5000 via HTTP and WebSocket.
 */
const { app, BrowserWindow, screen, ipcMain, shell } = require("electron");
const path = require("path");

const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:5000";
const isDev = process.env.NODE_ENV === "development" || !app.isPackaged;

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
