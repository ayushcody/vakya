import { existsSync } from "node:fs";
import { join } from "node:path";
import { Play } from "lucide-react";
import { Reveal } from "./motion";
import { SUBMISSION } from "./submissionData";

/**
 * The 60-second demo slot. A server component: it checks at render time whether public/landing/demo.mp4 exists and
 * plays it (with public/landing/demo-poster.jpg when that exists too). Until the video is recorded it shows a quiet
 * placeholder frame that says when it lands. Drop the file in, rebuild (`next build`) or reload the dev server, done.
 */
const exists = (publicPath: string) => {
  try {
    return existsSync(join(process.cwd(), "public", publicPath.replace(/^\//, "")));
  } catch {
    return false;
  }
};

export function DemoVideo() {
  const hasVideo = exists(SUBMISSION.demoVideoSrc);
  const poster = exists(SUBMISSION.demoVideoPoster) ? SUBMISSION.demoVideoPoster : undefined;
  return (
    <section id="demo" aria-label="60-second demo" className="s-container mt-12 md:mt-16">
      <Reveal>
        <figure className="mx-auto max-w-[56rem]">
          <div className="relative aspect-video overflow-hidden rounded-2xl border border-[var(--s-line)] bg-[#111]">
            {hasVideo ? (
              <video className="h-full w-full object-cover" controls playsInline preload="metadata" poster={poster} aria-label="Vakya demo, 60 seconds">
                <source src={SUBMISSION.demoVideoSrc} type="video/mp4" />
              </video>
            ) : (
              <div className="vk-demo-frame absolute inset-0 grid place-items-center p-6 text-center">
                <div>
                  <span aria-hidden className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-white text-black">
                    <Play className="h-6 w-6 translate-x-0.5" />
                  </span>
                  <p className="mt-5 text-lg font-semibold text-white">Demo video: {SUBMISSION.demoVideoDate}</p>
                  <p className="mx-auto mt-2 max-w-[40ch] text-sm leading-relaxed text-[var(--s-night-mute)]">
                    60 seconds: a call on a noisy line, the read-back catching a wrong digit, and the signed receipt.
                  </p>
                </div>
              </div>
            )}
          </div>
          <figcaption className="mt-3 text-center text-xs text-[var(--s-mute)]">
            Synthetic data only: the clinic, the caller and the number are invented.
          </figcaption>
        </figure>
      </Reveal>
    </section>
  );
}
