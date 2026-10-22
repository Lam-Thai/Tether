"use client";

import { useId, useState, type ReactNode } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useAuth } from "@clerk/nextjs";
import { LoaderCircle, Pencil, Plus, Trash2 } from "lucide-react";

import {
  createService,
  deleteService,
  updateService,
  type ServiceList,
  type ServiceListResult,
  type ServiceRead,
  type ServiceUpdate,
} from "@/lib/api/client";
import { formatDuration, formatPrice, priceToInput } from "@/lib/format-service";
import {
  SERVICE_LIMITS,
  serviceFormSchema,
  toServiceBody,
  type ServiceFormValues,
} from "@/lib/validation/service";
import { currencyDigits } from "@/lib/validation/business";
import { Dialog } from "@/components/agent/faq-dialog";
import {
  SESSION_EXPIRED,
  SectionLoadFailure,
  Switch,
  commonFailure,
} from "@/components/agent/section-shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError, FormError } from "@/components/ui/form-error";
import { Input, controlClasses } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

function toFormValues(service: ServiceRead | null, currency: string): ServiceFormValues {
  return {
    name: service?.name ?? "",
    description: service?.description ?? "",
    duration_minutes: service ? String(service.duration_minutes) : "",
    price: service ? priceToInput(service.price, currency) : "",
    active: service?.active ?? true,
  };
}

/** Only the fields whose validated value differs from what was last saved. */
export function changedFields(values: ServiceFormValues, saved: ServiceRead): ServiceUpdate {
  const body = toServiceBody(values);
  const patch: ServiceUpdate = {};
  if (body.name !== saved.name) patch.name = body.name;
  if (body.description !== saved.description) patch.description = body.description;
  if (body.duration_minutes !== saved.duration_minutes) patch.duration_minutes = body.duration_minutes;
  const priceChanged =
    body.price === null || saved.price === null
      ? body.price !== saved.price
      : Number(body.price) !== Number(saved.price);
  if (priceChanged) patch.price = body.price;
  if (body.active !== saved.active) patch.active = body.active;
  return patch;
}

