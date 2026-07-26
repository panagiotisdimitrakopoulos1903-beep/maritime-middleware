/**
 * src/components/ScoreBar.jsx
 *
 * Horizontal segmented bar showing the four scoring dimensions
 * for a matched vessel. Each segment is colour-coded and proportional
 * to its weighted contribution to the total score.
 */

const DIMENSIONS = [
  { key: "vessel_size",  label: "Size",    weight: 0.35, color: "#388bfd" },
  { key: "geography",    label: "Geo",     weight: 0.30, color: "#2ea043" },
  { key: "date_overlap", label: "Date",    weight: 0.20, color: "#e8a020" },
  { key: "cargo_type",   label: "Cargo",   weight: 0.15, color: "#bc8cff" },
];

const styles = {
  wrapper: { width: "100%" },
  barTrack: {
    display: "flex",
    height: 4,
    borderRadius: 2,
    overflow: "hidden",
    gap: 1,
    marginBottom: 4,
  },
  segment: (color, score, weight) => ({
    flex: weight,
    background: `${color}${Math.round(score * 255).toString(16).padStart(2, "0")}`,
    borderRadius: 2,
    transition: "all 0.3s ease",
    minWidth: 2,
  }),
  legend: {
    display: "flex",
    gap: 8,
    flexWrap: "wrap",
  },
  legendItem: {
    display: "flex",
    alignItems: "center",
    gap: 3,
    fontSize: 9,
    color: "var(--text-muted)",
  },
  legendDot: (color) => ({
    width: 5,
    height: 5,
    borderRadius: "50%",
    background: color,
  }),
};

export default function ScoreBar({ breakdown }) {
  if (!breakdown) return null;

  return (
    <div style={styles.wrapper}>
      <div style={styles.barTrack}>
        {DIMENSIONS.map(({ key, color, weight }) => (
          <div
            key={key}
            style={styles.segment(color, breakdown[key] ?? 0, weight)}
            title={`${key}: ${Math.round((breakdown[key] ?? 0) * 100)}%`}
          />
        ))}
      </div>
      <div style={styles.legend}>
        {DIMENSIONS.map(({ key, label, color, weight }) => (
          <div key={key} style={styles.legendItem}>
            <div style={styles.legendDot(color)} />
            <span>{label} {Math.round((breakdown[key] ?? 0) * 100)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}
