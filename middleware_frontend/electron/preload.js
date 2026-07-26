/**
 * electron/preload.js — secure context bridge
 *
 * Exposes a minimal, typed API to the React renderer.
 * Nothing from Node.js leaks through — only these explicit methods.
 */
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronAPI", {
  getBackendUrl: () => ipcRenderer.invoke("get-backend-url"),
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
  minimizeWindow: () => ipcRenderer.invoke("window-minimize"),
  dragWindow: (delta) => ipcRenderer.invoke("window-drag", delta),
});
