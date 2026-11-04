"use client";

import { useCallback, useRef, useState, type FormEvent, type MutableRefObject } from "react";
import { Mic, Send, Square } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui";
import { errMessage } from "@/components/desk/format";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Sentence {
  audio_url?: string;
}

/**
 * "Try it here": talk or type to the front desk from this page, without a phone. A turn goes to the owner's streaming
 * turn endpoint; the words and tool calls come back through the live call log (so this page shows them the same way
 * as a call from a phone), and the reply is spoken here sentence by sentence.
 */
export function Composer({ agentId, conversation, className }: { agentId: string; conversation: MutableRefObject<string | null>; className?: string }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const queue = useRef<Promise<void>>(Promise.resolve());
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);

  const speak = useCallback((url: string) => {
    queue.current = queue.current.then(async () => {
      try {
        const blob = await api.blob(url);
        const audio = new Audio(URL.createObjectURL(blob));
        await new Promise<void>((resolve) => {
          audio.onended = () => resolve();
          audio.onerror = () => resolve();
          audio.play().catch(() => resolve());
        });
      } catch {
        /* a sentence that cannot play is not worth stopping the call for */
      }
    });
  }, []);

  const send = useCallback(
    async (form: FormData) => {
      setBusy(true);
      try {
        if (conversation.current) form.append("conversation_id", conversation.current);
        await api.sse(`/api/conversations/${agentId}/turn/stream`, form, {
          onEvent: (event, data) => {
            if (event === "sentence" && !(data as Sentence).audio_url?.startsWith("data:") && (data as Sentence).audio_url) speak((data as Sentence).audio_url as string);
            if (event === "done") conversation.current = (data as { conversation_id?: string }).conversation_id ?? conversation.current;
            if (event === "error") toast.error((data as { message?: string }).message ?? "The turn failed.");
          },
        });
      } catch (e) {
        toast.error(errMessage(e, "Could not reach the front desk."));
      } finally {
        setBusy(false);
      }
    },
    [agentId, conversation, speak]
  );

  function submit(e: FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    setText("");
    const form = new FormData();
    form.append("text", t);
    form.append("test_call", "true");
    void send(form);
  }

  async function toggleMic() {
    if (recording) {
      recorder.current?.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      chunks.current = [];
      rec.ondataavailable = (ev) => ev.data.size && chunks.current.push(ev.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        const blob = new Blob(chunks.current, { type: rec.mimeType || "audio/webm" });
        if (blob.size < 1500) return;
        const form = new FormData();
        form.append("audio", blob, `recording.${(rec.mimeType || "audio/webm").includes("mp4") ? "m4a" : "webm"}`);
        form.append("test_call", "true");
        void send(form);
      };
      recorder.current = rec;
      rec.start();
      setRecording(true);
    } catch {
      toast.error("This browser cannot use the microphone here. Type instead.");
    }
  }

  return (
    <form onSubmit={submit} className={cn("flex items-center gap-2", className)} aria-label="Talk to the front desk from this page">
      <Button type="button" variant={recording ? "solid" : "outline"} onClick={() => void toggleMic()} aria-label={recording ? "Stop and send" : "Speak"} aria-pressed={recording} disabled={busy && !recording} className="h-11 w-11 shrink-0 px-0">
        {recording ? <Square aria-hidden className="size-4" /> : <Mic aria-hidden className="size-4" />}
      </Button>
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Or type what the caller says"
        aria-label="Type what the caller says"
        maxLength={1000}
        className="h-11 min-w-0 flex-1 rounded-lg border border-border bg-bg-surface px-3 text-[15px] text-text-primary placeholder:text-text-muted focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary"
      />
      <Button type="submit" loading={busy && !recording} disabled={!text.trim()} leftIcon={<Send className="size-4" />} className="h-11">
        Send
      </Button>
    </form>
  );
}
