import type { Metadata } from "next";

import { TestDataPanel } from "@/components/settings/test-data-panel";

export const metadata: Metadata = { title: "Data" };

// Nothing to fetch: the dashboard layout gates on business + plan, and the
// delete runs from the browser with the owner's session token.
export default function DataPage() {
  return <TestDataPanel />;
}
