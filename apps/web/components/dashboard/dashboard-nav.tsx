/**
 * Placeholder sidebar nav — every entry here is a future slice (#3 only
 * ships the business workspace itself). Rendered as non-interactive list
 * items rather than links so nothing routes to a page that doesn't exist
 * yet.
 */
const NAV_ITEMS = ["Agent", "FAQs", "Services", "Calendar", "Inbox", "Phone", "Settings"] as const;

export function DashboardNav() {
  return (
    <nav aria-label="Workspace sections">
      <ul className="flex flex-col gap-1">
        {NAV_ITEMS.map((item) => (
          <li key={item}>
            <span
              aria-disabled="true"
              className="block cursor-not-allowed rounded-md px-3 py-2 text-sm text-muted-foreground"
            >
              {item}
            </span>
          </li>
        ))}
      </ul>
    </nav>
  );
}
