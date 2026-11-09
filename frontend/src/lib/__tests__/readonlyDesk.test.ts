import { snapshotKey, readonlyResponse, READONLY_MESSAGE } from "@/lib/readonlyDesk";

test("the snapshot key keeps only the query parameters that change the answer", () => {
  expect(snapshotKey("/api/calls?range=7d&limit=200&hide_empty=true")).toBe("/api/calls");
  expect(snapshotKey("/api/schedules?agent_id=a2")).toBe("/api/schedules?agent_id=a2");
  expect(snapshotKey("/api/calls?limit=1&agent_id=a1&days=365")).toBe("/api/calls?agent_id=a1");
});

test("anything that would change something is refused in the read-only desk", async () => {
  await expect(readonlyResponse("POST", "/api/phone/call")).rejects.toThrow(READONLY_MESSAGE);
  await expect(readonlyResponse("DELETE", "/api/bookings/A1")).rejects.toThrow(READONLY_MESSAGE);
});
