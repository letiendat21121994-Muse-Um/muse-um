import { createHash, randomBytes, randomUUID } from "node:crypto";
import type { Db } from "./db.ts";
import { now, parse } from "./db.ts";
import { defaultConfig, trackingValid, validation, httpUrl, type Analysis, type Config, type Context, type Copy } from "./domain.ts";

export class InputError extends Error {
  constructor(message: string, public status = 400) { super(message); }
}
export const absent = () => new InputError("Không tìm thấy mục này.", 404);
const digest = (value: string) => createHash("sha256").update(value).digest("hex");
export const sessionAge = 30 * 86400;
export function newSession(d: Db) {
  const token = randomBytes(32).toString("base64url");
  const owner = randomUUID();
  d.tx(() => {
    d.run("INSERT INTO workspace VALUES (?,?,?)", owner, digest(token), Date.now() + sessionAge * 1000);
    createBatch(d, owner, "Bản nháp đầu tiên");
  });
  return token;
}
export function session(d: Db, token?: string) {
  if (!token || !/^[\w-]{43}$/.test(token)) return;
  return d.one<{ id: string }>("SELECT id FROM workspace WHERE token_hash=? AND expires_at>?", digest(token), Date.now())?.id;
}
export type Batch = { id: string; owner: string; name: string; context: string; config: string; created_at: string; published_at: string | null; campaign_ref: string | null; simulate_failure: number };
export type Video = { id: string; batch_id: string; filename: string; mime: string; status: string; error: string | null; duration: number | null; thumb: number; sample: string | null; overrides: string; included: number; group_key: string; destination: string; tracking: string; use_ai: number };
export type Variant = { id: string; video_id: string; selected: number; edited: number; data: string };
export type Ad = { id: string; batch_id: string; video_id: string; variant_id: string; group_key: string; name: string; state: string; media_ref: string | null; creative_ref: string | null; ad_ref: string | null; error: string | null; simulated_once: number; snapshot: string; published_at: string | null };
export type FrozenAd = { copy: Copy; destination: string; tracking: string; filename: string; videoId: string; variantId: string };
export function createBatch(d: Db, owner: string, name: string) {
  const id = randomUUID();
  d.run("INSERT INTO batch(id, owner, name, config, created_at) VALUES (?,?,?,?,?)", id, owner, name, JSON.stringify(defaultConfig()), now());
  return id;
}
export const ownedBatch = (d: Db, owner: string, id: string) =>
  d.one<Batch>("SELECT * FROM batch WHERE id=? AND owner=?", id, owner) ?? (() => { throw absent(); })();
