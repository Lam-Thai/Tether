import type { Metadata } from "next";

import { LegalDocument } from "@/components/legal/legal-document";
import { TERMS_SECTIONS } from "@/components/legal/terms-sections";
import { TERMS_PATH } from "@/lib/legal";

const description =
  "The terms for using Tether: the service and its limits, acceptable use, the trial and subscription, and liability.";

// Public (lib/routes.ts) and static, like /privacy.
export const metadata = {
  title: "Terms of Service",
  description,
  alternates: { canonical: TERMS_PATH },
} satisfies Metadata;

export default function TermsPage() {
  return (
    <LegalDocument
      title="Terms of Service"
      summary="The terms for using Tether, an AI receptionist that answers, books and escalates for your business."
      path={TERMS_PATH}
      sections={TERMS_SECTIONS}
    />
  );
}
