import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp,readFile,rm } from "node:fs/promises";
import { createServer } from "node:net";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { setTimeout } from "node:timers/promises";
import type { BatchView,Preview } from "../lib/workspace.ts";
import type { Report } from "../lib/reporting.ts";

const listener=createServer().listen(0,"127.0.0.1");
await once(listener,"listening");
const addr=listener.address();
assert(addr&&typeof addr!=="string");
const port=addr.port;
await new Promise<void>(resolve=>listener.close(()=>resolve()));
const data=await mkdtemp(join(tmpdir(),"meta-video-http-"));
const processTest=spawn(process.execPath,["scripts/run.mjs","start"],{stdio:"inherit",env:{
  ...process.env,PORT:String(port),HOST:"127.0.0.1",DATA_DIR:data,SECURE_COOKIE:"false",GEMINI_API_KEY:"",
}});
const closed=once(processTest,"close");
const origin=`http://127.0.0.1:${port}`;
const call=(path:string,cookie="",init:RequestInit={})=>{
  const headers=new Headers(init.headers);
  if(cookie)headers.set("cookie",cookie);
  return fetch(origin+"/api/"+path,{...init,headers});
};
const post=(path:string,cookie:string,data:unknown)=>call(path,cookie,{method:"POST",headers:{"content-type":"application/json","x-demo-request":"1"},body:JSON.stringify(data)});
async function ok<T>(promise:Promise<Response>,expected=200){
  const res=await promise;
  const body=await res.json();
  assert.equal(res.status,expected,JSON.stringify(body));
  return body as T;
}
async function waitFor<T>(find:()=>Promise<T|undefined>,error:string,timeout=25_000){
  const until=Date.now()+timeout;
  while(Date.now()<until){
    const result=await find();
    if(result)return result;
    await setTimeout(180);
  }
  throw new Error(error);
}
async function session(){
  const raw=await post("session","",{});
  assert.equal(raw.status,200);
  const cookie=raw.headers.get("set-cookie");
  assert.ok(cookie?.includes("HttpOnly"));assert.ok(cookie?.includes("SameSite=strict"));
  return cookie!.split(";")[0];
}
try {
  await waitFor(async()=>{try{return await fetch(origin).then(r=>r.ok||undefined);}catch{return undefined;}},"Web app chưa mở.");
  assert.equal((await call("workspace")).status,401);
  assert.equal((await call("session","",{method:"POST"})).status,403);
  const cookie=await session(), second=await session();
  const w=await ok<{batches:{id:string}[]}>(call("workspace",cookie));
  const id=w.batches[0].id, base="batches/"+id;
  const show=()=>ok<BatchView>(call(base,cookie));
  assert.equal((await call(base,second)).status,404);
  assert.equal((await post(base+"/samples",second,{keys:["guide"]})).status,404);
  const bad=await ok<{id:string}>(call(base+"/upload",cookie,{method:"POST",headers:{
    "content-type":"video/mp4","x-demo-request":"1","x-video-name":"invalid.mp4",
  },body:"not a video"}),201);
  const data=await readFile("public/samples/texture.mp4");
  const real=await ok<{id:string}>(call(base+"/upload",cookie,{method:"POST",headers:{
    "content-type":"video/mp4","x-demo-request":"1","x-video-name":encodeURIComponent("Tải thử.mp4"),
  },body:new Uint8Array(data)}),201);
  await ok(post(base+"/context",cookie,{url:"https://example.org/original"}));
  const samples=await ok<{ids:string[]}>(post(base+"/samples",cookie,{keys:["ritual","guide"]}),202);
  const processed=await waitFor(async()=>{
    const batch=await show();
    return batch.videos.filter(v=>v.status==="ready").length===3 && batch.videos.some(v=>v.id===bad.id&&v.status==="failed")?batch:undefined;
  },"Không xử lý đủ video; tệp hỏng không được chặn hàng đợi.");
  assert.equal(processed.videos.find(v=>v.id===real.id)?.analysis?.observations.onScreen,"");
  const range=await call("media/"+real.id,cookie,{headers:{Range:"bytes=0-12"}});
  assert.equal(range.status,206);assert.equal((await range.arrayBuffer()).byteLength,13);
  assert.equal((await call("media/"+real.id+"/thumb",cookie)).status,200);
  assert.equal((await call("media/"+real.id,second)).status,404);
  const variant=processed.videos.find(v=>v.id===samples.ids[0])!.variants[0];
  assert.equal((await post("variants/"+variant.id+"/edit",second,{selected:true})).status,404);
  await ok(post("variants/"+variant.id+"/edit",cookie,{selected:true,copy:{...variant.data,headline:"Bản đã chỉnh"}}));
  await ok(post("videos/"+variant.video_id+"/regenerate",cookie,{}),202);
  const regenerated=await waitFor(async()=>{
    const batch=await show(), v=batch.videos.find(v=>v.id===variant.video_id)!;
    return !v.regenerating&&v.variants.length===5?v:undefined;
  },"Không tạo lại copy.");
  assert.equal(regenerated.variants.find(c=>c.id===variant.id)?.data.headline,"Bản đã chỉnh");
  await ok(post("videos/"+variant.video_id+"/edit",cookie,{included:true,group_key:"shared",destination:"",tracking:"ref=manual"}));
  await ok(post("videos/"+variant.video_id+"/context",cookie,{url:"https://example.org/individual"}));
  await ok(post(base+"/bulk",cookie,{ids:[variant.video_id],all:false,overwrite:false,group:"other",destination:"https://example.com/bulk",tracking:"ref=bulk"}));
  const retained=(await show()).videos.find(v=>v.id===variant.video_id)!;
  assert.equal(retained.group_key,"shared");
  assert.equal(retained.tracking,"ref=manual");
  assert.equal(retained.overrides.url,"https://example.org/individual");
  assert.equal(retained.destination,"");
  const review=await ok<Preview>(call(base+"/preview",cookie));
  assert.equal(review.plan.length,3);
  assert.equal(review.plan.find(ad=>ad.videoId===real.id)?.destination,"https://example.org/original");
  assert.equal(review.excluded[0].videoId,bad.id);
  assert.equal(review.budget,processed.config.budget);
  assert.equal((await post(base+"/publish",cookie,{confirm:false,reviewKey:review.reviewKey})).status,400);
  const publish=()=>ok<{campaign:string}>(post(base+"/publish",cookie,{confirm:true,reviewKey:review.reviewKey,simulateFailure:true}),202);
  const [first,repeated]=await Promise.all([publish(),publish()]);
  assert.equal(first.campaign,repeated.campaign);
  const partial=await waitFor(async()=>{
    const b=await show();
    return b.ads.filter(a=>a.state==="simulated").length===2&&b.ads.some(a=>a.state==="failed")?b:undefined;
  },"Không tái hiện được partial failure.");
  const preserved=partial.ads.filter(a=>a.state==="simulated").map(a=>a.ad_ref);
  await ok(post(base+"/retry",cookie,{}),202);
  const done=await waitFor(async()=>{
    const b=await show();return b.ads.length===3&&b.ads.every(a=>a.state==="simulated")?b:undefined;
  },"Retry không hoàn tất.");
  assert.ok(preserved.every(ref=>done.ads.some(a=>a.ad_ref===ref)));
  assert.equal(done.ads.find(a=>a.variant_id===variant.id)?.snapshot.copy.headline,"Bản đã chỉnh");
  await waitFor(async()=>{
    const report=await ok<Report>(call("report?batch="+id,cookie));
    return report.rows.length===21?report:undefined;
  },"Báo cáo không có đủ snapshot.");
  assert.equal((await call("report?batch="+id,second)).status,404);
  assert.equal((await call("report/csv?batch="+id,cookie)).headers.get("content-type"),"text/csv; charset=utf-8");
  console.log("PASS: isolated sessions → upload/processing → preserve edits → validate/publish → partial-failure retry → reporting/CSV");
} finally {
  processTest.kill("SIGTERM");
  await closed;
  await rm(data,{recursive:true,force:true});
}
