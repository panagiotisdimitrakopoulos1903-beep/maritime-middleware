/**
 * src/components/OrderList.jsx
 *
 * Left pane — scrollable list of inbound orders, newest first, with a
 * minimal Inbox/Sent folder switcher (ADR 0004, Decision #6).
 *
 * Inbox (default): unchanged behavior — new orders flash briefly when they
 * arrive via WebSocket push, polling fallback refreshes every 20s.
 *
 * Sent: fetched from GET /sent, no WebSocket/polling (no live-push for
 * outbound messages in this codebase), rows are inert (not
 * selectable/clickable — no detail view exists for a sent message).
 *
 * Folder selection is local UI state, not lifted to App.jsx — self-contained
 * concern, matching MatchPanel.jsx's convention for showRaw/composeOpen.
 */
import { useEffect, useRef, useState } from "react";
import OrderCard from "./OrderCard";
import SentItem from "./SentItem";
import { api } from "../lib/api";

const styles = {
  pane: {
    width: 200,
    flexShrink: 0,
    display: "flex",
    flexDirection: "column",
    borderRight: "1px solid var(--border)",
    overflow: "hidden",
  },
  header: {
    padding: "10px 12px 8px",
    borderBottom: "1px solid var(--border)",
    flexShrink: 0,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
  },
  tabs: {
    display: "flex",
    gap: 4,
  },
  tab: (active) => ({
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: active ? "var(--text-primary)" : "var(--text-muted)",
    background: active ? "var(--bg-elevated)" : "transparent",
    border: "none",
    borderRadius: "var(--radius-sm)",
    padding: "3px 7px",
    cursor: "pointer",
  }),
  headerTitle: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: "var(--text-muted)",
  },
  count: {
    fontSize: 10,
    fontFamily: "var(--font-mono)",
    color: "var(--text-muted)",
    background: "var(--bg-elevated)",
    padding: "1px 5px",
    borderRadius: 8,
  },
  scroll: {
    flex: 1,
    overflowY: "auto",
  },
  empty: {
    padding: 16,
    textAlign: "center",
    color: "var(--text-muted)",
    fontSize: 11,
    lineHeight: 1.6,
    marginTop: 20,
  },
  newBadge: {
    display: "inline-block",
    width: 6,
    height: 6,
    borderRadius: "50%",
    background: "var(--accent)",
    animation: "pulse 1s ease 3",
    marginLeft: 4,
    verticalAlign: "middle",
  },
};

export default function OrderList({ selectedId, onSelect, newOrderIds }) {
  const [folder, setFolder] = useState("inbox");
  const [orders, setOrders] = useState([]);
  const [sent, setSent] = useState([]);
  const pollRef = useRef(null);

  const fetchOrders = () => {
    api.getLatestOrders(40)
      .then(setOrders)
      .catch(() => {});
  };

  const fetchSent = () => {
    api.getSentMessages(40)
      .then(setSent)
      .catch(() => {});
  };

  // Inbox: fetch on mount + poll every 20s (unchanged behavior).
  useEffect(() => {
    fetchOrders();
    pollRef.current = setInterval(fetchOrders, 20000);
    return () => clearInterval(pollRef.current);
  }, []);

  // Refresh inbox list when a new order arrives via WebSocket
  useEffect(() => {
    if (newOrderIds.size > 0) fetchOrders();
  }, [newOrderIds]);

  // Sent: fetch on demand when the Sent tab is selected. No WebSocket/poll —
  // there's no live-push for outbound messages in this codebase.
  useEffect(() => {
    if (folder === "sent") fetchSent();
  }, [folder]);

  const items = folder === "inbox" ? orders : sent;

  return (
    <div style={styles.pane}>
      <div style={styles.header}>
        <div style={styles.tabs}>
          <button
            style={styles.tab(folder === "inbox")}
            onClick={() => setFolder("inbox")}
          >
            Inbox
          </button>
          <button
            style={styles.tab(folder === "sent")}
            onClick={() => setFolder("sent")}
          >
            Sent
          </button>
        </div>
        {items.length > 0 && (
          <span style={styles.count}>{items.length}</span>
        )}
      </div>

      <div style={styles.scroll}>
        {folder === "inbox" ? (
          orders.length === 0 ? (
            <div style={styles.empty}>
              No inbound orders yet.
              <br />Waiting for mail…
            </div>
          ) : (
            orders.map(order => (
              <div key={order.order_id} style={{ position: "relative" }}>
                {newOrderIds.has(order.order_id) && (
                  <span
                    style={{
                      position: "absolute",
                      top: 8, right: 8,
                      width: 6, height: 6,
                      borderRadius: "50%",
                      background: "var(--accent)",
                      zIndex: 1,
                    }}
                  />
                )}
                <OrderCard
                  order={order}
                  selected={order.order_id === selectedId}
                  onClick={() => onSelect(order.order_id)}
                />
              </div>
            ))
          )
        ) : (
          sent.length === 0 ? (
            <div style={styles.empty}>
              No sent messages yet.
            </div>
          ) : (
            sent.map(message => (
              <SentItem key={message.id} message={message} />
            ))
          )
        )}
      </div>
    </div>
  );
}
