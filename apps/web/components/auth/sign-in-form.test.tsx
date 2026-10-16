import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const pushMock = vi.fn();
const signInCreateMock = vi.fn();
const setActiveMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("@clerk/nextjs/legacy", () => ({
  useSignIn: () => ({
    isLoaded: true,
    signIn: { create: signInCreateMock },
    setActive: setActiveMock,
  }),
}));

vi.mock("@clerk/nextjs/errors", () => ({
  isClerkAPIResponseError: (error: unknown) =>
    typeof error === "object" && error !== null && "errors" in error,
}));

import { SignInForm } from "./sign-in-form";

describe("SignInForm", () => {
  beforeEach(() => {
    pushMock.mockReset();
    signInCreateMock.mockReset();
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
});
