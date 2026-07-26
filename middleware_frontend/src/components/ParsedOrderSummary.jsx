/**
 * src/components/ParsedOrderSummary.jsx
 *
 * Shows the fields the LLM extracted from the inbound message.
 * Low-confidence fields are highlighted amber so the broker
 * knows to double-check them before acting.
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
  title: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: "var(--text-muted)",
  },
  confidencePill: (ok) => ({
    fontSize: 10,
    fontWeight: 600,
    padding: "1px 6px",
    borderRadius: 10,
    background: ok ? "var(--green-dim)" : "var(--amber-dim)",
    color: ok ? "var(--green)" : "var(--amber)",
  }),
  grid: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "6px 12px",
  },
  field: {
    display: "flex",
    flexDirection: "column",
    gap: 1,
  },
  fieldLabel: {
    fontSize: 9,
    fontWeight: 600,
    letterSpacing: "0.06em",
    textTransform: "uppercase",
    color: "var(--text-muted)",
  },
  fieldValue: (warn) => ({
    fontSize: 12,
    fontWeight: 500,
    color: warn ? "var(--amber)" : "var(--text-primary)",
    fontFamily: warn ? "inherit" : "inherit",
  }),
  warnIcon: {
    fontSize: 9,
    marginLeft: 3,
    color: "var(--amber)",
  },
  rawToggle: {
    marginTop: 8,
    paddingTop: 8,
    borderTop: "1px solid var(--border)",
  },
  rawBtn: {
    background: "none",
    border: "none",
    color: "var(--text-muted)",
    fontSize: 10,
    cursor: "pointer",
    padding: 0,
  },
  rawBody: {
    marginTop: 6,
    padding: 8,
    background: "var(--bg-base)",
    borderRadius: "var(--radius-sm)",
    fontSize: 10,
    fontFamily: "var(--font-mono)",
    color: "var(--text-secondary)",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    maxHeight: 120,
    overflowY: "auto",
    lineHeight: 1.6,
  },
};

function Field({ label, value, warn }) {
  if (!value) return null;
  return (
    <div style={styles.field}>
      <span style={styles.fieldLabel}>{label}</span>
      <span style={styles.fieldValue(warn)}>
        {value}
        {warn && <span style={styles.warnIcon} title="Low confidence">⚠</span>}
      </span>
    </div>
  );
}

function formatLaycan(start, end) {
  if (!start && !end) return null;
  const fmt = (d) => {
    if (!d) return "?";
    const dt = new Date(d);
    return dt.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  };
  return `${fmt(start)} – ${fmt(end)}`;
}

export default function ParsedOrderSummary({ order, showRaw, onToggleRaw }) {
  if (!order) return null;

  const lowFields = new Set(order.low_confidence_fields || []);
  const confidence = Math.round((order.parse_confidence || 0) * 100);
  const isGood = confidence >= 85;

  return (
    <div style={styles.wrapper}>
      <div style={styles.header}>
        <span style={styles.title}>Parsed order</span>
        <span style={styles.confidencePill(isGood)}>
          {confidence}% confidence
        </span>
      </div>

      <div style={styles.grid}>
        <Field
          label="Cargo"
          value={order.cargo_type}
          warn={lowFields.has("cargo_type")}
        />
        <Field
          label="Quantity"
          value={order.quantity_mt ? `${Number(order.quantity_mt).toLocaleString()} MT` : null}
          warn={lowFields.has("quantity_mt")}
        />
        <Field
          label="Load port"
          value={order.load_port_canonical || order.load_port}
          warn={lowFields.has("load_port")}
        />
        <Field
          label="Discharge port"
          value={order.discharge_port_canonical || order.discharge_port}
          warn={lowFields.has("discharge_port")}
        />
        <Field
          label="Laycan"
          value={formatLaycan(order.laycan_start, order.laycan_end)}
          warn={lowFields.has("laycan_start") || lowFields.has("laycan_end")}
        />
        {order.vessel_type && (
          <Field label="Vessel type" value={order.vessel_type} />
        )}
        {order.freight_rate && (
          <Field label="Rate indication" value={order.freight_rate} />
        )}
      </div>

      <div style={styles.rawToggle}>
        <button style={styles.rawBtn} onClick={onToggleRaw}>
          {showRaw ? "▲ Hide raw message" : "▼ Show raw message"}
        </button>
        {showRaw && (
          <div style={styles.rawBody}>
            {order.raw_body || "(no body)"}
          </div>
        )}
      </div>
    </div>
  );
}
