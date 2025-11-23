// frontend/app/order/page.tsx
"use client";

import { useEffect, useState } from "react";

type Order = {
  drinkType?: string;
  size?: string;
  milk?: string;
  extras?: string[];
  name?: string;
  timestamp?: string;
};

export default function OrderPage() {
  const [order, setOrder] = useState<Order | null>(null);
  const [loading, setLoading] = useState(true);
  const [debug, setDebug] = useState<any>(null);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const res = await fetch("/api/latest-order");
        const json = await res.json();
        setDebug(json);
        if (json.ok && json.order) setOrder(json.order);
        else setOrder(null);
      } catch (e) {
        setOrder(null);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  return (
    <div style={{ padding: 24, fontFamily: "system-ui, sans-serif" }}>
      <h1>Latest Order</h1>
      {loading && <p>Loading...</p>}

      {!loading && !order && (
        <div>
          <p>No orders yet.</p>
          <pre style={{ whiteSpace: "pre-wrap", background: "#f3f4f6", padding: 8 }}>
            Debug: {JSON.stringify(debug, null, 2)}
          </pre>
        </div>
      )}

      {!loading && order && (
        <div style={{ maxWidth: 520, border: "1px solid #e5e7eb", padding: 16, borderRadius: 8 }}>
          <h2 style={{ marginTop: 0 }}>{order.name ?? "Customer"}</h2>
          <p style={{ margin: "6px 0" }}>
            <strong>Drink:</strong> {order.drinkType ?? "-"}
          </p>
          <p style={{ margin: "6px 0" }}>
            <strong>Size:</strong> {order.size ?? "-"}
          </p>
          <p style={{ margin: "6px 0" }}>
            <strong>Milk:</strong> {order.milk ?? "-"}
          </p>
          <p style={{ margin: "6px 0" }}>
            <strong>Extras:</strong> {order.extras && order.extras.length ? order.extras.join(", ") : "None"}
          </p>
          <p style={{ marginTop: 12, color: "#6b7280" }}>
            {order.timestamp ? new Date(order.timestamp).toLocaleString() : ""}
          </p>
        </div>
      )}
    </div>
  );
}
