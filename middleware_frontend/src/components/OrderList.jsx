/**
 * src/components/OrderList.jsx
 *
 * Left pane — scrollable list of inbound orders, newest first.
 * New orders flash briefly when they arrive via WebSocket push.
 * Polling fallback refreshes every 20s if WebSocket is unavailable.
 */
import { useEffect, useRef, useState } from "react";
import OrderCard from "./OrderCard";
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
  const [orders, setOrders] = useState([]);
  const pollRef = useRef(null);

  const fetchOrders = () => {
    api.getLatestOrders(40)
      .then(setOrders)
      .catch(() => {});
  };

  useEffect(() => {
    fetchOrders();
    pollRef.current = setInterval(fetchOrders, 20000);
    return () => clearInterval(pollRef.current);
  }, []);

  // Refresh list when a new order arrives via WebSocket
  useEffect(() => {
    if (newOrderIds.size > 0) fetchOrders();
  }, [newOrderIds]);

  return (
    <div style={styles.pane}>
      <div style={styles.header}>
        <span style={styles.headerTitle}>Inbound</span>
        {orders.length > 0 && (
          <span style={styles.count}>{orders.length}</span>
        )}
      </div>

      <div style={styles.scroll}>
        {orders.length === 0 ? (
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
        )}
      </div>
    </div>
  );
}
