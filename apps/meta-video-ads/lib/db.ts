import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import { mkdirSync, readFileSync } from "node:fs";
import { resolve, join } from "node:path";

export const now = () => new Date().toISOString();
export const parse = <T>(value: string): T => JSON.parse(value);
export class Db {
  sql: DatabaseSync;
  root: string;
  constructor(data = process.env.DATA_DIR || "data") {
    this.root = resolve(data, "demo");
    mkdirSync(this.root, { mode: 0o700, recursive: true });
    mkdirSync(join(this.root, "media"), { mode: 0o700, recursive: true });
    this.sql = new DatabaseSync(join(this.root, "workspace.sqlite"), { timeout: 5000 });
    this.sql.exec("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;");
    this.tx(() => {
      if (!this.one("PRAGMA user_version")?.user_version) {
        this.sql.exec(readFileSync(resolve("migrations/001.sql"), "utf8"));
        this.sql.exec("PRAGMA user_version=1");
      }
    });
  }
  one<T = Record<string, unknown>>(q: string, ...p: SQLInputValue[]) { return this.sql.prepare(q).get(...p) as T | undefined; }
  all<T = Record<string, unknown>>(q: string, ...p: SQLInputValue[]) { return this.sql.prepare(q).all(...p) as T[]; }
  run(q: string, ...p: SQLInputValue[]) { return this.sql.prepare(q).run(...p); }
  tx<T>(fn: () => T) {
    this.sql.exec("BEGIN IMMEDIATE");
    try { const value = fn(); this.sql.exec("COMMIT"); return value; }
    catch (error) { this.sql.exec("ROLLBACK"); throw error; }
  }
  media(id: string, image = false) { return join(this.root, "media", id + (image ? ".jpg" : ".video")); }
}
let singleton: Db | undefined;
export const db = () => singleton ??= new Db();
