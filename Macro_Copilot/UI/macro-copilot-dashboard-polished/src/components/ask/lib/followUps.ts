// ============================================================================
// followUps
// ----------------------------------------------------------------------------
// Heuristic suggestions for "what to ask next" rendered below an
// assistant research card.  Goal: make the conversational iteration
// loop visible — every workflow result lights up 3 obvious next steps
// (change a slot, switch a primitive, condition on a regime).
//
// V1 is rule-based on the workflow template_id.  V2 will replace this
// with an LLM-generated context-aware suggestion that knows the user's
// last analysis + their typical follow-up style.  The component
// signature is stable so the swap is a single-file change.
// ============================================================================

import type { CopilotMessage } from '@/types/copilot';

export type FollowUp = {
  id: string;
  /** What appears as the chip text. */
  label: string;
  /** What gets dropped into the composer when clicked. */
  prompt: string;
};

export function deriveFollowUps(message: CopilotMessage): FollowUp[] {
  if (message.role !== 'assistant' || message.isStreaming) return [];

  const wf = message.workflow;
  if (wf?.routeDecision.template_id === 'event_study') {
    return [
      {
        id: 'qt-regime',
        label: '→ now condition on QT regimes',
        prompt:
          'Now condition the same event study on QT regimes (high QT-pace vs low QT-pace) and compare.',
      },
      {
        id: 'change-threshold',
        label: '→ same template with 2.0σ threshold',
        prompt:
          'Re-run the event study with a 2.0σ threshold instead of 1.5σ and compare the abnormal moves.',
      },
      {
        id: 'swap-target',
        label: '→ swap target to 5Y UST',
        prompt:
          'Swap the target to the 5Y UST yield and re-run the same event study.',
      },
    ];
  }

  if (wf?.routeDecision.template_id === 'regime_conditioned_relationship') {
    return [
      {
        id: 'longer-window',
        label: '→ same analysis with 60-day rolling window',
        prompt:
          'Re-run the same regime-conditioned regression but with a 60-day rolling window.',
      },
      {
        id: 'flip-regime',
        label: '→ condition on bull-vs-bear curve regimes instead',
        prompt:
          'Re-run the analysis but condition on bull-vs-bear curve regimes instead of steepening-vs-flattening.',
      },
      {
        id: 'change-tenor',
        label: '→ change LHS to 5Y change',
        prompt:
          'Change the LHS series to the 5Y UST change and re-run the regime-conditioned regression.',
      },
    ];
  }

  // Supervisor / non-workflow turn — surface a generic follow-up to
  // open the result in the workspace and to compare to a counterpart
  // instrument.
  if (message.workspaceContext) {
    return [
      {
        id: 'compare',
        label: '→ compare against a different curve',
        prompt:
          'Compare the same metric against a different sovereign curve.',
      },
      {
        id: 'longer-history',
        label: '→ extend lookback to 5 years',
        prompt: 'Re-run the same analysis with a 5-year lookback.',
      },
      {
        id: 'z-score',
        label: '→ where does today sit on its 252-day z-score?',
        prompt: 'Where does today sit on its 252-day z-score for this series?',
      },
    ];
  }

  return [];
}
