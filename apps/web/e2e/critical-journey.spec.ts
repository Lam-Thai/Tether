import { expect, test, type Page, type Response } from "@playwright/test";
import { setupClerkTestingToken } from "@clerk/testing/playwright";

/**
 * The MVP critical journey, one serial run in a single browser context (slice 17).
 * Each step starts where the previous one left off and asserts only what the UI shows
 * after the backend confirmed it. Providers behind the API are fakes (see apps/api/CLAUDE.md,
 * "E2E fake-provider mode"); Clerk is real.
 */
test.describe.configure({ mode: "serial" });

const EMAIL = process.env.E2E_CLERK_USER_EMAIL ?? "";
const PASSWORD = process.env.E2E_CLERK_USER_PASSWORD ?? "";
// Clerk development instances accept this code for +clerk_test addresses.
const CLERK_TEST_CODE = "424242";
// Start of USAGE_LIMITED_NOTE in components/agent/test-chat.tsx (a client component, not imported here).
const USAGE_LIMITED_NOTE = "Today's AI limit is reached";

const FAQ_QUESTION = "Is there free parking nearby?";
const FAQ_ANSWER = "Yes, there is free parking behind the shop.";

let page: Page;

test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext();
  page = await context.newPage();
  await setupClerkTestingToken({ page });
});

test.afterAll(async () => {
  await page.context().close();
});

/** Resolves when the API answers a call with this method and path (and an accepted status). */
function apiResponse(method: string, path: RegExp, status: number[] = [200, 201, 204]) {
  return page.waitForResponse((response: Response) => {
    const url = new URL(response.url());
    return (
      response.request().method() === method &&
      url.pathname.startsWith("/api/v1/") &&
      path.test(url.pathname) &&
      status.includes(response.status())
    );
  });
}

async function sendChatMessage(text: string) {
  const turn = apiResponse("POST", /\/agent\/test$/);
  await page.getByLabel("Message", { exact: true }).fill(text);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await turn;
  await expect(page.getByText(/is typing/)).toBeHidden();
}

const chatLog = () => page.getByRole("log", { name: "Test conversation" });

test("signs in with the custom form", async () => {
  await page.goto("/sign-in");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();

  // A new device (every fresh browser context) may need the emailed code.
  const code = page.getByLabel("Verification code");
  const onboarding = page.waitForURL("**/onboarding", { timeout: 60_000 });
  const codeStep = code.waitFor({ state: "visible", timeout: 60_000 });
  await Promise.race([onboarding, codeStep]);
  if (await code.isVisible()) {
    await code.fill(CLERK_TEST_CODE);
    await page.getByRole("button", { name: "Verify and sign in" }).click();
  }
  // A new account has no business yet, so the gate sends it to onboarding.
  await page.waitForURL("**/onboarding");
  await expect(page.getByRole("heading", { name: "Set up your business" })).toBeVisible();
});

test("onboarding creates the business", async () => {
  await page.getByLabel("Business name").fill("E2E Salon");
  await page.getByLabel("Industry").fill("Hair salon");
  await page.getByLabel("Address").fill("1 Test Street, New York");
  await page.getByLabel("Timezone").selectOption("America/New_York");
  const created = apiResponse("POST", /\/business$/, [201]);
  await page.getByRole("button", { name: "Create business" }).click();
  await created;
  await page.waitForURL("**/billing/plan");
});

test("chooses the free trial (Stripe checkout is intercepted)", async () => {
  await page.route("https://checkout.stripe.com/**", async (route) => {
    const sessionId = new URL(route.request().url()).pathname.split("/").filter(Boolean).pop() ?? "";
    await route.fulfill({
      status: 302,
      headers: { location: `http://localhost:3000/billing/return?session_id=${encodeURIComponent(sessionId)}` },
    });
  });

  await expect(page.getByRole("heading", { name: "Choose how to start" })).toBeVisible();
  await page.getByRole("button", { name: "Start free trial" }).click();
  // /billing/return only redirects once the API reports the plan as entitled.
  await page.waitForURL("**/dashboard");
  await expect(page.getByRole("navigation", { name: "Workspace sections" })).toBeVisible();
});

test("sets up the agent: persona, FAQ, service, hours", async () => {
  await page.goto("/dashboard/agent");
  await expect(page.getByLabel("Receptionist name")).toBeVisible();

  // Persona
  await page.getByLabel("Receptionist name").fill("Riley");
  await page.getByLabel("Greeting").fill("Hello, you have reached E2E Salon. How can I help?");
  const persona = apiResponse("PATCH", /\/agent$/, [200]);
  await page.getByRole("button", { name: "Save changes" }).click();
  await persona;

  // FAQ
  await page
    .getByRole("button", { name: /^Add (your first )?FAQ$/ })
    .first()
    .click();
  const faqDialog = page.getByRole("dialog", { name: "Add FAQ" });
  await faqDialog.getByLabel("Question", { exact: true }).fill(FAQ_QUESTION);
  await faqDialog.getByLabel("Answer", { exact: true }).fill(FAQ_ANSWER);
  const faqSaved = apiResponse("POST", /\/faqs$/, [201]);
  await faqDialog.getByRole("button", { name: "Add FAQ" }).click();
  await faqSaved;
  await expect(page.getByText(FAQ_QUESTION, { exact: true })).toBeVisible();

  // Service
  await page
    .getByRole("button", { name: /^Add (your first )?service$/ })
    .first()
    .click();
  const serviceDialog = page.getByRole("dialog", { name: "Add service" });
  await serviceDialog.getByLabel("Name", { exact: true }).fill("Haircut");
  await serviceDialog.getByLabel("Duration (minutes)").fill("30");
  const serviceSaved = apiResponse("POST", /\/services$/, [201]);
  await serviceDialog.getByRole("button", { name: "Add service" }).click();
  await serviceSaved;
  await expect(page.getByRole("button", { name: "Edit: Haircut" })).toBeVisible();

  // Hours: open every day, so "tomorrow" is open whatever weekday the run happens on.
  for (const day of ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]) {
    const closed = page.getByRole("switch", { name: `Closed: ${day}` });
    if ((await closed.getAttribute("aria-checked")) === "true") await closed.click();
    await expect(closed).toHaveAttribute("aria-checked", "false");
  }
  const hoursSaved = apiResponse("PUT", /\/business\/hours$/, [200]);
  await page.getByRole("button", { name: "Save hours" }).click();
  await hoursSaved;
});

