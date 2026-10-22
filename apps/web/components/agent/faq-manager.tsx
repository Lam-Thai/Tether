"use client";

import { useId, useState, type ReactNode } from "react";
import Link from "next/link";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useAuth } from "@clerk/nextjs";
import { LoaderCircle, Pencil, Plus, Trash2 } from "lucide-react";

import {
  createFaq,
  deleteFaq,
  updateFaq,
  type FaqList,
  type FaqListResult,
  type FaqRead,
  type FaqUpdate,
} from "@/lib/api/client";
import { FAQ_LIMITS, faqFormSchema, type FaqFormValues } from "@/lib/validation/faq";
import { Dialog } from "@/components/agent/faq-dialog";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError, FormError } from "@/components/ui/form-error";
import { Input, controlClasses } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

const SESSION_EXPIRED = "Your session has expired. Please sign in again.";

const PLAN_REQUIRED = (
  <>
    Your plan is not active.{" "}
    <Link href="/billing/plan" className="font-medium underline">
      Choose a plan
    </Link>
  </>
);

/** Messages for the failure kinds every action shares. */
function commonFailure(kind: string, reason?: string): ReactNode {
  if (kind === "unauthorized") return SESSION_EXPIRED;
  if (kind === "subscription_required") return PLAN_REQUIRED;
  return reason ?? "Something went wrong. Please try again.";
}

function toFormValues(faq: FaqRead | null): FaqFormValues {
  return {
    question: faq?.question ?? "",
    answer: faq?.answer ?? "",
    category: faq?.category ?? "",
    active: faq?.active ?? true,
  };
}

/** Only the fields whose validated value differs from what was last saved. */
export function changedFields(values: FaqFormValues, saved: FaqRead): FaqUpdate {
  const patch: FaqUpdate = {};
  if (values.question !== saved.question) patch.question = values.question;
  if (values.answer !== saved.answer) patch.answer = values.answer;
  if (values.category !== (saved.category ?? "")) patch.category = values.category || null;
  if (values.active !== saved.active) patch.active = values.active;
  return patch;
}

function Counter({ length, max }: { length: number; max: number }) {
  return (
    <p
      className={cn(
        "shrink-0 font-mono text-sm tabular-nums",
        length > max ? "text-danger-foreground" : "text-muted-foreground",
      )}
    >
      {length}/{max}
    </p>
  );
}

function Switch({
  checked,
  pending,
  label,
  onToggle,
}: {
  checked: boolean;
  pending: boolean;
  label: string;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-busy={pending || undefined}
      disabled={pending}
      onClick={onToggle}
      className="inline-flex h-11 items-center gap-2 rounded-md px-1 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-wait"
    >
      <span
        aria-hidden
        className={cn(
          "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border border-input transition-colors duration-200 motion-reduce:transition-none",
          checked ? "bg-foreground" : "bg-muted",
          pending && "opacity-60",
        )}
      >
        <span
          className={cn(
            "inline-block size-4 rounded-full transition-transform duration-200 motion-reduce:transition-none",
            checked ? "translate-x-6 bg-card" : "translate-x-1 bg-foreground",
          )}
        />
      </span>
      <span aria-hidden className="w-7 text-sm font-medium">
        {checked ? "On" : "Off"}
      </span>
    </button>
  );
}

