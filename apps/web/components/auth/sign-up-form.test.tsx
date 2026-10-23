import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const pushMock = vi.fn();
const signUpCreateMock = vi.fn();
const prepareEmailAddressVerificationMock = vi.fn();
const attemptEmailAddressVerificationMock = vi.fn();
const authenticateWithRedirectMock = vi.fn();
const setActiveMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("@clerk/nextjs/legacy", () => ({
  useSignUp: () => ({
    isLoaded: true,
    signUp: {
      create: signUpCreateMock,
      prepareEmailAddressVerification: prepareEmailAddressVerificationMock,
      attemptEmailAddressVerification: attemptEmailAddressVerificationMock,
      authenticateWithRedirect: authenticateWithRedirectMock,
    },
    setActive: setActiveMock,
  }),
}));

vi.mock("@clerk/nextjs/errors", () => ({
  isClerkAPIResponseError: (error: unknown) =>
    typeof error === "object" && error !== null && "errors" in error,
}));

import { SignUpForm } from "./sign-up-form";

describe("SignUpForm", () => {
  beforeEach(() => {
    pushMock.mockReset();
    signUpCreateMock.mockReset();
    prepareEmailAddressVerificationMock.mockReset();
    attemptEmailAddressVerificationMock.mockReset();
    authenticateWithRedirectMock.mockReset();
    setActiveMock.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows validation errors and never calls Clerk when submitted empty", async () => {
    const user = userEvent.setup();
    render(<SignUpForm />);

    await user.click(screen.getByRole("button", { name: /create account/i }));

    expect(await screen.findByText("Email is required.")).toBeInTheDocument();
    expect(screen.getByText("Use at least 8 characters.")).toBeInTheDocument();
    expect(signUpCreateMock).not.toHaveBeenCalled();
  });

  it("creates the account, prepares verification, and shows the code step", async () => {
    signUpCreateMock.mockResolvedValue({ status: "missing_requirements", createdSessionId: null });
    prepareEmailAddressVerificationMock.mockResolvedValue({});
    const user = userEvent.setup();
    render(<SignUpForm />);

    await user.type(screen.getByLabelText(/email/i), "owner@acme.test");
    await user.type(screen.getByLabelText(/password/i), "super-secret-1");
    await user.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() =>
      expect(signUpCreateMock).toHaveBeenCalledWith({
        emailAddress: "owner@acme.test",
        password: "super-secret-1",
      }),
    );
    await waitFor(() =>
      expect(prepareEmailAddressVerificationMock).toHaveBeenCalledWith({ strategy: "email_code" }),
    );
    expect(await screen.findByText("Check your email")).toBeInTheDocument();
  });

  it("skips the code step and redirects when sign-up is already complete", async () => {
    signUpCreateMock.mockResolvedValue({ status: "complete", createdSessionId: "sess_123" });
    const user = userEvent.setup();
    render(<SignUpForm />);

    await user.type(screen.getByLabelText(/email/i), "owner@acme.test");
    await user.type(screen.getByLabelText(/password/i), "super-secret-1");
    await user.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => expect(setActiveMock).toHaveBeenCalledWith({ session: "sess_123" }));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/onboarding"));
    expect(prepareEmailAddressVerificationMock).not.toHaveBeenCalled();
  });

  it("verifies the code, activates the session, and redirects to /onboarding", async () => {
    signUpCreateMock.mockResolvedValue({ status: "missing_requirements", createdSessionId: null });
    prepareEmailAddressVerificationMock.mockResolvedValue({});
    attemptEmailAddressVerificationMock.mockResolvedValue({
      status: "complete",
      createdSessionId: "sess_456",
    });
    const user = userEvent.setup();
    render(<SignUpForm />);

    await user.type(screen.getByLabelText(/email/i), "owner@acme.test");
    await user.type(screen.getByLabelText(/password/i), "super-secret-1");
    await user.click(screen.getByRole("button", { name: /create account/i }));

    await screen.findByText("Check your email");
    await user.type(screen.getByLabelText(/verification code/i), "123456");
    await user.click(screen.getByRole("button", { name: /verify email/i }));

    await waitFor(() =>
      expect(attemptEmailAddressVerificationMock).toHaveBeenCalledWith({ code: "123456" }),
    );
    await waitFor(() => expect(setActiveMock).toHaveBeenCalledWith({ session: "sess_456" }));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/onboarding"));
  });

  it("surfaces Clerk's error message on a failed verification instead of navigating", async () => {
    signUpCreateMock.mockResolvedValue({ status: "missing_requirements", createdSessionId: null });
    prepareEmailAddressVerificationMock.mockResolvedValue({});
    attemptEmailAddressVerificationMock.mockRejectedValue({
      errors: [{ message: "Incorrect code" }],
    });
    const user = userEvent.setup();
    render(<SignUpForm />);

    await user.type(screen.getByLabelText(/email/i), "owner@acme.test");
    await user.type(screen.getByLabelText(/password/i), "super-secret-1");
    await user.click(screen.getByRole("button", { name: /create account/i }));

    await screen.findByText("Check your email");
    await user.type(screen.getByLabelText(/verification code/i), "000000");
    await user.click(screen.getByRole("button", { name: /verify email/i }));

    expect(await screen.findByText("Incorrect code")).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("says the email is verified, not that the code failed, when Clerk still needs another field", async () => {
    signUpCreateMock.mockResolvedValue({
      status: "missing_requirements",
      createdSessionId: null,
      missingFields: [],
    });
    prepareEmailAddressVerificationMock.mockResolvedValue({});
    attemptEmailAddressVerificationMock.mockResolvedValue({
      status: "missing_requirements",
      createdSessionId: null,
      missingFields: ["username"],
    });
    const user = userEvent.setup();
    render(<SignUpForm />);

    await user.type(screen.getByLabelText(/email/i), "owner@acme.test");
    await user.type(screen.getByLabelText(/password/i), "super-secret-1");
    await user.click(screen.getByRole("button", { name: /create account/i }));
    await screen.findByText("Check your email");
    await user.type(screen.getByLabelText(/verification code/i), "123456");
    await user.click(screen.getByRole("button", { name: /verify email/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/your email is verified/i);
    expect(alert).toHaveTextContent(/a username/i);
    expect(alert).not.toHaveTextContent(/code didn.t work/i);
    expect(setActiveMock).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("doesn't send a code that could never finish sign-up when Clerk requires a field this form lacks", async () => {
    signUpCreateMock.mockResolvedValue({
      status: "missing_requirements",
      createdSessionId: null,
      missingFields: ["username", "legal_accepted"],
    });
    const user = userEvent.setup();
    render(<SignUpForm />);

    await user.type(screen.getByLabelText(/email/i), "owner@acme.test");
    await user.type(screen.getByLabelText(/password/i), "super-secret-1");
    await user.click(screen.getByRole("button", { name: /create account/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/a username and accepting the terms/i);
    expect(prepareEmailAddressVerificationMock).not.toHaveBeenCalled();
    expect(screen.queryByText("Check your email")).not.toBeInTheDocument();
  });

  it("points to sign-in when the account is created but the session can't be started", async () => {
    signUpCreateMock.mockResolvedValue({ status: "missing_requirements", createdSessionId: null });
    prepareEmailAddressVerificationMock.mockResolvedValue({});
    attemptEmailAddressVerificationMock.mockResolvedValue({
      status: "complete",
      createdSessionId: "sess_789",
    });
    setActiveMock.mockRejectedValue(new Error("network down"));
    const user = userEvent.setup();
    render(<SignUpForm />);

    await user.type(screen.getByLabelText(/email/i), "owner@acme.test");
    await user.type(screen.getByLabelText(/password/i), "super-secret-1");
    await user.click(screen.getByRole("button", { name: /create account/i }));
    await screen.findByText("Check your email");
    await user.type(screen.getByLabelText(/verification code/i), "123456");
    await user.click(screen.getByRole("button", { name: /verify email/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/your email is verified/i);
    expect(alert).not.toHaveTextContent(/code didn.t work/i);
    expect(screen.getByRole("link", { name: /sign in/i })).toHaveAttribute("href", "/sign-in");
    expect(pushMock).not.toHaveBeenCalled();
  });

  it.each([
    ["Google", "oauth_google"],
    ["GitHub", "oauth_github"],
  ])("signs up with %s through Clerk and lands on onboarding via /sso-callback", async (name, strategy) => {
    authenticateWithRedirectMock.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<SignUpForm />);

    await user.click(screen.getByRole("button", { name: `Continue with ${name}` }));

    await waitFor(() =>
      expect(authenticateWithRedirectMock).toHaveBeenCalledWith({
        strategy,
        redirectUrl: "/sso-callback",
        redirectUrlComplete: "/onboarding",
      }),
    );
    expect(signUpCreateMock).not.toHaveBeenCalled();
  });
});
