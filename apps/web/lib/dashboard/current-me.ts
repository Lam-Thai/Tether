import { cache } from "react";

import { getMe, type MeResult } from "@/lib/api/client";

/**
 * The dashboard layout (routing, sidebar) and page (overview, plan) both
 * need `GET /me` on every request; `cache()` makes that one call per render.
 */
export const getCurrentMe = cache((token: string): Promise<MeResult> => getMe(token));