function FaqForm({
  faq,
  categories,
  onSaved,
  onCancel,
  onLimit,
}: {
  faq: FaqRead | null;
  categories: string[];
  onSaved: (faq: FaqRead) => void;
  onCancel: () => void;
  onLimit: () => void;
}) {
  const { getToken } = useAuth();
  const uid = useId();
  const [formError, setFormError] = useState<ReactNode>(null);

  const {
    register,
    handleSubmit,
    setError,
    control,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<FaqFormValues>({
    resolver: zodResolver(faqFormSchema),
    defaultValues: toFormValues(faq),
  });

  const questionLength = (useWatch({ control, name: "question" }) ?? "").length;
  const answerLength = (useWatch({ control, name: "answer" }) ?? "").length;

  async function onSubmit(values: FaqFormValues) {
    setFormError(null);

    const patch = faq ? changedFields(values, faq) : null;
    if (faq && patch && Object.keys(patch).length === 0) {
      onCancel();
      return;
    }

    const token = await getToken();
    if (!token) {
      setFormError(SESSION_EXPIRED);
      return;
    }

    const result = faq
      ? await updateFaq(token, faq.id, patch ?? {})
      : await createFaq(token, {
          question: values.question,
          answer: values.answer,
          category: values.category || null,
          active: values.active,
        });

    switch (result.kind) {
      case "ok":
        onSaved(result.data);
        return;
      case "validation": {
        let placed = false;
        for (const [field, message] of Object.entries(result.fieldErrors)) {
          if (field in values) {
            setError(field as keyof FaqFormValues, { message });
            placed = true;
          }
        }
        if (!placed) setFormError(result.message);
        return;
      }
      case "limit_reached":
        onLimit();
        setFormError("You have reached the FAQ limit. Delete one to add another.");
        return;
      case "not_found":
        setFormError("This FAQ no longer exists. Close this window and reload the page.");
        return;
      case "unreachable":
        setFormError(result.reason);
        return;
      default:
        setFormError(commonFailure(result.kind));
    }
  }

  return (
    <form className="flex flex-col gap-5" onSubmit={handleSubmit(onSubmit)} noValidate>
      {formError && <FormError>{formError}</FormError>}

      <div className="flex flex-col gap-2">
        <Label htmlFor={`${uid}-question`}>Question</Label>
        <textarea
          id={`${uid}-question`}
          rows={2}
          className={cn(controlClasses, "h-auto resize-y")}
          aria-invalid={errors.question ? true : undefined}
          aria-describedby={errors.question ? `${uid}-question-error` : undefined}
          {...register("question")}
        />
        <div className="flex items-start justify-between gap-4">
          <p className="text-sm text-muted-foreground">How a customer would ask it.</p>
          <Counter length={questionLength} max={FAQ_LIMITS.question} />
        </div>
        {errors.question && (
          <div id={`${uid}-question-error`}>
            <FieldError>{errors.question.message}</FieldError>
          </div>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor={`${uid}-answer`}>Answer</Label>
        <textarea
          id={`${uid}-answer`}
          rows={5}
          className={cn(controlClasses, "h-auto resize-y")}
          aria-invalid={errors.answer ? true : undefined}
          aria-describedby={errors.answer ? `${uid}-answer-error` : undefined}
          {...register("answer")}
        />
        <div className="flex items-start justify-between gap-4">
          <p className="text-sm text-muted-foreground">The receptionist answers from this text only.</p>
          <Counter length={answerLength} max={FAQ_LIMITS.answer} />
        </div>
        {errors.answer && (
          <div id={`${uid}-answer-error`}>
            <FieldError>{errors.answer.message}</FieldError>
          </div>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor={`${uid}-category`}>Category (optional)</Label>
        <Input
          id={`${uid}-category`}
          list={`${uid}-categories`}
          autoComplete="off"
          aria-invalid={errors.category ? true : undefined}
          aria-describedby={errors.category ? `${uid}-category-error` : undefined}
          {...register("category")}
        />
        <datalist id={`${uid}-categories`}>
          {categories.map((category) => (
            <option key={category} value={category} />
          ))}
        </datalist>
        {errors.category && (
          <div id={`${uid}-category-error`}>
            <FieldError>{errors.category.message}</FieldError>
          </div>
        )}
      </div>

      <label className="flex cursor-pointer items-start gap-3 rounded-md border border-input bg-card p-3 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring">
        <input type="checkbox" className="mt-1 size-4 accent-foreground" {...register("active")} />
        <span className="flex flex-col">
          <span className="text-base font-medium">Active</span>
          <span className="text-sm text-muted-foreground">
            Turn off to keep this FAQ without the receptionist using it.
          </span>
        </span>
      </label>

      <div className="flex flex-wrap justify-end gap-3">
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={(faq !== null && !isDirty) || isSubmitting}>
          {isSubmitting ? "Saving…" : faq ? "Save FAQ" : "Add FAQ"}
        </Button>
      </div>
    </form>
  );
}

type DialogState =
  | { kind: "closed" }
  | { kind: "form"; faq: FaqRead | null }
  | { kind: "delete"; faq: FaqRead };

function FaqPanel({ initial }: { initial: FaqList }) {
  const { getToken } = useAuth();
  const [faqs, setFaqs] = useState<FaqRead[]>(initial.faqs);
  const [maxFaqs, setMaxFaqs] = useState(initial.max_faqs);
  const [dialog, setDialog] = useState<DialogState>({ kind: "closed" });
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  const [notice, setNotice] = useState<ReactNode>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<ReactNode>(null);

  const atLimit = faqs.length >= maxFaqs;
  const categories = [...new Set(faqs.map((f) => f.category).filter((c): c is string => !!c))].sort();

  function close() {
    setDialog({ kind: "closed" });
    setDeleteError(null);
  }

  function saved(faq: FaqRead) {
    setFaqs((current) =>
      current.some((f) => f.id === faq.id)
        ? current.map((f) => (f.id === faq.id ? faq : f))
        : [...current, faq],
    );
    setNotice(null);
    close();
  }

  async function toggle(faq: FaqRead) {
    setNotice(null);
    setPending((current) => new Set(current).add(faq.id));

    const token = await getToken();
    const result = token ? await updateFaq(token, faq.id, { active: !faq.active }) : null;

    setPending((current) => {
      const next = new Set(current);
      next.delete(faq.id);
      return next;
    });

    // The switch only moves once the API confirms; every failure leaves it as it was.
    if (!result) {
      setNotice(SESSION_EXPIRED);
    } else if (result.kind === "ok") {
      setFaqs((current) => current.map((f) => (f.id === faq.id ? result.data : f)));
    } else if (result.kind === "not_found") {
      setFaqs((current) => current.filter((f) => f.id !== faq.id));
      setNotice("That FAQ no longer exists, so it was removed from the list.");
    } else if (result.kind === "unreachable") {
      setNotice(result.reason);
    } else if (result.kind === "validation") {
      setNotice(result.message);
    } else {
      setNotice(commonFailure(result.kind));
    }
  }

  async function confirmDelete(faq: FaqRead) {
    setDeleteError(null);
    setDeleting(true);
    const token = await getToken();
    const result = token ? await deleteFaq(token, faq.id) : null;
    setDeleting(false);

    if (!result) {
      setDeleteError(SESSION_EXPIRED);
    } else if (result.kind === "ok" || result.kind === "not_found") {
      setFaqs((current) => current.filter((f) => f.id !== faq.id));
      setNotice(result.kind === "not_found" ? "That FAQ was already removed." : null);
      close();
    } else {
      setDeleteError(commonFailure(result.kind, result.kind === "unreachable" ? result.reason : undefined));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle as="h2">FAQs</CardTitle>
        <CardDescription>
          The receptionist answers questions only from these. Changes apply to its next reply, which
          you can try in the test chat. Turned-off FAQs are never used.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {notice && <FormError>{notice}</FormError>}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="font-mono text-sm text-muted-foreground tabular-nums">
            {faqs.length}/{maxFaqs} FAQs
          </p>
          <Button
            type="button"
            size="sm"
            disabled={atLimit}
            aria-describedby={atLimit ? "faq-limit-note" : undefined}
            onClick={() => setDialog({ kind: "form", faq: null })}
          >
            <Plus aria-hidden />
            Add FAQ
          </Button>
        </div>
        {atLimit && (
          <p id="faq-limit-note" className="text-sm text-muted-foreground">
            You have reached the limit of {maxFaqs} FAQs. Delete one to add another.
          </p>
        )}

        {faqs.length === 0 ? (
          <Callout
            tone="warning"
            compact
            headingLevel={3}
            title="No FAQs yet"
            action={
              <Button
                type="button"
                size="sm"
                disabled={atLimit}
                onClick={() => setDialog({ kind: "form", faq: null })}
              >
                <Plus aria-hidden />
                Add your first FAQ
              </Button>
            }
          >
            FAQs are the answers you approve: hours, prices, policies. Without them the receptionist
            has nothing to ground its replies in, so it hands the question to you instead of
            guessing.
          </Callout>
        ) : (
          <ul className="flex flex-col divide-y divide-border rounded-md border border-border">
            {faqs.map((faq) => (
              <li
                key={faq.id}
                className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between"
              >
                <div className={cn("flex min-w-0 flex-1 flex-col gap-1", !faq.active && "opacity-70")}>
                  <p className="text-base font-bold break-words">{faq.question}</p>
                  <p className="line-clamp-2 text-sm break-words text-muted-foreground">{faq.answer}</p>
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    {faq.category && <Badge>{faq.category}</Badge>}
                    {!faq.active && <Badge tone="warning">Off</Badge>}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2 sm:shrink-0 sm:justify-end">
                  <Switch
                    checked={faq.active}
                    pending={pending.has(faq.id)}
                    label={`Active: ${faq.question}`}
                    onToggle={() => void toggle(faq)}
                  />
                  {pending.has(faq.id) && (
                    <LoaderCircle aria-hidden className="size-4 animate-spin motion-reduce:animate-none" />
                  )}
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    aria-label={`Edit: ${faq.question}`}
                    onClick={() => setDialog({ kind: "form", faq })}
                  >
                    <Pencil aria-hidden />
                    Edit
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="destructive"
                    aria-label={`Delete: ${faq.question}`}
                    onClick={() => {
                      setDeleteError(null);
                      setDialog({ kind: "delete", faq });
                    }}
                  >
                    <Trash2 aria-hidden />
                    Delete
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>

      <Dialog
        open={dialog.kind === "form"}
        onClose={close}
        title={dialog.kind === "form" && dialog.faq ? "Edit FAQ" : "Add FAQ"}
      >
        {dialog.kind === "form" && (
          <FaqForm
            faq={dialog.faq}
            categories={categories}
            onSaved={saved}
            onCancel={close}
            onLimit={() => setMaxFaqs((max) => Math.min(max, faqs.length))}
          />
        )}
      </Dialog>

      <Dialog open={dialog.kind === "delete"} onClose={close} title="Delete FAQ?">
        {dialog.kind === "delete" && (
          <div className="flex flex-col gap-5">
            {deleteError && <FormError>{deleteError}</FormError>}
            <p className="text-base break-words text-muted-foreground">
              The receptionist will stop answering this question:{" "}
              <strong className="text-foreground">{dialog.faq.question}</strong>
            </p>
            <p className="text-sm text-muted-foreground">This cannot be undone.</p>
            <div className="flex flex-wrap justify-end gap-3">
              <Button type="button" variant="outline" onClick={close} disabled={deleting}>
                Cancel
              </Button>
              <Button
                type="button"
                variant="destructive"
                disabled={deleting}
                onClick={() => void confirmDelete(dialog.faq)}
              >
                {deleting ? "Deleting…" : "Delete FAQ"}
              </Button>
            </div>
          </div>
        )}
      </Dialog>
    </Card>
  );
}

/**
 * Agent Builder section 4. Switches on the `GET /faqs` result so a failed
 * list never breaks the rest of the page.
 */
export function FaqManager({ result }: { result: FaqListResult }) {
  switch (result.kind) {
    case "ok":
      return <FaqPanel initial={result.data} />;
    case "unauthorized":
      return (
        <Callout tone="danger" title="Signed out" headingLevel={2}>
          Your session has expired. <Link href="/sign-in" className="underline">Sign in again</Link>.
        </Callout>
      );
    case "subscription_required":
      return (
        <Callout
          tone="warning"
          title="Plan required"
          headingLevel={2}
          action={
            <Link href="/billing/plan" className={buttonVariants({ size: "sm" })}>
              Choose plan
            </Link>
          }
        >
          Choose a plan to manage FAQs.
        </Callout>
      );
    case "unreachable":
      return (
        <Callout tone="danger" title="Can't load your FAQs" headingLevel={2}>
          {result.reason} Reload the page to try again.
        </Callout>
      );
  }
}