test("connects Google Calendar (fake OAuth) and selects the calendar", async () => {
  await page.goto("/dashboard/settings/calendar");
  await page.getByRole("button", { name: "Connect Google Calendar" }).click();
  // The fake provider returns straight to the callback, which then returns here.
  await page.waitForURL("**/dashboard/settings/calendar");
  await page.getByLabel("Calendar for bookings").selectOption({ label: "E2E Calendar" });
  const selected = apiResponse("PATCH", /\/calendar$/, [200]);
  await page.getByRole("button", { name: "Save calendar" }).click();
  await selected;
  await expect(page.getByText('Connected · "E2E Calendar"')).toBeVisible();
});

test("test chat: FAQ answer, booking, escalation, end", async () => {
  await page.goto("/dashboard/agent");
  await expect(page.getByLabel("Message", { exact: true })).toBeVisible();

  await sendChatMessage(FAQ_QUESTION);
  await expect(chatLog().getByText(FAQ_ANSWER)).toBeVisible();

  await sendChatMessage("I would like to book a Haircut tomorrow at 10:00. My name is Sam Tester.");
  await expect(chatLog().getByText(/You're booked/)).toBeVisible();
  const bookings = page.getByRole("region", { name: "Bookings in this chat" });
  await expect(bookings.getByText("Confirmed")).toBeVisible();
  await expect(bookings.getByText("Haircut")).toBeVisible();
  await expect(bookings.getByText("For Sam Tester")).toBeVisible();
  await expect(bookings.getByText(/10:00 AM/)).toBeVisible();

  await sendChatMessage("I want to speak to a manager");
  await expect(chatLog().getByText(/team will be notified/)).toBeVisible();

  const ended = apiResponse("POST", /\/agent\/test\/[^/]+\/end$/, [200]);
  await page.getByRole("button", { name: "End chat" }).click();
  await ended;
  await expect(page.getByText("This chat has ended.")).toBeVisible();
});

test("inbox shows the conversation, summary and escalation", async () => {
  await page.goto("/dashboard/inbox");
  await expect(page.getByRole("heading", { name: "Inbox" })).toBeVisible();
  const rows = page.getByRole("list", { name: "Conversations" });
  await expect(rows.getByRole("listitem")).toHaveCount(1);
  await rows.getByRole("link", { name: /open conversation/ }).click();

  await page.waitForURL(/\/dashboard\/inbox\/[0-9a-f-]+/);
  // Transcript: the customer's and the agent's messages.
  const transcript = page.getByRole("list", { name: "Messages" });
  await expect(transcript.getByText(FAQ_QUESTION)).toBeVisible();
  await expect(transcript.getByText(FAQ_ANSWER)).toBeVisible();
  await expect(transcript.getByText(/You're booked/)).toBeVisible();
  await expect(transcript.getByText("I want to speak to a manager")).toBeVisible();
  // Summary: exactly two sentences, written for this outcome.
  await expect(page.getByRole("heading", { name: /summary/i })).toBeVisible();
  await expect(page.getByText(/^The customer .+\.\s.+\.$/)).toBeVisible();
  // Escalation record.
  await expect(page.getByRole("heading", { name: "Escalation", exact: true })).toBeVisible();
  const escalations = page.getByRole("list", { name: "Escalations" });
  await expect(escalations.getByText("Asked for a person")).toBeVisible();
  await expect(escalations.getByText("Open", { exact: true })).toBeVisible();
});

test("falls back safely once the daily AI limit is reached", async () => {
  await page.goto("/dashboard/agent");
  await expect(page.getByLabel("Message", { exact: true })).toBeVisible();
  // The previous chat ended; make sure this is a fresh one in case the page restored it.
  const newChat = page.getByRole("button", { name: "New chat" });
  if (await newChat.isVisible()) await newChat.click();

  // The API caps the business at 14 model requests a day; the first chat used 8 and each
  // FAQ turn uses 2, so the limit shows within a few sends. Bounded: never loop forever.
  const note = page.getByText(USAGE_LIMITED_NOTE);
  let limited = false;
  for (let sends = 1; sends <= 10 && !limited; sends += 1) {
    await sendChatMessage(FAQ_QUESTION);
    limited = await note.isVisible();
  }
  await expect(note).toBeVisible();
  await expect(page.getByRole("link", { name: "View usage" })).toBeVisible();
});
