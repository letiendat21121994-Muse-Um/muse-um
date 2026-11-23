import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
export const metadata: Metadata = { title:"Meta Video Ads · Studio demo", description:"Không gian thử nghiệm video thành chiến dịch quảng cáo." };
export default function Layout({children}: {children:ReactNode}) {
  return <html lang="vi"><body>{children}</body></html>;
}
