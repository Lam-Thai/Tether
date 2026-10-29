import { SignOutButton } from "@/components/dashboard/sign-out-button";
import { LegalLinks } from "@/components/legal/legal-links";

/** The bottom of the dashboard sidebar: Sign out, then the legal links. */
export function SidebarFooter() {
  return (
    <div className="flex flex-col items-start gap-4">
      <SignOutButton />
      <LegalLinks inverse />
    </div>
  );
}
