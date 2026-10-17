"use server";

import { revalidatePath } from "next/cache";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || process.env.PMS_API_BASE_URL || "http://127.0.0.1:8000";

async function post(path: string, body: unknown) {
  const key = process.env.OPS_API_KEY || "";
  if (!key) throw new Error("OPS_API_KEY is not configured on the dashboard service");
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-ops-key": key,
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

export async function placePaperOrder(formData: FormData) {
  const symbol = String(formData.get("symbol") || "").trim().toUpperCase();
  const side = String(formData.get("side") || "BUY").toUpperCase();
  const quantity = Number(formData.get("quantity") || 0);
  const orderType = String(formData.get("order_type") || "MARKET").toUpperCase();
  const rawLimit = String(formData.get("limit_price") || "").trim();
  const rawTrigger = String(formData.get("trigger_price") || "").trim();
  if (!symbol) throw new Error("Symbol is required");
  if (!Number.isInteger(quantity) || quantity <= 0) throw new Error("Quantity must be a positive whole number");
  await post("/v1/execution/paper-sandbox/orders", {
    instrument_id: symbol.includes(":") ? symbol : `NSE:${symbol}`,
    side,
    quantity,
    order_type: orderType,
    limit_price: rawLimit ? Number(rawLimit) : null,
    trigger_price: rawTrigger ? Number(rawTrigger) : null,
  });
  revalidatePath("/paper-trading");
}

export async function processPaperOrders() {
  await post("/v1/execution/paper-sandbox/process", {});
  revalidatePath("/paper-trading");
}

export async function cancelPaperOrder(formData: FormData) {
  const orderId = String(formData.get("order_id") || "").trim();
  if (!orderId) throw new Error("Order ID is required");
  await post(`/v1/execution/paper-sandbox/orders/${encodeURIComponent(orderId)}/cancel`, {});
  revalidatePath("/paper-trading");
}
