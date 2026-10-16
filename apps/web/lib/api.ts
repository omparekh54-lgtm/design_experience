const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || process.env.PMS_API_BASE_URL || "http://127.0.0.1:8000";

export type ApiResult<T> = { data: T | null; error: string | null };

async function request<T>(path: string): Promise<ApiResult<T>> {
  try {
    const response = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
    if (!response.ok) {
      const detail = await response.text();
      return { data: null, error: detail || `${response.status} ${response.statusText}` };
    }
    return { data: (await response.json()) as T, error: null };
  } catch (error) {
    return { data: null, error: error instanceof Error ? error.message : "API request failed" };
  }
}

export const api = {
  health: () => request<Record<string, unknown>>("/health"),
  portfolio: () => request<any>("/v1/portfolio"),
  positions: () => request<{ count: number; positions: any[] }>("/v1/positions"),
  orders: () => request<{ execution_enabled: boolean; count: number; order_candidates: any[] }>("/v1/orders"),
  dataHealth: () => request<any>("/v1/data-health"),
  moduleE: (d: string) => request<any>(`/v1/module-e/${d}`),
  moduleC: (d: string, selectedOnly = true) => request<{ trade_date: string; count: number; rankings: any[] }>(`/v1/module-c?trade_date=${d}&selected_only=${selectedOnly}`),
  moduleA: (instrument: string, d: string) => request<any>(`/v1/module-a/${encodeURIComponent(instrument)}?as_of=${d}`),
  moduleB: (instrument: string, d: string) => request<any>(`/v1/module-b/${encodeURIComponent(instrument)}?as_of=${d}`),
  backtests: () => request<{ count: number; runs: any[] }>("/v1/backtests"),
  researchRuns: () => request<{ count: number; runs: any[] }>("/v1/research/runs"),
  stress: () => request<{ count: number; runs: any[] }>("/v1/research/stress"),
  walkForward: () => request<{ count: number; runs: any[] }>("/v1/research/walk-forward"),
};

export function formatMoney(value: unknown): string {
  const n = Number(value ?? 0);
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(n);
}

export function formatNumber(value: unknown, digits = 2): string {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
}
