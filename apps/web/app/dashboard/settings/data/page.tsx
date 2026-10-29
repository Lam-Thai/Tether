import type { Metadata } from "next";

import { AccountDeletionPanel } from "@/components/settings/account-deletion-panel";
import { TestDataPanel } from "@/components/settings/test-data-panel";

export const metadata: Metadata = { title: "Data" };

// Nothing to fetch: the dashboard layout gates on business + plan, and the
// deletes run from the browser with the owner's session token.
export default function DataPage() {
  return (
    <div className="flex w-full max-w-[1280px] flex-col gap-6">
      <TestDataPanel />
      <AccountDeletionPanel />
    </div>
  );
}
