import { isInboxType, type InboxQuery } from "@/lib/api/client";

type RawParams = Record<string, string | string[] | undefined>;

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const WORD = /^[A-Za-z_]{1,40}$/;
const CURSOR = /^[A-Za-z0-9_=-]{1,512}$/;

function first(value: string | string[] | undefined): string | undefined {
  const v = Array.isArray(value) ? value[0] : value;
  return v === undefined || v === "" ? undefined : v;
}

/** Reads the inbox filters out of the page's `searchParams`, dropping anything malformed. */
export function parseInboxQuery(params: RawParams): InboxQuery {
  const query: InboxQuery = {};
  const type = first(params.type);
  if (type !== undefined && isInboxType(type)) query.type = type;
  const status = first(params.status);
  if (status !== undefined && WORD.test(status)) query.status = status;
  const intent = first(params.intent);
  if (intent !== undefined && WORD.test(intent)) query.intent = intent;
  const from = first(params.from);
  if (from !== undefined && DATE.test(from)) query.from = from;
  const to = first(params.to);
  if (to !== undefined && DATE.test(to)) query.to = to;
  const cursor = first(params.cursor);
  if (cursor !== undefined && CURSOR.test(cursor)) query.cursor = cursor;
  return query;
}

function search(query: InboxQuery): string {
  const params = new URLSearchParams();
  if (query.type && query.type !== "all") params.set("type", query.type);
  if (query.status) params.set("status", query.status);
  if (query.intent) params.set("intent", query.intent);
  if (query.from) params.set("from", query.from);
  if (query.to) params.set("to", query.to);
  if (query.cursor) params.set("cursor", query.cursor);
  return params.toString();
}

/** `/dashboard/inbox` with the query that applies. `limit` is never put in the URL. */
export function inboxHref(query: InboxQuery): string {
  const qs = search(query);
  return qs ? `/dashboard/inbox?${qs}` : "/dashboard/inbox";
}

/** A detail link that carries the inbox query, so its back link can return to the same page. */
export function interactionHref(id: string, query: InboxQuery): string {
  const qs = search(query);
  return qs ? `/dashboard/inbox/${encodeURIComponent(id)}?${qs}` : `/dashboard/inbox/${encodeURIComponent(id)}`;
}

/** Applies a filter change. The cursor always goes: it only means something for the old filters. */
export function withFilters(query: InboxQuery, patch: Partial<InboxQuery>): InboxQuery {
  const next: InboxQuery = { ...query, ...patch };
  delete next.cursor;
  for (const key of ["type", "status", "intent", "from", "to"] as const) {
    if (next[key] === "") delete next[key];
  }
  return next;
}
