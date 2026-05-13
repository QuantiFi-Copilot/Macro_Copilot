// ============================================================================
// CurveFamilyControl — polished select for the curve_family enum.
// ----------------------------------------------------------------------------
// Single-select dropdown grouped by domain (sovereign vs OIS).  When
// the descriptor's metadata restricts to one domain, only that group
// renders — closes the door on UX-level slips like selecting USD_TIPS
// inside an OIS slot.
//
// Visual: a styled native ``<select>`` so the control inherits the
// browser's accessibility (focus ring, keyboard navigation, screen-
// reader semantics).  Custom dropdowns lose all of that and pay an
// implementation tax we don't need here.
// ============================================================================

import { useId } from 'react';
import { cn } from '@/utils/cn';
import {
  registerControl,
} from '../lib/controlRegistry';
import type { ParamControlProps } from '../lib/controlRegistry';
import { ControlShell } from './ControlShell';

const SOVEREIGN: Array<{ value: string; label: string }> = [
  { value: 'UST', label: 'UST · US Treasuries' },
  { value: 'DE_BUND', label: 'DE_BUND · German Bunds' },
  { value: 'UK_GILT', label: 'UK_GILT · UK Gilts' },
  { value: 'JGB', label: 'JGB · Japan' },
  { value: 'FR_OAT', label: 'FR_OAT · France' },
  { value: 'IT_BTP', label: 'IT_BTP · Italy' },
  { value: 'ES_BONO', label: 'ES_BONO · Spain' },
  { value: 'AU_GOVT', label: 'AU_GOVT · Australia' },
  { value: 'CANADA_GOVT', label: 'CANADA_GOVT · Canada' },
  { value: 'USD_TIPS', label: 'USD_TIPS · US inflation-linked' },
];

const OIS: Array<{ value: string; label: string }> = [
  { value: 'USD_SOFR_OIS', label: 'USD_SOFR_OIS' },
  { value: 'EUR_ESTR_OIS', label: 'EUR_ESTR_OIS' },
  { value: 'GBP_SONIA_OIS', label: 'GBP_SONIA_OIS' },
  { value: 'JPY_OIS', label: 'JPY_OIS' },
  { value: 'AUD_OIS', label: 'AUD_OIS' },
  { value: 'CAD_OIS', label: 'CAD_OIS' },
];

const CurveFamilyControl = ({
  descriptor,
  override,
  onChange,
}: ParamControlProps) => {
  const id = useId();
  const meta = descriptor.meta;
  const allowed =
    meta.kind === 'curve_family'
      ? meta.allowedDomains
      : (['sovereign', 'ois'] as const);
  const value =
    override?.value !== undefined
      ? String(override.value)
      : (descriptor.currentValue as string | undefined) ?? '';

  return (
    <ControlShell
      descriptor={descriptor}
      override={override}
      onRevert={override ? () => onChange(undefined) : undefined}
    >
      <select
        id={id}
        value={value}
        disabled={descriptor.readOnly}
        onChange={(e) =>
          onChange(e.target.value === '' ? undefined : e.target.value)
        }
        className={cn(
          'w-full rounded-md border border-line-soft bg-white/[0.012] px-2 py-1.5 text-[12px] font-medium text-fg-primary',
          'focus:border-ice-400/40 focus:outline-none focus:ring-1 focus:ring-ice-400/30',
          descriptor.readOnly && 'cursor-not-allowed opacity-70',
        )}
      >
        {/* When the descriptor lacks a current value, surface a
            blank placeholder so the user sees "no value" rather
            than the first option being silently selected. */}
        {value === '' && <option value="">— select curve —</option>}

        {allowed.includes('sovereign') && (
          <optgroup label="Sovereign">
            {SOVEREIGN.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </optgroup>
        )}
        {allowed.includes('ois') && (
          <optgroup label="OIS">
            {OIS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </optgroup>
        )}
      </select>
    </ControlShell>
  );
};

registerControl('curve_family', CurveFamilyControl);
export { CurveFamilyControl };
