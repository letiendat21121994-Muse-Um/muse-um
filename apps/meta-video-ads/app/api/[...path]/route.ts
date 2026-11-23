import { createReadStream } from "node:fs";
import { stat, unlink } from "node:fs/promises";
import { resolve } from "node:path";
import { Readable } from "node:stream";
import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { db, type Db } from "../../../lib/db";
import { aiEnabled } from "../../../lib/ai";
import { addVideo, addVideos, absent, assertDraft, audit, batchView, createBatch, enqueue, InputError, newSession, ownedBatch, ownedVariant,
  ownedVideo, preview, publish, session, sessionAge, type Batch } from "../../../lib/workspace";
import { bytesLimit, durationLimit, mimeSchema, saveStream } from "../../../lib/media";
import { configSchema, contextSchema, copySchema, dayInZone, httpUrl, trackingValid } from "../../../lib/domain";
import { retryPublish } from "../../../lib/jobs";
import { report, reportCsv } from "../../../lib/reporting";
import { samples } from "../../../lib/samples";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
type Route = { params: Promise<{path: string[]}> };
const cookie = "mva_demo";
const json = (data: unknown, status=200) => NextResponse.json(data,{ status, headers: { "Cache-Control": "no-store" } });
const noStore = { "Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff" };
const id = z.uuid();
const name = z.string().trim().min(1).max(120);
const group = z.string().regex(/^[a-z0-9-]{1,32}$/);
const url = z.union([z.literal(""),httpUrl]);
const tracking = z.string().trim().max(500).refine(trackingValid,"Tracking phải có dạng key=value.");
const date = z.iso.date();
async function payload<T>(request: NextRequest, schema: z.ZodType<T>) {
  if (!request.headers.get("content-type")?.startsWith("application/json")) throw new InputError("Cần dữ liệu JSON.");
  if (!request.body) throw new InputError("Thiếu dữ liệu.");
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const part = await reader.read();
    if (part.done) break;
    size += part.value.length;
    if (size > 96 * 1024) { await reader.cancel(); throw new InputError("Dữ liệu quá dài.",413); }
    chunks.push(part.value);
  }
  let parsed: unknown;
  try { parsed = JSON.parse(Buffer.concat(chunks).toString("utf8")); }
  catch { throw new InputError("JSON không hợp lệ."); }
  return schema.parse(parsed);
}
function owner(d: Db, request: NextRequest) {
  const id = session(d,request.cookies.get(cookie)?.value);
  if (!id) throw new InputError("Phiên demo đã hết hạn hoặc chưa được tạo.",401);
  return id;
}
async function media(d: Db, request: NextRequest, user: string, video: string, thumb: boolean) {
  const v = ownedVideo(d,user,video);
  if (v.status === "uploading" || v.status === "upload_failed" || (thumb && !v.thumb)) throw absent();
  if (v.sample && !samples.some(s => s.key === v.sample)) throw absent();
  const path = v.sample && !thumb ? resolve("public/samples",v.sample+".mp4") : d.media(v.id,thumb);
  const info = await stat(path).catch(() => { throw absent(); });
  const headers: Record<string,string> = { ...noStore, "Content-Type":thumb ? "image/jpeg" : v.mime, "Accept-Ranges":"bytes",
    "Content-Security-Policy":"default-src 'none'; frame-ancestors 'self'" };
  let start=0, end=info.size-1, status=200;
  const range = request.headers.get("range");
  if (range) {
    const match = /^bytes=(\d*)-(\d*)$/.exec(range);
    if (match && (match[1] || match[2])) {
      start = match[1] ? Number(match[1]) : Math.max(0,info.size-Number(match[2]));
      if (match[1] && match[2]) end = Math.min(end,Number(match[2]));
    } else start = info.size;
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start>end || start>=info.size) {
      return new Response(null,{status:416,headers:{...headers,"Content-Range":`bytes */${info.size}`}});
    }
    headers["Content-Range"] = `bytes ${start}-${end}/${info.size}`;
    status=206;
  }
  headers["Content-Length"]=String(end-start+1);
  return new Response(Readable.toWeb(createReadStream(path,{start,end})) as ReadableStream,{status,headers});
}
async function upload(d: Db, request: NextRequest, batch: Batch) {
  const mime = mimeSchema.parse(request.headers.get("content-type"));
  const length = Number(request.headers.get("content-length") ?? "0");
  if (length > bytesLimit()) throw new InputError("Video vượt dung lượng cho phép.",413);
  let raw: string;
  try { raw = decodeURIComponent(request.headers.get("x-video-name") ?? ""); }
  catch { throw new InputError("Tên tệp không hợp lệ."); }
  const filename = z.string().trim().min(1).max(200).parse(raw.replace(/[\u0000-\u001F\u007F/\\]/g,"_"));
  if (!/\.(mp4|mov|webm)$/i.test(filename)) throw new InputError("Chọn tệp MP4, MOV hoặc WebM.");
  const useAi = request.headers.get("x-video-ai") === "gemini";
  if (useAi && !aiEnabled()) throw new InputError("Máy chủ chưa cấu hình Gemini.");
  if (useAi && length > 12 * 1024 * 1024) throw new InputError("Gemini hỗ trợ tối đa 12 MiB trong ứng dụng này.",413);
  const video = addVideo(d,batch,{filename,mime,useAi});
  try {
    await saveStream(request.body,d.media(video));
    d.tx(() => {
      d.run("UPDATE video SET status='queued' WHERE id=?",video);
      enqueue(d,"analyze",video);
    });
  } catch (err) {
    const message = err instanceof InputError ? err.message : "Tải video chưa hoàn tất.";
    d.run("UPDATE video SET status='upload_failed',error=? WHERE id=?",message,video);
    throw err;
  }
  return json({id:video},201);
}
async function read(request: NextRequest, path: string[]) {
  const d = db(), [resource,key,detail,extra] = path;
  const user = owner(d,request);
  if (resource === "workspace" && !key) return json({
    ai: aiEnabled(), bytesLimit: bytesLimit(), durationLimit: durationLimit(),
    batches: d.all("SELECT id,name,created_at,published_at FROM batch WHERE owner=? ORDER BY rowid DESC",user),
  });
  if (resource === "batches" && key && !extra) {
    if (detail === "preview") return json(d.tx(()=>preview(d,ownedBatch(d,user,key))));
    if (!detail) return json(batchView(d,user,key));
  }
  if (resource === "media" && key && !extra && (!detail || detail === "thumb")) return media(d,request,user,key,detail === "thumb");
  if (resource === "report" && (!key || key === "csv") && !detail) {
    const p = request.nextUrl.searchParams;
    const filters = {
      from: date.parse(p.get("from") ?? dayInZone(new Date(Date.now()-6*86400_000))),
      to: date.parse(p.get("to") ?? dayInZone()), batch: p.get("batch") ?? "",
      adSet: id.or(z.literal("")).parse(p.get("adSet") ?? ""), status: p.get("status") ?? "",
    };
    if (filters.from > filters.to) throw new InputError("Khoảng ngày không hợp lệ.");
    if (filters.batch) ownedBatch(d,user,filters.batch);
    const results = report(d,user,filters);
    return key === "csv" ? new Response(reportCsv(results.rows),{headers: {
      ...noStore,"Content-Type":"text/csv; charset=utf-8","Content-Disposition": 'attachment; filename="meta-video-ads-DEMO.csv"',
    }}) : json(results);
  }
  throw absent();
}
async function write(request: NextRequest, path: string[]) {
  if (request.headers.get("x-demo-request") !== "1" || request.headers.get("sec-fetch-site") === "cross-site") {
    throw new InputError("Yêu cầu không hợp lệ.",403);
  }
  const d = db(), [resource,key,action,extra] = path;
  if (extra) throw absent();
  if (resource === "session" && !key) {
    const res = json({mode:"demo"});
    if (!session(d,request.cookies.get(cookie)?.value)) res.cookies.set(cookie,newSession(d), {
      httpOnly:true, secure:process.env.SECURE_COOKIE === "true", sameSite:"strict", maxAge:sessionAge, path:"/",
    });
    return res;
  }
  const user = owner(d,request);
  if (resource === "batches" && !key) {
    const data = await payload(request,z.object({name}));
    return json({id:createBatch(d,user,data.name)},201);
  }
  if (resource === "report" && key === "refresh" && !action) {
    const data = await payload(request,z.object({batch:id.or(z.literal(""))}));
    if (data.batch) ownedBatch(d,user,data.batch);
    const queued = d.tx(() => {
      const list = d.all<{id:string}>("SELECT id FROM batch WHERE owner=? AND published_at IS NOT NULL AND (?='' OR id=?)",user,data.batch,data.batch);
      for (const b of list) enqueue(d,"sync",b.id);
      return list.length;
    });
    return json({queued},202);
  }
  if (resource === "batches" && key) {
    const b = ownedBatch(d,user,key);
    if (action === "upload") return upload(d,request,b);
    if (action === "samples") {
      const data = await payload(request,z.object({keys:z.array(z.string()).min(1).max(samples.length)}));
      if (data.keys.some(k => !samples.some(s => s.key === k))) throw absent();
      const ids = addVideos(d,b,samples.filter(s=>data.keys.includes(s.key)).map(s=>({
        filename:s.filename,mime:"video/mp4",sample:s.key,
      })));
      return json({ids},202);
    }
    if (action === "publish") {
      const data = await payload(request,z.object({confirm:z.literal(true),simulateFailure:z.boolean().default(false),reviewKey:z.string().regex(/^[a-f0-9]{64}$/)}));
      return json({campaign:publish(d,user,key,data.simulateFailure,data.reviewKey)},202);
    }
    if (action === "retry") {
      if (!b.published_at) throw new InputError("Chưa có lần xuất bản demo.");
      return json({queued:retryPublish(d,key)},202);
    }
    if (action === "config") {
      const cfg = await payload(request,configSchema);
      d.tx(() => {
        assertDraft(ownedBatch(d,user,key));
        d.run("UPDATE batch SET config=? WHERE id=?",JSON.stringify(cfg),key);
        audit(d,key,"Đã lưu cấu hình chiến dịch.");
      });
      return json({ok:true});
    }
    if (action === "context") {
      const data = await payload(request,contextSchema);
      d.tx(() => {
        assertDraft(ownedBatch(d,user,key));
        d.run("UPDATE batch SET context=? WHERE id=?",JSON.stringify(data),key);
      });
      return json({ok:true});
    }
    if (action === "bulk") {
      const data = await payload(request,z.object({
        ids:z.array(id).max(24), all:z.boolean(), overwrite:z.boolean(),
        destination:url.optional(), tracking:tracking.optional(), group:group.optional(),
      }));
      const changed = d.tx(() => {
        assertDraft(ownedBatch(d,user,key));
        const rows = d.all<{id:string;destination:string;tracking:string;group_key:string}>("SELECT id,destination,tracking,group_key FROM video WHERE batch_id=?",key);
        const targets = data.all ? rows : rows.filter(v => data.ids.includes(v.id));
        if (!targets.length) throw new InputError("Chọn ít nhất một dòng.");
        for (const v of targets) {
          d.run("UPDATE video SET destination=?,tracking=?,group_key=? WHERE id=?",
            data.destination !== undefined && (data.overwrite || !v.destination) ? data.destination : v.destination,
            data.tracking !== undefined && (data.overwrite || !v.tracking) ? data.tracking : v.tracking,
            data.group !== undefined && (data.overwrite || v.group_key === "shared") ? data.group : v.group_key,v.id);
        }
        return targets.length;
      });
      return json({changed});
    }
  }
  if (resource === "videos" && key && action) {
    const v = ownedVideo(d,user,key);
    if (action === "context") {
      const overrides = await payload(request,contextSchema);
      d.tx(() => {
        assertDraft(ownedBatch(d,user,v.batch_id));
        d.run("UPDATE video SET overrides=? WHERE id=?",JSON.stringify(overrides),key);
      });
      return json({ok:true});
    }
    if (action === "edit") {
      const data = await payload(request,z.object({included:z.boolean(),group_key:group,destination:url,tracking}));
      d.tx(() => {
        assertDraft(ownedBatch(d,user,v.batch_id));
        d.run("UPDATE video SET included=?,group_key=?,destination=?,tracking=? WHERE id=?",
          Number(data.included),data.group_key,data.destination,data.tracking,key);
      });
      return json({ok:true});
    }
    if (action === "remove") {
      d.tx(() => {
        assertDraft(ownedBatch(d,user,v.batch_id));
        if (ownedVideo(d,user,key).status === "uploading" ||
          d.one("SELECT 1 FROM job WHERE target=? AND status='running'",key)) throw new InputError("Đợi video xử lý xong rồi xóa.",409);
        d.run("DELETE FROM job WHERE target=?",key);
        d.run("DELETE FROM video WHERE id=?",key);
      });
      await Promise.all([unlink(d.media(key)).catch(() => {}),unlink(d.media(key,true)).catch(() => {})]);
      return json({ok:true});
    }
    if (action === "retry") {
      d.tx(() => {
        assertDraft(ownedBatch(d,user,v.batch_id));
        if (ownedVideo(d,user,key).status !== "failed") throw new InputError("Chỉ có thể thử lại video bị lỗi xử lý.");
        d.run("UPDATE video SET status='queued',error=NULL WHERE id=?",key);
        enqueue(d,"analyze",key);
      });
      return json({ok:true},202);
    }
    if (action === "regenerate") {
      const data = await payload(request,z.object({replace:z.boolean().default(false)}));
      d.tx(() => {
        assertDraft(ownedBatch(d,user,v.batch_id));
        if (ownedVideo(d,user,key).status !== "ready") throw new InputError("Video chưa hoàn tất phân tích.");
        const last = d.one<{payload:string}>("SELECT payload FROM job WHERE kind='regenerate' AND target=?",key);
        enqueue(d,"regenerate",key,{replace:data.replace,revision:(last ? parse<{revision:number}>(last.payload).revision : 0)+1});
      });
      return json({ok:true},202);
    }
  }
  if (resource === "variants" && key && action === "edit") {
    const c = ownedVariant(d,user,key);
    const data = await payload(request,z.object({selected:z.boolean(),copy:copySchema.optional()}));
    d.tx(() => {
      const current = ownedVariant(d,user,c.id);
      assertDraft(ownedBatch(d,user,ownedVideo(d,user,current.video_id).batch_id));
      d.run("UPDATE variant SET selected=?,data=?,edited=? WHERE id=?",Number(data.selected),
        data.copy ? JSON.stringify(data.copy) : current.data,data.copy ? 1 : current.edited,key);
    });
    return json({ok:true});
  }
  throw absent();
}
function failure(err: unknown) {
  if (err instanceof InputError) return json({error:err.message},err.status);
  if (err instanceof z.ZodError) return json({error:err.issues.map(i => (i.path.length ? i.path.join(".")+": " : "")+i.message).join(" ")},400);
  console.error("Meta Video Ads request failed:",err instanceof Error ? err.name : "Unknown");
  return json({error:"Có lỗi phía máy chủ. Hãy thử lại."},500);
}
export async function GET(request: NextRequest, route: Route) {
  try { return await read(request,(await route.params).path); } catch (err) { return failure(err); }
}
export async function POST(request: NextRequest, route: Route) {
  try { return await write(request,(await route.params).path); } catch (err) { return failure(err); }
}
