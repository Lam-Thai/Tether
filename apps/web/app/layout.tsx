import type { Metadata } from "next";
import localFont from "next/font/local";
import { ClerkProvider } from "@clerk/nextjs";
import { Suspense } from "react";

import { ReturnPathTracker } from "@/components/navigation/return-path-tracker";
import "./globals.css";

// Self-hosted, not next/font/google: Google Fonts sometimes answers with
// `/l/font?kit=...&skey=...` URLs, and Turbopack's font loader fails the whole build on
// them ("next/font/google queries have exactly one entry"). Both files are the variable
// latin subsets from Google Fonts (SIL OFL 1.1, licenses beside them in ./fonts).
const oswald = localFont({
  src: "./fonts/oswald-latin-wght.woff2",
  variable: "--font-oswald",
  weight: "400 700",
  display: "swap",
});

const jetbrainsMono = localFont({
  src: "./fonts/jetbrains-mono-latin-wght.woff2",
  variable: "--font-jetbrains-mono",
  weight: "400 500",
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "Tether: AI receptionist for small businesses",
    template: "%s | Tether",
  },
  description:
    "Tether answers calls and chats, books real appointments into your Google Calendar, and texts you what it cannot handle.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <ClerkProvider>
      {/* Font variables live on <html> so :root-level --font-sans can resolve them. */}
      <html lang="en" className={`${oswald.variable} ${jetbrainsMono.variable}`}>
        <body className="bg-background text-foreground antialiased">
          {children}
          {/* Remembers the last page for the legal pages' Back link (reads search params). */}
          <Suspense fallback={null}>
            <ReturnPathTracker />
          </Suspense>
        </body>
      </html>
    </ClerkProvider>
  );
}
