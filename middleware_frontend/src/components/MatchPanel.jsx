/**
 * src/components/MatchPanel.jsx
 *
 * Right pane of the layout. When an order is selected it shows:
 *   1. ParsedOrderSummary — extracted fields + confidence
 *   2. DealCard list — ranked vessel matches
 *
 * While loading, shows skeleton placeholders.
 * When no order is selected, shows an empty state.
 */
import { useState } from "react";
import ParsedOrderSummary from "./ParsedOrderSummary";
import DealCard from "./DealCard";
import ComposeReply from "./ComposeReply";

const styles = {
  panel: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
    background: "var(--bg-base)",
  },
  header: {
    padding: "10px 14px 8px",
    borderBottom: "1px solid var(--border)",
    flexShrink: 0,
  },
  headerTitle: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: "var(--text-muted)",
    marginBottom: 2,
  },
  headerSub: {
    fontSize: 11,
    color: "var(--text-secondary)",
  },
  scroll: {
    flex: 1,
    overflowY: "auto",
    padding: "12px 12px 24px",
  },
  empty: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    height: "100%",
    gap: 10,
    color: "var(--text-muted)",
    padding: 24,
    textAlign: "center",
  },
  emptyIcon: {
    fontSize: 28,
    opacity: 0.4,
  },
  emptyText: {
    fontSize: 12,
    lineHeight: 1.6,
  },
  matchesHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 8,
  },
  matchesLabel: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: "var(--text-muted)",
  },
  cacheAge: {
    fontSize: 10,
    color: "var(--text-muted)",
    fontFamily: "var(--font-mono)",
  },
  skeletonCard: {
    background: "var(--bg-panel)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-lg)",
    padding: 14,
    marginBottom: 8,
  },
  skeletonLine: (w, h = 10, mt = 0) => ({
    width: w,
    height: h,
    marginTop: mt,
    borderRadius: 4,
  }),
  noMatches: {
    padding: "20px 0",
    textAlign: "center",
    color: "var(--text-muted)",
    fontSize: 12,
  },
};

function SkeletonCard() {
  return (
    <div style={styles.skeletonCard}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10 }}>
        <div>
          <div className="skeleton" style={styles.skeletonLine("140px", 13)} />
          <div className="skeleton" style={styles.skeletonLine("80px", 10, 5)} />
        </div>
        <div className="skeleton" style={{ width: 36, height: 36, borderRadius: "50%" }} />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 12px", marginBottom: 10 }}>
        {[...Array(4)].map((_, i) => (
          <div key={i}>
            <div className="skeleton" style={styles.skeletonLine("40px", 8)} />
            <div className="skeleton" style={styles.skeletonLine("70px", 11, 3)} />
          </div>
        ))}
      </div>
      <div className="skeleton" style={styles.skeletonLine("100%", 4)} />
    </div>
  );
}

function formatCacheAge(isoString) {
  if (!isoString) return null;
  const mins = Math.round((Date.now() - new Date(isoString).getTime()) / 60000);
  return mins < 1 ? "just now" : `${mins}m ago`;
}

export default function MatchPanel({ order, loading }) {
  const [showRaw, setShowRaw] = useState(false);
  const [composeOpen, setComposeOpen] = useState(false);

  if (!order && !loading) {
    return (
      <div style={styles.panel}>
        <div style={styles.empty}>
          <div style={styles.emptyIcon}>⚓</div>
          <div style={styles.emptyText}>
            Select an inbound order to see ranked vessel matches
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={styles.panel}>
      {order && (
        <div style={styles.header}>
          <div style={styles.headerTitle}>Match results</div>
          <div style={styles.headerSub}>
            {order.cargo_type || "Unknown"} ·{" "}
            {order.load_port_canonical || "?"} → {order.discharge_port_canonical || "?"}
          </div>
        </div>
      )}

      <div style={styles.scroll}>
        {loading ? (
          <>
            <div style={{ ...styles.skeletonCard, padding: 14, marginBottom: 12 }}>
              <div className="skeleton" style={styles.skeletonLine("60px", 8)} />
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 12px", marginTop: 8 }}>
                {[...Array(6)].map((_, i) => (
                  <div key={i}>
                    <div className="skeleton" style={styles.skeletonLine("35px", 7)} />
                    <div className="skeleton" style={styles.skeletonLine("80px", 11, 3)} />
                  </div>
                ))}
              </div>
            </div>
            <SkeletonCard />
            <SkeletonCard />
            <SkeletonCard />
          </>
        ) : (
          <>
            <ParsedOrderSummary
              order={order}
              showRaw={showRaw}
              onToggleRaw={() => setShowRaw(v => !v)}
              onReply={() => setComposeOpen(true)}
            />

            {composeOpen && (
              <ComposeReply
                key={order.order_id}
                order={order}
                onClose={() => setComposeOpen(false)}
              />
            )}

            <div style={styles.matchesHeader}>
              <span style={styles.matchesLabel}>
                {order.matches?.length
                  ? `Top ${order.matches.length} vessels`
                  : "Vessels"}
              </span>
              {order.signal_last_refreshed && (
                <span style={styles.cacheAge}>
                  Signal {formatCacheAge(order.signal_last_refreshed)}
                </span>
              )}
            </div>

            {(!order.matches || order.matches.length === 0) ? (
              <div style={styles.noMatches}>
                No vessels matched this order.
                <br />Check Signal cache age above.
              </div>
            ) : (
              order.matches.map(match => (
                <DealCard key={`${match.vessel_id}-${match.rank}`} match={match} />
              ))
            )}
          </>
        )}
      </div>
    </div>
  );
}
