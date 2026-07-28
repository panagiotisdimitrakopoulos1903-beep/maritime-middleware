/**
 * src/lib/api.js — typed client for the Python FastAPI backend
 *
 * All network calls go through here. Components never fetch directly.
 */

const BASE = "http://127.0.0.1:5000";

// ── HTTP helpers ──────────────────────────────────────────────────────────────

async function get(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

/**
 * POST helper that surfaces the backend's real error detail on failure.
 * FastAPI's HTTPException responses are `{"detail": "..."}` JSON bodies
 * with the actual failure reason (e.g. "SMTP is not configured — set
 * SMTP_HOST, SMTP_USER, SMTP_PASS in .env") — unlike `get()`, this reads
 * the body so callers (the compose UI) can show the broker the real
 * reason a send failed, not just a bare status code.
 */
async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (data && data.detail) detail = data.detail;
    } catch {
      // Body wasn't JSON (or was empty) — fall back to statusText.
    }
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }

  return res.json();
}

/**
 * PATCH helper for fire-and-forget mutations where failure has no UI
 * consequence (e.g. marking an order read) — swallows errors internally
 * rather than surfacing `detail` like `post()` does, since there's nothing
 * useful for the broker-facing UI to show if this fails.
 */
async function patch(path) {
  try {
    const res = await fetch(`${BASE}${path}`, { method: "PATCH" });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

// ── API methods ───────────────────────────────────────────────────────────────

export const api = {
  /** Latest N inbound orders with their top match */
  getLatestOrders: (limit = 30) => get(`/orders/latest?limit=${limit}`),

  /** Full ranked matches for a specific order */
  getMatches: (orderId) => get(`/matches/${orderId}`),

  /** System health — cache age, vessel count, uptime */
  getStatus: () => get(`/status`),

  /** Latest N outbound (Sent) messages — ADR 0004 Decision #6 */
  getSentMessages: (limit = 40) => get(`/sent?limit=${limit}`),

  /**
   * Send a broker-composed reply. Synchronous on the backend — resolves
   * with {status: "sent", message_id} or rejects with an Error whose
   * message is the backend's real failure detail (SMTP not configured,
   * SMTP send failed, order not found, etc).
   */
  sendReply: (payload) => post(`/internal/send`, payload),

  /**
   * Mark an order as read (ADR 0004, Decision #7). Local-Postgres-only —
   * never touches the real mailbox's IMAP \Seen flag. Fire-and-forget: the
   * call site doesn't need to await or handle failure.
   */
  markOrderRead: (orderId) => patch(`/orders/${orderId}/read`),
};

// ── WebSocket — live push from Python backend ─────────────────────────────────

let ws = null;
let reconnectTimer = null;
const listeners = new Set();

export function subscribeToMatches(callback) {
  listeners.add(callback);
  return () => listeners.delete(callback);
}

export function connectWebSocket() {
  if (ws && ws.readyState === WebSocket.OPEN) return;

  ws = new WebSocket(`ws://127.0.0.1:5000/ws`);

  ws.onopen = () => {
    console.log("[ws] connected");
    if (reconnectTimer) clearInterval(reconnectTimer);
    // Send a heartbeat every 20s to keep the connection alive
    setInterval(() => ws?.readyState === WebSocket.OPEN && ws.send("ping"), 20000);
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === "NEW_MATCHES") {
        listeners.forEach((cb) => cb(data));
      }
    } catch (e) {
      console.warn("[ws] bad message", e);
    }
  };

  ws.onclose = () => {
    console.log("[ws] disconnected — reconnecting in 3s");
    reconnectTimer = setTimeout(connectWebSocket, 3000);
  };

  ws.onerror = (err) => {
    console.error("[ws] error", err);
    ws.close();
  };
}
