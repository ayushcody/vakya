import { initialLive, reduceLive, type LiveEvent } from "@/lib/liveCall";
import { median, seconds } from "@/components/live/ResponseTime";

const latency = (seq: number, ms: number): LiveEvent =>
  ({ seq, at: "", agent_id: "a1", conversation_id: "c1", turn_id: null, type: "latency", response_ms: ms } as unknown as LiveEvent);

test("each phone turn's response time is kept in order for Watch it think", () => {
  const s = reduceLive(initialLive(), [latency(1, 1100), latency(2, 950)]);
  expect(s.responses).toEqual([1100, 950]);
  expect(reduceLive(s, [latency(2, 950)]).responses).toEqual([1100, 950]);   // an event seen twice counts once
});

test("seconds and median read the way a caller feels them", () => {
  expect(seconds(949)).toBe("0.9 s");
  expect(seconds(1450)).toBe("1.4 s");
  expect(median([1240, 880, 930])).toBe(930);
  expect(median([800, 1000])).toBe(900);
  expect(median([])).toBeNull();
});
