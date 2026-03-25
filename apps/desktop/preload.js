const { contextBridge } = require("electron");

function readArg(prefix) {
  const hit = process.argv.find((arg) => arg.startsWith(prefix));
  if (!hit) return "";
  return hit.slice(prefix.length);
}

const apiBase = readArg("--api-base=");

contextBridge.exposeInMainWorld("desktopEnv", {
  isElectron: true,
  apiBase,
});
