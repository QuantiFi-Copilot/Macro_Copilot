// ============================================================================
// ParameterControlSwitch — render one descriptor via its registered control.
// ----------------------------------------------------------------------------
// Single look-up + dispatch.  The consumer (StageParameterEditor)
// iterates descriptors and renders one switch per entry — branchless
// w.r.t. control kind, identical to how the node renderer registry
// works in PR A.
// ============================================================================

import { resolveControl } from './lib/controlRegistry';
// Side-effect import — populates the registry before any control
// renders.  Must happen before ``resolveControl`` is called.
import './controls';
import type { ParamControlDescriptor, ParamOverride } from './lib/controlSchema';

type Props = {
  descriptor: ParamControlDescriptor;
  override: ParamOverride | undefined;
  onChange: (value: unknown | undefined) => void;
};

export function ParameterControlSwitch({
  descriptor,
  override,
  onChange,
}: Props) {
  const Control = resolveControl(descriptor.meta.kind);
  return (
    <Control
      descriptor={descriptor}
      override={override}
      onChange={onChange}
    />
  );
}
