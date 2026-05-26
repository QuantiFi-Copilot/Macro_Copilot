// ============================================================================
// WidgetCatalogModal
// ----------------------------------------------------------------------------
// Modal that opens from the "+ Add widget" tile in edit mode (or the
// cog button on a parameterized widget).  Two phases:
//
//   1. GALLERY: tile grid of available widget types.  Each tile shows
//      the type's category color rail, label, description, and source
//      tool.  Click a tile → enters config phase (or directly adds if
//      the widget is non-parameterized and we're in "add" mode).
//
//   2. CONFIG: per-type config form built from the type's `paramFields`.
//      Selects + numbers; cross-field constraints (mustDifferFrom)
//      surfaced inline.  Save commits to the layout via the supplied
//      callback.
//
// `mode = "add"` opens to the gallery; `mode = "configure"` skips
// straight to the config form for a specific instance.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Check, X } from 'lucide-react';
import {
  CATALOG_ORDER,
  WIDGET_TYPES,
  defaultParamsFor,
  widgetMeta,
  type WidgetCategory,
  type WidgetParamField,
  type WidgetTypeMeta,
} from './registry';
import type { WidgetInstance } from './registry';
import { cn } from '@/utils/cn';

type AddMode = {
  mode: 'add';
  onAdd: (
    typeId: string,
    overrides?: Partial<Pick<WidgetInstance, 'size' | 'params'>>,
  ) => void;
};

type ConfigureMode = {
  mode: 'configure';
  instance: WidgetInstance;
  onSave: (
    instanceId: string,
    patch: Partial<Pick<WidgetInstance, 'size' | 'params'>>,
  ) => void;
};

type Props = {
  open: boolean;
  onClose: () => void;
} & (AddMode | ConfigureMode);

