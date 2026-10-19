import {NextRequest, NextResponse} from "next/server";
import {placePaperOrder, processPaperOrders, cancelPaperOrder, disablePaperOrders, emergencyStop} from "@/app/paper-trading/actions";
import {boundedForm} from "@/lib/bounded-form";
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
        const form = await boundedForm(request);
        const operation = String(form.get("operation") || "");
        if (!Object.hasOwn(operations, operation)) return NextResponse.json({ok:false,message:"Unsupported operator action."},{status:400});
        const result = await operations[operation as keyof typeof operations](initialActionState,form);
        return NextResponse.json(result,{status:result.ok?200:400,headers:{"Cache-Control":"no-store"}});
    } catch(error) {
        if(error instanceof Error && ['BODY_LIMIT','BODY_TIMEOUT'].includes(error.message))
            return NextResponse.json({ok:false,message:'The request body was too large or timed out.'},{status:error.message==='BODY_LIMIT'?413:408,headers:{'Cache-Control':'no-store'}});
        return NextResponse.json({ok:false,message:"The request could not be confirmed. Refresh the account before retrying."},{status:500});
    }
}
