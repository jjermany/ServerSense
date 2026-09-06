import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { axe } from "jest-axe";
import { api } from "../api";
import AuthPage from "./AuthPage";

vi.mock("../api", () => ({ api: vi.fn() }));

describe("first-run wizard", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
  });
  afterEach(cleanup);

  it("guides the user from welcome to administrator creation", () => {
    render(<AuthPage mode="setup" onAuthenticated={vi.fn()} />);
    expect(screen.getByText("Welcome to ServerSense")).toBeInTheDocument();
    expect(screen.getByText("SENSE is optional")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Begin setup" }));
    expect(screen.getByText("Create administrator")).toBeInTheDocument();
    expect(screen.getByLabelText("Server name")).toHaveValue("Tower");
    expect(screen.getByLabelText("Username")).toBeRequired();
  });

  it("does not let Continue submit setup before live-mode selection", async () => {
    vi.mocked(api).mockResolvedValue({});
    render(<AuthPage mode="setup" onAuthenticated={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Begin setup" }));
    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "admin" },
    });
    fireEvent.change(screen.getByLabelText(/^Password/), {
      target: { value: "a-secure-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(screen.getByText("Choose monitoring mode")).toBeInTheDocument();
    expect(screen.getByLabelText(/Start with demo data/)).not.toBeChecked();
    expect(api).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Finish setup" }));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith("/api/auth/setup", {
        method: "POST",
        body: JSON.stringify({
          server_name: "Tower",
          username: "admin",
          password: "a-secure-password",
          demo_mode: false,
        }),
      }),
    );
  });

  it("has no detectable first-run accessibility violations", async () => {
    const { container } = render(
      <AuthPage mode="setup" onAuthenticated={vi.fn()} />,
    );
    const results = await axe(container);
    expect(results.violations).toHaveLength(0);
  });

  it("waits for MFA verification before authenticating and allows a retry", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({ mfa_required: true })
      .mockRejectedValueOnce(new Error("Invalid or already used code"))
      .mockResolvedValueOnce({ id: 1, username: "Admin" });
    const authenticated = vi.fn();
    render(<AuthPage mode="login" onAuthenticated={authenticated} />);
    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "ADMIN" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "test-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("heading", { name: "Verify your sign-in" });
    expect(authenticated).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Authenticator or recovery code/), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Verify and sign in" }));
    await screen.findByRole("alert");
    expect(authenticated).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/Authenticator or recovery code/)).toHaveValue(
      "",
    );
    fireEvent.change(screen.getByLabelText(/Authenticator or recovery code/), {
      target: { value: "ABCD-EF01-2345-6789-ABCD" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Verify and sign in" }));
    await waitFor(() => expect(authenticated).toHaveBeenCalledOnce());
    expect(api).toHaveBeenLastCalledWith("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: "ADMIN",
        password: "test-password",
        code: "ABCD-EF01-2345-6789-ABCD",
      }),
    });
  });

  it("lets users abandon the MFA step and clear credentials", async () => {
    vi.mocked(api).mockResolvedValue({ mfa_required: true });
    render(<AuthPage mode="login" onAuthenticated={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "admin" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "test-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Back to sign in" }),
    );
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.getByLabelText("Username")).toHaveValue("");
  });
});
