/**
 * electron/backendUrl.js — resolves the backend URL for the Electron main
 * process.
 *
 * Extracted from main.js so it can be unit tested without requiring a real
 * (or fully mocked) Electron runtime — this module only touches
 * `electron`'s `app.getPath` and Node's `fs`/`path`, a much smaller surface
 * than main.js's BrowserWindow/screen/ipcMain/shell/app.whenReady usage.
 *
 * This is a pure extraction — the precedence logic, logging, and
 * file-handling behavior are unchanged from what was previously inline in
 * main.js (empirically verified against a real packaged app).
 */
const { app } = require("electron");
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

module.exports = { resolveBackendUrl, DEFAULT_BACKEND_URL };
