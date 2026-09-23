import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../i18n", () => ({
  default: { t: (key: string) => key },
}));

import { ChunkErrorBoundary } from "./ChunkErrorBoundary";

function BrokenPage(): ReactElement {
  throw new Error("render failed");
}

describe("ChunkErrorBoundary", () => {
  it("renders a generic error fallback when a page fails", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);

    render(
      <ChunkErrorBoundary>
        <BrokenPage />
      </ChunkErrorBoundary>,
    );

    expect(screen.getByText("chunkError.genericTitle")).toBeInTheDocument();
    expect(screen.getByText("chunkError.reload")).toBeInTheDocument();
  });
});
