import { cache } from "react";

import { getBusiness, type BusinessResult } from "@/lib/api/client";

/**
 * `app/dashboard/layout.tsx` (sidebar name) and `app/dashboard/page.tsx`
 * (full overview) each need the current business on every request.
 * `cache()` dedupes those into a single `GET /business` call per render.
 */
export const getCurrentBusiness = cache((token: string): Promise<BusinessResult> => {
  return getBusiness(token);
});
