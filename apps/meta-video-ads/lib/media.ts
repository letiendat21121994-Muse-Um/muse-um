import { execFile } from "node:child_process";
import { createWriteStream } from "node:fs";
import { unlink } from "node:fs/promises";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import { promisify } from "node:util";
import { z } from "zod";
import { InputError } from "./workspace.ts";
import type { ReadableStream as NodeStream } from "node:stream/web";
const run = promisify(execFile);

const bounded = (value: string | undefined, fallback: number, max: number) => {
  const n = Number(value);
  return Number.isInteger(n) && n > 0 && n <= max ? n : fallback;
};
export const bytesLimit = () => bounded(process.env.MAX_VIDEO_MB,60,250) * 1024 * 1024;
export const durationLimit = () => bounded(process.env.MAX_VIDEO_SECONDS,180,900);
export const mimeSchema = z.enum(["video/mp4","video/quicktime","video/webm"]);
export async function saveStream(stream: ReadableStream<Uint8Array> | null, path: string) {
  if (!stream) throw new InputError("Không nhận được video.");
  let size = 0;
  const limit = bytesLimit();
  const throttle = new Transform({ transform(chunk: Buffer, _, next) {
    size += chunk.length;
    next(size > limit ? new InputError("Video vượt dung lượng cho phép.",413) : null, chunk);
  } });
  try {
    await pipeline(Readable.fromWeb(stream as NodeStream), throttle, createWriteStream(path,{ flags:"wx", mode:0o600 }),
      { signal: AbortSignal.timeout(180_000) });
    if (!size) throw new InputError("Video rỗng.");
  } catch (err) {
    await unlink(path).catch(() => {});
    if (err instanceof InputError) throw err;
    throw new InputError("Tải video chưa hoàn tất. Hãy tải lại.");
  }
}
export async function inspect(path: string, mime: string, thumbnail: string) {
  try {
    const {stdout} = await run("ffprobe", ["-v","error","-protocol_whitelist","file","-f",mime === "video/webm" ? "matroska" : "mov",
      "-show_entries","stream=codec_type:format=duration","-of","json",path], { timeout:15_000,maxBuffer:128_000 });
    const info = JSON.parse(stdout);
    const duration = Number(info.format.duration);
    if (!info.streams?.some((s: {codec_type: string}) => s.codec_type === "video") || !Number.isFinite(duration) || duration <= 0) {
      throw new InputError("Tệp không có video hợp lệ.");
    }
    if (duration > durationLimit()) throw new InputError(`Video dài hơn ${durationLimit()} giây.`);
    await run("ffmpeg", ["-v","error","-nostdin","-protocol_whitelist","file","-threads","1","-f",
      mime === "video/webm" ? "matroska" : "mov","-i",path,"-frames:v","1","-vf","scale=360:-2","-threads","1","-y",thumbnail],
      { timeout:20_000,maxBuffer:64_000 });
    return duration;
  } catch (err) {
    await unlink(thumbnail).catch(() => {});
    if (err instanceof InputError) throw err;
    if ((err as NodeJS.ErrnoException).code === "ENOENT") throw new InputError("Server chưa có ffmpeg hoặc ffprobe.");
    throw new InputError("Không đọc được video. Hãy kiểm tra định dạng hoặc tải tệp khác.");
  }
}
