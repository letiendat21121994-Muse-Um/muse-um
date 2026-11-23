import { spawn } from "node:child_process";
import { existsSync } from "node:fs";

process.umask(0o077);
for (const path of [".env.local", ".env"]) if (existsSync(path)) process.loadEnvFile(path);
const mode = process.argv[2] === "start" ? "start" : "dev";
const services = [
  spawn(process.execPath,["node_modules/next/dist/bin/next",mode,"--hostname",process.env.HOST || "127.0.0.1","--port",process.env.PORT || "3000"],{stdio:"inherit"}),
  spawn(process.execPath,["--disable-warning=ExperimentalWarning","scripts/worker.ts"],{stdio:"inherit"}),
];
let stopping = false;
const stop = (code) => {
  if (stopping) return;
  stopping = true;
  process.exitCode = code;
  for (const child of services) child.kill("SIGTERM");
};
for (const child of services) {
  child.on("exit",(code)=>stop(code ?? 1));
  child.on("error",()=>stop(1));
}
for (const signal of ["SIGINT","SIGTERM"]) process.on(signal,()=>stop(0));
