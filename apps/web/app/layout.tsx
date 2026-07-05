import type { Metadata } from "next";
import type { ReactNode } from "react";

import PageShell from "@/components/layout/PageShell";
import "../styles/globals.css";

export const metadata: Metadata = {
  title: "OmniTrade Legacy Engine",
  description: "Paper-trading research platform frontend",
};

type RootLayoutProps = {
  children: ReactNode;
};

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <html lang="en" className="dark">
      <body>
        <PageShell>{children}</PageShell>
      </body>
    </html>
  );
}
