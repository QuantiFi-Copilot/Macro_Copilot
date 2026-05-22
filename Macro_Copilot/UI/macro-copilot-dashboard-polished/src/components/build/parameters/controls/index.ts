// ============================================================================
// controls/index.ts — populate the control registry.
// ----------------------------------------------------------------------------
// Importing this barrel runs every control module's ``registerControl``
// side effect.  Consumers (ParameterControlSwitch, tests) import here
// before they call ``resolveControl`` so the registry is guaranteed-
// populated.
//
// ReadonlyJson is imported last so its ``registerFallbackControl``
// runs after every per-kind ``registerControl`` — keeps the
// diagnostic snapshot stable.
// ============================================================================

import './CurveFamilyControl';
import './TenorControl';
import './LookbackDaysControl';
import './WindowDaysControl';
import './FieldNameControl';
import './ThresholdControl';
import './DateControl';
import './BooleanControl';
import './NumericControl';
import './EnumControl';
import './StringControl';
import './ToolNameControl';
import './OutputFieldControl';
import './ReadonlyJsonControl';
