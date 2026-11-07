import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { CREATE_TIMEOUT_MS, TemplateGallery, personaFor, type TemplateInfo } from "@/components/agents/TemplateGallery";
import { api } from "@/lib/api";
import { useAgentTemplates, useAgents } from "@/hooks/useApi";
import type { Agent } from "@/types/api";

jest.mock("sonner", () => ({ toast: { error: jest.fn(), success: jest.fn() } }));
jest.mock("@/lib/api", () => ({ api: { post: jest.fn(), upload: jest.fn() }, ApiError: class extends Error {} }));
jest.mock("@/hooks/useApi", () => ({ useAgentTemplates: jest.fn(), useAgents: jest.fn() }));

const post = api.post as jest.Mock;
const upload = api.upload as jest.Mock;

const CLINIC: TemplateInfo = {
  id: "clinic",
  name: "{org} front desk",
  role: "Front desk for a clinic: timings, doctors, fees and appointments",
  type_label: "Clinic",
  tagline: "Timings, doctors and appointments.",
  illustration: "clinic",
  accent: "blue",
  persona: {
    tone: "Warm and calm",
    traits: ["Patient", "Clear", "Reassuring"],
    sample_line: { en: "Thank you for calling {org}. How can I help?", hi: "{org} में कॉल करने के लिए धन्यवाद। मैं कैसे मदद करूँ?" },
  },
  schedules: [{ name: "Doctor consultations", kind: "slots", slot_minutes: 15 }],
  languages: ["en", "hi"],
  greeting: "Hello, thank you for calling {org} in {city}. How can I help you today?",
  document: "clinic.md",
  suggested_questions: ["What are the timings?"],
};
const HOTEL: TemplateInfo = {
  ...CLINIC,
  id: "hotel",
  type_label: "Hotel",
  tagline: "Rooms, check-in and enquiries.",
  illustration: "hotel",
  accent: "amber",
  persona: { tone: "Courteous", traits: ["Polished", "Quick", "Helpful"], sample_line: { en: "Welcome to {org}.", hi: "{org} में आपका स्वागत है।" } },
};

const agent = (id: string, name: string) => ({ id, name, document_count: 0 }) as unknown as Agent;
const mutate = jest.fn(async () => undefined);

function setup(existing: Agent[] = [], list: TemplateInfo[] = [CLINIC, HOTEL], onScratch?: () => void) {
  (useAgentTemplates as jest.Mock).mockReturnValue({ data: list, isLoading: false, error: undefined });
  (useAgents as jest.Mock).mockReturnValue({ data: existing, mutate });
  const onCreated = jest.fn();
  render(<TemplateGallery onCreated={onCreated} onStartFromScratch={onScratch} />);
  return onCreated;
}

