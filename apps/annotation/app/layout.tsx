import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "@/styles/tokens.css";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: {
    default: "Annotation",
    template: "%s · Annotation",
  },
  description: "Internal tool for annotating training images. Not a public surface.",
  applicationName: "TCG Annotation",
  // Belt and braces beside the deployment boundary. ADR 0009 keeps this tool
  // off the public origin, so a crawler should never reach it — and if one ever
  // does, a corpus of training photographs is not something to have indexed.
  robots: { index: false, follow: false },
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
