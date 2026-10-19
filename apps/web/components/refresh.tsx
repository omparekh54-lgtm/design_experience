"use client";
import { useTransition } from "react";
import { useRouter } from "next/navigation";
export function Refresh() { const router = useRouter(); const [pending, start] = useTransition(); return <button className="link-button secondary" disabled={pending} onClick={() => start(() => router.refresh())} aria-label="Refresh saved data">{pending ? 'Refreshing…' : '↻ Refresh'}</button>; }
