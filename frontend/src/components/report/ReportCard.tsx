import { CONDITIONS, STRESS_RUN, findRow, type EntityKind, type StressRun } from "./stressRun";

/**
 * The acoustic-stress report card: how well the receptionist's ear holds up when the line is bad, scored on what a booking
 * needs (name, day, time) and not only on word errors. A server component (no state), shared by the public /report-card page
 * and the desk's Report card page.
 *
 * No recogniser has been measured yet, so with no run (reportCardResults.json holds `{ "run": null }`) the page says
 * "Gnani Prisma run pending", explains the method and shows the empty table. The numbers are only ever read from the
 * results file; nothing here is typed by hand.
 */

const KINDS: { id: EntityKind; label: string }[] = [
  { id: "name", label: "Name" },
  { id: "date", label: "Date" },
  { id: "time", label: "Time" },
  { id: "amount", label: "Amount" },
  { id: "phone", label: "Phone number" },
];

const METHOD: { title: string; body: string }[] = [
  {
    title: "The calls",
    body: "24 invented booking calls for a clinic front desk: 8 in Hindi, 8 in Marathi and 8 in Hinglish. Each has a person's name, a day or date and a time; some also have an amount or a ten-digit phone number spoken digit by digit. No real person or number.",
  },
  {
    title: "The line",
    body: "Each call is played clean, then over an 8 kHz phone band (300 to 3400 Hz), through street noise at 10, 5 and 0 dB, through the G.711 phone codec, and through the phone band with noise together. The degradations are applied by software and the seed is fixed, so the run can be repeated.",
  },
  {
    title: "The score",
    body: "A booking counts as completed only when the name, the day and the time were all found, in any accepted spelling in either script. That is stricter than word error, which is printed beside it, and it is what a booking needs.",
  },
  {
    title: "With and without keyterms",
    body: "Every condition is run twice: once plain, and once with the names, weekdays and services the business's own documents contain sent to Prisma as keyterms. The gap between the two columns is what the documents buy.",
  },
];

/** Violet at an opacity that follows the value, so a heat cell is never the only carrier of the number (the number is printed). */
const heat = (v: number) => ({ background: `color-mix(in srgb, var(--color-accent-primary) ${Math.round(8 + v * 0.45)}%, transparent)` });

const pct = (v: number | undefined) => (v == null ? null : `${Math.round(v)}%`);

const codeClass = "rounded bg-bg-elevated px-1.5 py-0.5 font-mono text-xs";

