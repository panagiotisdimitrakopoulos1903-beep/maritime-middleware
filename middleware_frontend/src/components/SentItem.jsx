/**
 * src/components/SentItem.jsx
 *
 * One row in the Sent folder (ADR 0004, Decision #6). Shows:
 *   • Recipient
 *   • Subject
 *   • Time sent
 *   • Send status — green "Sent" / red "Failed" (error_message on hover)
 *
 * Unlike OrderCard, these rows are not selectable/clickable — there's no
 * detail view for a sent message (no matches to show against it), so this
 * component takes no onClick/selected props.
 */

const styles = {
  row: {
    padding: "10px 14px",
    borderBottom: "1px solid var(--border)",
  },
  top: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 3,
  },
  subject: {
    fontWeight: 600,
    fontSize: 12,
    color: "var(--text-primary)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  time: {
    fontSize: 10,
    color: "var(--text-muted)",
    fontFamily: "var(--font-mono)",
    flexShrink: 0,
    marginLeft: 6,
  },
  to: {
    fontSize: 11,
    color: "var(--text-secondary)",
    marginBottom: 4,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  bottom: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 6,
  },
  statusPill: (ok) => ({
    fontSize: 10,
    fontFamily: "var(--font-mono)",
    fontWeight: 700,
    color: ok ? "var(--green)" : "var(--red)",
    background: ok ? "var(--green-dim)" : "var(--red-dim)",
    padding: "1px 5px",
    borderRadius: "var(--radius-sm)",
    whiteSpace: "nowrap",
  }),
  errorLine: {
    fontSize: 10,
    color: "var(--red)",
    marginTop: 3,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
};

function timeAgo(isoString) {
  const diff = (Date.now() - new Date(isoString).getTime()) / 1000;
  if (diff < 60) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  return `${Math.round(diff / 3600)}h ago`;
}

export default function SentItem({ message }) {
  const ok = message.send_status === "sent";

  return (
    <div style={styles.row}>
      <div style={styles.top}>
        <span style={styles.subject}>{message.subject || "(no subject)"}</span>
        <span style={styles.time}>{timeAgo(message.sent_at)}</span>
      </div>

      <div style={styles.to}>To: {message.to_addr}</div>

      <div style={styles.bottom}>
        <span
          style={styles.statusPill(ok)}
          title={!ok && message.error_message ? message.error_message : undefined}
        >
          {ok ? "Sent" : "Failed"}
        </span>
      </div>

      {!ok && message.error_message && (
        <div style={styles.errorLine} title={message.error_message}>
          {message.error_message}
        </div>
      )}
    </div>
  );
}
