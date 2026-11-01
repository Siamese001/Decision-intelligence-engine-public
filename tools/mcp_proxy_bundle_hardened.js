/**
 * Unified Hardened MCP Stdio Proxy Server.
 *
 * Provides resilient, zero-hang proxying between Antigravity Language Server and
 * background IPC Unix sockets / remote HTTP endpoints.
 *
 * Key Invariants:
 * 1. Fast-polling retry (50ms base) connecting in <100ms once socket appears.
 * 2. Handshake Shielding: Immediate response to initialize preventing Go client context cancellation.
 * 3. Multi-path socket discovery (resolves across os.tmpdir(), /tmp, and platform variants).
 * 4. Stdin early buffering without packet loss.
 * 5. Reconnection resilience without premature process termination.
 * 6. Remote HTTP OAuth mode support for Google Cloud ADC.
 */
const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");
const readline = require("readline");

const BASE_RETRY_MS = 50;
const MAX_RETRY_MS = 250;
const MAX_ATTEMPTS = 60;

const EMBEDDED_TOOL_SCHEMAS = {
  notebooks: [
    { name: "create_notebook", description: "Create a new notebook file in the workspace and open it.", inputSchema: { type: "object", properties: { directory: { type: "string" }, filename: { type: "string" } }, required: ["directory", "filename"] } },
    { name: "insert_markdown_cell", description: "Insert a markdown cell into a notebook (.ipynb) at index.", inputSchema: { type: "object", properties: { path: { type: "string" }, index: { type: "integer" }, markdown: { type: "string" } }, required: ["path", "index", "markdown"] } },
    { name: "insert_code_cell", description: "Insert a code cell into a notebook (.ipynb) at index.", inputSchema: { type: "object", properties: { path: { type: "string" }, index: { type: "integer" }, code: { type: "string" }, languageId: { type: "string" } }, required: ["path", "index", "code"] } },
    { name: "replace_cell", description: "Replace content of an existing cell at index in a notebook (.ipynb).", inputSchema: { type: "object", properties: { path: { type: "string" }, index: { type: "integer" }, code: { type: "string" }, languageId: { type: "string" } }, required: ["path", "index", "code"] } },
    { name: "delete_cell", description: "Delete a cell at index from a notebook (.ipynb).", inputSchema: { type: "object", properties: { path: { type: "string" }, index: { type: "integer" } }, required: ["path", "index"] } },
    { name: "get_notebook_info", description: "Get cell counts, kernel metadata, and modification info for a notebook (.ipynb).", inputSchema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] } },
    { name: "read_cell", description: "Read a specific cell by index: type, source, and outputs.", inputSchema: { type: "object", properties: { path: { type: "string" }, index: { type: "integer" } }, required: ["path", "index"] } },
    { name: "list_cells", description: "List cells in a notebook with index, type, preview, and execution status.", inputSchema: { type: "object", properties: { path: { type: "string" }, type_filter: { type: "string" }, preview_length: { type: "integer" } }, required: ["path"] } },
    { name: "search_cells", description: "Search notebook cells for text matching query.", inputSchema: { type: "object", properties: { path: { type: "string" }, query: { type: "string" }, type_filter: { type: "string" }, case_sensitive: { type: "boolean" } }, required: ["path", "query"] } },
    { name: "get_cell_range", description: "Read a range of cells from a notebook from start_index to end_index.", inputSchema: { type: "object", properties: { path: { type: "string" }, start_index: { type: "integer" }, end_index: { type: "integer" } }, required: ["path", "start_index", "end_index"] } },
    { name: "get_cell_outputs", description: "Read outputs from a code cell by index.", inputSchema: { type: "object", properties: { path: { type: "string" }, index: { type: "integer" } }, required: ["path", "index"] } }
  ],
  visualization: [
    { name: "render_chart", description: "Renders an Apache ECharts V5 specification into an SVG chart.", inputSchema: { type: "object", properties: { spec: { type: "object" }, width: { type: "number" }, height: { type: "number" } }, required: ["spec"] } }
  ]
};

function getToolCategory(target) {
  const l = target.toLowerCase();
  return l.includes("notebook") ? "notebooks" : l.includes("visual") ? "visualization" : "general";
}

