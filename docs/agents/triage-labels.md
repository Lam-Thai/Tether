# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ----------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

> Note: these five labels don't exist yet on `Lam-Thai/Tether` (only the GitHub defaults do). Create them with `gh label create <name> --color <hex>` the first time `/triage` needs one, or create all five up front:
> `gh label create needs-triage --color ededed && gh label create needs-info --color d876e3 && gh label create ready-for-agent --color 0e8a16 && gh label create ready-for-human --color 1d76db && gh label create wontfix --color ffffff`
