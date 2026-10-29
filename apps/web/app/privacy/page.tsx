import type { Metadata } from "next";

import { LegalDocument } from "@/components/legal/legal-document";
import { PRIVACY_SECTIONS } from "@/components/legal/privacy-sections";
import { PRIVACY_PATH } from "@/lib/legal";

const description =
  "What Tether collects, including Google Calendar data, what it is used for, who processes it, and how to delete it.";

// Public (lib/routes.ts) and static: Google's OAuth review links here signed out.
export const metadata = {
  title: "Privacy Policy",
  description,
} satisfies Metadata;

export default function PrivacyPage() {
  return (
    <LegalDocument
      title="Privacy Policy"
      summary="What Tether collects from business owners and their customers, what it is used for, and how to delete it."
      path={PRIVACY_PATH}
      sections={PRIVACY_SECTIONS}
    />
  );
}