const open = (label = "Clinic") => fireEvent.click(screen.getByRole("button", { name: `Use this front desk: ${label}` }));
const type = (label: RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

beforeEach(() => {
  jest.clearAllMocks();
  window.localStorage.clear();
  post.mockImplementation(async (_p: string, body: { org_name: string }) => agent("new1", `${body.org_name} front desk`));
});

describe("front-desk cards", () => {
  it("show an illustration, the type label, tagline, tone and three traits for each type", () => {
    setup();
    const card = document.querySelector('[data-desk="clinic"]') as HTMLElement;
    expect(within(card).getByRole("heading", { level: 3, name: "Clinic" })).toBeTruthy();
    expect(within(card).getByText("Timings, doctors and appointments.")).toBeTruthy();
    const chips = within(within(card).getByRole("list", { name: "How it sounds" })).getAllByRole("listitem");
    expect(chips.map((c) => c.textContent)).toEqual(["Warm and calm", "Patient", "Clear", "Reassuring"]);
    expect(card.querySelector("img")?.getAttribute("src")).toBe("/illustrations/clinic-icon.svg");
    expect(document.querySelector('[data-desk="hotel"] img')?.getAttribute("src")).toBe("/illustrations/hotel-icon.svg");
    expect(within(card).getByText(/English, Hindi/)).toBeTruthy();
    expect(screen.queryByText("en")).toBeNull(); // no raw codes
  });

  it("show the sample line as a quote with a per-type stand-in for the business, and switch to Hindi", () => {
    setup();
    const card = document.querySelector('[data-desk="clinic"]') as HTMLElement;
    const quote = () => card.querySelector("blockquote") as HTMLElement;
    expect(quote().textContent).toBe("“Thank you for calling [your clinic]. How can I help?”");
    expect(quote().getAttribute("lang")).toBe("en");
    fireEvent.click(within(card).getByRole("button", { name: "Hindi" }));
    expect(quote().textContent).toContain("[आपका क्लिनिक] में कॉल करने के लिए धन्यवाद");
    expect(quote().getAttribute("lang")).toBe("hi");
    expect(within(card).getByRole("button", { name: "Hindi" }).getAttribute("aria-pressed")).toBe("true");
    // the hotel card says "your hotel", in both languages, and never the generic "your name"
    const hotel = document.querySelector('[data-desk="hotel"]') as HTMLElement;
    expect(hotel.querySelector("blockquote")?.textContent).toBe("“Welcome to [your hotel].”");
    fireEvent.click(within(hotel).getByRole("button", { name: "Hindi" }));
    expect(hotel.querySelector("blockquote")?.textContent).toBe("“[आपका होटल] में आपका स्वागत है।”");
    expect(document.body.textContent).not.toContain("[your name]");
    fireEvent.click(within(hotel).getByRole("button", { name: "English" }));
    // the other card keeps its own language
    expect((document.querySelector('[data-desk="hotel"] blockquote') as HTMLElement).getAttribute("lang")).toBe("en");
  });

  it("invent no business names", () => {
    setup();
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/Sunrise|Lotus|Joshi|Bhosale/);
  });

  it("do not create anything on click, they open the form", () => {
    setup();
    open();
    expect(screen.getByRole("dialog", { name: "Name your organisation" })).toBeTruthy();
    expect(post).not.toHaveBeenCalled();
  });

  it("add a Start from scratch card only when the page wants it", () => {
    setup();
    expect(screen.queryByText("Start from scratch")).toBeNull();
    document.body.innerHTML = "";
    const onScratch = jest.fn();
    setup([], [CLINIC], onScratch);
    fireEvent.click(screen.getByRole("button", { name: /Start from scratch/ }));
    expect(onScratch).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[data-desk="custom"] img')?.getAttribute("src")).toBe("/illustrations/custom-icon.svg");
  });

  it("still list a template from an older server (no persona) with the old id drawn as custom", () => {
    const old: TemplateInfo = { ...CLINIC, id: "clinic_receptionist", type_label: undefined, tagline: undefined, illustration: undefined, persona: undefined, audience: "For a clinic." };
    setup([], [old]);
    const card = document.querySelector('[data-desk="clinic_receptionist"]') as HTMLElement;
    expect(within(card).getByRole("heading", { level: 3 }).textContent).toBe("Custom front desk");
    expect(within(card).getByText("For a clinic.")).toBeTruthy();
    expect(card.querySelector("img")?.getAttribute("src")).toBe("/illustrations/custom-icon.svg");
    expect(within(card).queryByRole("list", { name: "How it sounds" })).toBeNull();
  });
});

