import type { HealthResult } from "@/lib/api/client";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Callout } from "@/components/ui/callout";
import { Eyebrow } from "@/components/ui/eyebrow";

interface StatusViewProps {
  result: HealthResult;
}

/**
 * Pure presentational rendering of a `HealthResult`. Kept separate from
 * `app/page.tsx` (which does the actual fetch) so the three states can be
 * unit-tested without a Server Component boundary.
 */
export function StatusView({ result }: StatusViewProps) {
  if (result.kind === "unreachable") {
    return (
      <PageShell>
        <Callout tone="danger" title="Can't reach the API" headingLevel={1}>
          <p>{result.reason}</p>
          <p className="mt-2">
            Check that{" "}
            <code className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-sm text-foreground">
              NEXT_PUBLIC_API_BASE_URL
            </code>{" "}
            points at a running deployment, then reload this page.
          </p>
        </Callout>
      </PageShell>
    );
  }

  const isHealthy = result.kind === "healthy";
  const { data } = result;

  return (
    <PageShell>
      <div className="flex flex-col gap-3">
        <Eyebrow>Tether status</Eyebrow>
        <h1 className="text-h1 font-bold tracking-tight uppercase">
          {isHealthy ? "API: healthy · Database: connected" : "Database unavailable"}
        </h1>
        <p className="max-w-[72ch] text-base text-muted-foreground">
          {isHealthy
            ? "The API is reachable and the database is responding to queries."
            : "The API is reachable, but it could not reach the database."}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-[2fr_1fr_1fr]">
        <StatCell label="API" value="Reachable" tone="success" />
        <StatCell
          label="Database"
          value={data.db === "connected" ? "Connected" : "Error"}
          tone={data.db === "connected" ? "success" : "warning"}
        />
        <StatCell label="Version" value={data.version} tone="neutral" mono />
      </div>
    </PageShell>
  );
}

function PageShell({ children }: { children: React.ReactNode }) {
  return <div className="flex w-full max-w-3xl flex-col gap-8">{children}</div>;
}

function StatCell({
  label,
  value,
  tone,
  mono = false,
}: {
  label: string;
  value: string;
  tone: BadgeProps["tone"];
  mono?: boolean;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-md border border-border bg-card p-5 shadow-card">
      <Eyebrow>{label}</Eyebrow>
      <Badge tone={tone} className={mono ? "font-mono tracking-normal normal-case" : undefined}>
        {value}
      </Badge>
    </div>
  );
}
