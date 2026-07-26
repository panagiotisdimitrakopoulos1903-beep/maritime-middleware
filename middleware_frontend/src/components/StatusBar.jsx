/**
 * src/components/StatusBar.jsx
 *
 * Thin bar at the top of the panel showing:
 *   • WebSocket connection state
 *   • Signal cache age and vessel count
 *   • Draggable handle for the frameless window
 *   • Minimise button
 */
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";

const styles = {
  bar: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "0 12px",
    height: 36,
    background: "var(--bg-panel)",
    borderBottom: "1px solid var(--border)",
    WebkitAppRegion: "drag",   // CSS drag for Electron frameless window
    flexShrink: 0,
    gap: 8,
    userSelect: "none",
  },
  left: {
    display: "flex",
    alignItems: "center",
    gap: 8,
  },
  logo: {
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: "0.06em",
    color: "var(--text-accent)",
    fontFamily: "var(--font-mono)",
  },
  dot: (connected) => ({
    width: 6,
    height: 6,
    borderRadius: "50%",
    background: connected ? "var(--green)" : "var(--red)",
    animation: connected ? "none" : "pulse 1.5s infinite",
  }),
  pill: {
    display: "flex",
    alignItems: "center",
    gap: 4,
    padding: "1px 7px",
    borderRadius: 10,
    background: "var(--bg-elevated)",
    color: "var(--text-secondary)",
    fontSize: 10,
    fontFamily: "var(--font-mono)",
  },
  stale: {
    color: "var(--amber)",
  },
  right: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    WebkitAppRegion: "no-drag",
  },
  btn: {
    width: 20,
    height: 20,
    borderRadius: "50%",
    border: "none",
    background: "var(--bg-elevated)",
    color: "var(--text-secondary)",
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 12,
    lineHeight: 1,
    transition: "background 0.15s",
  },
};

export default function StatusBar({ connected }) {
  const [status, setStatus] = useState(null);
  const intervalRef = useRef(null);

  const fetchStatus = () => {
    api.getStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  };

  useEffect(() => {
    fetchStatus();
    intervalRef.current = setInterval(fetchStatus, 30000);
    return () => clearInterval(intervalRef.current);
  }, []);

  const cacheAge = status?.signal_cache_age_minutes;
  const isStale = cacheAge != null && cacheAge > 8;

  const handleMinimize = () => {
    window.electronAPI?.minimizeWindow();
  };

  return (
    <div style={styles.bar}>
      <div style={styles.left}>
        <span style={styles.logo}>⚓ MARITIME</span>
        <div style={styles.dot(connected)} title={connected ? "Live" : "Disconnected"} />
        {status && (
          <>
            <div style={styles.pill}>
              <span>{status.vessel_count} vessels</span>
            </div>
            <div style={{ ...styles.pill, ...(isStale ? styles.stale : {}) }}>
              <span>
                {cacheAge != null
                  ? `${cacheAge < 1 ? "<1" : Math.round(cacheAge)}m ago`
                  : "—"}
              </span>
            </div>
          </>
        )}
      </div>
      <div style={styles.right}>
        <button
          style={styles.btn}
          onClick={handleMinimize}
          title="Minimise"
          onMouseEnter={e => e.currentTarget.style.background = "var(--border-strong)"}
          onMouseLeave={e => e.currentTarget.style.background = "var(--bg-elevated)"}
        >
          —
        </button>
      </div>
    </div>
  );
}
