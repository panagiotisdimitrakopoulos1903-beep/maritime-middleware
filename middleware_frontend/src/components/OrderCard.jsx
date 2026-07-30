/**
 * src/components/OrderCard.jsx
 *
 * One row in the inbound order list. Shows:
 *   • Cargo type + quantity
 *   • Load → discharge route
 *   • Time received
 *   • Parse confidence indicator
 *   • Top match score if available
 *
 * `read` (ADR 0004, Decision #7) drives a quieter visual treatment for
 * already-opened orders — distinct from the `newOrderIds`-driven pulsing
 * accent dot in OrderList.jsx, which means "just arrived" (a separate,
 * temporary concept, not persisted read/unread state).
 */

const styles = {
  card: (selected) => ({
    padding: "10px 14px",
    borderBottom: "1px solid var(--border)",
    background: selected ? "var(--bg-elevated)" : "transparent",
    cursor: "pointer",
    transition: "background 0.1s",
    borderLeft: selected ? "2px solid var(--accent)" : "2px solid transparent",
  }),
  top: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 3,
  },
  cargo: (read) => ({
    fontWeight: read ? 400 : 600,
    fontSize: 12,
    color: read ? "var(--text-secondary)" : "var(--text-primary)",
    textTransform: "capitalize",
  }),
  time: {
    fontSize: 10,
    color: "var(--text-muted)",
    fontFamily: "var(--font-mono)",
  },
  route: {
    fontSize: 11,
    color: "var(--text-secondary)",
    marginBottom: 4,
  },
  arrow: {
    color: "var(--text-muted)",
    margin: "0 4px",
  },
  bottom: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 6,
  },
  sender: {
    fontSize: 10,
    color: "var(--text-muted)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    maxWidth: 160,
  },
  scoreChip: (score) => {
    const pct = Math.round((score || 0) * 100);
    const color = pct >= 80 ? "var(--green)" : pct >= 60 ? "var(--amber)" : "var(--red)";
    return {
      fontSize: 10,
      fontFamily: "var(--font-mono)",
      fontWeight: 700,
      color,
      padding: "1px 5px",
      borderRadius: 3,
      background: pct >= 80 ? "var(--green-dim)" : pct >= 60 ? "var(--amber-dim)" : "var(--red-dim)",
      whiteSpace: "nowrap",
    };
  },
  warnDot: {
    width: 5,
    height: 5,
    borderRadius: "50%",
    background: "var(--amber)",
    display: "inline-block",
    marginRight: 4,
    verticalAlign: "middle",
  },
  // Parse-failure indicator (ADR 0007, Decision 4) — deliberately the same
  // restrained size/shape as warnDot, just the --red token instead of
  // --amber. No full-row background, no animation — a broker should be
  // able to spot this scanning the list without it reading as an alarm.
  failedDot: {
    width: 5,
    height: 5,
    borderRadius: "50%",
    background: "var(--red)",
    display: "inline-block",
    marginRight: 4,
    verticalAlign: "middle",
  },
};

function timeAgo(isoString) {
  const diff = (Date.now() - new Date(isoString).getTime()) / 1000;
  if (diff < 60) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  return `${Math.round(diff / 3600)}h ago`;
}

export default function OrderCard({ order, selected, read, onClick }) {
  const topScore = order.top_match?.total_score;
  const failedParse = order.parse_status === "failed";
  const qty = order.quantity_mt
    ? `${(order.quantity_mt / 1000).toFixed(0)}k MT`
    : "";

  return (
    <div
      style={styles.card(selected)}
      onClick={onClick}
      onMouseEnter={e => !selected && (e.currentTarget.style.background = "var(--bg-elevated)")}
      onMouseLeave={e => !selected && (e.currentTarget.style.background = "transparent")}
    >
      <div style={styles.top}>
        <span style={styles.cargo(read)}>
          {failedParse ? (
            <span style={styles.failedDot} title="Parsing failed — needs review" />
          ) : (
            order.has_low_confidence_fields && (
              <span style={styles.warnDot} title="Low confidence fields" />
            )
          )}
          {failedParse ? "Parsing failed — needs review" : (order.cargo_type || "Unknown cargo")}
          {qty && <span style={{ color: "var(--text-secondary)", fontWeight: 400 }}> · {qty}</span>}
        </span>
        <span style={styles.time}>{timeAgo(order.received_at)}</span>
      </div>

      <div style={styles.route}>
        <span>{order.load_port_canonical || "—"}</span>
        <span style={styles.arrow}>→</span>
        <span>{order.discharge_port_canonical || "—"}</span>
      </div>

      <div style={styles.bottom}>
        <span style={styles.sender}>{order.sender}</span>
        {topScore != null && (
          <span style={styles.scoreChip(topScore)}>
            {Math.round(topScore * 100)}% match
          </span>
        )}
      </div>
    </div>
  );
}
