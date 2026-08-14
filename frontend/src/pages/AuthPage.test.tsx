import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  it("does not let form submission bypass live-mode selection", async () => {
    vi.mocked(api).mockResolvedValue({});
    render(<AuthPage mode="setup" onAuthenticated={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Begin setup" }));
    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "admin" },
    });
    const password = screen.getByLabelText(/^Password/);
    fireEvent.change(password, {
      target: { value: "a-secure-password" },
    });
    fireEvent.submit(password.closest("form")!);

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
});