export function WidgetCatalogModal(props: Props) {
  const { open, onClose } = props;
  const [phase, setPhase] = useState<'gallery' | 'config'>(
    props.mode === 'configure' ? 'config' : 'gallery',
  );
  const [selectedTypeId, setSelectedTypeId] = useState<string | null>(
    props.mode === 'configure' ? props.instance.type : null,
  );

  // Reset to entry-point phase whenever the modal re-opens.
  useEffect(() => {
    if (!open) return;
    if (props.mode === 'configure') {
      setPhase('config');
      setSelectedTypeId(props.instance.type);
    } else {
      setPhase('gallery');
      setSelectedTypeId(null);
    }
  }, [open, props.mode, props.mode === 'configure' ? props.instance.id : null]);

  // Close on Escape.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onClose]);

  if (!open) return null;

  const selectedMeta = selectedTypeId ? widgetMeta(selectedTypeId) : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-6"
      aria-modal
      role="dialog"
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-ink-900/65 backdrop-blur-md"
        onClick={onClose}
      />

      {/* Card */}
      <div className="relative flex max-h-[80vh] w-full max-w-[820px] flex-col overflow-hidden rounded-[14px] bg-[linear-gradient(180deg,rgba(255,255,255,0.03),rgba(255,255,255,0.008)_60%),rgba(14,16,22,0.92)] shadow-[0_32px_80px_-20px_rgba(0,0,0,0.7),inset_0_1px_0_rgba(255,255,255,0.05),inset_0_0_0_1px_rgba(148,163,184,0.10)]">
        {/* Header */}
        <div className="flex items-center justify-between gap-3 border-b border-line-subtle px-5 py-3.5">
          <div className="flex items-center gap-2.5">
            {phase === 'config' && props.mode === 'add' && (
              <button
                type="button"
                onClick={() => {
                  setPhase('gallery');
                  setSelectedTypeId(null);
                }}
                className="flex h-7 w-7 items-center justify-center rounded-md text-fg-secondary transition-colors hover:bg-white/[0.04] hover:text-fg-primary"
                aria-label="Back to catalog"
              >
                <ArrowLeft size={12} />
              </button>
            )}
            <div className="flex flex-col">
              <span className="text-[10px] font-medium uppercase tracking-[0.16em] text-fg-muted">
                {props.mode === 'configure' ? 'CONFIGURE WIDGET' : 'WIDGET CATALOG'}
              </span>
              <span className="text-[14px] font-medium tracking-[-0.005em] text-fg-primary">
                {phase === 'gallery'
                  ? 'Choose a widget'
                  : selectedMeta?.label ?? 'Configure'}
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-7 w-7 items-center justify-center rounded-md text-fg-secondary transition-colors hover:bg-white/[0.04] hover:text-fg-primary"
          >
            <X size={13} />
          </button>
        </div>

        {/* Body */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {phase === 'gallery' ? (
            <Gallery
              onPick={(typeId) => {
                setSelectedTypeId(typeId);
                const meta = widgetMeta(typeId);
                if (!meta) return;
                if (!meta.parameterized && props.mode === 'add') {
                  // Non-parameterized + add mode → commit directly.
                  props.onAdd(typeId);
                  onClose();
                  return;
                }
                setPhase('config');
              }}
            />
          ) : selectedMeta ? (
            <ConfigForm
              meta={selectedMeta}
              initialParams={
                props.mode === 'configure'
                  ? props.instance.params
                  : defaultParamsFor(selectedMeta)
              }
              initialSize={
                props.mode === 'configure'
                  ? props.instance.size
                  : selectedMeta.defaultSize
              }
              onCancel={onClose}
              onSubmit={(patch) => {
                if (props.mode === 'configure') {
                  props.onSave(props.instance.id, patch);
                } else {
                  props.onAdd(selectedMeta.id, patch);
                }
                onClose();
              }}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------

function Gallery({ onPick }: { onPick: (typeId: string) => void }) {
  return (
    <div className="grid grid-cols-1 gap-3 px-5 py-5 sm:grid-cols-2">
      {CATALOG_ORDER.map((id) => {
        const meta = WIDGET_TYPES[id];
        if (!meta) return null;
        return (
          <CatalogTile
            key={id}
            meta={meta}
            onClick={() => onPick(id)}
          />
        );
      })}
    </div>
  );
}

function CatalogTile({
  meta,
  onClick,
}: {
  meta: WidgetTypeMeta;
  onClick: () => void;
}) {
  const railColor = railColorFor(meta.category);
  return (
    <button
      type="button"
      onClick={onClick}
      className="group relative flex flex-col gap-2 rounded-[12px] bg-white/[0.018] px-4 py-3.5 text-left ring-1 ring-line-subtle transition-all duration-200 ease-sleek hover:bg-white/[0.030] hover:ring-line-strong"
      style={{ ['--rail-color' as string]: railColor }}
    >
      <span aria-hidden className="research-card-rail" />
      <div className="flex items-center justify-between gap-3">
        <span className="text-[13px] font-medium tracking-[-0.005em] text-fg-primary">
          {meta.label}
        </span>
        <span
          className={cn(
            'inline-flex items-center rounded-full px-1.5 py-[1px] text-[9.5px] font-medium uppercase tracking-[0.12em]',
            categoryChipClass(meta.category),
          )}
        >
          {meta.category}
        </span>
      </div>
      <p className="text-[11.5px] leading-[1.55] text-fg-secondary">
        {meta.description}
      </p>
      <div className="mt-1 flex items-center gap-2 font-mono text-[10px] text-fg-faint">
        <span className="opacity-80">tool:</span>
        <span className="truncate text-fg-muted">{meta.sourceTool}</span>
      </div>
    </button>
  );
}

// ----------------------------------------------------------------------------

function ConfigForm({
  meta,
  initialParams,
  initialSize,
  onCancel,
  onSubmit,
}: {
  meta: WidgetTypeMeta;
  initialParams: Record<string, unknown>;
  initialSize: WidgetInstance['size'];
  onCancel: () => void;
  onSubmit: (patch: Partial<Pick<WidgetInstance, 'size' | 'params'>>) => void;
}) {
  const [params, setParams] =
    useState<Record<string, unknown>>(initialParams);
  const [size, setSize] = useState<WidgetInstance['size']>(initialSize);

  const fields = meta.paramFields ?? [];

  // Cross-field validation: any select with a `mustDifferFrom` whose
  // value matches the referenced field's value is considered invalid.
  const errors = useMemo(() => {
    const errs: Record<string, string> = {};
    for (const f of fields) {
      if (f.kind !== 'select' || !f.mustDifferFrom) continue;
      const otherValue = params[f.mustDifferFrom];
      if (otherValue !== undefined && otherValue === params[f.name]) {
        errs[f.name] = `Must differ from ${labelOf(f.mustDifferFrom, fields)}.`;
      }
    }
    return errs;
  }, [params, fields]);

  const isValid = Object.keys(errors).length === 0;

  return (
    <div className="flex flex-col gap-4 px-5 py-5">
      {/* Description */}
      <p className="text-[12px] leading-[1.55] text-fg-secondary">
        {meta.description}
      </p>

      {/* Param fields */}
      {fields.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {fields.map((f) => (
            <FormField
              key={f.name}
              field={f}
              value={params[f.name]}
              error={errors[f.name]}
              onChange={(v) => setParams((p) => ({ ...p, [f.name]: v }))}
            />
          ))}
        </div>
      )}

      {/* Size picker (only when more than one allowed size) */}
      {meta.allowedSizes.length > 1 && (
        <div className="flex flex-col gap-1.5">
          <span className="text-[10px] font-medium uppercase tracking-[0.16em] text-fg-muted">
            Size
          </span>
          <div className="flex gap-1.5">
            {meta.allowedSizes.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setSize(s)}
                className={cn(
                  'rounded-md px-2.5 py-1.5 text-[11.5px] font-medium tracking-[-0.005em] transition-colors',
                  size === s
                    ? 'bg-ice-500/15 text-ice-100 ring-1 ring-ice-400/35'
                    : 'text-fg-secondary ring-1 ring-line-soft hover:bg-white/[0.025] hover:text-fg-primary',
                )}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Footer */}
      <div className="mt-1 flex items-center justify-end gap-1.5">
        <button
          type="button"
          onClick={onCancel}
          className="flex h-8 items-center gap-1 rounded-md px-3 text-[12px] font-medium text-fg-secondary transition-colors hover:bg-white/[0.025] hover:text-fg-primary"
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={!isValid}
          onClick={() => isValid && onSubmit({ size, params })}
          className={cn(
            'flex h-8 items-center gap-1.5 rounded-md px-3 text-[12px] font-medium transition-all duration-200 ease-sleek',
            isValid
              ? 'composer-send-active'
              : 'cursor-not-allowed border border-line-soft bg-white/[0.02] text-fg-faint',
          )}
        >
          <Check size={11} strokeWidth={2.25} />
          <span>Save widget</span>
        </button>
      </div>
    </div>
  );
}

function FormField({
  field,
  value,
  error,
  onChange,
}: {
  field: WidgetParamField;
  value: unknown;
  error?: string;
  onChange: (v: unknown) => void;
}) {
  if (field.kind === 'select') {
    return (
      <label className="flex flex-col gap-1.5">
        <span className="text-[10px] font-medium uppercase tracking-[0.16em] text-fg-muted">
          {field.label}
        </span>
        <select
          value={(value as string) ?? field.defaultValue}
          onChange={(e) => onChange(e.target.value)}
          className={cn(
            'rounded-md bg-white/[0.025] px-3 py-2 text-[12.5px] text-fg-primary ring-1 transition-all duration-200 focus:outline-none',
            error
              ? 'ring-coral-400/40 focus:ring-coral-400/60'
              : 'ring-line-soft focus:ring-ice-400/45 focus:bg-white/[0.035]',
          )}
        >
          {field.options.map((o) => (
            <option key={o.value} value={o.value} className="bg-ink-900">
              {o.label}
            </option>
          ))}
        </select>
        {error && (
          <span className="text-[10.5px] font-medium text-coral-300">
            {error}
          </span>
        )}
      </label>
    );
  }
  // number
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[10px] font-medium uppercase tracking-[0.16em] text-fg-muted">
        {field.label}
      </span>
      <input
        type="number"
        value={typeof value === 'number' ? value : field.defaultValue}
        min={field.min}
        max={field.max}
        step={field.step ?? 1}
        onChange={(e) => onChange(Number(e.target.value))}
        className="rounded-md bg-white/[0.025] px-3 py-2 font-mono text-[12.5px] text-fg-primary ring-1 ring-line-soft transition-all duration-200 focus:bg-white/[0.035] focus:outline-none focus:ring-ice-400/45"
      />
    </label>
  );
}

// ----------------------------------------------------------------------------

function railColorFor(category: WidgetCategory): string {
  switch (category) {
    case 'data':
      return 'rgba(122, 162, 255, 0.55)';
    case 'analysis':
      return 'rgba(155, 140, 255, 0.45)';
    case 'anomaly':
      return 'rgba(243, 183, 85, 0.55)';
  }
}

function categoryChipClass(category: WidgetCategory): string {
  switch (category) {
    case 'data':
      return 'bg-ice-400/[0.12] text-ice-200 ring-1 ring-ice-400/30';
    case 'analysis':
      return 'bg-lineage-400/[0.12] text-lineage-200 ring-1 ring-lineage-400/30';
    case 'anomaly':
      return 'bg-amber-400/[0.12] text-amber-300 ring-1 ring-amber-400/30';
  }
}

function labelOf(name: string, fields: ReadonlyArray<WidgetParamField>): string {
  return fields.find((f) => f.name === name)?.label ?? name;
}
