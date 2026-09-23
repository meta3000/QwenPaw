import { describe, it, expect } from "vitest";
import { renderHook } from "@testing-library/react";
import { useIsMobile } from "./useIsMobile";

describe("useIsMobile", () => {
  it("falls back to viewport width", () => {
    // jsdom default innerWidth is 1024 (> 768).
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });
});
