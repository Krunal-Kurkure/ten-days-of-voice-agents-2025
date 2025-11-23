// frontend/app/api/latest-order/route.ts
import fs from "fs";
import path from "path";
import { NextResponse } from "next/server";

function probePaths() {
  // Candidate locations relative to where Next.js runs
  const candidates = [
    path.join(process.cwd(), "backend", "orders.json"),       // repo-root/frontend -> ../backend maybe
    path.join(process.cwd(), "..", "backend", "orders.json"), // repo-root/frontend => ../backend
    path.join(process.cwd(), "..", "..", "backend", "orders.json"),
    path.join(process.cwd(), "src", "backend", "orders.json"),
    path.join(process.cwd(), "../backend", "orders.json"),
  ];
  const found: string[] = [];
  for (const p of candidates) {
    if (fs.existsSync(p)) {
      found.push(p);
    }
  }
  return { candidates, found };
}

export async function GET() {
  try {
    const { candidates, found } = probePaths();
    if (found.length === 0) {
      return NextResponse.json({ ok: false, message: "no orders.json found", candidates, found });
    }
    const file = found[0];
    const raw = fs.readFileSync(file, "utf8");
    let data = null;
    try {
      data = JSON.parse(raw);
    } catch (e) {
      return NextResponse.json({ ok: false, message: "parse error", file, error: String(e) });
    }
    const last = Array.isArray(data) && data.length ? data[data.length - 1] : null;
    return NextResponse.json({ ok: true, file, order: last });
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e) });
  }
}
