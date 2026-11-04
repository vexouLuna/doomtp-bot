import { afterEach, expect, it, vi } from "vitest";

afterEach(() => vi.unstubAllGlobals());

it("retries a failed fetch instead of caching the failure", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce({ ok: false, status: 503 })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ commands: [] }) });
  vi.stubGlobal("fetch", fetchMock);
  const { commands } = await import("../src/api.js");
  await expect(commands()).rejects.toThrow("503");
  await expect(commands()).resolves.toEqual({ commands: [] });
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
