import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ORCA",
  description:
    "Agentic reasoning and trust layer over INCOIS, IMD and ISRO marine feeds.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
