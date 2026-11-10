"use client";

import Image from "next/image";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ArrowLeft, ArrowRight, Info, Maximize, Pause, Play, RotateCcw, RotateCw, Volume2, VolumeX } from "lucide-react";
import { FILM, MORE_FILMS, clock, type Film, type MoreFilm } from "./films";

/**
 * The demo, presented like a title page: a silent teaser fills the top of the screen behind the film's name, Play opens the
 * full 3-minute film with sound, and More films opens the animated real case and the short film in the same player.
 * Vakya's own look throughout
 * (Gambarino titles, Inter text, black and off-white, the violet accent, 8 px buttons, #e6e6e6 lines); nothing from any
 * streaming service's brand. Where a visitor stopped is kept in this browser only, so Play can offer to resume.
 */

const RESUME_KEY = "vakya-demo-position";

function readResume(): number {
  try {
    const v = Number(localStorage.getItem(RESUME_KEY));
    return Number.isFinite(v) && v > 5 && v < FILM.seconds - 8 ? v : 0;
  } catch {
    return 0;
  }
}

const resumeListeners = new Set<() => void>();
function subscribeResume(cb: () => void) {
  resumeListeners.add(cb);
  return () => {
    resumeListeners.delete(cb);
  };
}

function writeResume(seconds: number) {
  try {
    if (seconds > 5 && seconds < FILM.seconds - 8) localStorage.setItem(RESUME_KEY, String(Math.floor(seconds)));
    else localStorage.removeItem(RESUME_KEY);
  } catch {
    /* private mode: nothing to remember */
  }
  resumeListeners.forEach((l) => l());
}

const subscribeMotion = (cb: () => void) => {
  const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
  mq.addEventListener("change", cb);
  return () => mq.removeEventListener("change", cb);
};
const useReducedMotion = () =>
  useSyncExternalStore(subscribeMotion, () => window.matchMedia("(prefers-reduced-motion: reduce)").matches, () => false);

/* ------------------------------------------------------------------------------------------------ the player */

/** The film player: Vakya's own controls, so the progress line is the violet accent (browsers do not let their built-in
 *  video controls be recoloured). Space or K plays and pauses, the arrows skip 10 s, M mutes, F goes full screen, Esc
 *  closes. The controls fade while the film plays and come back on any movement, tap or key. */