describe("the form", () => {
  it("asks for the organisation with a pointer to the owner's own name, never an invented one", () => {
    setup();
    open();
    const input = screen.getByLabelText(/Clinic name/) as HTMLInputElement;
    expect(input.placeholder).toBe("e.g. your clinic's name");
  });

  it("pre-fills languages by name and previews the greeting with the typed name and city", () => {
    setup();
    open();
    expect((screen.getByRole("checkbox", { name: "English" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "Hindi" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "Tamil" }) as HTMLInputElement).checked).toBe(false);
    const line = () => screen.getByText(/Your agent will greet callers as:/).parentElement?.textContent ?? "";
    expect(line()).toContain("[Clinic name]");
    type(/Clinic name/, "Lotus Care");
    expect(line()).toContain("thank you for calling Lotus Care. How can I help you today?");
    type(/City or area/, "Nashik");
    expect(line()).toContain("calling Lotus Care in Nashik.");
  });

  it("needs an organisation name and does not call the API without one", () => {
    setup();
    open();
    fireEvent.click(screen.getByRole("button", { name: "Create front desk" }));
    expect(screen.getByText("Type the name callers will hear.")).toBeTruthy();
    expect(post).not.toHaveBeenCalled();
  });

  it("sends the overrides, remembers the type for the agent, then calls onCreated with the agent and template", async () => {
    const onCreated = setup();
    open();
    type(/Clinic name/, "  Lotus Care ");
    type(/City or area/, "Nashik");
    type(/Opening hours/, "9 to 6");
    fireEvent.click(screen.getByRole("checkbox", { name: "Tamil" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Hindi" }));
    fireEvent.click(screen.getByRole("button", { name: "Create front desk" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledWith("/api/agents/from-template", {
      template_id: "clinic",
      org_name: "Lotus Care",
      city: "Nashik",
      details: "9 to 6",
      languages: ["en", "ta"],
      include_sample: true,
    }, { signal: expect.any(AbortSignal) });
    expect(mutate).toHaveBeenCalled();
    expect(onCreated.mock.calls[0][0].id).toBe("new1");
    expect(onCreated.mock.calls[0][1]).toBe(CLINIC);
    expect(window.localStorage.getItem("vakya.deskType.new1")).toBe("clinic");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("uploads the user's document after creating and then leaves the sample out", async () => {
    upload.mockResolvedValue({});
    const onCreated = setup();
    open();
    type(/Clinic name/, "Lotus Care");
    const file = new File(["hours"], "fees.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Choose your own document"), { target: { files: [file] } });
    expect(screen.getByText("fees.pdf")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Create front desk" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(post.mock.calls[0][1].include_sample).toBe(false);
    const fd = upload.mock.calls[0][1] as FormData;
    expect(upload.mock.calls[0][0]).toBe("/api/knowledge/upload");
    expect(fd.get("agent_id")).toBe("new1");
    expect((fd.get("file") as File).name).toBe("fees.pdf");
  });

  it("still lands on the agent if the upload fails, and says so", async () => {
    upload.mockRejectedValue(new Error("too big"));
    const onCreated = setup();
    open();
    type(/Clinic name/, "Lotus Care");
    fireEvent.change(screen.getByLabelText("Choose your own document"), {
      target: { files: [new File(["x"], "a.txt", { type: "text/plain" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create front desk" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(toast.error).toHaveBeenCalled();
  });

  it("can leave the sample document out", async () => {
    const onCreated = setup();
    open();
    type(/Clinic name/, "Lotus Care");
    fireEvent.click(screen.getByRole("checkbox", { name: /include a sample document/i }));
    fireEvent.click(screen.getByRole("button", { name: "Create front desk" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(post.mock.calls[0][1].include_sample).toBe(false);
  });

  it("stays open and reports a failure", async () => {
    post.mockRejectedValue(new Error("boom"));
    const onCreated = setup();
    open();
    type(/Clinic name/, "Lotus Care");
    fireEvent.click(screen.getByRole("button", { name: "Create front desk" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(onCreated).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});

describe("while the front desk is being made (the first one loads the language model)", () => {
  afterEach(() => jest.useRealTimers());

  it("says what is happening, counts the seconds and never looks frozen", async () => {
    jest.useFakeTimers();
    let finish!: (v: unknown) => void;
    post.mockImplementation(() => new Promise((r) => (finish = r)));
    setup();
    open();
    type(/Clinic name/, "Test Clinic");
    fireEvent.click(screen.getByRole("button", { name: "Create front desk" }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Setting up your front desk");
    expect(status).toHaveTextContent("The first one takes up to a minute while the language model loads.");
    expect(status).toHaveTextContent("0 s");
    act(() => {
      jest.advanceTimersByTime(25_000);
    });
    expect(status).toHaveTextContent("25 s");
    expect(status).toHaveTextContent(/language model/);
    expect(screen.queryByRole("button", { name: "Create front desk" })).toBeNull();
    await act(async () => finish(agent("n1", "Test Clinic front desk")));
  });

  it("gives up after three minutes, keeps what was typed, and offers to try again", async () => {
    jest.useFakeTimers();
    let signal: AbortSignal | undefined;
    post.mockImplementation((_p: string, _b: unknown, opts?: { signal?: AbortSignal }) => {
      signal = opts?.signal;
      return new Promise((_r, rej) => opts?.signal?.addEventListener("abort", () => rej(new Error("aborted"))));
    });
    const refresh = jest.fn(async () => undefined);
    (useAgents as jest.Mock).mockReturnValue({ data: [], mutate: refresh });
    (useAgentTemplates as jest.Mock).mockReturnValue({ data: [CLINIC, HOTEL], isLoading: false, error: undefined });
    render(<TemplateGallery onCreated={jest.fn()} />);
    open();
    type(/Clinic name/, "Test Clinic");
    fireEvent.click(screen.getByRole("button", { name: "Create front desk" }));
    await screen.findByRole("status");
    await act(async () => {
      jest.advanceTimersByTime(CREATE_TIMEOUT_MS - 1000);
    });
    expect(signal?.aborted).toBe(false);
    expect(screen.getByRole("status")).toBeInTheDocument();
    await act(async () => {
      jest.advanceTimersByTime(1500);
    });
    expect(signal?.aborted).toBe(true);
    const alert = await screen.findByTestId("desk-timeout");
    expect(alert).toHaveTextContent("taking longer than expected");
    expect((screen.getByLabelText(/Clinic name/) as HTMLInputElement).value).toBe("Test Clinic");
    expect(refresh).toHaveBeenCalled(); // so a retry finds a front desk the server finished meanwhile
    expect(toast.error).not.toHaveBeenCalled();
    // retry
    post.mockImplementation(async (_p: string, body: { org_name: string }) => agent("n2", `${body.org_name} front desk`));
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  });
});

describe("after creating: the schedules that came with it", () => {
  const withSchedules = () =>
    post.mockImplementation(async (_p: string, body: { org_name: string }) => ({
      ...agent("h1", `${body.org_name} front desk`),
      schedules: [
        { id: "S1", name: "Rooms", kind: "nights", capacity: 10, summary: { hours_text: "Check-in 12:00, check-out 11:00" } },
        { id: "S2", name: "Doctor consultations", kind: "slots", slot_minutes: 15, summary: { hours_text: "Mon to Sat, 10:00 to 13:00" } },
      ],
    }));

  it("lists them with links to Bookings before leaving, and continues on the button", async () => {
    withSchedules();
    const onCreated = setup([], [HOTEL, CLINIC]);
    open("Hotel");
    type(/Hotel name/, "Lotus Stay");
    fireEvent.click(screen.getByRole("button", { name: "Create front desk" }));
    const done = await screen.findByTestId("desk-created");
    expect(done.textContent).toContain("Lotus Stay front desk is ready");
    expect(screen.getByRole("dialog", { name: "Your front desk is ready" })).toBeInTheDocument();
    const list = within(done).getByRole("list", { name: "Schedules created" });
    const rows = within(list).getAllByRole("link");
    expect(rows.map((r) => r.textContent)).toEqual([
      "RoomsNightly, 10 rooms. Check-in 12:00, check-out 11:00",
      "Doctor consultationsEvery 15 min. Mon to Sat, 10:00 to 13:00",
    ]);
    expect(rows[0].getAttribute("href")).toBe("/desk/bookings/S1");
    expect(within(done).getByRole("link", { name: "Open Bookings" }).getAttribute("href")).toBe("/desk/bookings");
    expect(onCreated).not.toHaveBeenCalled();
    fireEvent.click(within(done).getByRole("button", { name: "Go to my front desk" }));
    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(onCreated.mock.calls[0][2]).toHaveLength(2);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("still opens the front desk if the dialog is closed instead", async () => {
    withSchedules();
    const onCreated = setup([], [HOTEL]);
    open("Hotel");
    type(/Hotel name/, "Lotus Stay");
    fireEvent.click(screen.getByRole("button", { name: "Create front desk" }));
    await screen.findByTestId("desk-created");
    fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("duplicates", () => {
  it("warns before creating a second agent with the same name, and can open the existing one", async () => {
    const existing = agent("old1", "Lotus Care front desk");
    const onCreated = setup([existing]);
    open();
    type(/Clinic name/, "lotus care");
    fireEvent.click(screen.getByRole("button", { name: "Create front desk" }));
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe("You already have an agent called Lotus Care front desk. Open it instead?");
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: /^Open/ }));
    expect(onCreated).toHaveBeenCalledWith(existing, CLINIC, undefined);
    expect(post).not.toHaveBeenCalled();
  });

  it("creates anyway when asked to", async () => {
    const onCreated = setup([agent("old1", "Lotus Care front desk")]);
    open();
    type(/Clinic name/, "Lotus Care");
    fireEvent.click(screen.getByRole("button", { name: "Create front desk" }));
    fireEvent.click(screen.getByRole("button", { name: "Create another one" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(post).toHaveBeenCalledTimes(1);
  });
});

describe("template order", () => {
  it("puts the clinic first and keeps the server's order for the rest", () => {
    const salon: TemplateInfo = { ...CLINIC, id: "salon", type_label: "Salon and spa" };
    const fitness: TemplateInfo = { ...CLINIC, id: "fitness", type_label: "Gym and studio" };
    (useAgentTemplates as jest.Mock).mockReturnValue({ data: [salon, fitness, CLINIC], isLoading: false, error: undefined });
    (useAgents as jest.Mock).mockReturnValue({ data: [], mutate });
    render(<TemplateGallery onCreated={jest.fn()} />);
    const titles = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(titles).toEqual(["Clinic", "Salon and spa", "Gym and studio"]);
  });
});

describe("personaFor", () => {
  it("swaps sample names in one pass and applies the name pattern", () => {
    const hod: TemplateInfo = {
      ...CLINIC,
      name: "HOD Office Assistant: Prof. Jayashri Prasad",
      greeting: "this is the office of Professor Jayashri Prasad, Head",
      personalise: {
        name_pattern: "HOD Office Assistant: {org}",
        sample_orgs: ["Professor Jayashri Prasad", "Prof. Jayashri Prasad", "Jayashri Prasad"],
        strip: [],
      },
    };
    expect(personaFor(hod, "Prof. Jayashri Rao")).toEqual({
      name: "HOD Office Assistant: Prof. Jayashri Rao",
      greeting: "this is the office of Prof. Jayashri Rao, Head",
    });
  });
  it("fills the {org} and {city} placeholders of a generic template, and drops the city when there is none", () => {
    expect(personaFor(CLINIC, "Lotus Care", "Nashik")).toEqual({
      name: "Lotus Care front desk",
      greeting: "Hello, thank you for calling Lotus Care in Nashik. How can I help you today?",
    });
    expect(personaFor(CLINIC, "Lotus Care").greeting).toBe("Hello, thank you for calling Lotus Care. How can I help you today?");
  });
});