function resolveCandidateSocketPaths(target) {
  if (path.isAbsolute(target) || target.startsWith("\\\\?\\pipe\\")) return [target];
  if (process.platform === "win32") return [path.join("\\\\?\\pipe\\", `datacloud-mcp-${target}`)];
  const tmpDirs = [os.tmpdir(), "/tmp"];
  const names = new Set([`datacloud-mcp-${target}.sock`, `datacloud-mcp-${target}`]);
  if (target.includes("-antigravityide")) names.add(`datacloud-mcp-${target.replace("-antigravityide", "")}.sock`);
  else names.add(`datacloud-mcp-${target}-antigravityide.sock`);

  const results = [];
  for (const d of tmpDirs) {
    if (!d) continue;
    for (const n of names) results.push(path.join(d, n));
    try {
      if (fs.existsSync(d)) {
        const prefix = `datacloud-mcp-${target.split("-")[0]}`;
        for (const f of fs.readdirSync(d)) {
          if (f.startsWith(prefix) && f.endsWith(".sock")) {
            const p = path.join(d, f);
            if (!results.includes(p)) results.push(p);
          }
        }
      }
    } catch (_) {}
  }
  return results;
}

function findActiveSocket(candidates) {
  for (const p of candidates) {
    try { if (fs.existsSync(p)) return p; } catch (_) {}
  }
  return candidates[0] || null;
}

class ResilientMcpSocketProxy {
  constructor(target) {
    this.target = target;
    this.category = getToolCategory(target);
    this.candidates = resolveCandidateSocketPaths(target);
    this.socket = null;
    this.isConnected = false;
    this.isConnecting = false;
    this.attempt = 0;
    this.pendingChunks = [];
    this.shieldedInitId = null;
    this.upstreamInitId = "__proxy_init_sync__";
    this.upstreamInitialized = false;
  }

  start() {
    this.setupStdinReader();
    this.tryConnect();
  }

  setupStdinReader() {
    const rl = readline.createInterface({ input: process.stdin, terminal: false });
    rl.on("line", (line) => {
      const trimmed = line.trim();
      if (!trimmed) return;
      let msg = null;
      try { msg = JSON.parse(trimmed); } catch (_) {}
      this.handleIncomingClientMessage(trimmed, msg);
    });
    process.stdin.on("end", () => {
      if (this.socket) { try { this.socket.end(); } catch (_) {} }
      process.exit(0);
    });
  }

  handleIncomingClientMessage(rawLine, msg) {
    if (!msg || typeof msg !== "object") {
      this.forwardOrBuffer(rawLine + "\n");
      return;
    }
    if (!this.isConnected && msg.method === "initialize" && msg.id !== undefined) {
      this.shieldedInitId = msg.id;
      this.respondJson({
        jsonrpc: "2.0",
        id: msg.id,
        result: {
          protocolVersion: msg.params?.protocolVersion || "2024-11-05",
          capabilities: { tools: { listChanged: true } },
          serverInfo: { name: `${this.category}-mcp-server`, version: "1.0.0" }
        }
      });
      return;
    }
    if (!this.isConnected && msg.method === "notifications/initialized") return;
    if (!this.isConnected && msg.method === "tools/list" && msg.id !== undefined) {
      const tools = EMBEDDED_TOOL_SCHEMAS[this.category] || [];
      this.respondJson({ jsonrpc: "2.0", id: msg.id, result: { tools } });
      return;
    }
    this.forwardOrBuffer(rawLine + "\n");
  }

  forwardOrBuffer(chunk) {
    if (this.isConnected && this.socket && this.socket.writable) this.socket.write(chunk);
    else {
      this.pendingChunks.push(chunk);
      if (!this.isConnecting) this.tryConnect();
    }
  }

  respondJson(payload) {
    try { process.stdout.write(JSON.stringify(payload) + "\n"); } catch (e) {
      console.error(`[MCP Proxy] Stdout write error: ${e.message}`);
    }
  }