export function ReportCard({ run = STRESS_RUN }: { run?: StressRun | null }) {
  const pending = !run;
  const empty = <span className="text-text-secondary">pending</span>;

  return (
    <div className="space-y-10 text-text-primary">
      <aside role="note" className="rounded-2xl border border-border bg-bg-surface p-5 text-sm leading-relaxed text-text-secondary">
        {pending ? (
          <>
            <p className="font-semibold text-text-primary">Gnani Prisma run pending</p>
            <p className="mt-1.5">
              No recogniser has been measured yet, so this page publishes no number. The table below is empty on purpose: it fills in from the run&apos;s result file
              the day Gnani Prisma has heard the task set, and until then nothing here is an estimate or a stand-in.
            </p>
            <p className="mt-2">
              When it fills, read it with two cautions: the audio is <strong className="text-text-primary">synthetic speech</strong>, not real callers, and the degradations are simulated,
              not recorded. Each condition is 24 clips, so one clip is about 4 points: read the shape (where the cliff is), not single cells.
            </p>
          </>
        ) : (
          <>
            <p className="font-semibold text-text-primary">Read this first</p>
            <p className="mt-1.5">
              The audio is <strong className="text-text-primary">synthetic speech</strong>, not real callers, and the degradations are simulated, not recorded. Each condition is {run.tasks} clips,
              so one clip is about 4 points: read the shape (where the cliff is), not single cells. Repeat on real recordings before quoting a number.
            </p>
            <p className="mt-2">
              Recogniser measured: <strong className="text-text-primary">Gnani Prisma</strong> ({run.model}), run on {run.date}.
            </p>
          </>
        )}
      </aside>

      <section aria-labelledby="rc-method" className="space-y-3">
        <h2 id="rc-method" className="font-display text-2xl text-text-primary">How it is measured</h2>
        <dl className="grid gap-4 sm:grid-cols-2">
          {METHOD.map((m) => (
            <div key={m.title} className="rounded-2xl border border-border bg-bg-surface p-5">
              <dt className="text-xs font-semibold uppercase tracking-[1px] text-text-secondary">{m.title}</dt>
              <dd className="mt-2 text-[15px] leading-relaxed text-text-primary">{m.body}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section aria-labelledby="rc-table" className="space-y-3">
        <div>
          <h2 id="rc-table" className="font-display text-2xl text-text-primary">Booking completed, by condition</h2>
          <p className="mt-1 text-sm text-text-secondary">
            Share of clips where the name, the day and the time were all found. {pending ? "Every cell reads pending until the Prisma run exists." : ""}
          </p>
        </div>
        <div className="overflow-x-auto rounded-2xl border border-border bg-bg-surface">
          <table className="w-full min-w-[20rem] border-collapse text-sm sm:min-w-[34rem]">
            <caption className="sr-only">Task completion by condition, in percent of clips, without and with keyterms</caption>
            <thead>
              <tr className="border-b border-border text-left text-xs font-semibold uppercase tracking-[1px] text-text-secondary">
                <th scope="col" className="px-4 py-3">Condition</th>
                <th scope="col" className="px-3 py-3 text-right">No keyterms</th>
                <th scope="col" className="px-3 py-3 text-right">With keyterms</th>
                <th scope="col" className="hidden px-3 py-3 text-right sm:table-cell">Word error</th>
                <th scope="col" className="hidden px-4 py-3 text-right sm:table-cell">Median time</th>
              </tr>
            </thead>
            <tbody>
              {CONDITIONS.map((c) => {
                const plain = findRow(run, c.id, false);
                const keyed = findRow(run, c.id, true);
                return (
                  <tr key={c.id} className="border-b border-border last:border-b-0">
                    <th scope="row" className="px-4 py-3 text-left font-medium">
                      {c.name}
                      <span className="block text-xs font-normal text-text-secondary">{c.what}</span>
                    </th>
                    <td className="px-3 py-3 text-right font-semibold tabular-nums" style={plain ? heat(plain.taskCompletion) : undefined}>
                      {plain ? pct(plain.taskCompletion) : empty}
                    </td>
                    <td className="px-3 py-3 text-right font-semibold tabular-nums" style={keyed ? heat(keyed.taskCompletion) : undefined}>
                      {keyed ? pct(keyed.taskCompletion) : empty}
                    </td>
                    <td className="hidden px-3 py-3 text-right tabular-nums text-text-secondary sm:table-cell">{plain ? plain.wer.toFixed(2) : empty}</td>
                    <td className="hidden px-4 py-3 text-right tabular-nums text-text-secondary sm:table-cell">{plain ? `${plain.latencyS.toFixed(2)} s` : empty}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="rc-slots" className="space-y-3">
        <div>
          <h2 id="rc-slots" className="font-display text-2xl text-text-primary">Which part of the booking was lost</h2>
          <p className="mt-1 text-sm text-text-secondary">
            Share of each field found, all languages together, without keyterms. Amounts and phone numbers only count the clips that have one.
          </p>
        </div>
        <div className="overflow-x-auto rounded-2xl border border-border bg-bg-surface">
          <table className="w-full min-w-[30rem] border-collapse text-sm">
            <caption className="sr-only">Share of each field found, by condition, in percent</caption>
            <thead>
              <tr className="border-b border-border text-left text-xs font-semibold uppercase tracking-[1px] text-text-secondary">
                <th scope="col" className="px-4 py-3">Condition</th>
                {KINDS.map((k) => (
                  <th key={k.id} scope="col" className="px-3 py-3 text-right">{k.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {CONDITIONS.map((c) => {
                const plain = findRow(run, c.id, false);
                return (
                  <tr key={c.id} className="border-b border-border last:border-b-0">
                    <th scope="row" className="px-4 py-3 text-left font-medium">{c.name}</th>
                    {KINDS.map((k) => {
                      const v = plain?.hitByKind[k.id];
                      return (
                        <td key={k.id} className="px-3 py-3 text-right tabular-nums" style={v == null ? undefined : heat(v)}>
                          {v == null ? (plain ? "n/a" : empty) : `${Math.round(v)}%`}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="rc-says" className="space-y-3">
        <h2 id="rc-says" className="font-display text-2xl text-text-primary">What to look for</h2>
        <ul className="list-disc space-y-2 pl-5 text-[15px] leading-relaxed text-text-primary">
          <li>
            <strong>Where the cliff is.</strong> Whether the phone band and the codec cost little, and how far noise pulls a booking down.
          </li>
          <li>
            <strong>Which field goes first.</strong> Dates, names, times and ten-digit phone numbers fail differently. A phone number heard whole is the case the read-back exists for: the front desk
            reads every number back and commits only on a spoken yes, whatever this table says.
          </li>
          <li>
            <strong>What keyterms buy.</strong> The business&apos;s own names sent as keyterms against the plain run.
          </li>
        </ul>
        <p className="text-sm text-text-secondary">
          Method, the task set and the result file format are in <code className={codeClass}>docs/STRESS_REPORT.md</code>. Run it with{" "}
          <code className={codeClass}>./vakya stress --conditions all --provider gnani --keyterms</code> once a Gnani key is set.
        </p>
      </section>
    </div>
  );
}
