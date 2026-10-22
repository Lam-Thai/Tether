"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Modal built on the native `<dialog>`: `showModal()` gives the focus trap,
 * inert background and Esc-to-close; the title labels it. The parent owns
 * `open`, and `onClose` fires for Esc, the close button and backdrop clicks.
 * Children are mounted only while open, so forms reset on every open.
 */
export function Dialog({
  open,
  onClose,
  title,
  children,
  className,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (open && !node.open) node.showModal();
    if (!open && node.open) node.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={(event) => {
        // A click on the backdrop lands on the dialog element itself.
        if (event.target === ref.current) onClose();
      }}
      className={cn(
        "m-auto w-[calc(100%-2rem)] max-w-lg rounded-md border border-border bg-card p-0 text-card-foreground shadow-card backdrop:bg-foreground/40",
        className,
      )}
    >
      {open && (
        <div className="flex max-h-[calc(100dvh-4rem)] flex-col gap-4 overflow-y-auto p-6">
          <div className="flex items-start justify-between gap-4">
            <h2 id={titleId} className="text-h2 font-bold tracking-tight uppercase">
              {title}
            </h2>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="-m-2 inline-flex size-10 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              <X aria-hidden className="size-5" />
            </button>
          </div>
          {children}
        </div>
      )}
    </dialog>
  );
}
