const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronAPI", {
  close:          ()      => ipcRenderer.send("close-window"),
  minimize:       ()      => ipcRenderer.send("minimize-window"),
  pin:            (state) => ipcRenderer.send("toggle-pin", state),
  restartBackend: ()      => ipcRenderer.send("restart-backend"),
  onGlobalTrigger:      (cb)     => ipcRenderer.on("global-trigger", cb),
  setIgnoreMouseEvents: (ignore) => ipcRenderer.send("set-ignore-mouse-events", ignore),
});
