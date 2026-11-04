import { PageIntro, NextPage } from "@/components/site/PageIntro";
import { RigCheck } from "@/components/site/RigCheck";
import { pageMetadata } from "@/components/site/pageMeta";

export const metadata = pageMetadata({
  title: "Run it",
  description:
    "Five commands to run Vakya on your own machine: ./vakya setup and ./vakya start, the API on 8020, the web app on 3010, and the Gnani, Embeddings, Vobiz and Google Calendar keys.",
  path: "/run-it",
});

export default function RunItPage() {
  return (
    <>
      <PageIntro title="Run it yourself.">
        Five commands, three ports and a few keys. The Gnani and Embeddings keys switch on speech and replies; the Vobiz keys switch on the phone line.
      </PageIntro>
      <RigCheck />
      <NextPage from="/run-it" />
    </>
  );
}
