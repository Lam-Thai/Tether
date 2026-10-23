import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const callbackProps = vi.fn();

vi.mock("@clerk/nextjs", () => ({
  AuthenticateWithRedirectCallback: (props: Record<string, unknown>) => {
    callbackProps(props);
    return null;
  },
}));

import SsoCallbackPage from "./page";

describe("SsoCallbackPage", () => {
  it("lets Clerk finish the Google/GitHub hand-off back to this app's own pages", () => {
    render(<SsoCallbackPage />);

    expect(callbackProps).toHaveBeenCalledWith(
      expect.objectContaining({
        signInUrl: "/sign-in",
        signUpUrl: "/sign-up",
        signInFallbackRedirectUrl: "/dashboard",
        signUpFallbackRedirectUrl: "/onboarding",
      }),
    );
    expect(screen.getByRole("status")).toHaveTextContent(/finishing sign-in/i);
  });
});
