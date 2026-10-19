"use client";
import { useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { initialActionState, type ActionState } from "@/lib/action-state";
type ActionKind = "create" | "process" | "cancel" | "disable" | "stop";
export function ActionForm({ kind, orderId, requestId }: {
    kind: ActionKind;
    orderId?: string;
    requestId?: string;
}) { const [state, setState] = useState<ActionState>(initialActionState);
    const [pending, setPending] = useState(false);
    const [token, setToken] = useState(requestId);
    const inFlight = useRef(false);
    const router = useRouter();
    async function submit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        if (inFlight.current) return;
        const form = event.currentTarget;
        const body = new FormData(form);
        inFlight.current = true;
        setPending(true);
        setState(initialActionState);
        try {
            const response = await fetch('/api/operator', {method:'POST', body, signal:AbortSignal.timeout(20000)});
            const result = await response.json() as ActionState;
            setState(result);
            if (result.ok) {
                form.reset();
                setToken(crypto.randomUUID());
                router.refresh();
            }
        } catch {
            setState({ok:false,message:'The request could not be confirmed. Refresh the account before retrying.'});
        } finally {
            inFlight.current = false;
            setPending(false);
        }
    }
     const [type, setType] = useState('MARKET'); const labels = { create: 'Create sandbox order', process: 'Process saved market bar', cancel: 'Cancel sandbox order', disable: 'Disable new orders', stop: 'Record emergency stop' }; return <form action="/api/operator" method="post" onSubmit={submit} className="action-form"><input type="hidden" name="operation" value={kind}/><div className="form-grid">{kind === 'create' ? <><input type="hidden" name="request_id" value={token}/><label>NSE symbol<input name="symbol" required maxLength={34} placeholder="RELIANCE" autoCapitalize="characters"/></label><label>Side<select name="side"><option>BUY</option><option>SELL</option></select></label><label>Shares<input name="quantity" type="number" min="1" max="10000000" step="1" defaultValue="1" required/></label><label>Order type<select name="order_type" value={type} onChange={e => setType(e.target.value)}><option>MARKET</option><option>LIMIT</option><option>STOP_MARKET</option></select></label>{type === 'LIMIT' ? <label>Limit price<input name="limit_price" type="number" step="0.01" min="0.01" required/></label> : null}{type === 'STOP_MARKET' ? <label>Buy / sell stop trigger<input name="trigger_price" type="number" step="0.01" min="0.01" required/></label> : null}</> : null}{kind === 'cancel' ? <input type="hidden" name="order_id" value={orderId}/> : null}{kind === 'stop' ? <><label className="full">Reason<input name="reason" required minLength={3} maxLength={200} placeholder="Describe the condition requiring a stop"/></label><label className="full checkbox"><input type="checkbox" name="confirm" value="yes" required/>I confirm that new entries should be blocked.</label></> : null}<label className="full">Operator key<input type="password" name="operator_key" autoComplete="off" required maxLength={256} placeholder="Required for this action"/></label><button className={`link-button ${kind === 'stop' ? 'danger' : ''}`} type="submit" disabled={pending}>{pending ? 'Submitting…' : labels[kind]}</button></div>{state.message ? <div className={`action-feedback ${state.ok ? 'good' : 'bad'}`} role={state.ok ? 'status' : 'alert'}>{state.message}</div> : null}<p className="form-help">Authorization is checked by the API. Your key is not stored in the form result.</p></form>; }
