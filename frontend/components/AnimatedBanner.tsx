// frontend/components/AnimatedBanner.tsx
import React, { useEffect, useRef, useState } from "react";

/**
 * JS-driven AnimatedBanner
 * - Inline styles only (no external CSS or styled-jsx)
 * - White text forced
 * - Uses transform + opacity transitions so it works despite global CSS
 * - Logs to console for easy debugging
 */

const MESSAGES = [
  "Talk to the agent — say anything!",
  "Order coffee: “I’d like a large latte”",
  "Wellness check: “I feel low energy today”",
];

export default function AnimatedBanner({
  intervalMs = 1500,
  className = "",
}: {
  intervalMs?: number;
  className?: string;
}) {
  const [index, setIndex] = useState(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    const id = setInterval(() => {
      // advance index in a safe way
      setIndex((prev) => {
        const next = (prev + 1) % MESSAGES.length;
        // debug log
        // eslint-disable-next-line no-console
        console.debug("[AnimatedBanner] switch to", next, MESSAGES[next]);
        return next;
      });
    }, intervalMs);

    return () => {
      mounted.current = false;
      clearInterval(id);
    };
  }, [intervalMs]);

  // base container style (ensure banner is visible over any background)
  const containerStyle: React.CSSProperties = {
    position: "relative",
    height: 36,
    overflow: "hidden",
    display: "flex",
    alignItems: "center",
    userSelect: "none",
    zIndex: 50,
    width: "100%",
  };

  const itemCommon: React.CSSProperties = {
    position: "absolute",
    left: 0,
    top:12,
    width: "100%",
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
    padding: "0 6px",
    boxSizing: "border-box",
    transition: "transform 480ms cubic-bezier(.2,.9,.2,1), opacity 420ms ease",
    color: "#5DE2E7", // force white text
    textShadow: "0 2px 6px rgba(0,0,0,0.35)",
    fontSize: 16,
    fontWeight: 500,
  };

  return (
    <div className={className} style={containerStyle} aria-hidden>
      {MESSAGES.map((msg, i) => {
        const isActive = i === index;
        const style: React.CSSProperties = {
          ...itemCommon,
          transform: isActive ? "translateY(0) scale(1)" : "translateY(14px) scale(0.995)",
          opacity: isActive ? 1 : 0,
          pointerEvents: "none",
        };
        return (
          <div key={i} style={style} data-index={i}>
            {msg}
          </div>
        );
      })}
    </div>
  );
}
