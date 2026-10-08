import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api";
import CodexModelPicker from "./CodexModelPicker";

vi.mock("../api", () => ({ api: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("loads the catalog before settings are saved and allows selecting a model", async () => {
  vi.mocked(api).mockResolvedValue({ models: [{ id: "gpt-codex-one" }, { id: "gpt-codex-two" }] });
  const change = vi.fn();
  render(<CodexModelPicker name="model" value="gpt-codex-one" onChange={change} />);
  expect(await screen.findByRole("option", { name: "gpt-codex-two" })).toBeVisible();
  expect(api).toHaveBeenCalledWith("/api/settings/ai/codex/models", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  fireEvent.change(screen.getByRole("combobox", { name: "Model" }), { target: { value: "gpt-codex-two" } });
  expect(change).toHaveBeenCalledWith("gpt-codex-two");
});

it("keeps a saved fallback selection when discovery fails and provides a retry", async () => {
  vi.mocked(api).mockRejectedValueOnce(new Error("Catalog unavailable"));
  render(<CodexModelPicker name="fallback_model" value="saved-codex" onChange={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Catalog unavailable");
  expect(screen.getByRole("combobox", { name: "Fallback model" })).toHaveValue("saved-codex");
  vi.mocked(api).mockResolvedValueOnce({ models: [{ id: "saved-codex" }, { id: "new-codex" }] });
  fireEvent.click(screen.getByRole("button", { name: "Refresh fallback Codex models" }));
  expect(await screen.findByRole("option", { name: "new-codex" })).toBeVisible();
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  expect(screen.getByRole("combobox")).toHaveValue("saved-codex");
});
