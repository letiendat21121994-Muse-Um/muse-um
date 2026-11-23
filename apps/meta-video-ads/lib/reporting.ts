import { createHash } from "node:crypto";
import type { Db } from "./db.ts";
import { now, parse } from "./db.ts";
import { CURRENCY, dayInZone, ZONE } from "./domain.ts";
import { type Ad, type FrozenAd } from "./workspace.ts";
import { totals, type Metrics } from "./metrics.ts";
export function syncDemo(d: Db, batchId: string) {
  const created = d.one<{config: string}>("SELECT config FROM batch WHERE id=?", batchId);
  if (!created) return;
  const purchase = parse<{objective: string}>(created.config).objective === "OUTCOME_SALES";
  const ads = d.all<Ad>("SELECT * FROM ad WHERE batch_id=? AND state='simulated'", batchId);
  for (const ad of ads) {
    const seed = createHash("sha256").update(ad.id).digest();
    const base = new Date(ad.published_at!);
    for (let i = 0; i < 7; i++) {
      const day = dayInZone(new Date(+base - (6 - i) * 86400_000));
      const impressions = 600 + seed[i] * 5 + i * 40;
      const linkClicks = Math.round(impressions * (0.011 + seed[i+7] / 14000));
      const purchases = purchase ? Math.floor(linkClicks * (0.06 + seed[i+14] / 4000)) : null;
      const m: Metrics = { impressions, linkClicks, purchases, reach: Math.round(impressions * 0.79),
        spend: linkClicks * (1100 + seed[i+21] * 14), value: purchases === null ? null : purchases * 399000 };
      d.run("INSERT INTO snapshot VALUES (?,?,?) ON CONFLICT(ad_id,day) DO UPDATE SET values_json=excluded.values_json",
        ad.id, day, JSON.stringify(m));
    }
  }
  d.run("INSERT INTO report_sync VALUES (?,?) ON CONFLICT(batch_id) DO UPDATE SET synced_at=excluded.synced_at", batchId, now());
}
export type ReportRow = {
  day: string; currency: string; metrics: Metrics; adId: string; adName: string; adRef: string;
  videoId: string; variantId: string; group: string; batchId: string; batchName: string; campaign: string;
  adSetId: string; groupName: string; campaignName: string; status: "simulated"; snapshot: FrozenAd;
};
export function report(d: Db, owner: string, filters: { from: string; to: string; batch: string; adSet: string; status: string }) {
  type Raw = { day: string; values_json: string; batch_name: string; campaign_ref: string; config: string; ad_set_id: string } & Ad;
  const rows: ReportRow[] = d.all<Raw>(`SELECT s.day,s.values_json,a.*,b.name batch_name,b.campaign_ref,b.config,g.id ad_set_id
    FROM snapshot s JOIN ad a ON a.id=s.ad_id JOIN batch b ON b.id=a.batch_id
    JOIN ad_set g ON g.batch_id=b.id AND g.group_key=a.group_key
    WHERE b.owner=? AND s.day BETWEEN ? AND ? AND (?='' OR b.id=?) AND (?='' OR g.id=?)
      AND (?='' OR a.state=?) ORDER BY s.day,a.id`,
    owner,filters.from,filters.to,filters.batch,filters.batch,filters.adSet,filters.adSet,filters.status,filters.status)
    .map(r => ({ day: r.day, currency: CURRENCY, metrics: parse<Metrics>(r.values_json), adId: r.id, adName: r.name, adRef: r.ad_ref!,
      videoId: r.video_id, variantId: r.variant_id, group: r.group_key, batchId: r.batch_id, batchName: r.batch_name,
      campaign: r.campaign_ref, campaignName: parse<{name: string}>(r.config).name, adSetId:r.ad_set_id,
      groupName: parse<{groups: {key: string; name: string}[]}>(r.config).groups.find(g => g.key === r.group_key)?.name ?? r.group_key,
      status: "simulated", snapshot: parse<FrozenAd>(r.snapshot) }));
  const aggregate = (list: ReportRow[]) => totals(list.map(r => ({ ...r.metrics, currency: r.currency })));
  const sync = d.one<{lastSync: string | null}>(`SELECT max(s.synced_at) lastSync FROM report_sync s JOIN batch b ON b.id=s.batch_id
    WHERE b.owner=? AND (?='' OR b.id=?)`, owner, filters.batch, filters.batch);
  return { rows, totals: aggregate(rows), lastSync: sync?.lastSync ?? null, zone: ZONE, currency: CURRENCY,
    attribution: "Kịch bản minh hoạ; không có attribution từ Meta.", trend: [...new Set(rows.map(r => r.day))].map(day => ({ day, ...aggregate(rows.filter(r => r.day === day)) })) };
}
export type Report = ReturnType<typeof report>;
export function reportCsv(rows: ReportRow[]) {
  const cell = (value: unknown) => {
    const raw = String(value ?? "");
    return '"' + (typeof value === "string" && /^[\s]*[=+@-]|^[\r\n\t]/.test(raw) ? "'" : "") + raw.replaceAll('"','""') + '"';
  };
  return "\uFEFF" + [
    ["source","date","timezone","currency","campaign","batch","ad_set","ad","video_id","variant_id","spend","impressions","reach_per_ad_day","link_clicks","purchases","value"],
    ...rows.map(r => ["DEMO",r.day,ZONE,r.currency,r.campaign,r.batchName,r.groupName,r.adName,r.videoId,r.variantId,r.metrics.spend,r.metrics.impressions,r.metrics.reach,r.metrics.linkClicks,r.metrics.purchases,r.metrics.value]),
  ].map(r => r.map(cell).join(",")).join("\r\n");
}