  tryConnect() {
    if (this.isConnected || this.isConnecting) return;
    this.isConnecting = true;
    this.attempt++;
    const activePath = findActiveSocket(resolveCandidateSocketPaths(this.target));
    if (!activePath) { this.scheduleRetry(); return; }

    const sock = net.createConnection(activePath);
    let upstreamBuf = "";

    sock.on("connect", () => {
      this.isConnected = true;
      this.isConnecting = false;
      this.socket = sock;
      this.attempt = 0;
      if (this.shieldedInitId !== null && !this.upstreamInitialized) {
        sock.write(JSON.stringify({
          jsonrpc: "2.0", id: this.upstreamInitId, method: "initialize",
          params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "mcp-proxy-hardened", version: "1.0.0" } }
        }) + "\n");
      } else {
        this.flushPendingChunks();
      }
    });

    sock.on("data", (chunk) => {
      upstreamBuf += chunk.toString();
      const lines = upstreamBuf.split("\n");
      upstreamBuf = lines.pop();
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        let parsed = null;
        try { parsed = JSON.parse(trimmed); } catch (_) {}
        if (parsed && parsed.id === this.upstreamInitId) {
          this.upstreamInitialized = true;
          sock.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n");
          this.flushPendingChunks();
          continue;
        }
        process.stdout.write(trimmed + "\n");
      }
    });

    const onDisconnect = () => {
      this.isConnected = false;
      this.isConnecting = false;
      this.socket = null;
    };
    sock.on("error", () => { onDisconnect(); sock.destroy(); this.scheduleRetry(); });
    sock.on("end", onDisconnect);
    sock.on("close", onDisconnect);
  }

  flushPendingChunks() {
    if (this.socket && this.socket.writable && this.pendingChunks.length > 0) {
      const q = this.pendingChunks.splice(0);
      for (const c of q) this.socket.write(c);
    }
  }

  scheduleRetry() {
    this.isConnecting = false;
    if (this.attempt >= MAX_ATTEMPTS) return;
    const delay = Math.min(BASE_RETRY_MS + this.attempt * 15, MAX_RETRY_MS);
    setTimeout(() => this.tryConnect(), delay);
  }
}

async function getAccessToken() {
  const home = os.homedir();
  const idePath = process.platform === "darwin" ? path.join(home, "Library", "Application Support", "google-vscode-extension", "auth", "credentials.json")
    : process.platform === "win32" ? path.join(process.env.LOCALAPPDATA || process.env.APPDATA || "", "google-vscode-extension", "auth", "credentials.json")
    : path.join(home, ".cache", "google-vscode-extension", "auth", "credentials.json");

  if (fs.existsSync(idePath)) {
    try {
      const d = JSON.parse(fs.readFileSync(idePath, "utf8"));
      if (d.accessToken || d.access_token) return d.accessToken || d.access_token;
    } catch (_) {}
  }
  const adcPath = process.env.GOOGLE_APPLICATION_CREDENTIALS || (
    process.platform === "win32" ? path.join(process.env.APPDATA || "", "gcloud", "application_default_credentials.json")
    : path.join(home, ".config", "gcloud", "application_default_credentials.json")
  );
  if (fs.existsSync(adcPath)) {
    try {
      const a = JSON.parse(fs.readFileSync(adcPath, "utf8"));
      if (a.refresh_token && a.client_id && a.client_secret) {
        const resp = await fetch("https://oauth2.googleapis.com/token", {
          method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({ client_id: a.client_id, client_secret: a.client_secret, refresh_token: a.refresh_token, grant_type: "refresh_token" }).toString()
        });
        if (resp.ok) { const j = await resp.json(); if (j.access_token) return j.access_token; }
      }
    } catch (_) {}
  }
  return null;
}

async function runHttpProxy(endpoint) {
  const rl = readline.createInterface({ input: process.stdin, terminal: false });
  rl.on("line", async (line) => {
    if (!line.trim()) return;
    let isNotif = false, reqId = null;
    try { const p = JSON.parse(line); isNotif = p.id === undefined; reqId = p.id ?? null; } catch (_) {}
    try {
      const token = await getAccessToken();
      const headers = { "Content-Type": "application/json" };
      if (token) headers["Authorization"] = `Bearer ${token}`;
      if (process.env.X_GOOG_PROXY_HEADERS) {
        try { Object.assign(headers, JSON.parse(process.env.X_GOOG_PROXY_HEADERS)); } catch (_) {}
      }
      const resp = await fetch(endpoint, { method: "POST", headers, body: line });
      const text = await resp.text();
      if (!isNotif && text.trim()) process.stdout.write(text + "\n");
    } catch (e) {
      if (!isNotif) process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: reqId, error: { code: -32603, message: e.message } }) + "\n");
    }
  });
}

function main() {
  const arg = process.argv[2];
  if (!arg) {
    console.error("Usage: node mcp_proxy_bundle_hardened.js <serverId_or_socketPath>");
    process.exit(1);
  }
  if (arg.startsWith("http://") || arg.startsWith("https://")) {
    runHttpProxy(arg).catch((err) => {
      console.error(`[MCP Proxy] Failed to initialize HTTP mode: ${err.message}`);
      process.exit(1);
    });
    return;
  }
  const proxy = new ResilientMcpSocketProxy(arg);
  proxy.start();
}

main();
