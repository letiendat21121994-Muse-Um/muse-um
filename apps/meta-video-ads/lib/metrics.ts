const additive = ["spend", "impressions", "linkClicks", "purchases", "value"] as const;
export type Metrics = Record<(typeof additive)[number], number | null> & { reach: number | null };
export function totals(rows: (Metrics & { currency: string })[]) {
  if (new Set(rows.map(r => r.currency)).size > 1) throw new Error("Mixed currencies");
  const values = Object.fromEntries(additive.map(k => [k, rows.length && rows.every(r => r[k] !== null) ? rows.reduce((n, r) => n + r[k]!, 0) : null])) as Omit<Metrics,"reach">;
  const ratio = (n: number | null, d: number | null, m = 1) => n !== null && d !== null && d > 0 ? n / d * m : null;
  const { spend, impressions, linkClicks, purchases, value } = values;
  return { ...values, reach: rows.length === 1 ? rows[0].reach : null, ctr: ratio(linkClicks,impressions,100),
    cpc: ratio(spend,linkClicks), cpm: ratio(spend,impressions,1000), cpa: ratio(spend,purchases), roas: ratio(value,spend) };
}
