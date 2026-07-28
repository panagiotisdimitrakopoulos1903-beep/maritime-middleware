/**
 * src/components/ComposeReply.jsx
 *
 * Reply-compose form for the currently selected inbound order. Sends via
 * POST /internal/send (api.sendReply), which is synchronous on the backend
 * — the broker gets a definitive sent/failed result, never a silent
 * fire-and-forget. See ADR 0004, Decision #4/#5.
 *
 * There is no broker-identity/account config anywhere in this frontend
 * (single-mailbox system) and the backend requires a `sender`, so this
 * component adds a small editable "From" field, persisted to localStorage
 * so the broker doesn't retype it on every reply. This is a pragmatic
 * addition beyond ADR 0004's literal field list, not a settings page.
 */
import { useState } from "react";
import { api } from "../lib/api";

const FROM_STORAGE_KEY = "maritime-middleware:reply-from";

const styles = {
  wrapper: {
    background: "var(--bg-panel)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-lg)",
    padding: "12px 14px",
    marginBottom: 12,
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 10,
  },
  title: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: "var(--text-muted)",
  },
  closeBtn: {
    background: "none",
    border: "none",
    color: "var(--text-muted)",
    fontSize: 11,
    cursor: "pointer",
    padding: 0,
  },
  form: {
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  field: {
    display: "flex",
    flexDirection: "column",
    gap: 2,
  },
  fieldLabel: {
    fontSize: 9,
    fontWeight: 600,
    letterSpacing: "0.06em",
    textTransform: "uppercase",
    color: "var(--text-muted)",
  },
  input: {
    background: "var(--bg-input)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-sm)",
    padding: "5px 7px",
    fontSize: 12,
    color: "var(--text-primary)",
    fontFamily: "var(--font-sans)",
  },
  textarea: {
    background: "var(--bg-input)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-sm)",
    padding: "6px 8px",
    fontSize: 12,
    color: "var(--text-primary)",
    fontFamily: "var(--font-sans)",
    minHeight: 90,
    resize: "vertical",
    lineHeight: 1.5,
  },
  quoteAttribution: {
    fontSize: 10,
    color: "var(--text-muted)",
    marginTop: 2,
    marginBottom: 4,
  },
  quoteBody: {
    padding: 8,
    background: "var(--bg-base)",
    borderRadius: "var(--radius-sm)",
    fontSize: 10,
    fontFamily: "var(--font-mono)",
    color: "var(--text-secondary)",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    maxHeight: 140,
    overflowY: "auto",
    lineHeight: 1.6,
  },
  actions: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    marginTop: 2,
  },
  sendBtn: (disabled) => ({
    background: disabled ? "var(--bg-elevated)" : "var(--accent)",
    color: disabled ? "var(--text-muted)" : "#000",
    border: "none",
    borderRadius: "var(--radius-sm)",
    padding: "6px 14px",
    fontSize: 12,
    fontWeight: 600,
    cursor: disabled ? "default" : "pointer",
  }),
  cancelBtn: (disabled) => ({
    background: "none",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-sm)",
    padding: "6px 14px",
    fontSize: 12,
    color: disabled ? "var(--text-muted)" : "var(--text-secondary)",
    cursor: disabled ? "default" : "pointer",
  }),
  resultBlock: (kind) => ({
    padding: "8px 10px",
    borderRadius: "var(--radius-sm)",
    fontSize: 11,
    lineHeight: 1.5,
    fontWeight: 600,
    background: kind === "sent" ? "var(--green-dim)" : "var(--red-dim)",
    color: kind === "sent" ? "var(--green)" : "var(--red)",
  }),
};

/** Strip any number of leading "Re:" prefixes (case-insensitive), then add exactly one. */
function dedupeReplySubject(subject) {
  const base = (subject || "").replace(/^(re:\s*)+/i, "").trim();
  return `Re: ${base}`;
}