function Theatre({ film = FILM, start, onClose }: { film?: Film; start: number; onClose: (at: number) => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const shell = useRef<HTMLDivElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  const hideTimer = useRef<number | undefined>(undefined);
  const [paused, setPaused] = useState(false);
  const [muted, setMuted] = useState(false);
  const [time, setTime] = useState(start);
  const [buffered, setBuffered] = useState(0);
  const [duration, setDuration] = useState<number>(film.seconds);
  const [shown, setShown] = useState(true);
  const close = useCallback(() => onClose(video.current?.currentTime ?? start), [onClose, start]);

  const wake = useCallback(() => {
    setShown(true);
    window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => {
      if (video.current && !video.current.paused) setShown(false);
    }, 2600);
  }, []);

  const toggle = useCallback(() => {
    const v = video.current;
    if (!v) return;
    if (v.paused) void v.play().catch(() => {});
    else v.pause();
    wake();
  }, [wake]);
  const seek = useCallback((to: number) => {
    const v = video.current;
    if (!v) return;
    v.currentTime = Math.max(0, Math.min(to, (v.duration || film.seconds) - 0.1));
    setTime(v.currentTime);
    wake();
  }, [wake, film.seconds]);
  const toggleMute = useCallback(() => {
    const v = video.current;
    if (!v) return;
    v.muted = !v.muted;
    setMuted(v.muted);
    wake();
  }, [wake]);
  const fullscreen = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
    else void shell.current?.requestFullscreen?.().catch(() => {});
    wake();
  }, [wake]);

  useEffect(() => {
    const v = video.current;
    if (v) {
      v.currentTime = start;
      void v.play().catch(() => setPaused(true));
    }
    back.current?.focus();
    hideTimer.current = window.setTimeout(() => {
      if (video.current && !video.current.paused) setShown(false);
    }, 2600);
    const key = (e: KeyboardEvent) => {
      const target = e.target instanceof HTMLElement ? e.target : null;
      const onSlider = target?.getAttribute("type") === "range";
      if (e.key === "Escape") {
        if (!document.fullscreenElement) close();
      } else if ((e.key === " " && target?.tagName !== "BUTTON") || e.key === "k" || e.key === "K") {
        e.preventDefault();
        toggle();
      } else if (!onSlider && e.key === "ArrowRight") {
        seek((video.current?.currentTime ?? 0) + 10);
      } else if (!onSlider && e.key === "ArrowLeft") {
        seek((video.current?.currentTime ?? 0) - 10);
      } else if (e.key === "m" || e.key === "M") {
        toggleMute();
      } else if (e.key === "f" || e.key === "F") {
        fullscreen();
      } else {
        wake();
      }
    };
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("keydown", key);
      window.clearTimeout(hideTimer.current);
      document.body.style.overflow = overflow;
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
    };
  }, [start, close, toggle, seek, toggleMute, fullscreen, wake]);

  const total = duration || film.seconds;
  const isDemo = film.id === FILM.id;
  const played = Math.min(100, (time / total) * 100);
  const loaded = Math.min(100, (buffered / total) * 100);

  return (
    <div
      ref={shell}
      role="dialog"
      aria-modal="true"
      aria-label={film.label}
      className={`watch-theatre fixed inset-0 z-[80] bg-black ${shown ? "" : "is-idle"}`}
      onMouseMove={wake}
      onTouchStart={wake}
    >
      <video
        ref={video}
        className="absolute inset-0 h-full w-full object-contain"
        src={film.src}
        poster={film.poster}
        playsInline
        preload="metadata"   // the film loads when someone plays it, not when the page opens (mobile data)
        onClick={toggle}
        onPlay={() => { setPaused(false); wake(); }}
        onPause={(e) => { setPaused(true); setShown(true); if (isDemo) writeResume(e.currentTarget.currentTime); }}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        onProgress={(e) => {
          const b = e.currentTarget.buffered;
          if (b.length) setBuffered(b.end(b.length - 1));
        }}
        onEnded={() => onClose(0)}
        aria-label={`${film.label}, ${clock(film.seconds)}`}
      />

      <div className="watch-chrome watch-top absolute inset-x-0 top-0 flex items-center gap-3 px-4 pb-10 pt-4 sm:px-6">
        <button ref={back} type="button" onClick={close} className="watch-ghost inline-flex h-10 items-center gap-2 rounded-lg px-3 text-sm font-medium text-white">
          <ArrowLeft className="size-4" aria-hidden /> Back
        </button>
        <p className="min-w-0 truncate font-[family-name:var(--s-font-display)] text-lg text-white">{film.title}</p>
      </div>

      <div className="watch-chrome watch-bottom absolute inset-x-0 bottom-0 px-4 pb-4 pt-14 sm:px-6 sm:pb-5">
        <div className="watch-seek relative flex h-5 items-center">
          <div aria-hidden className="watch-track absolute inset-x-0 h-1 overflow-hidden rounded-full bg-white/20">
            <div className="absolute inset-y-0 left-0 bg-white/30" style={{ width: `${loaded}%` }} />
            <div className="absolute inset-y-0 left-0 bg-[var(--s-bronze)]" style={{ width: `${played}%` }} />
          </div>
          <div aria-hidden className="watch-thumb absolute size-3.5 -translate-x-1/2 rounded-full bg-[var(--s-bronze)]" style={{ left: `${played}%` }} />
          <input
            type="range"
            min={0}
            max={total}
            step={1}
            value={Math.floor(time)}
            onChange={(e) => seek(Number(e.target.value))}
            aria-label="Seek"
            aria-valuetext={`${clock(time)} of ${clock(total)}`}
            className="watch-range absolute inset-0 h-full w-full cursor-pointer opacity-0"
          />
        </div>
        <div className="mt-2 flex items-center gap-1 text-white sm:gap-2">
          <button type="button" onClick={toggle} aria-label={paused ? "Play" : "Pause"} className="watch-icon grid size-10 place-items-center rounded-lg">
            {paused ? <Play className="size-5 fill-current" aria-hidden /> : <Pause className="size-5 fill-current" aria-hidden />}
          </button>
          <button type="button" onClick={() => seek(time - 10)} aria-label="Back 10 seconds" className="watch-icon grid size-10 place-items-center rounded-lg">
            <RotateCcw className="size-5" aria-hidden />
          </button>
          <button type="button" onClick={() => seek(time + 10)} aria-label="Forward 10 seconds" className="watch-icon grid size-10 place-items-center rounded-lg">
            <RotateCw className="size-5" aria-hidden />
          </button>
          <button type="button" onClick={toggleMute} aria-label={muted ? "Unmute" : "Mute"} className="watch-icon grid size-10 place-items-center rounded-lg">
            {muted ? <VolumeX className="size-5" aria-hidden /> : <Volume2 className="size-5" aria-hidden />}
          </button>
          <p className="ml-1 text-sm tabular-nums text-white/85">
            {clock(time)} <span className="text-white/50">/ {clock(total)}</span>
          </p>
          <button type="button" onClick={fullscreen} aria-label="Full screen" className="watch-icon ml-auto grid size-10 place-items-center rounded-lg">
            <Maximize className="size-5" aria-hidden />
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------ more films */

function FilmCard({ f, onPlay }: { f: MoreFilm; onPlay: (f: MoreFilm) => void }) {
  return (
    <li className="watch-card w-[15.5rem] shrink-0 snap-start sm:w-[18rem]">
      <button type="button" onClick={() => onPlay(f)} className="group block w-full text-left" aria-label={`Play ${f.title}, ${clock(f.seconds)}`}>
        <span className="relative block aspect-video overflow-hidden rounded-lg border border-[var(--s-night-line)] bg-black">
          <Image src={f.poster} alt="" fill sizes="(min-width: 640px) 18rem, 15.5rem" className="watch-still object-cover" />
          <span aria-hidden className="watch-play absolute inset-0 grid place-items-center">
            <span className="grid size-11 place-items-center rounded-full bg-white text-black">
              <Play className="size-4 translate-x-px fill-current" />
            </span>
          </span>
          <span className="absolute bottom-2 left-2 rounded-md bg-black/75 px-1.5 py-0.5 text-xs font-medium tabular-nums text-white">
            {clock(f.seconds)}
          </span>
        </span>
        <span className="mt-3 block text-[15px] font-semibold leading-snug text-white">{f.title}</span>
        <span className="mt-1 block text-sm leading-snug text-[var(--s-night-mute)]">{f.line}</span>
      </button>
    </li>
  );
}

function MoreFilms({ onPlay }: { onPlay: (f: MoreFilm) => void }) {
  return (
    <section id="more-films" aria-labelledby="more-films-title" className="relative z-10 pb-16 md:-mt-36">
      <div className="watch-gutter">
        <h2 id="more-films-title" className="font-[family-name:var(--s-font-display)] text-[1.75rem] leading-tight text-white sm:text-[2rem]">
          More films
        </h2>
        <p className="mt-1 text-sm text-[var(--s-night-mute)]">The same real call, told two more ways.</p>
      </div>
      <ul className="watch-row watch-gutter mt-5 flex snap-x snap-mandatory gap-4 overflow-x-auto pb-2">
        {MORE_FILMS.map((f) => (
          <FilmCard key={f.id} f={f} onPlay={onPlay} />
        ))}
      </ul>
    </section>
  );
}

/* ------------------------------------------------------------------------------------------------ the page */

export function WatchPreview() {
  const reduce = useReducedMotion();
  const [playing, setPlaying] = useState<{ film: Film; at: number } | null>(null);
  const resume = useSyncExternalStore(subscribeResume, readResume, () => 0);
  const playButton = useRef<HTMLButtonElement>(null);

  const open = (at: number) => setPlaying({ film: FILM, at });
  const openFilm = (film: Film) => setPlaying({ film, at: 0 });
  const close = useCallback(
    (at: number) => {
      if (playing?.film.id === FILM.id) writeResume(at);
      setPlaying(null);
      requestAnimationFrame(() => playButton.current?.focus());
    },
    [playing],
  );

  return (
    <div className="watch min-h-screen bg-[var(--s-night)] text-white">
      <style>{WATCH_CSS}</style>

      <header className="watch-gutter absolute inset-x-0 top-0 z-20 flex h-16 items-center justify-between sm:h-20">
        <Link href="/home" aria-label="Vakya home" className="flex items-center">
          <Image src="/brand/vakya-lockup-dark.svg" alt="Vakya" width={112} height={32} priority className="h-7 w-auto sm:h-8" />
        </Link>
        <Link href="/home" className="watch-ghost inline-flex h-10 items-center gap-1.5 rounded-lg px-3.5 text-sm font-medium text-white">
          Visit the site <ArrowRight className="size-4" aria-hidden />
        </Link>
      </header>

      <section aria-labelledby="film-title" className="relative w-full overflow-hidden md:h-[92svh] md:min-h-[38rem]">
        {/* Phones: the teaser on top, the title under it on night. Wider screens: the teaser to the right, behind a night
            fade, so the title reads on the film's light screens. */}
        <div aria-hidden className="watch-media relative aspect-video w-full md:absolute md:inset-y-0 md:right-0 md:aspect-auto md:h-full md:w-[70%]">
          {reduce ? (
            <Image src={FILM.teaserPoster} alt="" fill priority sizes="(min-width: 768px) 70vw, 100vw" className="object-cover object-[60%_30%]" />
          ) : (
            <video
              className="absolute inset-0 h-full w-full object-cover object-[60%_30%]"
              src={FILM.teaser}
              poster={FILM.teaserPoster}
              autoPlay
              muted
              loop
              playsInline
              preload="auto"
              tabIndex={-1}
            />
          )}
        </div>
        <div aria-hidden className="watch-shade pointer-events-none absolute inset-x-0 top-0 aspect-video md:inset-0 md:aspect-auto" />

        <div className="watch-gutter relative z-10 -mt-6 pb-12 md:absolute md:inset-x-0 md:bottom-[12.5rem] md:mt-0 md:pb-0">
          <div className="max-w-[38rem]">
            <p className="inline-flex items-center gap-2 rounded-md bg-white/10 px-2.5 py-1 text-xs font-medium uppercase tracking-[0.12em] text-white backdrop-blur-sm">
              <span aria-hidden className="size-1.5 rounded-full bg-[var(--s-bronze)]" />
              Demo film
            </p>
            <h1 id="film-title" className="mt-4 font-[family-name:var(--s-font-display)] text-[clamp(2.75rem,7vw,5.5rem)] leading-[0.98] tracking-[-0.01em]">
              Call once,
              <br />
              it&rsquo;s <em className="not-italic text-[var(--s-marigold)]">booked.</em>
            </h1>
            <p className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-[var(--s-night-mute)]">
              <span className="tabular-nums text-white">{clock(FILM.seconds)}</span>
              <span aria-hidden>·</span>
              <span>A real phone call</span>
              <span aria-hidden>·</span>
              <span>Hindi, with English subtitles</span>
            </p>
            <p className="mt-3 max-w-[34rem] text-[15px] leading-relaxed text-white/85 sm:text-base">
              A caller rings a clinic. Vakya checks the schedule, reads every detail back, books on a spoken yes and sends the
              confirmation on WhatsApp. Then the booking is moved, and cancelled, the same way.
            </p>
            <div className="mt-6 flex flex-wrap items-center gap-3">
              <button
                ref={playButton}
                type="button"
                onClick={() => open(resume)}
                className="watch-primary inline-flex h-12 items-center gap-2.5 rounded-lg bg-white px-6 text-[15px] font-semibold text-black"
              >
                <Play className="size-5 fill-current" aria-hidden />
                {resume ? `Resume from ${clock(resume)}` : "Play"}
              </button>
              {resume > 0 && (
                <button type="button" onClick={() => open(0)} className="watch-ghost inline-flex h-12 items-center gap-2 rounded-lg px-5 text-[15px] font-medium text-white">
                  <RotateCcw className="size-4" aria-hidden /> From the start
                </button>
              )}
              <a href="#about" className="watch-ghost inline-flex h-12 items-center gap-2 rounded-lg px-5 text-[15px] font-medium text-white">
                <Info className="size-4" aria-hidden /> About the film
              </a>
            </div>
            {resume > 0 && (
              <div className="mt-4 h-1 w-full max-w-[18rem] overflow-hidden rounded-full bg-white/20" aria-hidden>
                <div className="h-full rounded-full bg-[var(--s-bronze)]" style={{ width: `${(resume / FILM.seconds) * 100}%` }} />
              </div>
            )}
          </div>
        </div>
      </section>

      <MoreFilms onPlay={openFilm} />

      <section id="about" aria-labelledby="about-title" className="bg-[var(--s-offwhite)] text-black">
        <div className="watch-gutter mx-auto grid max-w-[72rem] gap-10 py-16 sm:py-20 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
          <div>
            <h2 id="about-title" className="font-[family-name:var(--s-font-display)] text-[clamp(2rem,4vw,3rem)] leading-tight">
              A voice front desk for India.
            </h2>
            <p className="mt-4 max-w-[36rem] text-base leading-relaxed text-[var(--s-mute)]">
              Vakya answers a business&rsquo;s phone, checks its real schedule for a free time, reads every detail back and
              books only after the caller says yes. The film follows one clinic through a booking, a change and a
              cancellation, each confirmed on WhatsApp, and ends on the proof kept after the call.
            </p>
            <div className="mt-7 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => open(0)}
                className="inline-flex h-11 items-center gap-2 rounded-lg bg-black px-5 text-sm font-semibold text-white transition-opacity hover:opacity-85"
              >
                <Play className="size-4 fill-current" aria-hidden /> Watch the film
              </button>
              <Link href="/home" className="inline-flex h-11 items-center gap-1.5 rounded-lg border border-[var(--s-line)] bg-white px-5 text-sm font-semibold text-black hover:border-black/40">
                Visit the site <ArrowRight className="size-4" aria-hidden />
              </Link>
            </div>
          </div>
          <dl className="grid content-start gap-0 divide-y divide-[var(--s-line)] rounded-2xl border border-[var(--s-line)] bg-white px-5">
            {[
              ["Runtime", `${clock(FILM.seconds)} minutes`],
              ["In the film", "Hindi, with English subtitles"],
              ["Hearing", "Gnani Prisma"],
              ["Voice", "Gnani Timbre"],
              ["Shown", "Booking, changing and cancelling, with WhatsApp confirmations"],
            ].map(([k, v]) => (
              <div key={k} className="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-3 py-3.5 text-sm">
                <dt className="text-[var(--s-mute)]">{k}</dt>
                <dd className="font-medium text-black">{v}</dd>
              </div>
            ))}
          </dl>
        </div>
        <p className="watch-gutter border-t border-[var(--s-line)] py-6 text-center text-xs text-[var(--s-mute)]">
          Vakya · built on Gnani Prisma and Timbre
        </p>
      </section>

      {playing !== null && <Theatre key={playing.film.id} film={playing.film} start={playing.at} onClose={close} />}
    </div>
  );
}

const WATCH_CSS = `
.watch-gutter { padding-left: clamp(1rem, 4vw, 3.5rem); padding-right: clamp(1rem, 4vw, 3.5rem); }
html:root:has(.watch), html:root:has(.watch) body { background-color: #111111; color-scheme: dark; }
.watch-media { filter: none; }
.watch-shade {
  background:
    linear-gradient(0deg, #111111 0%, rgba(17,17,17,0.35) 30%, rgba(17,17,17,0) 55%, rgba(17,17,17,0.75) 100%);
}
@media (min-width: 768px) {
  .watch-shade {
    background:
      linear-gradient(90deg, #111111 0%, #111111 30%, rgba(17,17,17,0.55) 48%, rgba(17,17,17,0.1) 70%, rgba(17,17,17,0) 100%),
      linear-gradient(0deg, #111111 0%, #111111 14%, rgba(17,17,17,0.7) 30%, rgba(17,17,17,0) 52%),
      linear-gradient(180deg, #111111 0%, rgba(17,17,17,0.85) 9%, rgba(17,17,17,0) 24%);
  }
}
.watch-ghost { background: rgba(255,255,255,0.12); transition: background-color .18s ease; }
.watch-ghost:hover { background: rgba(255,255,255,0.22); }
.watch-primary { transition: opacity .18s ease; }
.watch-primary:hover { opacity: .88; }
.watch :focus-visible { outline: 2px solid var(--s-bronze); outline-offset: 3px; }
.watch-row { scrollbar-width: none; scroll-padding-left: clamp(1rem, 4vw, 3.5rem); }
.watch-row::-webkit-scrollbar { display: none; }
.watch-still { transition: transform .45s cubic-bezier(.2,.7,.2,1); }
.watch-play { opacity: 0; background: rgba(17,17,17,0.35); transition: opacity .2s ease; }
.watch-card button:hover .watch-still, .watch-card button:focus-visible .watch-still { transform: scale(1.04); }
.watch-card button:hover .watch-play, .watch-card button:focus-visible .watch-play { opacity: 1; }
.watch-top { background: linear-gradient(180deg, rgba(0,0,0,0.7), rgba(0,0,0,0)); }
.watch-bottom { background: linear-gradient(0deg, rgba(0,0,0,0.8), rgba(0,0,0,0)); }
.watch-chrome { transition: opacity .3s ease; }
.watch-theatre.is-idle { cursor: none; }
.watch-theatre.is-idle .watch-chrome { opacity: 0; pointer-events: none; }
.watch-track { transition: height .15s ease; }
.watch-seek:hover .watch-track, .watch-seek:focus-within .watch-track { height: 6px; }
.watch-thumb { transform: translateX(-50%) scale(0); transition: transform .15s ease; box-shadow: 0 0 0 4px rgba(108,98,237,0.25); }
.watch-seek:hover .watch-thumb, .watch-seek:focus-within .watch-thumb { transform: translateX(-50%) scale(1); }
.watch-seek:focus-within { outline: 2px solid var(--s-bronze); outline-offset: 4px; border-radius: 4px; }
.watch-icon { transition: background-color .15s ease; }
.watch-icon:hover { background: rgba(255,255,255,0.14); }
@media (prefers-reduced-motion: reduce) {
  .watch-still, .watch-play, .watch-ghost, .watch-primary, .watch-chrome, .watch-track, .watch-thumb, .watch-icon { transition: none; }
  .watch-card button:hover .watch-still { transform: none; }
}
`;
