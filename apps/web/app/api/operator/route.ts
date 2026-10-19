import {NextRequest, NextResponse} from "next/server";
import {placePaperOrder, processPaperOrders, cancelPaperOrder, disablePaperOrders, emergencyStop} from "@/app/paper-trading/actions";
import {initialActionState} from "@/lib/action-state";

export const dynamic = "force-dynamic";
const operations = {create:placePaperOrder, process:processPaperOrders, cancel:cancelPaperOrder, disable:disablePaperOrders, stop:emergencyStop};
export async function POST(request:NextRequest) {
    // Never turn this into a privileged environment-key proxy. Each request supplies its own key.
    const origin = request.headers.get("origin");
    let trusted = false;
    try { trusted = !!origin && new URL(origin).host === request.headers.get("host") && ["http:","https:"].includes(new URL(origin).protocol); } catch {}
    if (!trusted) return NextResponse.json({ok:false,message:"The request origin was not accepted."},{status:403});
    try {
        const form = await request.formData();
        const operation = String(form.get("operation") || "");
        if (!Object.hasOwn(operations, operation)) return NextResponse.json({ok:false,message:"Unsupported operator action."},{status:400});
        const result = await operations[operation as keyof typeof operations](initialActionState,form);
        return NextResponse.json(result,{status:result.ok?200:400,headers:{"Cache-Control":"no-store"}});
    } catch {
        return NextResponse.json({ok:false,message:"The request could not be confirmed. Refresh the account before retrying."},{status:500});
    }
}
