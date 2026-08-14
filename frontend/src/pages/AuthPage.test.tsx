import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { axe } from "jest-axe";
import AuthPage from "./AuthPage";

describe("first-run wizard", () => {
  it("guides the user from welcome to administrator creation", () => {
    render(<AuthPage mode="setup" onAuthenticated={vi.fn()} />);
    expect(screen.getByText("Welcome to ServerSense")).toBeInTheDocument();
    expect(screen.getByText("SENSE is optional")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Begin setup" }));
    expect(screen.getByText("Create administrator")).toBeInTheDocument();
    expect(screen.getByLabelText("Server name")).toHaveValue("Tower");
    expect(screen.getByLabelText("Username")).toBeRequired();
  });

  it("has no detectable first-run accessibility violations", async () => {
    const { container } = render(
      <AuthPage mode="setup" onAuthenticated={vi.fn()} />,
    );
    const results = await axe(container);
    expect(results.violations).toHaveLength(0);
  });
});
