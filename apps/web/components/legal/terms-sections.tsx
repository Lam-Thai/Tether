import type { LegalSection } from "@/components/legal/legal-document";
import { LegalFact } from "@/components/legal/legal-fact";
import { InlineLink, List, P } from "@/components/legal/prose";
import { CONTACT_EMAIL, JURISDICTION, LEGAL_NAME, PRIVACY_PATH } from "@/lib/legal";

/*
 * Must match how Tether actually works. The trial length follows
 * BILLING_TRIAL_DAYS and the limits in "Usage limits" follow the API's
 * defaults (apps/api core/config.py); change this text and LAST_UPDATED
 * (lib/legal.ts) whenever either changes.
 */

const operator = <LegalFact value={LEGAL_NAME} label="legal name" />;
const contact = <LegalFact value={CONTACT_EMAIL} label="contact email" email />;

const privacyLink = <InlineLink href={PRIVACY_PATH}>Privacy Policy</InlineLink>;

export const TERMS_SECTIONS: LegalSection[] = [
  {
    id: "agreement",
    title: "Agreement",
    body: (
      <>
        <P>
          These terms are an agreement between you and {operator}, which operates Tether. By creating an account
          or using Tether, you agree to them and to the {privacyLink}. If you use Tether for a business, you
          confirm that you can agree to these terms on its behalf.
        </P>
        <P>
          Tether is in a prototype phase. Features may change, and you should use it with synthetic test data
          only.
        </P>
      </>
    ),
  },
  {
    id: "service",
    title: "The service",
    body: (
      <>
        <P>
          Tether is an AI receptionist. It answers customer chats, books appointments into your Google Calendar
          and escalates to you what it cannot handle. Answering phone calls and sending text alerts are not
          active yet.
        </P>
        <P>
          Tether uses AI, and AI can make mistakes. It can misunderstand a customer, give an incomplete answer or
          miss something that needed your attention. Tether only tells a customer a booking is made once Google
          Calendar has accepted it, but you are responsible for reviewing your bookings and conversations, and
          for keeping your services, prices, hours and FAQs accurate.
        </P>
      </>
    ),
  },
  {
    id: "not-for-emergencies",
    title: "Not for emergencies or regulated use",
    body: (
      <List>
        <li>Tether is not an emergency service and must not be used in place of one.</li>
        <li>Do not use Tether for healthcare, medical advice or diagnosis, or other regulated services.</li>
        <li>
          Tether makes no claim of compliance with healthcare, payment card or other industry regulations. Do
          not use it to collect payment card numbers or health information.
        </li>
      </List>
    ),
  },
  {
    id: "accounts",
    title: "Your account",
    body: (
      <P>
        Keep your account details accurate and your sign-in secure. You are responsible for what happens under
        your account and for how your receptionist is configured. If you think someone else has accessed your
        account, email {contact}.
      </P>
    ),
  },
  {
    id: "acceptable-use",
    title: "Acceptable use",
    body: (
      <>
        <P>You agree not to use Tether to:</P>
        <List>
          <li>make or send unlawful calls or messages</li>
          <li>send spam or marketing that people have not agreed to receive</li>
          <li>impersonate a person or business, or mislead customers about who they are talking to</li>
          <li>break laws on recording, consent or privacy that apply to your conversations</li>
          <li>get around usage limits, probe or disrupt the service, or access other businesses&apos; data</li>
        </List>
      </>
    ),
  },
  {
    id: "subscription",
    title: "Trial and subscription",
    body: (
      <List>
        <li>Each business gets one 14-day free trial. No card is needed to start it.</li>
        <li>
          After the trial, Tether is a monthly subscription billed by Stripe at the price shown at checkout. If
          no card has been added when the trial ends, the subscription ends.
        </li>
        <li>
          You can cancel at any time from the Billing Portal, reached from Tether&apos;s billing page. Stripe shows
          when the cancellation takes effect.
        </li>
        <li>
          When a subscription ends, the dashboard and your receptionist stop working until you subscribe again.
          Your data is kept until you delete your account, as the {privacyLink} describes.
        </li>
      </List>
    ),
  },
  {
    id: "third-parties",
    title: "Third-party services",
    body: (
      <P>
        Tether works through other providers: Google (Google Calendar and Gemini), Stripe (payments), Clerk
        (sign-in) and, once phone features are active, Twilio. Their own terms apply to your use of them, for
        example Google&apos;s terms when you connect a calendar. Tether is not responsible for their outages or
        changes, and a feature that depends on one may stop working while it is unavailable.
      </P>
    ),
  },
  {
    id: "usage-limits",
    title: "Usage limits and fair use",
    body: (
      <>
        <P>
          To keep the service available to everyone, Tether limits how much each business can use per day. The
          current limits are:
        </P>
        <List>
          <li>50 AI requests</li>
          <li>10 voice replies and 10 speech transcriptions</li>
          <li>10 phone calls, each up to 10 minutes, and 10 text alerts, once those features are active</li>
          <li>5 conversations at the same time</li>
          <li>200 FAQs and 100 services in total</li>
        </List>
        <P>
          There are also limits across the whole platform. Daily limits reset at 00:00 UTC, and Settings, Usage
          shows where you stand. When a limit is reached, your receptionist gives the customer a safe reply with
          your contact details instead. We may change these limits as the service grows.
        </P>
      </>
    ),
  },
  {
    id: "your-content",
    title: "Your content",
    body: (
      <P>
        Your business information and your conversations stay yours. You allow Tether to process them only to
        run the service for you, as the {privacyLink} describes.
      </P>
    ),
  },
  {
    id: "disclaimers",
    title: "Disclaimers",
    body: (
      <P>
        To the extent the law allows, Tether is provided as is and as available, without warranties of any
        kind. We do not promise that it will be uninterrupted, error free, or that every answer or booking will
        be correct.
      </P>
    ),
  },
  {
    id: "liability",
    title: "Limitation of liability",
    body: (
      <P>
        To the extent the law allows, {operator} is not liable for indirect or consequential losses, such as
        lost profits, lost bookings or lost data. Our total liability for any claim about Tether is limited to
        the amount you paid for Tether in the 12 months before the claim.
      </P>
    ),
  },
  {
    id: "termination",
    title: "Ending your account",
    body: (
      <P>
        You can stop using Tether, cancel your subscription and delete your account at any time. Deleting your
        account is self-serve: use Settings, Data, or the plan page if you have no plan. It ends your subscription at once with no refund, and it
        cannot be undone. The{" "}
        <InlineLink href={PRIVACY_PATH}>Privacy Policy</InlineLink> explains what is deleted and what is kept. We
        may suspend or end your access if you break these terms or to protect the service or other people.
        Sections that by their nature should continue after your account ends, such as disclaimers and
        limitation of liability, continue to apply.
      </P>
    ),
  },
  {
    id: "governing-law",
    title: "Governing law",
    body: (
      <P>
        These terms are governed by the laws of <LegalFact value={JURISDICTION} label="jurisdiction" />, except
        where the law where you live gives you rights that cannot be waived.
      </P>
    ),
  },
  {
    id: "changes",
    title: "Changes and contact",
    body: (
      <>
        <P>
          When these terms change, the Last updated date above changes too. We will email account owners before
          a significant change takes effect. Continuing to use Tether after that means you accept the new terms.
        </P>
        <P>Questions about these terms: email {contact}.</P>
      </>
    ),
  },
];
