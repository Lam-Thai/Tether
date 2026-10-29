import type { LegalSection } from "@/components/legal/legal-document";
import { LegalFact } from "@/components/legal/legal-fact";
import { Code, ExternalLink, InlineLink, List, P } from "@/components/legal/prose";
import { CONTACT_EMAIL, LEGAL_NAME, TERMS_PATH } from "@/lib/legal";

/*
 * Every statement here must match what Tether actually does. When a slice
 * changes what data is collected, sent to a provider or kept, change this
 * text and LAST_UPDATED (lib/legal.ts) in the same PR. legal.test.tsx pins
 * the Google Limited Use sentence and the three Calendar scopes.
 */

const contact = <LegalFact value={CONTACT_EMAIL} label="contact email" email />;

export const PRIVACY_SECTIONS: LegalSection[] = [
  {
    id: "overview",
    title: "Who we are",
    body: (
      <>
        <P>
          Tether is an AI receptionist for small businesses, operated by{" "}
          <LegalFact value={LEGAL_NAME} label="legal name" />. This policy explains what data Tether collects
          from business owners who sign up and from the customers who talk to their receptionist, what it is
          used for, who processes it, and how to delete it.
        </P>
        <P>
          Tether is in a prototype phase. During this phase it should only be used with synthetic test data,
          not with real customer conversations. Using Tether is also covered by our{" "}
          <InlineLink href={TERMS_PATH}>Terms of Service</InlineLink>.
        </P>
      </>
    ),
  },
  {
    id: "account-data",
    title: "Your account",
    body: (
      <>
        <P>
          Sign-in is handled by Clerk. You can create an account with an email address and password, or with
          Google or GitHub. Tether stores your email address and the account id Clerk gives it. Your password
          and any Google or GitHub sign-in tokens are handled by Clerk and never reach Tether&apos;s servers.
        </P>
        <P>
          Signing in with Google does not connect your Google Calendar. That is a separate step, described
          below.
        </P>
      </>
    ),
  },
  {
    id: "business-data",
    title: "Business information you enter",
    body: (
      <>
        <P>To set up your receptionist, you give Tether information about your business:</P>
        <List>
          <li>your business profile: name, address, phone number, email, timezone and currency</li>
          <li>your services, their prices and durations, and your opening hours</li>
          <li>your FAQs, your receptionist&apos;s persona, greeting and voice</li>
          <li>your booking rules and escalation settings, including the phone number alerts go to</li>
        </List>
        <P>Tether uses this information only to run your receptionist and show it back to you.</P>
      </>
    ),
  },
  {
    id: "google-user-data",
    title: "Google Calendar data",
    body: (
      <>
        <P>
          Connecting a Google Calendar is optional. It lets your receptionist offer real open times and book
          appointments. Tether asks Google for three permissions (scopes), and uses each only for this:
        </P>
        <List>
          <li>
            <Code>calendar.calendarlist.readonly</Code>: to list the calendars you own, so you can choose the
            one Tether books into. Tether stores the id and name of the calendar you choose.
          </li>
          <li>
            <Code>calendar.freebusy</Code>: to read free/busy times on that calendar when a customer asks
            about availability, so only open slots are offered. Busy times are read when needed and not
            stored.
          </li>
          <li>
            <Code>calendar.events.owned</Code>: to create and cancel only the bookings Tether makes. A booking
            event holds the service, the time, the customer&apos;s name and phone number if they gave one, and
            a booking reference. Tether does not read your other events.
          </li>
        </List>
        <P>
          Google gives Tether a refresh token for these permissions. Tether stores it encrypted with
          AES-256-GCM, tied to your business, and never stores an access token. When you disconnect your
          calendar, Tether asks Google to revoke the token and deletes it. If Google can&apos;t confirm the
          revocation, Tether tells you so. You can always remove Tether&apos;s access yourself at{" "}
          <ExternalLink href="https://myaccount.google.com/permissions">
            myaccount.google.com/permissions
          </ExternalLink>
          .
        </P>
        <P>
          The only Calendar-derived data that reaches Google Gemini is up to three open start times for the day
          a customer asked about, worked out from free/busy times. Event titles, descriptions, attendees and
          calendar names are never sent to Gemini.
        </P>
        <P>
          Tether&apos;s use and transfer of information received from Google APIs to any other app will adhere to
          the{" "}
          <ExternalLink href="https://developers.google.com/terms/api-services-user-data-policy">
            Google API Services User Data Policy
          </ExternalLink>
          , including the Limited Use requirements.
        </P>
        <P>In particular, data Tether receives from Google APIs:</P>
        <List>
          <li>is used only to provide the calendar features described above</li>
          <li>is not sold, and is not used for advertising of any kind</li>
          <li>
            is not read by people, except with your permission, when needed for security, or when the law
            requires it
          </li>
          <li>
            is not used by Tether to develop, improve or train generalized AI or machine learning models, with the
            one prototype-phase exception explained in the next paragraph
          </li>
        </List>
        <P>
          The exception: during the prototype phase, Tether uses the free tier of the Gemini API. Under Google&apos;s{" "}
          <ExternalLink href="https://ai.google.dev/gemini-api/terms">Gemini API terms</ExternalLink>, Google
          may use content sent through the free tier to improve its products. Because offered start times are
          part of what Tether sends, Tether will move to a paid Gemini tier, under which Google does not use
          that content to improve its products, before Google Calendar is offered beyond testing.
        </P>
      </>
    ),
  },
  {
    id: "conversations",
    title: "Conversations and Google Gemini",
    body: (
      <>
        <P>
          Tether uses Google Gemini to understand what a customer is asking and to write a reply. For each
          reply, Gemini receives:
        </P>
        <List>
          <li>your business profile, opening hours, persona and greeting</li>
          <li>the FAQs and services that match the question</li>
          <li>the conversation so far, up to its 20 most recent messages</li>
          <li>open start times, when the customer asked about availability</li>
        </List>
        <P>
          When a conversation ends, its transcript is sent to Gemini to write a two-sentence summary for your
          inbox. Before any message is stored or sent to Gemini, Tether replaces payment card numbers, US Social
          Security numbers and card security codes with markers.
        </P>
        <P>
          Transcripts, summaries and a log of the actions Tether took (such as availability checks and
          bookings) are stored so you can review them in your inbox.
        </P>
        <P>
          During the prototype phase, you must not enter real customer data: test your receptionist with
          made-up conversations only. Tether cannot tell real text from made-up text, so anything typed into a
          conversation is processed as described here. Content sent through the Gemini API&apos;s free tier may be
          used by Google to improve its products, as explained above.
        </P>
      </>
    ),
  },
  {
    id: "payments",
    title: "Payments",
    body: (
      <P>
        Subscriptions go through Stripe. Checkout and the Billing Portal are hosted by Stripe, so your card
        details go straight to Stripe and Tether never sees or stores them. Tether stores your subscription
        status and the customer and subscription ids Stripe gives it.
      </P>
    ),
  },
  {
    id: "phone-sms",
    title: "Phone calls and text messages",
    body: (
      <P>
        Answering phone calls and sending text alerts through Twilio are not active yet. Until they are, Tether
        answers no calls and sends no text messages for your business. This section will describe what call
        and message data is processed before either feature goes live.
      </P>
    ),
  },
  {
    id: "retention",
    title: "Keeping and deleting data",
    body: (
      <>
        <P>
          Tether keeps your data while your account exists, including after a subscription ends, so nothing is
          lost if you subscribe again. You can delete data in these ways:
        </P>
        <List>
          <li>
            <strong>Delete test data</strong> (Settings, Data) removes your conversations, including transcripts,
            summaries and action logs, your bookings, your usage logs, daily usage counts from before today (UTC)
            and short-lived reply audio. Tether first tries to cancel the matching events in your Google
            Calendar. Your setup is kept: agent, services, FAQs, hours, rules, calendar connection, phone numbers
            and subscription.
          </li>
          <li>
            <strong>Disconnecting Google Calendar</strong> asks Google to revoke the stored token and deletes it.
          </li>
          <li>
            <strong>Deleting your account</strong> (Settings, Data, or the Delete account link on the plan page
            if you have no plan yet) works straight away, with no email request. You confirm by typing a phrase.
            Tether then does these steps in this order:
            <ol className="mt-2 flex list-decimal flex-col gap-2 pl-6">
              <li>
                Your Stripe customer is deleted and your subscription ends at once, with no refund. If Stripe
                can&apos;t be reached, Tether stops and deletes nothing, so you can try again. Stripe keeps
                billing records under its own obligations.
              </li>
              <li>
                Upcoming appointments that Tether added to your Google Calendar are cancelled. This is best
                effort, and Tether tells you about any it couldn&apos;t cancel. Past events stay on your calendar.
              </li>
              <li>Your calendar token is revoked with Google and deleted.</li>
              <li>
                Your business and everything in it is deleted: its setup, conversations, bookings, usage logs
                and reply audio.
              </li>
              <li>
                Your sign-in is deleted from Clerk. If that step fails, Tether tells you and you can try again.
              </li>
            </ol>
            Usage totals that are not tied to any business are kept.
          </li>
        </List>
      </>
    ),
  },
  {
    id: "subprocessors",
    title: "Service providers",
    body: (
      <>
        <P>Tether relies on these providers to run the service:</P>
        <List>
          <li>Clerk: sign-in and account management</li>
          <li>Google: Gemini, to understand conversations and write summaries, and Google Calendar</li>
          <li>Stripe: subscriptions and payments</li>
          <li>Neon: the database</li>
          <li>Vercel: hosting for the web app and the API</li>
          <li>Twilio: phone calls and text messages, once those features are active</li>
        </List>
      </>
    ),
  },
  {
    id: "contact",
    title: "Contact and changes",
    body: (
      <>
        <P>Questions about this policy or your data: email {contact}.</P>
        <P>
          When this policy changes, the Last updated date above changes too. If a change affects how your data
          is used, we will email account owners before it takes effect.
        </P>
      </>
    ),
  },
];
