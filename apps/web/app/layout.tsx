import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "@/styles/tokens.css";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: {
    default: "TCG Grading Advisor",
    template: "%s · TCG Grading Advisor",
  },
  description:
    "Estimate condition, likely grades and whether grading is worthwhile. " +
    "Not an official grading service; every grade is a probability.",
  applicationName: "TCG Grading Advisor",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
