/**
 * Placeholder sidebar nav: every entry is a future slice, so they render as
 * non-interactive items rather than links to pages that don't exist yet.
 */
const NAV_ITEMS = ["Agent", "FAQs", "Services", "Calendar", "Inbox", "Phone", "Settings"] as const;

export function DashboardNav() {
  return (
    <nav aria-label="Workspace sections">
      <ul className="flex flex-wrap gap-1 md:flex-col">
        {NAV_ITEMS.map((item) => (
          <li key={item}>
            <span
              aria-disabled="true"
              className="block cursor-not-allowed rounded-sm border-l-4 border-transparent px-3 py-1.5 text-base text-surface-dark-muted"
            >
              {item}
            </span>
          </li>
        ))}
      </ul>
    </nav>
  );
}
