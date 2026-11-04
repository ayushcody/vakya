import Link from "next/link";
import { REPO_PUBLIC } from "./repo";
import { CHECKS, COMMANDS, HARDWARE, KEYS, PORTS } from "./rigCheckData";
import { Reveal } from "./motion";

/**
 * /run-it: the real setup, in the order a person does it. A server component with no state: five commands, the ports,
 * the keys and what happens without each, the checks that prove it works, and the one machine it has been tried on.
 */

const code = "overflow-x-auto rounded-lg bg-[#111] px-4 py-3 font-mono text-[13px] leading-relaxed text-white";

export function RigCheck() {
  return (
    <>
      <section id="run-it" aria-labelledby="commands-title" className="s-container mt-12 md:mt-16">
        <h2 id="commands-title" className="h2 max-w-[18ch]">
          Five commands.
        </h2>
        <ol className="mt-8 divide-y divide-[var(--s-line)] border-y border-[var(--s-line)]">
          {COMMANDS.map((c, i) => (
            <li key={c.cmd} className="grid gap-x-4 gap-y-2 py-5 md:grid-cols-[2rem_minmax(0,1.1fr)_minmax(0,1fr)]">
              <span className="s-item tabular-nums text-[var(--violet-700)]">{i + 1}</span>
              <pre className={code}>
                <code>{c.cmd}</code>
              </pre>
              <p className="text-sm leading-relaxed text-[var(--s-ink-2)] md:pt-2">{c.note}</p>
            </li>
          ))}
        </ol>
        <p className="mt-4 text-sm leading-relaxed text-[var(--s-mute)]">
          {REPO_PUBLIC
            ? "Then open http://localhost:3010/desk/try and make a call from the browser."
            : "The repository is private until the challenge closes, so the clone step needs access: ask the team. Then open http://localhost:3010/desk/try and make a call from the browser."}
        </p>
      </section>

      <section id="ports" aria-labelledby="ports-title" className="s-container mt-20 md:mt-28">
        <h2 id="ports-title" className="h2 max-w-[18ch]">
          Three ports.
        </h2>
        <dl className="mt-8 grid gap-4 md:grid-cols-3">
          {PORTS.map((p) => (
            <Reveal key={p.port} className="rounded-2xl border border-[var(--s-line)] bg-white p-6">
              <dt className="s-stat tabular-nums">{p.port}</dt>
              <dd className="mt-3 text-sm leading-relaxed text-[var(--s-ink-2)]">{p.what}</dd>
            </Reveal>
          ))}
        </dl>
      </section>

      <section id="keys" aria-labelledby="keys-title" className="s-container mt-20 md:mt-28">
        <h2 id="keys-title" className="h2 max-w-[18ch]">
          Keys, and what you lose without them.
        </h2>
        <div className="mt-8 overflow-x-auto rounded-2xl border border-[var(--s-line)] bg-white">
          <table className="vk-table min-w-[44rem] text-sm">
            <caption className="sr-only">Environment settings in backend/.env, what each gives and what happens without it</caption>
            <thead>
              <tr>
                <th scope="col">Setting</th>
                <th scope="col">Needed</th>
                <th scope="col">It gives you</th>
                <th scope="col">Without it</th>
              </tr>
            </thead>
            <tbody>
              {KEYS.map((k) => (
                <tr key={k.name}>
                  <th scope="row" className="break-words font-mono !text-xs">
                    {k.name}
                  </th>
                  <td className="whitespace-nowrap text-[var(--s-ink-2)]">{k.needed}</td>
                  <td className="text-[var(--s-ink-2)]">{k.gives}</td>
                  <td className="text-[var(--s-mute)]">{k.without}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 max-w-[64ch] text-sm leading-relaxed text-[var(--s-mute)]">
          Keys live only in backend/.env on your computer. The Gnani paths are integrated but have not run against the live API yet; the first thing to do with a key is the smoke
          test below.
        </p>
      </section>

      <section id="checks" aria-labelledby="checks-title" className="s-container mt-20 md:mt-28">
        <h2 id="checks-title" className="h2 max-w-[18ch]">
          Check that it works.
        </h2>
        <ul className="mt-8 space-y-6">
          {CHECKS.map((c) => (
            <li key={c.cmd} className="grid gap-x-6 gap-y-2 md:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
              <pre className={code}>
                <code>{c.cmd}</code>
              </pre>
              <p className="text-sm leading-relaxed text-[var(--s-ink-2)] md:pt-2">{c.what}</p>
            </li>
          ))}
        </ul>
      </section>

      <section id="hardware" aria-labelledby="hardware-title" className="s-container mt-20 md:mt-28">
        <h2 id="hardware-title" className="h2 max-w-[18ch]">
          Which machine.
        </h2>
        <div className="mt-8 grid gap-x-12 gap-y-4 md:grid-cols-2">
          <p className="text-base leading-relaxed text-[var(--s-ink-2)]">
            <span className="font-semibold text-black">Tried on:</span> {HARDWARE.tested} {HARDWARE.notTested}
          </p>
          <p className="text-base leading-relaxed text-[var(--s-ink-2)]">
            {HARDWARE.withKeys} {HARDWARE.without}
          </p>
        </div>
        <p className="mt-6 text-sm text-[var(--s-mute)]">
          Stuck? <Link href="/faq" className="underline underline-offset-4">The FAQ</Link> lists what is not ready yet.
        </p>
      </section>
    </>
  );
}
