import type { Metadata } from "next";
import { JetBrains_Mono, Oswald } from "next/font/google";
import { ClerkProvider } from "@clerk/nextjs";
import "./globals.css";

const oswald = Oswald({
  variable: "--font-oswald",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
});

export const metadata: Metadata = {
  title: "Tether status",
  description: "Live health status for the Tether API and database.",
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
        </body>
      </html>
    </ClerkProvider>
  );
}
