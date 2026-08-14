import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Metric, Status } from "./UI";

describe("shared monitoring UI", () => {
  it("renders measured metric context", () => {
    render(<Metric label="Free storage" value="4.07 TB" detail="94.3% array used" />);
    expect(screen.getByText("4.07 TB")).toBeInTheDocument();
    expect(screen.getByText("94.3% array used")).toBeInTheDocument();
  });

  it("maps health states to accessible visible labels", () => {
    const { rerender } = render(<Status value="healthy" />);
    expect(screen.getByText("healthy")).toHaveClass("good");
    rerender(<Status value="warning" />);
    expect(screen.getByText("warning")).toHaveClass("warn");
  });
});

