// Bound multipart parsing before it can reach an authenticated operator action.
export async function boundedForm(request:Request):Promise<FormData>{
 const limit=1024*1024,length=request.headers.get('content-length');
 if(length && (!/^\d+$/.test(length)||Number(length)>limit))throw new Error('BODY_LIMIT');
 const reader=request.body?.getReader();const parts:Uint8Array[]=[];let size=0;
 if(reader){
   let timer:ReturnType<typeof setTimeout>|undefined;
   const timeout=new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error('BODY_TIMEOUT')),15000);});
   try {for(;;){const {done,value}=await Promise.race([reader.read(),timeout]);if(done)break;size+=value.byteLength;if(size>limit)throw new Error('BODY_LIMIT');parts.push(value);}}
   catch(error){void reader.cancel().catch(()=>{});throw error;}
   finally{if(timer)clearTimeout(timer);}
 }
 const body=new Uint8Array(size);let offset=0;for(const part of parts){body.set(part,offset);offset+=part.byteLength;}
 return new Response(body,{headers:{'content-type':request.headers.get('content-type')||''}}).formData();
}
