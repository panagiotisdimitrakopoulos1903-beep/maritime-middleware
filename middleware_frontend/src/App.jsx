/**
 * src/App.jsx — root component
 *
 * Layout:
 *   [ StatusBar (top, full width)        ]
 *   [ OrderList (left) | MatchPanel (right) ]
 *
 * Manages:
 *   - WebSocket connection + push handling
 *   - Selected order state
 *   - Loading state for match fetch
 */
import { useEffect, useRef, useState, useCallback } from "react";
import StatusBar from "./components/StatusBar";
import OrderList from "./components/OrderList";
import MatchPanel from "./components/MatchPanel";
import { api, connectWebSocket, subscribeToMatches } from "./lib/api";

const styles = {
  root: {
    display: "flex",
    flexDirection: "column",
    height: "100vh",
    background: "var(--bg-base)",
    overflow: "hidden",
  },
  body: {
    display: "flex",
    flex: 1,
    overflow: "hidden",
  },
};

export default function App() {
  const [wsConnected, setWsConnected] = useState(false);
  const [selectedOrderId, setSelectedOrderId] = useState(null);
  const [selectedOrder, setSelectedOrder] = useState(null);
  const [loadingMatch, setLoadingMatch] = useState(false);
  const [newOrderIds, setNewOrderIds] = useState(new Set());

  const clearNewTimer = useRef(null);

  // ── WebSocket setup ─────────────────────────────────────────────────────────
  useEffect(() => {
    // Poll connection state
    const checkInterval = setInterval(() => {
      // We infer connection state from whether push messages arrive
      // A proper impl would expose ws.readyState, but this is sufficient
    }, 5000);

    connectWebSocket();

    const unsub = subscribeToMatches((data) => {
      setWsConnected(true);

      // Mark the new order as highlighted
      setNewOrderIds(prev => {
        const next = new Set(prev);
        next.add(data.order_id);
        return next;
      });

      // Auto-select the new order if nothing is currently selected
      setSelectedOrderId(prev => {
        if (!prev) {
          loadOrder(data.order_id);
          return data.order_id;
        }
        return prev;
      });

      // Clear new badge after 8s
      if (clearNewTimer.current) clearTimeout(clearNewTimer.current);
      clearNewTimer.current = setTimeout(() => {
        setNewOrderIds(new Set());
      }, 8000);
    });

    return () => {
      unsub();
      clearInterval(checkInterval);
    };
  }, []);

  // ── Load a specific order's matches ────────────────────────────────────────
  const loadOrder = useCallback((orderId) => {
    setLoadingMatch(true);
    setSelectedOrder(null);
    api.getMatches(orderId)
      .then(data => {
        setSelectedOrder(data);
        setLoadingMatch(false);
        setWsConnected(true);
      })
      .catch(() => {
        setLoadingMatch(false);
      });
  }, []);

  const handleSelectOrder = useCallback((orderId) => {
    if (orderId === selectedOrderId) return;
    setSelectedOrderId(orderId);
    loadOrder(orderId);
    // Clear new badge when broker opens the order
    setNewOrderIds(prev => {
      const next = new Set(prev);
      next.delete(orderId);
      return next;
    });
    // Mark read (ADR 0004, Decision #7) — fire-and-forget, local-only,
    // no bearing on order selection if it fails.
    api.markOrderRead(orderId);
  }, [selectedOrderId, loadOrder]);

  return (
    <div style={styles.root}>
      <StatusBar connected={wsConnected} />
      <div style={styles.body}>
        <OrderList
          selectedId={selectedOrderId}
          onSelect={handleSelectOrder}
          newOrderIds={newOrderIds}
        />
        <MatchPanel
          order={selectedOrder}
          loading={loadingMatch}
        />
      </div>
    </div>
  );
}
