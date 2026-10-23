import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const pushMock = vi.fn();
const signInCreateMock = vi.fn();
const prepareSecondFactorMock = vi.fn();
const attemptSecondFactorMock = vi.fn();
const authenticateWithRedirectMock = vi.fn();
const setActiveMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("@clerk/nextjs/legacy", () => ({
  useSignIn: () => ({
    isLoaded: true,
    signIn: {
      create: signInCreateMock,
      prepareSecondFactor: prepareSecondFactorMock,
      attemptSecondFactor: attemptSecondFactorMock,
      authenticateWithRedirect: authenticateWithRedirectMock,
    },
    setActive: setActiveMock,
  }),
}));

const EMAIL_CODE_FACTOR = {
  strategy: "email_code",
  emailAddressId: "idn_123",
  safeIdentifier: "o***@acme.test",
};

async function submitCredentials(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/email/i), "owner@acme.test");
  await user.type(screen.getByLabelText(/password/i), "correct-password");
  await user.click(screen.getByRole("button", { name: /sign in/i }));
}

vi.mock("@clerk/nextjs/errors", () => ({
  isClerkAPIResponseError: (error: unknown) =>
    typeof error === "object" && error !== null && "errors" in error,
}));

import { SignInForm } from "./sign-in-form";

describe("SignInForm", () => {
  beforeEach(() => {
    pushMock.mockReset();
    signInCreateMock.mockReset();
    prepareSecondFactorMock.mockReset();
    attemptSecondFactorMock.mockReset();
    authenticateWithRedirectMock.mockReset();
    setActiveMock.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows validation errors and never calls Clerk when submitted empty", async () => {
    const user = userEvent.setup();
    render(<SignInForm />);

    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("Email is required.")).toBeInTheDocument();
    expect(screen.getByText("Password is required.")).toBeInTheDocument();
    expect(signInCreateMock).not.toHaveBeenCalled();
  });

  it("signs in, activates the session, and redirects to /dashboard on success", async () => {
    signInCreateMock.mockResolvedValue({ status: "complete", createdSessionId: "sess_123" });
    const user = userEvent.setup();
    render(<SignInForm />);

    await user.type(screen.getByLabelText(/email/i), "owner@acme.test");
    await user.type(screen.getByLabelText(/password/i), "correct-password");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() =>
      expect(signInCreateMock).toHaveBeenCalledWith({
        identifier: "owner@acme.test",
        password: "correct-password",
      }),
    );
    await waitFor(() => expect(setActiveMock).toHaveBeenCalledWith({ session: "sess_123" }));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
  });

  it("surfaces Clerk's error message instead of navigating", async () => {
    signInCreateMock.mockRejectedValue({
      errors: [{ message: "Password is incorrect" }],
    });
    const user = userEvent.setup();
    render(<SignInForm />);

    await user.type(screen.getByLabelText(/email/i), "owner@acme.test");
    await user.type(screen.getByLabelText(/password/i), "wrong-password");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("Password is incorrect")).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
    expect(setActiveMock).not.toHaveBeenCalled();
  });

  it("asks for the emailed code on a new device, then signs in once Clerk confirms it", async () => {
    signInCreateMock.mockResolvedValue({
      status: "needs_client_trust",
      createdSessionId: null,
      supportedSecondFactors: [EMAIL_CODE_FACTOR],
    });
    prepareSecondFactorMock.mockResolvedValue({});
    attemptSecondFactorMock.mockResolvedValue({ status: "complete", createdSessionId: "sess_777" });
    const user = userEvent.setup();
    render(<SignInForm />);

    await submitCredentials(user);

    await waitFor(() =>
      expect(prepareSecondFactorMock).toHaveBeenCalledWith({
        strategy: "email_code",
        emailAddressId: "idn_123",
      }),
    );
    expect(await screen.findByText(/new device/i)).toBeInTheDocument();
    expect(screen.getByText(/o\*\*\*@acme\.test/)).toBeInTheDocument();
    expect(setActiveMock).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText(/verification code/i), "654321");
    await user.click(screen.getByRole("button", { name: /verify/i }));

    await waitFor(() =>
      expect(attemptSecondFactorMock).toHaveBeenCalledWith({ strategy: "email_code", code: "654321" }),
    );
    await waitFor(() => expect(setActiveMock).toHaveBeenCalledWith({ session: "sess_777" }));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/dashboard"));
  });

  it("uses the same code step for a second factor sent by email", async () => {
    signInCreateMock.mockResolvedValue({
      status: "needs_second_factor",
      createdSessionId: null,
      supportedSecondFactors: [{ strategy: "totp" }, EMAIL_CODE_FACTOR],
    });
    prepareSecondFactorMock.mockResolvedValue({});
    const user = userEvent.setup();
    render(<SignInForm />);

    await submitCredentials(user);

    await waitFor(() =>
      expect(prepareSecondFactorMock).toHaveBeenCalledWith({
        strategy: "email_code",
        emailAddressId: "idn_123",
      }),
    );
    expect(await screen.findByLabelText(/verification code/i)).toBeInTheDocument();
  });

  it("shows Clerk's message for a wrong second-factor code and stays signed out", async () => {
    signInCreateMock.mockResolvedValue({
      status: "needs_client_trust",
      createdSessionId: null,
      supportedSecondFactors: [EMAIL_CODE_FACTOR],
    });
    prepareSecondFactorMock.mockResolvedValue({});
    attemptSecondFactorMock.mockRejectedValue({ errors: [{ message: "Incorrect code" }] });
    const user = userEvent.setup();
    render(<SignInForm />);

    await submitCredentials(user);
    await user.type(await screen.findByLabelText(/verification code/i), "000000");
    await user.click(screen.getByRole("button", { name: /verify/i }));

    expect(await screen.findByText("Incorrect code")).toBeInTheDocument();
    expect(setActiveMock).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("says plainly when the account needs a second factor this form can't handle", async () => {
    signInCreateMock.mockResolvedValue({
      status: "needs_second_factor",
      createdSessionId: null,
      supportedSecondFactors: [{ strategy: "totp" }],
    });
    const user = userEvent.setup();
    render(<SignInForm />);

    await submitCredentials(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(/authenticator app/i);
    expect(prepareSecondFactorMock).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("explains a required password reset instead of a generic failure", async () => {
    signInCreateMock.mockResolvedValue({ status: "needs_new_password", createdSessionId: null });
    const user = userEvent.setup();
    render(<SignInForm />);

    await submitCredentials(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(/password needs to be reset/i);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("doesn't navigate when Clerk accepts the password but the session can't be started", async () => {
    signInCreateMock.mockResolvedValue({ status: "complete", createdSessionId: "sess_123" });
    setActiveMock.mockRejectedValue(new Error("network down"));
    const user = userEvent.setup();
    render(<SignInForm />);

    await submitCredentials(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn.t start your session/i);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it.each([
    ["Google", "oauth_google"],
    ["GitHub", "oauth_github"],
  ])("hands off to %s through Clerk and comes back via /sso-callback", async (name, strategy) => {
    authenticateWithRedirectMock.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<SignInForm />);

    await user.click(screen.getByRole("button", { name: `Continue with ${name}` }));

    await waitFor(() =>
      expect(authenticateWithRedirectMock).toHaveBeenCalledWith({
        strategy,
        redirectUrl: "/sso-callback",
        redirectUrlComplete: "/dashboard",
      }),
    );
    expect(signInCreateMock).not.toHaveBeenCalled();
    expect(setActiveMock).not.toHaveBeenCalled();
  });

  it("shows Clerk's message and re-enables the buttons when the hand-off fails", async () => {
    authenticateWithRedirectMock.mockRejectedValue({
      errors: [{ message: "Google sign-in is not enabled" }],
    });
    const user = userEvent.setup();
    render(<SignInForm />);

    await user.click(screen.getByRole("button", { name: "Continue with Google" }));

    expect(await screen.findByText("Google sign-in is not enabled")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue with Google" })).toBeEnabled();
    expect(pushMock).not.toHaveBeenCalled();
  });
});
