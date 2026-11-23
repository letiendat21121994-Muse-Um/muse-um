import { z } from "zod";

export const ZONE = "Asia/Ho_Chi_Minh";
export const CURRENCY = "VND";
export const dayInZone = (date = new Date()) => new Intl.DateTimeFormat("en-CA", {
  timeZone: ZONE, year: "numeric", month: "2-digit", day: "2-digit",
}).format(date);
const text = (max = 500) => z.string().trim().max(max);
const required = (max = 500) => text(max).min(1, "Không được để trống.");
export const httpUrl = z.url({ protocol: /^https?$/ }).max(2000);
const optionalUrl = z.union([z.literal(""), httpUrl]);
export const contextFields = {
  product: "Tên sản phẩm", description: "Mô tả đã xác minh", url: "Landing page",
  offer: "Giá / ưu đãi", voice: "Giọng thương hiệu", language: "Ngôn ngữ copy",
  market: "Thị trường", goal: "Mục tiêu",
} as const;
export const contextSchema = z.object({
  product: text().optional(), description: text(2000).optional(), url: optionalUrl.optional(),
  offer: text().optional(), voice: text().optional(), language: text().optional(),
  market: text().optional(), goal: text().optional(),
});
export type Context = z.infer<typeof contextSchema>;
export const ctas = { LEARN_MORE: "Tìm hiểu thêm", SHOP_NOW: "Mua ngay", SIGN_UP: "Đăng ký" } as const;
export const copySchema = z.object({
  style: required(80), primary: required(2000), headline: required(200), description: required(500),
  cta: z.enum(["LEARN_MORE", "SHOP_NOW", "SIGN_UP"]), audience: required(), rationale: required(),
  improvements: required(),
});
export type Copy = z.infer<typeof copySchema>;
export const analysisSchema = z.object({
  product: text(), sellingPoints: text(1500), speech: text(2500), onScreen: text(1500),
  problems: text(), benefits: text(), offer: text(), callToAction: text(), hook: text(), style: text(),
  audienceInference: text(), watchouts: text(2000),
});
export type AnalysisFields = z.infer<typeof analysisSchema>;
export type Analysis = { provider: "fixture" | "gemini"; observations: AnalysisFields; provided: Context; attribution: string };
export const objectives = { OUTCOME_SALES: "Doanh số", OUTCOME_TRAFFIC: "Lưu lượng truy cập" } as const;
export const groupSchema = z.object({
  key: z.string().regex(/^[a-z0-9-]{1,32}$/), name: required(100),
  country: z.string().regex(/^[A-Z]{2}$/, "Dùng mã quốc gia gồm 2 chữ cái, ví dụ US."),
  agesMin: z.number().int().min(18).max(65), agesMax: z.number().int().min(18).max(65),
  interests: text(), placements: z.enum(["automatic", "feeds", "reels"]),
  budget: z.number().int().min(1000).max(10_000_000_000),
});
const dateTime = z.string().regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/).refine(value => {
  const parsed = new Date(value + ":00Z");
  return !isNaN(+parsed) && parsed.toISOString().slice(0, 16) === value;
});
export const configSchema = z.object({
  name: required(120), account: z.literal("demo-vn"), page: required(100), instagram: text(100),
  objective: z.enum(["OUTCOME_SALES", "OUTCOME_TRAFFIC"]), optimization: z.enum(["PURCHASE", "LINK_CLICKS"]),
  dataset: text(100), structure: z.enum(["shared", "grouped"]), scope: z.enum(["campaign", "adset"]),
  budgetType: z.enum(["daily", "lifetime"]), budget: z.number().int().min(1000).max(10_000_000_000),
  spendCap: z.number().int().min(0).max(10_000_000_000), start: dateTime, end: z.union([dateTime, z.literal("")]),
  namePrefix: required(50), destination: optionalUrl, tracking: text(500),
  groups: z.array(groupSchema).min(1).max(8),
});
export type Config = z.infer<typeof configSchema>;
export const trackingValid = (value: string) => !value || value.split("&").every(pair => /^[\w.-]+=[^#&]*$/.test(pair));
export function defaultConfig(): Config {
  return {
    name: "Chiến dịch chăm sóc tóc", account: "demo-vn", page: "Trang mẫu · Hair Studio", instagram: "",
    objective: "OUTCOME_SALES", optimization: "PURCHASE", dataset: "Dataset mẫu",
    structure: "shared", scope: "campaign", budgetType: "daily", budget: 300000, spendCap: 0,
    start: dayInZone() + "T09:00", end: "", namePrefix: "Hair", destination: "", tracking: "utm_source=meta",
    groups: [{ key: "shared", name: "Nhóm chính", country: "US", agesMin: 18, agesMax: 65, interests: "", placements: "automatic", budget: 150000 }],
  };
}
export function validation(config: Config, used: string[], count: number) {
  const issues: string[] = [];
  if (!count) issues.push("Chưa có quảng cáo hợp lệ để xuất bản.");
  if (config.objective === "OUTCOME_SALES" && (config.optimization !== "PURCHASE" || !config.dataset)) issues.push("Doanh số cần tối ưu Purchase và có dataset.");
  if (config.objective === "OUTCOME_TRAFFIC" && config.optimization !== "LINK_CLICKS") issues.push("Lưu lượng truy cập cần tối ưu Link clicks.");
  if (config.budgetType === "lifetime" && !config.end) issues.push("Ngân sách trọn đời cần ngày kết thúc.");
  if (config.end && config.end <= config.start) issues.push("Kết thúc phải sau thời gian bắt đầu.");
  const keys = config.groups.map(g => g.key);
  if (new Set(keys).size !== keys.length) issues.push("Mỗi nhóm phải có mã riêng.");
  if (config.structure === "shared" && (keys.length !== 1 || keys[0] !== "shared")) issues.push("Cấu trúc chung chỉ có một nhóm shared.");
  if (config.structure === "grouped" && keys.length < 2) issues.push("Cấu trúc theo nhóm cần định nghĩa ít nhất hai nhóm.");
  if (used.some(k => !keys.includes(k))) issues.push("Một hoặc nhiều video chưa được gán vào nhóm hợp lệ.");
  for (const g of config.groups) if (g.agesMin > g.agesMax) issues.push(`Độ tuổi không hợp lệ trong nhóm ${g.name}.`);
  if (!trackingValid(config.tracking)) issues.push("Tracking phải có dạng key=value&key2=value2.");
  const budget = config.scope === "campaign" ? config.budget : config.groups.filter(g => used.includes(g.key)).reduce((n,g) => n + g.budget, 0);
  if (config.spendCap && config.spendCap < budget) issues.push("Giới hạn chi tiêu không được thấp hơn tổng ngân sách đã cấu hình.");
  return { issues, budget };
}
