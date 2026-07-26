/**
 * src/components/DealCard.jsx
 *
 * One ranked vessel match. Shows:
 *   • Rank number + vessel name + class
 *   • DWT, open port, open date
 *   • Total score as large number
 *   • Segmented score breakdown bar
 */
import ScoreBar from "./ScoreBar";

const styles = {
  card: (rank) => ({
    background: "var(--bg-panel)",
    border: `1px solid ${rank === 1 ? "var(--accent-dim)" : "var(--border)"}`,
    borderRadius: "var(--radius-lg)",
    padding: "12px 14px",
    marginBottom: 8,
    animation: "fadeSlideIn 0.2s ease-out both",
    animationDelay: `${(rank - 1) * 0.05}s`,
    position: "relative",
    overflow: "hidden",
  }),
  topGlow: {
    position: "absolute",
    top: 0, left: 0, right: 0,
    height: 2,
    background: "linear-gradient(90deg, var(--accent), transparent)",
  },
  header: {
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    marginBottom: 8,
    gap: 8,
  },
  rankBadge: (rank) => ({
    width: 20,
    height: 20,
    borderRadius: "50%",
    background: rank === 1 ? "var(--accent)" : "var(--bg-elevated)",
    color: rank === 1 ? "#000" : "var(--text-secondary)",
    fontSize: 10,
    fontWeight: 700,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
    fontFamily: "var(--font-mono)",
  }),
  nameBlock: {
    flex: 1,
    minWidth: 0,
  },
  vesselName: {
    fontSize: 13,
    fontWeight: 600,
    color: "var(--text-primary)",
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
  },
  vesselClass: {
    fontSize: 10,
    color: "var(--text-secondary)",
    marginTop: 1,
  },
  scoreBlock: {
    textAlign: "right",
    flexShrink: 0,
  },
  scoreNumber: (score) => {
    const pct = Math.round(score * 100);
    const color = pct >= 80 ? "var(--green)" : pct >= 60 ? "var(--amber)" : "var(--red)";
    return {
      fontSize: 22,
      fontWeight: 700,
      fontFamily: "var(--font-mono)",
      color,
      lineHeight: 1,
    };
  },
  scoreLabel: {
    fontSize: 9,
    color: "var(--text-muted)",
    textTransform: "uppercase",
    letterSpacing: "0.06em",
  },
  specs: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "4px 12px",
    marginBottom: 10,
  },
  spec: {
    display: "flex",
    flexDirection: "column",
    gap: 1,
  },
  specLabel: {
    fontSize: 9,
    fontWeight: 600,
    letterSpacing: "0.06em",
    textTransform: "uppercase",
    color: "var(--text-muted)",
  },
  specValue: {
    fontSize: 11,
    color: "var(--text-primary)",
    fontFamily: "var(--font-mono)",
  },
};

function formatDate(isoString) {
  if (!isoString) return "—";
  return new Date(isoString).toLocaleDateString("en-GB", {
    day: "numeric", month: "short", year: "numeric"
  });
}

function formatDwt(dwt) {
  if (!dwt) return "—";
  return `${(dwt / 1000).toFixed(0)}k DWT`;
}

export default function DealCard({ match }) {
  const isTop = match.rank === 1;

  return (
    <div style={styles.card(match.rank)}>
      {isTop && <div style={styles.topGlow} />}

      <div style={styles.header}>
        <div style={styles.rankBadge(match.rank)}>
          {match.rank}
        </div>
        <div style={styles.nameBlock}>
          <div style={styles.vesselName}>{match.vessel_name || "Unknown vessel"}</div>
          <div style={styles.vesselClass}>
            {match.vessel_class}{match.built_year ? ` · ${match.built_year}` : ""}
          </div>
        </div>
        <div style={styles.scoreBlock}>
          <div style={styles.scoreNumber(match.total_score)}>
            {Math.round(match.total_score * 100)}
          </div>
          <div style={styles.scoreLabel}>score</div>
        </div>
      </div>

      <div style={styles.specs}>
        <div style={styles.spec}>
          <span style={styles.specLabel}>Size</span>
          <span style={styles.specValue}>{formatDwt(match.dwt)}</span>
        </div>
        <div style={styles.spec}>
          <span style={styles.specLabel}>Open port</span>
          <span style={styles.specValue}>{match.open_port || "—"}</span>
        </div>
        <div style={styles.spec}>
          <span style={styles.specLabel}>Open area</span>
          <span style={styles.specValue}>{match.open_port_area || "—"}</span>
        </div>
        <div style={styles.spec}>
          <span style={styles.specLabel}>Open date</span>
          <span style={styles.specValue}>{formatDate(match.open_date)}</span>
        </div>
      </div>

      <ScoreBar breakdown={match.score_breakdown} />
    </div>
  );
}
