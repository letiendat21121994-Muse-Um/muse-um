import { setTimeout } from "node:timers/promises";
import { db } from "../lib/db.ts";
import { cleanupUploads,tick } from "../lib/jobs.ts";
const store = db();
let stopping = false;
process.on("SIGTERM",()=>{ stopping = true; });
process.on("SIGINT",()=>{ stopping = true; });
let nextCleanup = 0;
while (!stopping) {
  if (Date.now() >= nextCleanup) { cleanupUploads(store); nextCleanup = Date.now() + 60_000; }
  if (!await tick(store)) await setTimeout(350);
}
store.sql.close();