function ServiceForm({
  service,
  currency,
  onSaved,
  onCancel,
  onLimit,
}: {
  service: ServiceRead | null;
  currency: string;
  onSaved: (service: ServiceRead) => void;
  onCancel: () => void;
  onLimit: () => void;
}) {
  const { getToken } = useAuth();
  const uid = useId();
  const [formError, setFormError] = useState<ReactNode>(null);
  const digits = currencyDigits(currency);

  const {
    register,
    handleSubmit,
    setError,
    control,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<ServiceFormValues>({
    resolver: zodResolver(serviceFormSchema(currency)),
    defaultValues: toFormValues(service, currency),
  });

  const descriptionLength = (useWatch({ control, name: "description" }) ?? "").length;

  async function onSubmit(values: ServiceFormValues) {
    setFormError(null);

    const patch = service ? changedFields(values, service) : null;
    if (service && patch && Object.keys(patch).length === 0) {
      onCancel();
      return;
    }

    const token = await getToken();
    if (!token) {
      setFormError(SESSION_EXPIRED);
      return;
    }

    const result = service
      ? await updateService(token, service.id, patch ?? {})
      : await createService(token, toServiceBody(values));

    switch (result.kind) {
      case "ok":
        onSaved(result.data);
        return;
      case "validation": {
        let placed = false;
        for (const [field, message] of Object.entries(result.fieldErrors)) {
          if (field in values) {
            setError(field as keyof ServiceFormValues, { message });
            placed = true;
          }
        }
        if (!placed) setFormError(result.message);
        return;
      }
      case "name_taken":
        setError("name", { message: "You already have a service with this name." });
        return;
      case "limit_reached":
        onLimit();
        setFormError("You have reached the service limit. Delete one to add another.");
        return;
      case "not_found":
        setFormError("This service no longer exists. Close this window and reload the page.");
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
        <Label htmlFor={`${uid}-name`}>Name</Label>
        <Input
          id={`${uid}-name`}
          autoComplete="off"
          aria-invalid={errors.name ? true : undefined}
          aria-describedby={errors.name ? `${uid}-name-error` : undefined}
          {...register("name")}
        />
        {errors.name && (
          <div id={`${uid}-name-error`}>
            <FieldError>{errors.name.message}</FieldError>
          </div>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor={`${uid}-description`}>Description (optional)</Label>
        <textarea
          id={`${uid}-description`}
          rows={3}
          className={cn(controlClasses, "h-auto resize-y")}
          aria-invalid={errors.description ? true : undefined}
          aria-describedby={errors.description ? `${uid}-description-error` : undefined}
          {...register("description")}
        />
        <div className="flex items-start justify-between gap-4">
          <p className="text-sm text-muted-foreground">What the receptionist can tell customers about it.</p>
          <p
            className={cn(
              "shrink-0 font-mono text-sm tabular-nums",
              descriptionLength > SERVICE_LIMITS.description ? "text-danger-foreground" : "text-muted-foreground",
            )}
          >
            {descriptionLength}/{SERVICE_LIMITS.description}
          </p>
        </div>
        {errors.description && (
          <div id={`${uid}-description-error`}>
            <FieldError>{errors.description.message}</FieldError>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
        <div className="flex flex-col gap-2">
          <Label htmlFor={`${uid}-duration`}>Duration (minutes)</Label>
          <Input
            id={`${uid}-duration`}
            inputMode="numeric"
            autoComplete="off"
            aria-invalid={errors.duration_minutes ? true : undefined}
            aria-describedby={errors.duration_minutes ? `${uid}-duration-error` : undefined}
            {...register("duration_minutes")}
          />
          {errors.duration_minutes && (
            <div id={`${uid}-duration-error`}>
              <FieldError>{errors.duration_minutes.message}</FieldError>
            </div>
          )}
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor={`${uid}-price`}>Price in {currency} (optional)</Label>
          <Input
            id={`${uid}-price`}
            inputMode={digits === 0 ? "numeric" : "decimal"}
            autoComplete="off"
            aria-invalid={errors.price ? true : undefined}
            aria-describedby={errors.price ? `${uid}-price-error` : `${uid}-price-help`}
            {...register("price")}
          />
          {errors.price ? (
            <div id={`${uid}-price-error`}>
              <FieldError>{errors.price.message}</FieldError>
            </div>
          ) : (
            <p id={`${uid}-price-help`} className="text-sm text-muted-foreground">
              Leave blank for no listed price.
            </p>
          )}
        </div>
      </div>

      <label className="flex cursor-pointer items-start gap-3 rounded-md border border-input bg-card p-3 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring">
        <input type="checkbox" className="mt-1 size-4 accent-foreground" {...register("active")} />
        <span className="flex flex-col">
          <span className="text-base font-medium">Active</span>
          <span className="text-sm text-muted-foreground">
            Turn off to keep this service without the receptionist offering or pricing it.
          </span>
        </span>
      </label>

      <div className="flex flex-wrap justify-end gap-3">
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={(service !== null && !isDirty) || isSubmitting}>
          {isSubmitting ? "Saving…" : service ? "Save service" : "Add service"}
        </Button>
      </div>
    </form>
  );
}

type DialogState =
  | { kind: "closed" }
  | { kind: "form"; service: ServiceRead | null }
  | { kind: "delete"; service: ServiceRead };

function ServicesPanel({ initial }: { initial: ServiceList }) {
  const { getToken } = useAuth();
  const currency = initial.currency;
  const [services, setServices] = useState<ServiceRead[]>(initial.services);
  const [maxServices, setMaxServices] = useState(initial.max_services);
  const [dialog, setDialog] = useState<DialogState>({ kind: "closed" });
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  const [notice, setNotice] = useState<ReactNode>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<ReactNode>(null);

  const atLimit = services.length >= maxServices;

  function close() {
    setDialog({ kind: "closed" });
    setDeleteError(null);
  }

  function saved(service: ServiceRead) {
    setServices((current) =>
      current.some((s) => s.id === service.id)
        ? current.map((s) => (s.id === service.id ? service : s))
        : [...current, service],
    );
    setNotice(null);
    close();
  }

  async function toggle(service: ServiceRead) {
    setNotice(null);
    setPending((current) => new Set(current).add(service.id));

    const token = await getToken();
    const result = token ? await updateService(token, service.id, { active: !service.active }) : null;

    setPending((current) => {
      const next = new Set(current);
      next.delete(service.id);
      return next;
    });

    // The switch only moves once the API confirms; every failure leaves it as it was.
    if (!result) {
      setNotice(SESSION_EXPIRED);
    } else if (result.kind === "ok") {
      setServices((current) => current.map((s) => (s.id === service.id ? result.data : s)));
    } else if (result.kind === "not_found") {
      setServices((current) => current.filter((s) => s.id !== service.id));
      setNotice("That service no longer exists, so it was removed from the list.");
    } else if (result.kind === "unreachable") {
      setNotice(result.reason);
    } else if (result.kind === "validation") {
      setNotice(result.message);
    } else if (result.kind === "name_taken") {
      setNotice("You already have a service with this name.");
    } else {
      setNotice(commonFailure(result.kind));
    }
  }

  async function confirmDelete(service: ServiceRead) {
    setDeleteError(null);
    setDeleting(true);
    const token = await getToken();
    const result = token ? await deleteService(token, service.id) : null;
    setDeleting(false);

    if (!result) {
      setDeleteError(SESSION_EXPIRED);
    } else if (result.kind === "ok" || result.kind === "not_found") {
      setServices((current) => current.filter((s) => s.id !== service.id));
      setNotice(result.kind === "not_found" ? "That service was already removed." : null);
      close();
    } else {
      setDeleteError(commonFailure(result.kind, result.kind === "unreachable" ? result.reason : undefined));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle as="h2">Services</CardTitle>
        <CardDescription>
          What you offer, how long it takes and what it costs. The receptionist quotes only these
          durations and prices. Inactive services are never offered or priced.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {notice && <FormError>{notice}</FormError>}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="font-mono text-sm text-muted-foreground tabular-nums">
            {services.length}/{maxServices} services
          </p>
          <Button
            type="button"
            size="sm"
            disabled={atLimit}
            aria-describedby={atLimit ? "service-limit-note" : undefined}
            onClick={() => setDialog({ kind: "form", service: null })}
          >
            <Plus aria-hidden />
            Add service
          </Button>
        </div>
        {atLimit && (
          <p id="service-limit-note" className="text-sm text-muted-foreground">
            You have reached the limit of {maxServices} services. Delete one to add another.
          </p>
        )}

        {services.length === 0 ? (
          <Callout
            tone="warning"
            compact
            headingLevel={3}
            title="No services yet"
            action={
              <Button
                type="button"
                size="sm"
                disabled={atLimit}
                onClick={() => setDialog({ kind: "form", service: null })}
              >
                <Plus aria-hidden />
                Add your first service
              </Button>
            }
          >
            Without services the receptionist can&apos;t say what you offer, how long it takes or what it
            costs, so it hands those questions to you.
          </Callout>
        ) : (
          <ul className="flex flex-col divide-y divide-border rounded-md border border-border">
            {services.map((service) => (
              <li
                key={service.id}
                className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between"
              >
                <div className={cn("flex min-w-0 flex-1 flex-col gap-1", !service.active && "opacity-70")}>
                  <p className="text-base font-bold break-words">{service.name}</p>
                  {service.description && (
                    <p className="line-clamp-2 text-sm break-words text-muted-foreground">
                      {service.description}
                    </p>
                  )}
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    <Badge>{formatDuration(service.duration_minutes)}</Badge>
                    <Badge>{formatPrice(service.price, currency)}</Badge>
                    {!service.active && <Badge tone="warning">Off</Badge>}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2 sm:shrink-0 sm:justify-end">
                  <Switch
                    checked={service.active}
                    pending={pending.has(service.id)}
                    label={`Active: ${service.name}`}
                    onToggle={() => void toggle(service)}
                  />
                  {pending.has(service.id) && (
                    <LoaderCircle aria-hidden className="size-4 animate-spin motion-reduce:animate-none" />
                  )}
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    aria-label={`Edit: ${service.name}`}
                    onClick={() => setDialog({ kind: "form", service })}
                  >
                    <Pencil aria-hidden />
                    Edit
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="destructive"
                    aria-label={`Delete: ${service.name}`}
                    onClick={() => {
                      setDeleteError(null);
                      setDialog({ kind: "delete", service });
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
        title={dialog.kind === "form" && dialog.service ? "Edit service" : "Add service"}
      >
        {dialog.kind === "form" && (
          <ServiceForm
            service={dialog.service}
            currency={currency}
            onSaved={saved}
            onCancel={close}
            onLimit={() => setMaxServices((max) => Math.min(max, services.length))}
          />
        )}
      </Dialog>

      <Dialog open={dialog.kind === "delete"} onClose={close} title="Delete service?">
        {dialog.kind === "delete" && (
          <div className="flex flex-col gap-5">
            {deleteError && <FormError>{deleteError}</FormError>}
            <p className="text-base break-words text-muted-foreground">
              The receptionist will stop offering this service:{" "}
              <strong className="text-foreground">{dialog.service.name}</strong>
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
                onClick={() => void confirmDelete(dialog.service)}
              >
                {deleting ? "Deleting…" : "Delete service"}
              </Button>
            </div>
          </div>
        )}
      </Dialog>
    </Card>
  );
}

/** Agent Builder section 5. Switches on the `GET /services` result so a failed list never breaks the page. */
export function ServicesManager({ result }: { result: ServiceListResult }) {
  if (result.kind === "ok") return <ServicesPanel initial={result.data} />;
  return (
    <SectionLoadFailure
      kind={result.kind}
      reason={result.kind === "unreachable" ? result.reason : undefined}
      noun="services"
    />
  );
}