function formatAttributionDate(isoString) {
  if (!isoString) return "an earlier date";
  const dt = new Date(isoString);
  return dt.toLocaleString("en-GB", {
    day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

/** Standard plain-text mail-client quoting — leading "> " on every line. */
function quoteLines(rawBody) {
  const text = rawBody || "(no body)";
  return text.split("\n").map((line) => `> ${line}`).join("\n");
}

export default function ComposeReply({ order, onClose }) {
  const [from, setFrom] = useState(
    () => localStorage.getItem(FROM_STORAGE_KEY) || ""
  );
  const [to, setTo] = useState(order?.sender || "");
  const [subject, setSubject] = useState(dedupeReplySubject(order?.subject));
  const [body, setBody] = useState("");
  const [status, setStatus] = useState("idle"); // idle | sending | sent | error
  const [errorMessage, setErrorMessage] = useState("");

  if (!order) return null;

  const attributionLine = `On ${formatAttributionDate(order.received_at)}, ${order.sender || "unknown sender"} wrote:`;
  const quotedBody = quoteLines(order.raw_body);
  const quoteBlock = `${attributionLine}\n${quotedBody}`;

  const busy = status === "sending";
  const sent = status === "sent";
  const locked = busy || sent;
  const canSend = !locked && from.trim() && to.trim() && subject.trim();

  function handleFromChange(e) {
    const value = e.target.value;
    setFrom(value);
    localStorage.setItem(FROM_STORAGE_KEY, value);
  }

  async function handleSend() {
    if (!canSend) return;
    setStatus("sending");
    setErrorMessage("");
    try {
      await api.sendReply({
        sender: from.trim(),
        to: to.trim(),
        subject: subject.trim(),
        body: `${body}\n\n${quoteBlock}`,
        in_reply_to_order_id: order.order_id,
      });
      setStatus("sent");
    } catch (err) {
      setStatus("error");
      setErrorMessage(err.message || "Send failed — unknown error");
    }
  }

  return (
    <div style={styles.wrapper}>
      <div style={styles.header}>
        <span style={styles.title}>Reply</span>
        <button style={styles.closeBtn} onClick={onClose} disabled={busy}>
          ✕ Close
        </button>
      </div>

      <div style={styles.form}>
        <div style={styles.field}>
          <span style={styles.fieldLabel}>From</span>
          <input
            style={styles.input}
            type="text"
            value={from}
            onChange={handleFromChange}
            placeholder="you@brokerfirm.com"
            disabled={locked}
          />
        </div>

        <div style={styles.field}>
          <span style={styles.fieldLabel}>To</span>
          <input
            style={styles.input}
            type="text"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            disabled={locked}
          />
        </div>

        <div style={styles.field}>
          <span style={styles.fieldLabel}>Subject</span>
          <input
            style={styles.input}
            type="text"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            disabled={locked}
          />
        </div>

        <div style={styles.field}>
          <span style={styles.fieldLabel}>Message</span>
          <textarea
            style={styles.textarea}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Type your reply…"
            disabled={locked}
          />
        </div>

        <div>
          <div style={styles.quoteAttribution}>{attributionLine}</div>
          <div style={styles.quoteBody}>{quotedBody}</div>
        </div>

        {status === "error" && (
          <div style={styles.resultBlock("error")}>
            Send failed: {errorMessage}
          </div>
        )}

        {status === "sent" && (
          <div style={styles.resultBlock("sent")}>✓ Sent</div>
        )}

        <div style={styles.actions}>
          {!sent && (
            <button
              style={styles.sendBtn(!canSend)}
              onClick={handleSend}
              disabled={!canSend}
            >
              {busy ? "Sending…" : "Send"}
            </button>
          )}
          <button style={styles.cancelBtn(busy)} onClick={onClose} disabled={busy}>
            {sent ? "Close" : "Cancel"}
          </button>
        </div>
      </div>
    </div>
  );
}
