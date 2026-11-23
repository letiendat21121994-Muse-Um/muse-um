import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import { rmSync } from "node:fs";
import { setTimeout } from "node:timers/promises";
import type { Db } from "./db.ts";
import { now, parse } from "./db.ts";
import { analyze, generate, ProviderError } from "./ai.ts";
import { inspect } from "./media.ts";
import type { Analysis, Context } from "./domain.ts";
import { type Ad, type Batch, type Video, audit, enqueue, InputError } from "./workspace.ts";
import { syncDemo } from "./reporting.ts";

type Job = { id: string; kind: string; target: string; payload: string; attempts: number; lease: string };
const maxAttempts = 3;
export function retryPublish(d: Db, batchId: string) {
  return d.tx(() => {
    const failed = d.all<Ad>("SELECT * FROM ad WHERE batch_id=? AND state='failed'", batchId);
    for (const ad of failed) {
      d.run("UPDATE ad SET state='queued',error=NULL WHERE id=?", ad.id);
      enqueue(d,"publish",ad.id);
    }
    if (failed.length) audit(d,batchId,`Đã xếp hàng thử lại ${failed.length} quảng cáo lỗi.`);
    return failed.length;
  });
}
function fail(d: Db, j: Job, error: string) {
  if (j.kind === "analyze") d.run("UPDATE video SET status='failed',error=? WHERE id=?",error,j.target);
  if (j.kind === "publish") {
    d.run("UPDATE ad SET state='failed',error=? WHERE id=?",error,j.target);
    const ad = d.one<Ad>("SELECT * FROM ad WHERE id=?",j.target);
    if (ad) audit(d,ad.batch_id,`Lỗi demo · ${ad.name}: ${error}`);
  }
  d.run("UPDATE job SET status='failed',lease=NULL,lease_until=NULL,error=? WHERE id=?",error,j.id);
}
function claim(d: Db): Job | undefined {
  return d.tx(() => {
    const time = Date.now();
    const stale = d.all<Job>("SELECT * FROM job WHERE status='running' AND lease_until<=? AND attempts>=?",time,maxAttempts);
    for (const j of stale) fail(d,j,"Tác vụ bị gián đoạn. Hãy thử lại.");
    const j = d.one<Job>(`SELECT * FROM job WHERE (status='queued' AND next_at<=?) OR
      (status='running' AND lease_until<=?) ORDER BY next_at,rowid LIMIT 1`,time,time);
    if (!j) return;
    j.attempts++;
    j.lease = randomUUID();
    d.run("UPDATE job SET status='running',attempts=?,lease=?,lease_until=? WHERE id=?",j.attempts,j.lease,time+300_000,j.id);
    return j;
  });
}
function withLease<T>(d: Db, j: Job, fn: () => T) {
  return d.tx(() => {
    if (!d.one("SELECT 1 FROM job WHERE id=? AND lease=? AND status='running'",j.id,j.lease)) {
      throw new Error("Lost job lease");
    }
    return fn();
  });
}
function done(d: Db, j: Job) {
  d.run("UPDATE job SET status='done',lease=NULL,lease_until=NULL,error=NULL WHERE id=?",j.id);
}
async function analyzeJob(d: Db, j: Job) {
  const v = d.one<Video>("SELECT * FROM video WHERE id=?",j.target);
  if (!v) return withLease(d,j,() => done(d,j));
  const batch = d.one<Batch>("SELECT * FROM batch WHERE id=?",v.batch_id)!;
  const regenerate = j.kind === "regenerate";
  if (!regenerate) withLease(d,j,() => d.run("UPDATE video SET status='processing',error=NULL WHERE id=?",v.id));
  const original = v.sample ? resolve("public/samples",v.sample + ".mp4") : d.media(v.id);
  const duration = v.duration ?? await inspect(original,v.mime,d.media(v.id,true));
  if (!v.duration) withLease(d,j,() => d.run("UPDATE video SET duration=?,thumb=1 WHERE id=?",duration,v.id));
  const previous = d.one<{data: string}>("SELECT data FROM analysis WHERE video_id=?",v.id);
  const snapshot = { ...parse<Context>(batch.context), ...parse<Context>(v.overrides) };
  const analysis = previous ? parse<Analysis>(previous.data) :
    await analyze(v.sample,snapshot,v.use_ai ? {path:original,mime:v.mime,duration} : undefined);
  const options = parse<{revision?: number; replace?: boolean}>(j.payload);
  const variants = await generate({ ...analysis, provided: snapshot },v.filename,options.revision);
  withLease(d,j,() => {
    if (previous) {
      d.run("UPDATE analysis SET data=? WHERE video_id=?",JSON.stringify({...analysis,provided:snapshot}),v.id);
      // A pending regeneration can be frozen by publishing, so only edit open drafts.
      if (d.one<Batch>("SELECT * FROM batch WHERE id=?",v.batch_id)?.published_at) return done(d,j);
    } else {
      d.run("INSERT INTO analysis VALUES (?,?,?)",v.id,JSON.stringify(analysis),now());
      if (d.one<Batch>("SELECT * FROM batch WHERE id=?",v.batch_id)?.published_at) {
        d.run("UPDATE video SET status='ready',error=NULL WHERE id=?",v.id);
        return done(d,j);
      }
    }
    d.run("DELETE FROM variant WHERE video_id=? AND (edited=0 OR ?=1)",v.id,Number(!!options.replace));
    const selected = !!d.one("SELECT 1 FROM variant WHERE video_id=? AND selected=1",v.id);
    for (const [i,c] of variants.entries()) d.run("INSERT INTO variant(id,video_id,data,selected,created_at) VALUES (?,?,?,?,?)",
      randomUUID(),v.id,JSON.stringify(c),Number(!selected && i===0),now());
    d.run("UPDATE video SET status='ready',error=NULL WHERE id=?",v.id);
    done(d,j);
  });
}
async function publishJob(d: Db, j: Job, pause: number) {
  const ad = d.one<Ad>("SELECT * FROM ad WHERE id=?",j.target);
  if (!ad || ad.state === "simulated") return withLease(d,j,() => done(d,j));
  const stage = (state: string) => withLease(d,j,() => d.run("UPDATE ad SET state=?,error=NULL WHERE id=?",state,ad.id));
  if (!ad.media_ref) {
    stage("media");
    await setTimeout(pause);
    withLease(d,j,() => {
      d.run("INSERT INTO media_mapping VALUES (?,?) ON CONFLICT(video_id) DO NOTHING",ad.video_id,"demo_media_"+ad.video_id);
      d.run("UPDATE ad SET media_ref=(SELECT ref FROM media_mapping WHERE video_id=?) WHERE id=?",ad.video_id,ad.id);
    });
  }
  if (!ad.creative_ref) {
    stage("creative");
    await setTimeout(pause);
    withLease(d,j,() => d.run("UPDATE ad SET creative_ref=? WHERE id=?","demo_creative_"+ad.id,ad.id));
  }
  stage("ad");
  await setTimeout(pause);
  withLease(d,j,() => {
    const config = d.one<Batch>("SELECT * FROM batch WHERE id=?",ad.batch_id)!;
    const first = d.one<{id: string}>("SELECT id FROM ad WHERE batch_id=? ORDER BY rowid LIMIT 1",ad.batch_id);
    if (config.simulate_failure && first?.id === ad.id && !ad.simulated_once) {
      d.run("UPDATE ad SET simulated_once=1 WHERE id=?",ad.id);
      return fail(d,j,"Lỗi demo tại bước tạo quảng cáo. Media và creative đã lưu; hãy thử lại quảng cáo lỗi.");
    }
    d.run("UPDATE ad SET state='simulated',ad_ref=?,published_at=?,error=NULL WHERE id=?","demo_ad_"+ad.id,now(),ad.id);
    audit(d,ad.batch_id,`Hoàn tất mô phỏng · ${ad.name}.`);
    enqueue(d,"sync",ad.batch_id);
    done(d,j);
  });
}
export async function tick(d: Db, pause = 300) {
  const j = claim(d);
  if (!j) return false;
  try {
    if (j.kind === "analyze" || j.kind === "regenerate") await analyzeJob(d,j);
    else if (j.kind === "publish") await publishJob(d,j,pause);
    else if (j.kind === "sync") withLease(d,j,() => { syncDemo(d,j.target); done(d,j); });
    else withLease(d,j,() => fail(d,j,"Không nhận diện được tác vụ."));
  } catch (err) {
    const message = err instanceof InputError || err instanceof ProviderError ? err.message : "Không hoàn tất tác vụ. Hãy thử lại.";
    d.tx(() => {
      if (!d.one("SELECT 1 FROM job WHERE id=? AND lease=? AND status='running'",j.id,j.lease)) return;
      if (err instanceof ProviderError && err.retryable && j.attempts < maxAttempts) {
        d.run("UPDATE job SET status='queued',next_at=?,lease=NULL,lease_until=NULL,error=? WHERE id=?",
          Date.now()+3000*2**(j.attempts-1),message,j.id);
        if (j.kind === "analyze") d.run("UPDATE video SET status='queued',error=? WHERE id=?",message,j.target);
      } else fail(d,j,message);
    });
  }
  return true;
}
export function cleanupUploads(d: Db) {
  const cutoff = new Date(Date.now()-300_000).toISOString();
  d.tx(() => {
    for (const v of d.all<{id:string}>("SELECT id FROM video WHERE status='uploading' AND created_at<?",cutoff)) {
      rmSync(d.media(v.id),{force:true});
      d.run("UPDATE video SET status='upload_failed',error=? WHERE id=?","Tải lên bị gián đoạn. Hãy chọn lại tệp.",v.id);
    }
  });
}
