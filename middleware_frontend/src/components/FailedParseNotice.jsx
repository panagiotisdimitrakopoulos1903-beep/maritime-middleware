/**
 * src/components/FailedParseNotice.jsx
 *
 * Rendered by MatchPanel.jsx instead of ParsedOrderSummary + the vessel
 * list when order.parse_status === "failed" (ADR 0007, Decision 4).
 *
 * A failed parse means parse_message() raised outright — no real
 * structured data was ever extracted, and the matching engine was never
 * run (api/app.py::_process_inbound skips Step 3/4 entirely for these
 * orders, ADR 0007 Decision 2). Reusing ParsedOrderSummary here — even
 * with a "0% confidence" pill — would recreate exactly the failure mode
 * this ADR fixes one level up: a broker-facing number that looks like a
 * computed judgment but isn't one. So this is a distinct component, not a
 * degraded version of the match view.
 *
 * The raw exception (order.parse_error) is developer-facing text — same
 * treatment SentItem.jsx already uses for OutboundMessage.error_message:
 * hover/title text on a small details affordance, never inline body copy.
 * The raw message body is always visible (not behind ParsedOrderSummary's
 * showRaw toggle) since for a failed parse it's the only real information
 * available.
 *
 * The Reply button/ComposeReply.jsx flow is still available here — it
 * only ever depends on raw_body/sender/subject/received_at, never on
 * parsed fields, so a broker can always manually reply.
 */

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
  headerActions: {
    display: "flex",
    alignItems: "center",
    gap: 8,
  },
  title: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: "var(--text-muted)",
  },
  replyBtn: {
    background: "none",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-sm)",
    color: "var(--text-secondary)",
    fontSize: 10,
    fontWeight: 600,
    padding: "2px 8px",
    cursor: "pointer",
  },
  banner: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
    padding: "8px 10px",
    borderRadius: "var(--radius-sm)",
    background: "var(--red-dim)",
    color: "var(--red)",
    fontSize: 12,
    fontWeight: 600,
    marginBottom: 8,
  },
  detailsAffordance: {
    fontSize: 10,
    fontWeight: 600,
    color: "var(--red)",
    border: "1px solid var(--red)",
    borderRadius: "var(--radius-sm)",
    padding: "1px 6px",
    cursor: "default",
    whiteSpace: "nowrap",
    flexShrink: 0,
  },
  explainer: {
    fontSize: 11,
    color: "var(--text-secondary)",
    lineHeight: 1.5,
    marginBottom: 10,
  },
  rawLabel: {
    fontSize: 9,
    fontWeight: 600,
    letterSpacing: "0.06em",
    textTransform: "uppercase",
    color: "var(--text-muted)",
    marginBottom: 4,
  },
  rawBody: {
    padding: 8,
    background: "var(--bg-base)",
    borderRadius: "var(--radius-sm)",
    fontSize: 10,
    fontFamily: "var(--font-mono)",
    color: "var(--text-secondary)",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    maxHeight: 260,
    overflowY: "auto",
    lineHeight: 1.6,
  },
};

export default function FailedParseNotice({ order, onReply }) {
  if (!order) return null;

  return (
    <div style={styles.wrapper}>
      <div style={styles.header}>
        <span style={styles.title}>Parse failed</span>
        <div style={styles.headerActions}>
          <button style={styles.replyBtn} onClick={onReply}>
            ↩ Reply
          </button>
        </div>
      </div>

      <div style={styles.banner}>
        <span>Unable to parse this message</span>
        {order.parse_error && (
          <span style={styles.detailsAffordance} title={order.parse_error}>
            details
          </span>
        )}
      </div>

      <div style={styles.explainer}>
        Vessel matching was not run because this message could not be
        parsed. Review the message below.
      </div>

      <div style={styles.rawLabel}>Raw message</div>
      <div style={styles.rawBody}>{order.raw_body || "(no body)"}</div>
    </div>
  );
}
