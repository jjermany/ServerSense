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
import SecuritySettings from "./SecuritySettings";

vi.mock("../api", () => ({ api: vi.fn() }));

describe("optional MFA settings", () => {
  beforeEach(() => vi.mocked(api).mockReset());
  afterEach(cleanup);

  it("keeps MFA off until QR setup is confirmed, then shows recovery codes once", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({ enabled: false, recovery_codes_remaining: 0 })
      .mockResolvedValueOnce({
        secret: "EXAMPLEKEY",
        qr_code: "data:image/png;base64,example",
        expires_in_seconds: 600,
      })
      .mockResolvedValueOnce({
        enabled: true,
        recovery_codes: ["ABCD-EF01-2345-6789-ABCD"],
      });
    const { container } = render(<SecuritySettings />);
    await screen.findByText("Off");
    expect(api).toHaveBeenCalledTimes(1);
    expect((await axe(container)).violations).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Current password"), {
      target: { value: "current-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Set up MFA" }));
    expect(await screen.findByAltText(/Scan this QR code/)).toHaveAttribute(
      "src",
      "data:image/png;base64,example",
    );
    expect(screen.getByLabelText("Manual setup key")).toHaveValue("EXAMPLEKEY");
    expect(screen.getByText("Off")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Authenticator code"), {
      target: { value: "123456" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Verify and enable MFA" }),
    );
    expect(
      await screen.findByText("ABCD-EF01-2345-6789-ABCD"),
    ).toBeInTheDocument();
    expect(screen.queryByAltText(/Scan this QR code/)).not.toBeInTheDocument();
    expect(screen.getByText("Enabled")).toBeInTheDocument();
    expect(api).toHaveBeenLastCalledWith("/api/auth/mfa/confirm", {
      method: "POST",
      body: JSON.stringify({ password: "current-password", code: "123456" }),
    });
    fireEvent.click(
      screen.getByRole("button", { name: "I saved my recovery codes" }),
    );
    expect(
      screen.queryByText("ABCD-EF01-2345-6789-ABCD"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Disable MFA" }),
    ).toBeInTheDocument();
  });

  it("requires an explicit password and factor confirmation to disable MFA", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({ enabled: true, recovery_codes_remaining: 5 })
      .mockRejectedValueOnce(new Error("Invalid or already used code"))
      .mockResolvedValueOnce({ enabled: false });
    render(<SecuritySettings />);
    fireEvent.click(await screen.findByRole("button", { name: "Disable MFA" }));
    expect(api).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByLabelText("Current password"), {
      target: { value: "current-password" },
    });
    fireEvent.change(screen.getByLabelText("Authenticator or recovery code"), {
      target: { value: "bad-code" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm disable MFA" }),
    );
    await screen.findByRole("alert");
    expect(screen.getByText("Enabled")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Authenticator or recovery code"), {
      target: { value: "654321" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm disable MFA" }),
    );
    await waitFor(() => expect(screen.getByText("Off")).toBeInTheDocument());
    expect(screen.getByLabelText("Current password")).toHaveValue("");
  });

  it("cancels enrollment without enabling MFA", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({ enabled: false, recovery_codes_remaining: 0 })
      .mockResolvedValueOnce({
        secret: "EXAMPLEKEY",
        qr_code: "data:image/png;base64,example",
        expires_in_seconds: 600,
      });
    render(<SecuritySettings />);
    await screen.findByText("Off");
    fireEvent.change(screen.getByLabelText("Current password"), {
      target: { value: "current-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Set up MFA" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(screen.getByText("Off")).toBeInTheDocument();
    expect(screen.queryByLabelText("Manual setup key")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Current password")).toHaveValue("");
    expect(api).toHaveBeenCalledTimes(2);
  });
});
