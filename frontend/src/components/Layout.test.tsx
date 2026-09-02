import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import Layout from "./Layout";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../App", () => ({ Brand: () => <span>ServerSense</span> }));

const renderLayout = () =>
  render(
    <MemoryRouter>
      <Routes>
        <Route element={<Layout onLogout={() => undefined} />}>
          <Route index element={<div>Overview</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

describe("Layout monitoring status", () => {
  afterEach(cleanup);

  it("reports an active monitoring connection after renewing the viewer lease", async () => {
    vi.mocked(api).mockResolvedValue(undefined);
    renderLayout();
    expect(await screen.findByText("Monitoring active")).toBeVisible();
  });

  it("reports an interrupted connection when the viewer lease cannot be renewed", async () => {
    vi.mocked(api).mockRejectedValue(new Error("offline"));
    renderLayout();
    expect(await screen.findByText("Connection interrupted")).toBeVisible();
  });
});
