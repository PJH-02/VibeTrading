import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "VibeTrading | 운용 모니터",
  description: "주식, 코인, 타임폴리오의 실시간 시장 데이터와 전략 운용 현황",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body className="antialiased">{children}</body>
    </html>
  );
}