export function ownedVideo(d: Db, owner: string, id: string) {
  const row = d.one<Video>("SELECT v.* FROM video v JOIN batch b ON b.id=v.batch_id WHERE v.id=? AND b.owner=?", id, owner);
  if (!row) throw absent();
  return row;
}
export function ownedVariant(d: Db, owner: string, id: string) {
  const row = d.one<Variant>("SELECT c.* FROM variant c JOIN video v ON v.id=c.video_id JOIN batch b ON b.id=v.batch_id WHERE c.id=? AND b.owner=?", id, owner);
  if (!row) throw absent();
  return row;
}
export function assertDraft(batch: Batch) {
  if (batch.published_at) throw new InputError("Lô này đã khóa khi xuất bản. Hãy tạo lô mới.", 409);
}
export function audit(d: Db, batchId: string, note: string) {
  d.run("INSERT INTO activity(batch_id,message,created_at) VALUES (?,?,?)", batchId, note, now());
}
export function enqueue(d: Db, kind: string, target: string, payload = {}) {
  d.run(`INSERT INTO job(id,kind,target,payload,next_at) VALUES (?,?,?,?,?)
    ON CONFLICT(kind,target) DO UPDATE SET payload=excluded.payload,status='queued',attempts=0,
      next_at=excluded.next_at,lease=NULL,lease_until=NULL,error=NULL WHERE job.status IN ('done','failed')`,
  randomUUID(), kind, target, JSON.stringify(payload), Date.now());
}
export function addVideo(d: Db, batch: Batch, input: { filename: string; mime: string; sample?: string; useAi?: boolean }) {
  return d.tx(() => {
    assertDraft(ownedBatch(d, batch.owner, batch.id));
    if ((d.one<{ n: number }>("SELECT count(*) n FROM video WHERE batch_id=?", batch.id)?.n ?? 0) >= 24) {
      throw new InputError("Mỗi lô chứa tối đa 24 video.");
    }
    const id = randomUUID();
    d.run("INSERT INTO video(id,batch_id,filename,mime,status,created_at,sample,use_ai) VALUES (?,?,?,?,?,?,?,?)",
      id, batch.id, input.filename, input.mime, input.sample ? "queued" : "uploading", now(), input.sample ?? null, Number(!!input.useAi));
    if (input.sample) enqueue(d, "analyze", id);
    return id;
  });
}
export function batchView(d: Db, owner: string, id: string) {
  const b = ownedBatch(d, owner, id);
  const { owner: _owner, ...batch } = b;
  const videos = d.all<Video>("SELECT * FROM video WHERE batch_id=? ORDER BY rowid", id).map(v => {
    const a = d.one<{ data: string }>("SELECT data FROM analysis WHERE video_id=?", v.id);
    return { ...v, overrides: parse<Context>(v.overrides), analysis: a ? parse<Analysis>(a.data) : null,
      variants: d.all<Variant>("SELECT * FROM variant WHERE video_id=? ORDER BY rowid", v.id)
        .map(c => ({ ...c, data: parse<Copy>(c.data) })),
      regenerating: !!d.one("SELECT 1 FROM job WHERE kind='regenerate' AND target=? AND status IN ('queued','running')", v.id),
      regenerationError: d.one<{ error: string }>("SELECT error FROM job WHERE kind='regenerate' AND target=? AND status='failed'", v.id)?.error,
    };
  });
  return {
    ...batch, context: parse<Context>(b.context), config: parse<Config>(b.config), videos,
    ads: d.all<Ad>("SELECT * FROM ad WHERE batch_id=? ORDER BY rowid", id).map(a => ({ ...a, snapshot: parse<FrozenAd>(a.snapshot) })),
    activity: d.all<{message: string; created_at: string}>("SELECT message,created_at FROM activity WHERE batch_id=? ORDER BY id DESC LIMIT 20", id),
  };
}
export type BatchView = ReturnType<typeof batchView>;
export type DraftVideo = BatchView["videos"][number];
export function preview(d: Db, batch: Batch) {
  const config = parse<Config>(batch.config);
  const excluded: { videoId: string; filename: string; reason: string }[] = [];
  const plan: (FrozenAd & { name: string; group: string })[] = [];
  for (const v of d.all<Video>("SELECT * FROM video WHERE batch_id=? ORDER BY rowid", batch.id)) {
    const variants = d.all<Variant>("SELECT * FROM variant WHERE video_id=? ORDER BY rowid", v.id);
    const destination = v.destination || parse<Context>(v.overrides).url || config.destination || parse<Context>(batch.context).url || "";
    const tracking = v.tracking || config.tracking;
    const group = config.structure === "shared" ? "shared" : v.group_key;
    const reason = !v.included ? "Đã bỏ chọn" : v.status !== "ready" ? "Video chưa xử lý xong" :
      !variants.some(c => c.selected) ? "Chưa chọn copy" : !httpUrl.safeParse(destination).success ? "Thiếu URL đích hợp lệ" :
      !trackingValid(tracking) ? "Tracking không hợp lệ" : "";
    if (reason) { excluded.push({ videoId: v.id, filename: v.filename, reason }); continue; }
    for (const [i,c] of variants.entries()) if (c.selected) {
      plan.push({ name: `${config.namePrefix} · ${v.filename.slice(0,60)} · V${i+1} · ${c.id.slice(0,6)}`,
        group, copy: parse<Copy>(c.data), destination, tracking, filename: v.filename, videoId: v.id, variantId: c.id });
    }
  }
  const groups = [...new Set(plan.map(a => a.group))];
  const reviewKey = digest(JSON.stringify({config,plan,excluded}));
  return { plan, excluded, groups, reviewKey, ...validation(config, groups, plan.length) };
}
export type Preview = ReturnType<typeof preview>;
export function publish(d: Db, owner: string, id: string, simulateFailure: boolean, reviewKey: string) {
  return d.tx(() => {
    const batch = ownedBatch(d, owner, id);
    if (batch.published_at) return batch.campaign_ref;
    const p = preview(d, batch);
    if (reviewKey !== p.reviewKey) throw new InputError("Bản nháp đã thay đổi. Vui lòng kiểm tra lại trước khi xuất bản.",409);
    if (p.issues.length) throw new InputError(p.issues.join(" "));
    const campaign = "demo_campaign_" + id;
    d.run("UPDATE batch SET published_at=?,campaign_ref=?,simulate_failure=? WHERE id=?", now(), campaign, Number(simulateFailure), id);
    for (const group of p.groups) d.run("INSERT INTO ad_set(id,batch_id,group_key,ref) VALUES (?,?,?,?)",
      randomUUID(), id, group, "demo_set_" + id + "_" + group);
    for (const ad of p.plan) {
      const adId = randomUUID();
      d.run("INSERT INTO ad(id,batch_id,video_id,variant_id,group_key,name,snapshot) VALUES (?,?,?,?,?,?,?)",
        adId, id, ad.videoId, ad.variantId, ad.group, ad.name, JSON.stringify(ad));
      enqueue(d, "publish", adId);
    }
    audit(d, id, `Đã khóa bản nháp và đưa ${p.plan.length} quảng cáo vào hàng đợi demo.`);
    return campaign;
  });
}
