const { app, BrowserWindow, ipcMain, globalShortcut, screen } = require("electron");
const { spawn } = require("child_process");
const path  = require("path");
const fs    = require("fs");

const ROOT   = path.join(__dirname, "..");
const IS_DEV = process.argv.includes("--dev");
let win     = null;
let backend = null;

// ─── Resolve Python executable ────────────────────────────────────────────────
// "python" is often not in PATH when launched from cmd.exe or an elevated UAC
// session. Try several well-known locations in order; first one that exists wins.
function resolvePython() {
  const candidates = [
    // uv-managed env (has all pip packages) — found via `uv run`
    // We call uv directly so it picks the right venv automatically.
    // Key: uv.exe location on this machine (WinGet install)
    path.join(process.env.LOCALAPPDATA || "", "Microsoft", "WinGet", "Packages",
              "astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe", "uv.exe"),
    // Microsoft Store Python stubs (user-level, works in non-elevated sessions)
    path.join(process.env.LOCALAPPDATA || "", "Microsoft", "WindowsApps",
              "PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0", "python.exe"),
    // Official Python installer location
    path.join(process.env.LOCALAPPDATA || "", "Programs", "Python", "Python311", "python.exe"),
    path.join(process.env.LOCALAPPDATA || "", "Programs", "Python", "Python310", "python.exe"),
  ];

  for (const p of candidates) {
    if (fs.existsSync(p)) {
      console.log("[Jarvis] Python resolver: using", p);
      return p;
    }
  }
  // Last resort: hope "python" is on PATH
  console.log("[Jarvis] Python resolver: falling back to 'python' on PATH");
  return null;  // null = use "python"
}

// ─── Backend lifecycle ────────────────────────────────────────────────────────

function startBackend() {
  const envPath = path.join(ROOT, ".env");
  if (!fs.existsSync(envPath)) {
    console.error("[Jarvis] .env not found — run install.bat first");
    app.quit();
    return;
  }

  const pythonExe = resolvePython();
  let cmd, backendArgs;

  if (pythonExe && pythonExe.endsWith("uv.exe")) {
    // uv run python — uv finds the correct virtualenv automatically
    cmd = pythonExe;
    backendArgs = ["run", "python", path.join(ROOT, "backend", "main.py")];
  } else {
    cmd = pythonExe || "python";
    backendArgs = [path.join(ROOT, "backend", "main.py")];
  }
  if (IS_DEV) backendArgs.push("--dev");

  console.log("[Jarvis] Starting backend" + (IS_DEV ? " [DEV]" : "") + "...");
  backend = spawn(cmd, backendArgs, {
    cwd:   ROOT,
    stdio: "inherit",   // backend logs appear in this terminal
  });

  backend.on("error", (err) => {
    console.error("[Jarvis] Backend failed to start:", err.message);
  });

  backend.on("exit", (code, signal) => {
    console.log(`[Jarvis] Backend stopped  code=${code}  signal=${signal}`);
  });
}

function stopBackend() {
  if (backend && !backend.killed) {
    console.log("[Jarvis] Stopping backend…");
    if (process.platform === "win32" && backend.pid) {
      // Kill the entire process tree so uv.exe + its python child both die.
      // backend.kill() only kills the immediate child (uv.exe), leaving
      // Python running with the mic open.
      try {
        require("child_process").execSync(
          `taskkill /F /T /PID ${backend.pid}`,
          { stdio: "ignore" }
        );
      } catch (_) { /* already gone */ }
    } else {
      backend.kill();
    }
    backend = null;
  }
}

// ─── Window ───────────────────────────────────────────────────────────────────

function createWindow() {
  win = new BrowserWindow({
    width:    920,
    height:   640,
    minWidth: 680,
    minHeight:500,
    frame:       false,        // custom title bar in HTML
    transparent: false,
    resizable:   true,
    backgroundColor: "#03081a",
    webPreferences: {
      preload:          path.join(__dirname, "preload.js"),
      nodeIntegration:  false,
      contextIsolation: true,
    },
  });

  win.loadFile("index.html");

  if (IS_DEV) {
    win.webContents.openDevTools({ mode: "detach" });
  }

  win.on("closed", () => { win = null; });
}

// ─── App lifecycle ────────────────────────────────────────────────────────────

app.whenReady().then(() => {
  startBackend();

  // Give backend 2 s to bind the port before the frontend tries to connect
  setTimeout(() => {
    createWindow();

    // ── Frontend hot-reload in dev mode ──────────────────────────────────────
    if (IS_DEV) {
      const watchFiles = ["renderer.js", "styles.css", "index.html"];
      let reloadTimer = null;

      watchFiles.forEach((file) => {
        const full = path.join(__dirname, file);
        fs.watch(full, () => {
          // Debounce: some editors write files twice in quick succession
          clearTimeout(reloadTimer);
          reloadTimer = setTimeout(() => {
            if (win) {
              console.log(`[Jarvis][DEV] ${file} changed — reloading renderer`);
              win.webContents.reload();
            }
          }, 150);
        });
      });

      console.log("[Jarvis][DEV] Watching frontend files for changes...");
    }
  }, 2000);

  globalShortcut.register("CommandOrControl+Shift+J", () => {
    if (win) win.webContents.send("global-trigger");
  });
});

app.on("window-all-closed", () => {
  stopBackend();
  globalShortcut.unregisterAll();
  app.quit();
});

app.on("before-quit", () => stopBackend());

// ─── IPC ──────────────────────────────────────────────────────────────────────

ipcMain.on("close-window",   () => { stopBackend(); app.quit(); });
ipcMain.on("minimize-window",() => { if (win) win.minimize(); });
ipcMain.on("toggle-pin", (_, pin) => { if (win) win.setAlwaysOnTop(pin); });

// Click-through toggle: renderer sends this when mouse enters/leaves interactive zones
ipcMain.on("set-ignore-mouse-events", (_, ignore) => {
  if (win) win.setIgnoreMouseEvents(ignore, { forward: true });
});

// Backend hot-restart: triggered by Jarvis after editing Python source files
ipcMain.on("restart-backend", () => {
  console.log("[Jarvis] Restarting backend for code reload…");
  stopBackend();
  setTimeout(() => startBackend(), 2000);   // give the killed tree time to release the port
});
