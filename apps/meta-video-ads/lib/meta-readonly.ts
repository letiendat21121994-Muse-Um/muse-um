import { createCipheriv,createDecipheriv,randomBytes } from "node:crypto";
import { setTimeout } from "node:timers/promises";
import { z } from "zod";

export const graphVersion = "v27.0";
const accountId = z.string().regex(/^(?:act_)?[0-9]{1,32}$/);
const accountSchema = z.object({id:z.string(),name:z.string(),currency:z.string().length(3),timezone_name:z.string(),account_status:z.number()});
const campaign = z.object({id:z.string(),name:z.string(),objective:z.string(),effective_status:z.string()});
const page = z.object({data:z.array(campaign),paging:z.object({
  next:z.string().optional(),cursors:z.object({after:z.string().max(4096).optional()}).optional(),
}).optional()});
const metaError = z.object({error:z.object({code:z.number().optional(),is_transient:z.boolean().optional()})});

const separator = Buffer.from("mva:meta-readonly:v1");
export function vaultKey(input: string | undefined) {
  if (!input || !/^[a-f0-9]{64}$/i.test(input)) throw new Error("META_VAULT_KEY phải có 64 ký tự hex ngẫu nhiên.");
  return Buffer.from(input,"hex");
}
export function encryptToken(token: string,key: Buffer) {
  const iv=randomBytes(12);
  const cipher=createCipheriv("aes-256-gcm",key,iv);
  cipher.setAAD(separator);
  const encrypted=Buffer.concat([cipher.update(token,"utf8"),cipher.final()]);
  return Buffer.concat([iv,cipher.getAuthTag(),encrypted]);
}
export function decryptToken(ciphertext:Buffer,key:Buffer) {
  if(ciphertext.length<=28)throw new Error("Kho token Meta không hợp lệ.");
  try {
    const d=createDecipheriv("aes-256-gcm",key,ciphertext.subarray(0,12));
    d.setAAD(separator);d.setAuthTag(ciphertext.subarray(12,28));
    return Buffer.concat([d.update(ciphertext.subarray(28)),d.final()]).toString("utf8");
  } catch {throw new Error("Không mở được kho token Meta. Kiểm tra META_VAULT_KEY.");}
}

export class MetaReadonly {
  #token:string;
  #base:string;
  #request:typeof fetch;
  constructor(account: string, token: string, version=graphVersion, request=fetch) {
    if (!/^v\d+\.0$/.test(version)) throw new Error("Phiên bản Graph API không hợp lệ.");
    const parsed=accountId.safeParse(account);
    if(!parsed.success) throw new Error("ID tài khoản quảng cáo phải là ID số hoặc act_ID.");
    if(!token || /[\r\n\s]/.test(token)) throw new Error("Token server không hợp lệ.");
    this.#base=`https://graph.facebook.com/${version}/act_${parsed.data.replace(/^act_/,"")}`;
    this.#token=token;
    this.#request=request;
  }
  async #get<T>(edge:string, params:Record<string,string>, schema:z.ZodType<T>) {
    const url=this.#base+edge+"?"+new URLSearchParams(params);
    for(let attempt=1;attempt<=3;attempt++){
      let response:Response;
      try {
        response=await this.#request(url,{
          method:"GET",redirect:"error",signal:AbortSignal.timeout(15_000),
          headers:{Authorization:"Bearer "+this.#token,Accept:"application/json"},
        });
      } catch {
        if(attempt===3)throw new Error("Không liên hệ được Graph API.");
        await setTimeout(500*2**(attempt-1));continue;
      }
      const data:unknown=await response.json().catch(()=>null);
      const err=metaError.safeParse(data);
      if(response.ok&&!err.success) {
        const validated=schema.safeParse(data);
        if(!validated.success)throw new Error("Graph API trả về dữ liệu không đúng định dạng.");
        return validated.data;
      }
      const code=err.success?err.data.error.code:undefined;
      if(response.status===401||code===190||code===102)throw new Error("Token Meta đã hết hạn, không hợp lệ hoặc bị thu hồi. Ngắt kết nối và cấp lại quyền.");
      const rate=response.status===429||code===4||code===17||code===32||code===613;
      const transient=rate||response.status>=500||(err.success&&err.data.error.is_transient);
      if(transient&&attempt<3){
        const retryHeader=Number(response.headers.get("retry-after")||"0");
        if(retryHeader>15)throw new Error("Graph API đang giới hạn yêu cầu. Chờ và chạy lại.");
        await setTimeout(Math.max(500*2**(attempt-1),retryHeader*1000));continue;
      }
      throw new Error(rate?"Graph API đang giới hạn yêu cầu. Chờ và chạy lại.":response.status===403||code===200?
        "Không có quyền đọc tài khoản quảng cáo. Kiểm tra ads_read và quyền tài sản.":"Graph API không hoàn thành yêu cầu đọc.");
    }
    throw new Error("Graph API không khả dụng.");
  }
  account(){return this.#get("",{fields:"id,name,currency,timezone_name,account_status"},accountSchema);}
  async campaigns() {
    const list:z.infer<typeof campaign>[]=[];
    const seen=new Set<string>();
    let after="";
    for(let i=0;i<10;i++){
      const result=await this.#get("/campaigns",{fields:"id,name,objective,effective_status",limit:"50",...(after?{after}:{})},page);
      list.push(...result.data);
      if(!result.paging?.next)return {items:list,truncated:false};
      const next=result.paging.cursors?.after;
      if(!next||seen.has(next))return {items:list,truncated:true};
      seen.add(next);after=next;
    }
    return {items:list,truncated:true};
  }
}
