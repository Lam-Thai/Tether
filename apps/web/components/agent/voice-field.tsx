"use client";

import { useEffect, useRef, useState } from "react";
import type { UseFormRegisterReturn } from "react-hook-form";
import { useAuth } from "@clerk/nextjs";
import { CircleAlert, Volume2 } from "lucide-react";

import { previewVoice, type VoiceListResult } from "@/lib/api/client";
import { SESSION_EXPIRED, commonFailure } from "@/components/agent/section-shared";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { FormError } from "@/components/ui/form-error";
import { controlClasses } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

export const STANDARD_VOICE = "standard";

type PreviewState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; voice: string; url: string }
  | { kind: "error"; message: string };

const PREVIEW_UNAVAILABLE =
  "Voice preview isn't available right now. Calls will use the standard voice until it is.";
const PREVIEW_LIMIT = "Today's voice preview limit is reached. It resets at 00:00 UTC.";

/**
 * Voice picker for the persona card. The select is a registered form field
 * (saved with the form's Save button); Preview plays the SAVED greeting in the
 * currently selected voice, so an unsaved voice can be heard before saving.
 */
export function VoiceField({
  voices,
  selectProps,
  selected,
  greetingChanged,
}: {
  voices: VoiceListResult;
  selectProps: UseFormRegisterReturn;
  selected: string;
  greetingChanged: boolean;
}) {
  const { getToken } = useAuth();
  const [preview, setPreview] = useState<PreviewState>({ kind: "idle" });
  const urlRef = useRef<string | null>(null);
  const requestRef = useRef(0);

  // Revoke the last object URL and drop any in-flight result on unmount.
  useEffect(() => {
    return () => {
      requestRef.current += 1;
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    };
  }, []);

  function replaceUrl(next: string | null) {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = next;
  }

  if (voices.kind !== "ok") {
    return (
      <div className="flex flex-col gap-2">
        <Label>Voice</Label>
        <FormError>
          {voices.kind === "unreachable"
            ? `Voices could not be loaded. ${voices.reason} Reload the page to try again.`
            : commonFailure(voices.kind)}
        </FormError>
      </div>
    );
  }

  const { voices: options, tts_configured: ttsConfigured } = voices.data;
  const current = options.find((v) => v.id === selected);
  const isStandard = selected === STANDARD_VOICE;
  const canPreview = ttsConfigured && !isStandard && preview.kind !== "loading";

  async function onPreview() {
    const requestId = ++requestRef.current;
    const voice = selected;
    replaceUrl(null);
    setPreview({ kind: "loading" });

    const token = await getToken();
    if (requestId !== requestRef.current) return;
    if (!token) {
      setPreview({ kind: "error", message: SESSION_EXPIRED });
      return;
    }

    const result = await previewVoice(token, voice);
    if (requestId !== requestRef.current) return;

    switch (result.kind) {
      case "ok": {
        const url = URL.createObjectURL(result.audio);
        urlRef.current = url;
        setPreview({ kind: "ready", voice, url });
        return;
      }
      case "usage_limited":
        setPreview({ kind: "error", message: PREVIEW_LIMIT });
        return;
      case "unavailable":
        setPreview({ kind: "error", message: PREVIEW_UNAVAILABLE });
        return;
      case "invalid":
        setPreview({ kind: "error", message: result.message });
        return;
      case "unauthorized":
        setPreview({ kind: "error", message: SESSION_EXPIRED });
        return;
      case "subscription_required":
        setPreview({ kind: "error", message: "Your plan is not active. Choose a plan to preview voices." });
        return;
      case "unreachable":
        setPreview({ kind: "error", message: result.reason });
        return;
    }
  }

  const showAudio = preview.kind === "ready" && preview.voice === selected;
  const helpId = "agent-voice-help";

  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor="agent-voice">Voice</Label>
      <div className="flex flex-wrap items-start gap-3">
        <select
          id="agent-voice"
          className={cn(controlClasses, "w-auto min-w-56 max-w-full flex-1")}
          aria-describedby={helpId}
          {...selectProps}
        >
          {options.map((v) => (
            <option key={v.id} value={v.id}>
              {v.id === STANDARD_VOICE ? v.label : `${v.label} (${v.description})`}
            </option>
          ))}
          {!current && <option value={selected}>{selected}</option>}
        </select>
        <Button
          type="button"
          variant="outline"
          disabled={!canPreview}
          aria-busy={preview.kind === "loading" || undefined}
          onClick={onPreview}
        >
          <Volume2 aria-hidden />
          {preview.kind === "loading" ? "Generating..." : "Preview voice"}
        </Button>
      </div>

      <p id={helpId} className="text-sm text-muted-foreground">
        {isStandard
          ? "The standard phone voice has nothing to preview. It is the built-in voice of the phone line."
          : "Preview plays your saved greeting at phone quality. Each preview uses one of today's voice requests."}
        {greetingChanged && !isStandard && " Your greeting has unsaved edits, so the preview uses the saved greeting."}
      </p>

      {!ttsConfigured && (
        <Callout tone="warning" title="Demo mode" headingLevel={3} compact>
          Voice generation isn&apos;t set up here, so calls use the standard phone voice whatever you
          pick. Preview is turned off.
        </Callout>
      )}

      <div role="status" aria-live="polite" className="flex flex-col gap-2">
        {preview.kind === "error" && (
          <p className="flex items-start gap-1.5 text-sm text-danger-foreground">
            <CircleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            <span>{preview.message}</span>
          </p>
        )}
        {showAudio && (
          // Generated speech has no transcript to caption; the greeting is shown above.
          <audio controls src={preview.url} aria-label={`Preview of the ${current?.label ?? selected} voice`} className="w-full" />
        )}
      </div>
    </div>
  );
}
