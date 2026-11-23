import { mkdir,readFile,rename,rm,writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import { MetaReadonly,decryptToken,encryptToken,vaultKey } from "../lib/meta-readonly.ts";

process.umask(0o077);
const root=resolve(process.env.DATA_DIR||"data","meta-readonly");
const path=resolve(root,"token.enc");
async function atomic(file:string,data:Uint8Array|string) {
  await mkdir(root,{recursive:true,mode:0o700});
  const temp=resolve(root,randomUUID()+".tmp");
  try {await writeFile(temp,data,{mode:0o600,flag:"wx"});await rename(temp,file);}
  finally {await rm(temp,{force:true});}
}
try {
  const [action,consent]=process.argv.slice(2);
  if(action==="disconnect") {
    await rm(path,{force:true});await rm(resolve(root,"campaigns.json"),{force:true});
    console.log("Đã xóa token và dữ liệu nhập Meta trên máy này. Thu hồi token ở Meta nếu cần.");
  } else if(action==="store-token") {
    const token=process.env.META_ACCESS_TOKEN;
    if(!token||/\s/.test(token))throw Error("Thiếu token Meta hợp lệ từ secret manager.");
    await atomic(path,encryptToken(token,vaultKey(process.env.META_VAULT_KEY)));
    console.log("Đã lưu token mã hóa tại kho riêng. Không gọi Meta.");
  } else if(action==="inspect"||action==="import") {
    if(consent!=="--authorized-live-read")throw Error("Không đọc live nếu thiếu --authorized-live-read. Không dùng tài khoản của chủ repo cho demo.");
    const ciphertext=await readFile(path).catch(()=>{throw Error("Chưa lưu token Meta mã hóa.");});
    const provider=new MetaReadonly(process.env.META_AD_ACCOUNT_ID||"",decryptToken(ciphertext,vaultKey(process.env.META_VAULT_KEY)),process.env.META_GRAPH_VERSION);
    const account=await provider.account();
    if(action==="import") {
      const campaigns=await provider.campaigns();
      await atomic(resolve(root,"campaigns.json"),JSON.stringify({source:"meta-readonly",sync:new Date().toISOString(),account,...campaigns},null,2));
      console.log(`Đã lưu ${campaigns.items.length} chiến dịch chỉ đọc trong kho riêng${campaigns.truncated?" (trang còn lại chưa nhập)":""}. Không hiển thị trong demo.`);
    } else console.log(`Graph API read-only: currency=${account.currency}; timezone=${account.timezone_name}; status=${account.account_status}.`);
  } else throw Error("Lệnh hợp lệ: store-token, disconnect, inspect --authorized-live-read, import --authorized-live-read.");
} catch (err) {
  const e=err as Error & {code?:string};
  console.error(e.code?"Không thể truy cập kho Meta chỉ đọc. Kiểm tra quyền filesystem.":e.message);
  process.exitCode=1;
}
