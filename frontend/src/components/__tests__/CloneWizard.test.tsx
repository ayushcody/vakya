import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CloneWizard } from "@/components/voices/CloneWizard";
import { api, ApiError } from "@/lib/api";

jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api");
  return { ...actual, api: { get: jest.fn(), upload: jest.fn(), post: jest.fn(), del: jest.fn() } };
});
jest.mock("@/components/voices/VoiceThumb", () => ({ VoiceThumb: () => null })); // canvas + ResizeObserver
jest.mock("@/components/audio/WaveformPlayer", () => ({ __esModule: true, default: () => null }));
jest.mock("@/components/voices/audioUtils", () => ({
  readAudioInfo: jest.fn(async () => ({ duration: 12, peaks: [] })),
  toWav: jest.fn(),
}));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

const get = api.get as jest.Mock;
const upload = api.upload as jest.Mock;
const TICK_LABEL = "I own this voice or have the speaker's permission to clone it for Vakya";

function mockServer(mode: "tick" | "strict" | "missing") {
  get.mockImplementation(async (url: string) => {
    if (url === "/api/consent/mode") {
      if (mode === "missing") throw new ApiError("Not found", 404, "NOT_FOUND");
      return { consent_mode: mode, statement: TICK_LABEL };
    }
    if (url.startsWith("/api/consent/challenge")) {
      return {
        challenge_id: "ch1",
        text: "I, Asha, consent to creating an AI version of my voice with Vakya on 3 November 2026. My code is mango 42.",
        expires_at: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
        retention_days: 365,
      };
    }
    throw new Error(`unexpected GET ${url}`);
  });
  upload.mockImplementation(async (url: string) => {
    if (url === "/api/transcribe") return { text: "hello there", language: "en" };
    return { id: "v1", engine: "timbre_clone", consent: { verified: true, score: null, heard: null } };
  });
}

async function openWithSample(onCreated = jest.fn()) {
  render(<CloneWizard open onClose={() => undefined} onCreated={onCreated} />);
  const input = await screen.findByLabelText("Choose audio file");
  const file = new File(["RIFFxxxxWAVE"], "asha_voice.wav", { type: "audio/wav" });
  fireEvent.change(input, { target: { files: [file] } });
  await screen.findByText("asha_voice.wav");
  return onCreated;
}

const continueBtn = () => screen.getByRole("button", { name: /Continue/ });

beforeEach(() => {
  get.mockReset();
  upload.mockReset();
});

describe("clone wizard in tick mode", () => {
  it("has two steps: the sample, then one checkbox and a Create button", async () => {
    mockServer("tick");
    const onCreated = await openWithSample();
    const steps = screen.getByRole("list", { name: "Progress" });
    expect(steps.querySelectorAll("li")).toHaveLength(2);
    expect(steps).toHaveTextContent("Sample");
    expect(steps).toHaveTextContent("Create");
    expect(steps).not.toHaveTextContent("Transcript");
    await waitFor(() => expect(continueBtn()).toBeEnabled());
    fireEvent.click(continueBtn());

    // no sentence, no code, no read-back, no recording of consent
    expect(screen.queryByRole("button", { name: /Record consent/ })).toBeNull();
    expect(screen.queryByText(/My code is/)).toBeNull();
    expect(screen.queryByText(/Speaker consent/)).toBeNull();
    expect(screen.queryByText(/Get a different sentence/)).toBeNull();
    expect(screen.queryByTestId("transcript-explainer")).toBeNull();
    expect(screen.getByTestId("consent-notice")).toHaveTextContent(/record that the consent box was ticked/);

    const box = screen.getByRole("checkbox", { name: TICK_LABEL });
    const create = screen.getByRole("button", { name: "Create voice" });
    expect(box).not.toBeChecked();
    expect(create).toBeDisabled();
    fireEvent.click(box);
    expect(create).toBeEnabled();
    fireEvent.click(create);

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ id: "v1", engine: "timbre_clone", name: "asha voice", language: "en" }));
    const cloneCalls = upload.mock.calls.filter(([url]) => url === "/api/voices/clone");
    expect(cloneCalls).toHaveLength(1);
    const fd = cloneCalls[0][1] as FormData;
    expect(fd.get("consent_given")).toBe("true");
    expect(fd.get("display_name")).toBe("asha voice");
    expect(fd.get("reference_audio")).toBeInstanceOf(File);
    for (const k of ["consent_audio", "consent_text", "consent_confirmed", "challenge_id", "transcript"]) {
      expect(fd.has(k)).toBe(false);
    }
    // the transcript step and the consent sentence are never asked for
    expect(upload.mock.calls.some(([url]) => url === "/api/transcribe")).toBe(false);
    expect(get.mock.calls.some(([url]) => String(url).startsWith("/api/consent/challenge"))).toBe(false);
  });

  it("does not create the voice until the box is ticked, and the name is required", async () => {
    mockServer("tick");
    await openWithSample();
    await waitFor(() => expect(continueBtn()).toBeEnabled());
    fireEvent.click(continueBtn());
    fireEvent.click(screen.getByRole("checkbox", { name: TICK_LABEL }));
    const create = screen.getByRole("button", { name: "Create voice" });
    expect(create).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Voice name"), { target: { value: "  " } });
    expect(create).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Voice name"), { target: { value: "Asha" } });
    expect(create).toBeEnabled();
    expect(upload.mock.calls.some(([url]) => url === "/api/voices/clone")).toBe(false);
  });

  it("shows the server's error when the clone fails and lets the person try again", async () => {
    mockServer("tick");
    upload.mockImplementation(async (url: string) => {
      if (url === "/api/voices/clone") throw new ApiError("Tick the box.", 400, "CONSENT_REQUIRED");
      return {};
    });
    await openWithSample();
    await waitFor(() => expect(continueBtn()).toBeEnabled());
    fireEvent.click(continueBtn());
    fireEvent.click(screen.getByRole("checkbox", { name: TICK_LABEL }));
    fireEvent.click(screen.getByRole("button", { name: "Create voice" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Tick the box.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });
});

describe("clone wizard in strict mode", () => {
  it.each(["strict", "missing"] as const)("keeps the three steps and the spoken consent (%s)", async (mode) => {
    mockServer(mode);
    await openWithSample();
    const steps = screen.getByRole("list", { name: "Progress" });
    expect(steps.querySelectorAll("li")).toHaveLength(3);
    expect(screen.getByTestId("consent-upfront")).toHaveTextContent(/reads one short sentence aloud/);
    await waitFor(() => expect(continueBtn()).toBeEnabled());
    fireEvent.click(continueBtn());
    expect(await screen.findByTestId("transcript-explainer")).toBeInTheDocument();
    await waitFor(() => expect(upload).toHaveBeenCalledWith("/api/transcribe", expect.anything(), expect.anything()));
    await waitFor(() => expect(continueBtn()).toBeEnabled());
    fireEvent.click(continueBtn());

    expect(screen.queryByRole("checkbox", { name: TICK_LABEL })).toBeNull();
    expect(screen.getByText("Speaker consent")).toBeInTheDocument();
    expect(await screen.findByText(/My code is mango 42/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Record consent/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clone voice" })).toBeDisabled();
  });
});
