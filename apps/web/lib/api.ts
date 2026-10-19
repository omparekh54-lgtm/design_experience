import { cache } from "react";
import type { DataStatus, Overview, Paper, Sandbox, Signals, TradeDetail, Universe, Json, Safety, Run } from "./console-types";
// Server-only reads: the private origin takes precedence. No operator credential is forwarded by GET.
const API_BASE = (process.env.PMS_API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
export type ApiResult<T> = {
    data: T | null;
    error: string | null;
    status?: number;
};
export const request = cache(async <T,>(path: string): Promise<ApiResult<T>> => {
    try {
        const response = await fetch(`${API_BASE}${path}`, { cache: "no-store", signal: AbortSignal.timeout(12000) });
        if (!response.ok) {
            return { data: null, status: response.status, error: response.status === 404 ? "No saved result was found for this view." : response.status < 500 ? "The requested view is not valid. Check the symbol and date." : "The data service is unavailable. Try refreshing shortly." };
        }
        return { data: (await response.json()) as T, error: null, status: response.status };
    }
    catch {
        return { data: null, error: "Unable to reach the data service. Refresh to try again." };
    }
});
function query(values: Record<string, string | number | undefined>) { const p = new URLSearchParams(); Object.entries(values).forEach(([k, v]) => { if (v !== undefined && v !== "")
    p.set(k, String(v)); }); return p.toString(); }
export const api = {
    overview: (d?: string) => request<Overview>(`/v1/console/overview?${query({ as_of: d })}`),
    signals: (d?: string, q?: string, view = "all", page = 1) => request<Signals>(`/v1/console/signals?${query({ as_of: d, q, view, page })}`),
    universe: (d?: string, q?: string, page = 1) => request<Universe>(`/v1/console/universe?${query({ as_of: d, q, page })}`),
    paper: () => request<Paper>("/v1/console/paper"),
    trade: (id: string) => request<TradeDetail>(`/v1/console/trades/${encodeURIComponent(id)}`),
    consoleData: () => request<DataStatus>("/v1/console/data"),
    sandbox: () => request<Sandbox>("/v1/execution/paper-sandbox/account"),
    health: () => request<Json>("/health"),
    moduleA: (instrument: string, d: string) => request<{
        snapshot: Json;
        decision: Json;
    }>(`/v1/module-a/${encodeURIComponent(instrument)}?as_of=${d}`),
    moduleB: (instrument: string, d: string) => request<Json>(`/v1/module-b/${encodeURIComponent(instrument)}?as_of=${d}`),
    backtests: () => request<{
        count: number;
        runs: Json[];
    }>("/v1/backtests"),
    backtest: (id: string) => request<{
        run: Json;
        fills: Json[];
    }>(`/v1/backtests/${encodeURIComponent(id)}`),
    researchRuns: () => request<{
        count: number;
        runs: Json[];
    }>("/v1/research/runs"),
    stress: () => request<{
        count: number;
        runs: Json[];
    }>("/v1/research/stress"),
    walkForward: () => request<{
        count: number;
        runs: Json[];
    }>("/v1/research/walk-forward"),
    executionSafety: () => request<Safety>("/v1/execution/safety"),
    executionReadiness: () => request<Json>("/v1/execution/readiness"),
    latestPaperValidation: () => request<Json>("/v1/execution/paper-validation/latest"),
    latestReconciliation: () => request<Json>("/v1/execution/reconciliation/latest"),
    workers: () => request<{
        count: number;
        runs: Run[];
    }>("/v1/execution/workers"),
    audit: (limit = 25) => request<{
        count: number;
        events: Json[];
    }>(`/v1/execution/audit?limit=${limit}`),
};
export function number(value: unknown): number | null { if (value === null || value === undefined || value === "" || typeof value === "boolean")
    return null; const n = Number(value); return Number.isFinite(n) ? n : null; }
export function formatMoney(value: unknown): string { const n = number(value); return n === null ? "—" : new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(n); }
export function formatNumber(value: unknown, digits = 2): string { const n = number(value); return n === null ? "—" : n.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits }); }
export function display(value: unknown): string { return value === null || value === undefined || value === "" ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value); }
export function record(value: unknown): Json { return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Json : {}; }
export function strings(value: unknown): string[] { return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : []; }
export function todayIST(): string { return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date()); }
export function timeIST(value: unknown): string { if (!value)
    return "—"; const d = new Date(String(value)); return Number.isNaN(d.getTime()) ? "—" : new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }).format(d); }
export function validDate(value: unknown): string { const d = String(value || ""); return /^\d{4}-\d{2}-\d{2}$/.test(d) && !Number.isNaN(Date.parse(d)) && new Date(d).toISOString().slice(0, 10) === d && d <= todayIST() ? d : todayIST(); }
export function positivePage(value: unknown): number { const p = Number(value); return Number.isInteger(p) && p > 0 && p <= 10000 ? p : 1; }
export function href(path: string, values: Record<string, string | number | undefined>): string { return `${path}?${query(values)}`; }
