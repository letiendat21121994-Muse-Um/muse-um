import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createServer } from "node:net";
import { delimiter, dirname, resolve } from "node:path";
import { setTimeout } from "node:timers/promises";

const root = resolve(import.meta.dirname, "..", "..");
const free = (port) => new Promise((ok) => {
  const probe = createServer().once("error", () => ok(false)).listen(port, "127.0.0.1", () => probe.close(() => ok(true)));
});
let port = Number(process.env.PORT) || 3939;
while (!(await free(port))) port++;
const url = `http://127.0.0.1:${port}`;
const dataDir = process.env.DATA_DIR || resolve(root, "data");
const env = {
  ...process.env,
  HOST: "127.0.0.1",
  PORT: String(port),
  DATA_DIR: dataDir,
  COOKIE_NAME: `mva_demo_${createHash("sha256").update(dataDir.toLowerCase()).digest("hex").slice(0, 12)}`,
  SECURE_COOKIE: "false",
  PATH: [resolve(root, "runtime", "ffmpeg"), dirname(process.execPath), process.env.PATH].join(delimiter),
};
const app = spawn(process.execPath, ["scripts/run.mjs", "start"], { env, stdio: "inherit" });
app.on("exit", (code) => process.exit(code ?? 1));
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => app.kill("SIGTERM"));

for (let i = 0; i < 120; i++) {
  if (await fetch(url).then((r) => r.ok, () => false)) break;
  await setTimeout(500);
}
console.log(`\nMeta Video Ads (demo) đang chạy tại ${url}\nĐóng cửa sổ này để tắt ứng dụng.\n`);
if (process.platform === "win32" && !process.env.NO_BROWSER)
  spawn("cmd", ["/c", "start", "", url], { detached: true, stdio: "ignore", windowsHide: true }).unref();
